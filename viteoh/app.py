import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from google.cloud import firestore

from viteoh.config import Settings, get_settings
from viteoh.discord_api import DiscordClient
from viteoh.receiver import InteractionReceiver
from viteoh.repository import FirestoreRepository
from viteoh.security import SignatureVerifier
from viteoh.tasks import TaskDispatcher
from viteoh.worker import InteractionProcessor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


def create_app(
    settings: Settings | None = None,
    *,
    receiver: InteractionReceiver | None = None,
    processor: InteractionProcessor | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    resources: dict[str, Any] = {}

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if receiver is None and processor is None:
            tasks = TaskDispatcher(settings)
            if settings.service_role == "receiver":
                resources["receiver"] = InteractionReceiver(
                    settings,
                    SignatureVerifier(
                        settings.discord_public_key,
                        settings.signature_max_age_seconds,
                    ),
                    tasks,
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

    else:

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

    return application


app = create_app()
