import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from google.cloud import firestore
from starlette.exceptions import HTTPException as StarletteHTTPException

from viteoh.config import Settings, get_settings
from viteoh.discord_api import DiscordAPIError, DiscordClient
from viteoh.receiver import InteractionReceiver
from viteoh.repository import FirestoreRepository
from viteoh.security import SignatureVerifier
from viteoh.tasks import TaskDispatcher
from viteoh.worker import InteractionProcessor
from viteoh.workspace import WEB_ROOT, Workspace
from viteoh.workspace_client import WorkspaceWorkerClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


def create_app(
    settings: Settings | None = None,
    *,
    receiver: InteractionReceiver | None = None,
    processor: InteractionProcessor | None = None,
    workspace: Workspace | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    resources: dict[str, Any] = {}
    if settings.service_role == "web" and workspace is None:
        if not settings.google_cloud_project:
            raise RuntimeError("The web service requires GOOGLE_CLOUD_PROJECT.")
        web_repository = FirestoreRepository(
            firestore.AsyncClient(
                project=settings.google_cloud_project,
                database=settings.firestore_database,
            )
        )
        workspace = Workspace(
            settings,
            web_repository,
            TaskDispatcher(settings),
            WorkspaceWorkerClient(settings),
        )
        resources["workspace"] = workspace

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if receiver is None and processor is None and workspace is None:
            tasks = TaskDispatcher(settings)
            if settings.service_role == "receiver":
                repository = None
                if settings.google_cloud_project:
                    firestore_client = firestore.AsyncClient(
                        project=settings.google_cloud_project,
                        database=settings.firestore_database,
                    )
                    repository = FirestoreRepository(firestore_client)
                resources["receiver"] = InteractionReceiver(
                    settings,
                    SignatureVerifier(
                        settings.discord_public_key,
                        settings.signature_max_age_seconds,
                    ),
                    tasks,
                    repository,
                )
            else:
                firestore_client = firestore.AsyncClient(
                    project=settings.google_cloud_project,
                    database=settings.firestore_database,
                )
                discord = DiscordClient(settings, httpx.AsyncClient(timeout=10.0))
                resources["processor"] = InteractionProcessor(
                    settings,
                    FirestoreRepository(firestore_client),
                    tasks,
                    discord,
                )
                resources["discord"] = discord
        yield
        if "discord" in resources:
            await resources["discord"].close()
        if "workspace" in resources:
            await resources["workspace"].close()

    application = FastAPI(
        title="Vite-oh",
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )

    @application.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "role": settings.service_role}

    if settings.service_role == "receiver":

        @application.post("/interactions")
        async def interactions(request: Request) -> JSONResponse:
            active_receiver = receiver or resources["receiver"]
            status, payload = await active_receiver.receive(
                await request.body(),
                request.headers.get("X-Signature-Ed25519"),
                request.headers.get("X-Signature-Timestamp"),
            )
            return JSONResponse(payload, status_code=status)

    elif settings.service_role == "worker":

        @application.post("/tasks/interactions", status_code=204)
        async def process_interaction(request: Request) -> Response:
            active_processor = processor or resources["processor"]
            await active_processor.process(await request.json())
            return Response(status_code=204)

        @application.post("/tasks/proposals/{proposal_id}/finalize")
        async def finalize(proposal_id: str) -> dict[str, str]:
            active_processor = processor or resources["processor"]
            return {"result": await active_processor.finalize(proposal_id)}

        @application.post("/tasks/reconcile")
        async def reconcile() -> dict[str, int]:
            active_processor = processor or resources["processor"]
            return await active_processor.reconcile()

        @application.post("/tasks/workspace", status_code=204)
        async def process_workspace(request: Request) -> Response:
            active_processor = processor or resources["processor"]
            await active_processor.process_workspace(await request.json())
            return Response(status_code=204)

        @application.post("/internal/workspace/exchange")
        async def exchange_workspace(request: Request) -> JSONResponse:
            active_processor = processor or resources["processor"]
            body = await request.json()
            result = await active_processor.exchange_workspace_launch(
                str(body.get("code", ""))
            )
            if not result:
                return JSONResponse(
                    {"detail": "This workspace link is invalid, expired, or used."},
                    status_code=401,
                )
            return JSONResponse(result)

        @application.post("/internal/workspace/access")
        async def workspace_access(request: Request) -> JSONResponse:
            active_processor = processor or resources["processor"]
            body = await request.json()
            try:
                result = await active_processor.workspace_access(
                    str(body.get("guild_id", "")),
                    str(body.get("user_id", "")),
                )
            except DiscordAPIError as exc:
                logging.getLogger(__name__).exception(
                    "Could not authorize workspace member"
                )
                if exc.retryable:
                    return JSONResponse(
                        {
                            "detail": (
                                "Discord is temporarily unavailable. "
                                "Please try again shortly."
                            )
                        },
                        status_code=503,
                    )
                return JSONResponse(
                    {"detail": "You are not authorized for this server."},
                    status_code=403,
                )
            return JSONResponse(result)

        @application.post("/internal/workspace/guilds")
        async def workspace_guilds(request: Request) -> JSONResponse:
            active_processor = processor or resources["processor"]
            body = await request.json()
            guild_ids = body.get("guild_ids")
            if not isinstance(guild_ids, list):
                return JSONResponse(
                    {"detail": "Server identifiers are required."}, status_code=422
                )
            result = await active_processor.workspace_guild_summaries(
                [str(item) for item in guild_ids],
                str(body.get("user_id", "")),
            )
            return JSONResponse(result)

        @application.post("/internal/workspace/members")
        async def workspace_members(request: Request) -> JSONResponse:
            active_processor = processor or resources["processor"]
            body = await request.json()
            try:
                result = await active_processor.workspace_member_search(
                    str(body.get("guild_id", "")),
                    str(body.get("user_id", "")),
                    str(body.get("query", "")),
                )
            except DiscordAPIError as exc:
                if exc.retryable:
                    return JSONResponse(
                        {"detail": "Discord is temporarily unavailable."},
                        status_code=503,
                    )
                return JSONResponse(
                    {
                        "detail": (
                            "Member search is unavailable. Confirm that the "
                            "Server Members Intent is enabled for Vite-oh."
                        )
                    },
                    status_code=503,
                )
            return JSONResponse(result)
    else:
        assert workspace is not None
        application.include_router(workspace.router)

        @application.exception_handler(StarletteHTTPException)
        async def workspace_http_error(
            request: Request, exc: StarletteHTTPException
        ) -> Response:
            if request.url.path.startswith("/static/"):
                return JSONResponse(
                    {"detail": str(exc.detail)}, status_code=exc.status_code
                )
            detail = (
                str(exc.detail)
                if isinstance(exc.detail, str)
                else "The requested action could not be completed."
            )
            return workspace.error_response(request, exc.status_code, detail)

        @application.exception_handler(RequestValidationError)
        async def workspace_validation_error(
            request: Request, exc: RequestValidationError
        ) -> Response:
            error = exc.errors()[0] if exc.errors() else {}
            location = tuple(str(item) for item in error.get("loc", ()))
            field = next(
                (
                    item.replace("_", " ")
                    for item in reversed(location)
                    if item not in {"body", "query", "path"}
                ),
                "",
            )
            message = str(error.get("msg") or "Check the information you entered.")
            return workspace.error_response(
                request,
                422,
                message.removeprefix("Value error, "),
                field=field,
            )

        @application.exception_handler(Exception)
        async def workspace_unexpected_error(
            request: Request, exc: Exception
        ) -> Response:
            logging.getLogger(__name__).exception(
                "Unhandled workspace request error",
                extra={"path": request.url.path},
            )
            return workspace.error_response(
                request,
                500,
                "Nothing was saved. Try again, and use the displayed reference "
                "from any failed job if the problem continues.",
            )

        application.mount(
            "/static",
            StaticFiles(directory=WEB_ROOT / "static"),
            name="static",
        )

        @application.middleware("http")
        async def workspace_security_headers(
            request: Request, call_next: Any
        ) -> Response:
            response = cast(Response, await call_next(request))
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "img-src 'self' data: https://cdn.discordapp.com; "
                "style-src 'self'; script-src 'self'; "
                "connect-src 'self'; frame-ancestors 'none'; form-action 'self'"
            )
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )
            response.headers["Cache-Control"] = (
                "public, max-age=86400, immutable"
                if request.url.path.startswith("/static/")
                else "no-store"
            )
            response.headers["Permissions-Policy"] = (
                "camera=(), microphone=(), geolocation=()"
            )
            return response

    return application


app = create_app()
