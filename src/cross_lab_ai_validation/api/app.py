"""FastAPI application factory: REST API, A2A endpoint and the mounted MCP server."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager

from agentic_core.observability import bind_correlation_id, get_logger
from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from mcp.server.transport_security import TransportSecuritySettings

from cross_lab_ai_validation import __version__
from cross_lab_ai_validation.api.routes import router
from cross_lab_ai_validation.domain.errors import AssuranceError
from cross_lab_ai_validation.protocols import a2a
from cross_lab_ai_validation.protocols.mcp_server import build_mcp_server
from cross_lab_ai_validation.runtime.container import Container

ContainerFactory = Callable[[], Awaitable[Container]]

log = get_logger("cross_lab_ai_validation.api")


def create_app(
    factory: ContainerFactory,
    *,
    mcp_allowed_hosts: Sequence[str] = ("127.0.0.1:*", "localhost:*"),
    mcp_allowed_origins: Sequence[str] = (),
) -> FastAPI:
    holder: dict[str, Container] = {}

    def current() -> Container:
        return holder["container"]

    mcp = build_mcp_server(current)
    mcp_app = mcp.streamable_http_app(
        streamable_http_path="/",
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=list(mcp_allowed_hosts),
            allowed_origins=list(mcp_allowed_origins),
        ),
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        container = await factory()
        holder["container"] = container
        app.state.container = container
        try:
            async with mcp.session_manager.run():
                log.info("startup", version=__version__)
                yield
        finally:
            await container.aclose()

    app = FastAPI(
        title="Clinical Laboratory AI Assurance Platform",
        version=__version__,
        description=(
            "Cross-site validation of laboratory AI models: model registry with approved "
            "versions per site, benchmark harness, population shift detection, CLIA/CAP-style "
            "method verification and medical-director reapproval, on federated aggregates."
        ),
        lifespan=lifespan,
    )
    app.include_router(router)
    app.include_router(a2a.router)
    app.mount("/mcp", mcp_app)

    @app.middleware("http")
    async def correlation_and_metrics(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        incoming = request.headers.get("x-correlation-id")
        cid = incoming if incoming and len(incoming) <= 64 and incoming.isprintable() else None
        started = time.perf_counter()
        with bind_correlation_id(cid) as bound:
            response = await call_next(request)
        response.headers["X-Correlation-ID"] = bound
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        route = request.scope.get("route")
        path = getattr(
            route, "path", "/mcp" if request.url.path.startswith("/mcp") else "unmatched"
        )
        container: Container | None = getattr(request.app.state, "container", None)
        if container is not None:
            container.metrics.requests.labels(
                request.method, path, str(response.status_code)
            ).observe(time.perf_counter() - started)
        return response

    @app.exception_handler(AssuranceError)
    async def assurance_error(_: Request, exc: AssuranceError) -> JSONResponse:
        headers = {"WWW-Authenticate": "Bearer"} if exc.status == 401 else None
        return JSONResponse(
            status_code=exc.status,
            content={"error": exc.code, "error_description": exc.message},
            headers=headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": "invalid_request",
                "error_description": "request failed validation",
                "details": [
                    {"loc": list(e.get("loc", ())), "msg": str(e.get("msg", ""))}
                    for e in exc.errors()[:50]
                ],
            },
        )

    return app
