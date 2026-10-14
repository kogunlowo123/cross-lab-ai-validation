"""A2A (Agent2Agent) interface: Agent Card and JSON-RPC ``message/send``.

A calling agent sends a Message whose data part names a skill and its input:

    {"kind": "data", "data": {"skill": "detect_population_shift", "input": {...}}}

The skill runs with the caller's identity and the result is returned as a
Task artifact. Task states map the governance outcome: ``completed``,
``input-required`` (reapproval awaiting the medical director), ``rejected``
(policy denied) or ``failed`` (invalid evidence or state).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from agentic_core.domain.identifiers import new_id
from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from cross_lab_ai_validation import __version__
from cross_lab_ai_validation.config import Settings
from cross_lab_ai_validation.domain.errors import (
    ApprovalRequiredError,
    AssuranceError,
    ForbiddenError,
    UnauthorizedError,
)
from cross_lab_ai_validation.protocols.invoke import invoke_skill
from cross_lab_ai_validation.protocols.skills import SKILLS
from cross_lab_ai_validation.runtime.container import Container

PROTOCOL_VERSION = "0.3.0"

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
UNAUTHENTICATED = -32001


class TextPart(BaseModel):
    model_config = ConfigDict(extra="allow")

    kind: Literal["text"]
    text: str


class DataPart(BaseModel):
    model_config = ConfigDict(extra="allow")

    kind: Literal["data"]
    data: dict[str, Any]


Part = Annotated[TextPart | DataPart, Field(discriminator="kind")]


class Message(BaseModel):
    model_config = ConfigDict(extra="allow")

    kind: Literal["message"] = "message"
    role: Literal["user", "agent"]
    parts: list[Part] = Field(min_length=1, max_length=20)
    messageId: str = Field(min_length=1, max_length=128)  # noqa: N815 - A2A wire name
    contextId: str | None = Field(default=None, max_length=128)  # noqa: N815 - A2A wire name


class MessageSendParams(BaseModel):
    model_config = ConfigDict(extra="allow")

    message: Message


def agent_card(settings: Settings) -> dict[str, Any]:
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "name": "Clinical Laboratory AI Assurance Agent",
        "description": (
            "Cross-site validation for laboratory AI models: benchmarks, population shift "
            "detection, CLIA/CAP-style method verification and medical-director reapproval, "
            "computed from federated aggregate statistics."
        ),
        "url": f"{settings.public_base}/a2a",
        "preferredTransport": "JSONRPC",
        "version": __version__,
        "documentationUrl": "https://github.com/kogunlowo123/cross-lab-ai-validation",
        "capabilities": {
            "streaming": False,
            "pushNotifications": False,
            "stateTransitionHistory": False,
        },
        "securitySchemes": {
            "agentIdentity": {
                "type": "http",
                "scheme": "bearer",
                "bearerFormat": "JWT",
                "description": "Agent credential issued by agent-identity-fabric",
            },
            "staffOidc": {
                "type": "openIdConnect",
                "openIdConnectUrl": (
                    f"{settings.oidc_issuer.rstrip('/')}/.well-known/openid-configuration"
                ),
            },
        },
        "security": [{"agentIdentity": []}, {"staffOidc": []}],
        "defaultInputModes": ["application/json"],
        "defaultOutputModes": ["application/json"],
        "skills": [
            {
                "id": skill.id,
                "name": skill.name,
                "description": skill.description,
                "tags": list(skill.tags),
                "inputModes": ["application/json"],
                "outputModes": ["application/json"],
            }
            for skill in SKILLS.values()
        ],
    }


def _task(
    message: Message,
    *,
    state: str,
    text: str,
    skill_id: str,
    result: dict[str, Any] | None,
) -> dict[str, Any]:
    task: dict[str, Any] = {
        "kind": "task",
        "id": new_id(),
        "contextId": message.contextId or new_id(),
        "status": {
            "state": state,
            "timestamp": datetime.now(UTC).isoformat(),
            "message": {
                "kind": "message",
                "role": "agent",
                "messageId": new_id(),
                "parts": [{"kind": "text", "text": text}],
            },
        },
        "history": [message.model_dump(mode="json", exclude_none=True)],
    }
    if result is not None:
        task["artifacts"] = [
            {
                "artifactId": new_id(),
                "name": skill_id,
                "parts": [{"kind": "data", "data": result}],
            }
        ]
    return task


router = APIRouter(tags=["a2a"])


@router.get("/.well-known/agent-card.json")
async def get_agent_card(request: Request) -> dict[str, Any]:
    """A2A Agent Card describing skills, endpoint and security schemes."""
    container: Container = request.app.state.container
    return agent_card(container.settings)


class RpcError(Exception):
    def __init__(
        self,
        code: int,
        message: str,
        *,
        request_id: Any = None,
        status: int = 200,
        data: Any = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.request_id = request_id
        self.status = status
        self.data = data

    def response(self) -> JSONResponse:
        error: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.data is not None:
            error["data"] = self.data
        headers = {"WWW-Authenticate": "Bearer"} if self.status == 401 else None
        return JSONResponse(
            status_code=self.status,
            content={"jsonrpc": "2.0", "id": self.request_id, "error": error},
            headers=headers,
        )


def parse_send(body: Any) -> tuple[Any, Message, str, dict[str, Any]]:
    """Validate a ``message/send`` envelope; return id, message, skill and its input."""
    if not isinstance(body, dict):
        raise RpcError(INVALID_REQUEST, "request must be a JSON object")
    request_id = body.get("id")
    if not isinstance(request_id, str | int) or isinstance(request_id, bool):
        request_id = None
    if body.get("jsonrpc") != "2.0" or not isinstance(body.get("method"), str):
        raise RpcError(INVALID_REQUEST, "invalid JSON-RPC 2.0 request", request_id=request_id)
    if body["method"] != "message/send":
        raise RpcError(
            METHOD_NOT_FOUND, f"method {body['method']!r} not supported", request_id=request_id
        )
    try:
        params = MessageSendParams.model_validate(body.get("params"))
    except ValidationError as exc:
        raise RpcError(
            INVALID_PARAMS, f"invalid params: {exc.error_count()} errors", request_id=request_id
        ) from exc
    data = next((p.data for p in params.message.parts if isinstance(p, DataPart)), None)
    skill = data.get("skill") if data else None
    arguments = data.get("input") if data else None
    if not isinstance(skill, str) or not isinstance(arguments, dict):
        raise RpcError(
            INVALID_PARAMS,
            "message needs a data part with 'skill' and 'input'",
            request_id=request_id,
        )
    if skill not in SKILLS:
        raise RpcError(
            INVALID_PARAMS, f"unknown skill {skill!r}", request_id=request_id, data=sorted(SKILLS)
        )
    return request_id, params.message, skill, arguments


def _result(request_id: Any, task: dict[str, Any]) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": request_id, "result": task})


@router.post(
    "/a2a",
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": {"type": "object"}}},
        }
    },
)
async def a2a_endpoint(
    request: Request, authorization: Annotated[str | None, Header()] = None
) -> JSONResponse:
    """JSON-RPC 2.0 endpoint implementing A2A ``message/send``."""
    try:
        try:
            body = await request.json()
        except ValueError as exc:
            raise RpcError(PARSE_ERROR, "parse error") from exc
        request_id, message, skill_id, arguments = parse_send(body)
        try:
            _, result = await invoke_skill(
                request.app.state.container,
                protocol="a2a",
                skill_id=skill_id,
                authorization=authorization,
                arguments=arguments,
            )
        except UnauthorizedError as exc:
            raise RpcError(
                UNAUTHENTICATED, f"unauthorized: {exc.message}", request_id=request_id, status=401
            ) from exc
        except ValidationError as exc:
            raise RpcError(
                INVALID_PARAMS,
                f"invalid skill input: {exc.error_count()} errors",
                request_id=request_id,
            ) from exc
        except AssuranceError as exc:
            state = (
                "rejected" if isinstance(exc, ForbiddenError | ApprovalRequiredError) else "failed"
            )
            task = _task(
                message,
                state=state,
                text=f"{exc.code}: {exc.message}",
                skill_id=skill_id,
                result=None,
            )
            return _result(request_id, task)
    except RpcError as exc:
        return exc.response()

    payload = result.model_dump(mode="json")
    if SKILLS[skill_id].human_gate:
        state = "input-required"
        text = (
            f"Reapproval {payload.get('request_id')} is pending medical-director sign-off; "
            "clinical use is not permitted until it is signed."
        )
    else:
        state, text = "completed", f"{SKILLS[skill_id].name} completed"
    task = _task(message, state=state, text=text, skill_id=skill_id, result=payload)
    return _result(request_id, task)
