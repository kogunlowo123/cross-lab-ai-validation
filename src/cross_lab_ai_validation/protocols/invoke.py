"""One invocation path for agent protocols: authenticate, run the skill, meter, audit."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ValidationError

from cross_lab_ai_validation.domain.errors import AssuranceError
from cross_lab_ai_validation.protocols.skills import SKILLS
from cross_lab_ai_validation.runtime.container import Container
from cross_lab_ai_validation.security.authentication import Principal


async def invoke_skill(
    container: Container,
    *,
    protocol: str,
    skill_id: str,
    authorization: str | None,
    arguments: Mapping[str, Any],
) -> tuple[Principal, BaseModel]:
    skill = SKILLS[skill_id]
    who: Principal | None = None
    try:
        who = await container.authenticator.authenticate(authorization)
        result = await skill.run(container.service, who, arguments)
    except AssuranceError as exc:
        container.metrics.protocol_calls.labels(protocol, skill_id, exc.code).inc()
        await container.service.record_call(
            who, protocol=protocol, operation=skill_id, outcome=exc.code, detail=exc.message
        )
        raise
    except ValidationError as exc:
        container.metrics.protocol_calls.labels(protocol, skill_id, "invalid_params").inc()
        await container.service.record_call(
            who,
            protocol=protocol,
            operation=skill_id,
            outcome="invalid_params",
            detail=f"{exc.error_count()} validation errors",
        )
        raise
    container.metrics.protocol_calls.labels(protocol, skill_id, "success").inc()
    await container.service.record_call(
        who, protocol=protocol, operation=skill_id, outcome="success"
    )
    return who, result
