import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from viteoh.config import Settings
from viteoh.domain import GuildConfig, Proposal, ProposalStatus
from viteoh.repository import Repository
from viteoh.tasks import TaskDispatcher
from viteoh.workspace_client import WorkspaceWorkerClient, WorkspaceWorkerError
from viteoh.workspace_markdown import safe_markdown
from viteoh.workspace_security import InvalidToken, SignedTokenCodec, new_csrf_token

SESSION_COOKIE = "viteoh_session"
WEB_ROOT = Path(__file__).parent / "web"
logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class WorkspaceSession:
    user_id: str
    display_name: str
    guild_ids: tuple[str, ...]
    csrf: str


class Workspace:
    def __init__(
        self,
        settings: Settings,
        repository: Repository,
        tasks: TaskDispatcher,
        worker: WorkspaceWorkerClient,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.tasks = tasks
        self.worker = worker
        self.codec = SignedTokenCodec(settings.workspace_signing_secret)
        self.templates = Jinja2Templates(directory=WEB_ROOT / "templates")
        self.templates.env.filters["safe_markdown"] = safe_markdown
        self.router = APIRouter()
        self._routes()

    async def close(self) -> None:
        await self.worker.close()

    def _routes(self) -> None:
        router = self.router

        @router.get("/", response_class=HTMLResponse)
        async def landing(request: Request) -> Response:
            session = self._session(request)
            if session:
                return RedirectResponse("/app", status_code=303)
            return self.templates.TemplateResponse(
                request,
                "landing.html",
                {"title": "Vite-oh", "session": None},
            )

        @router.get("/launch")
        async def launch(request: Request, code: str = "") -> Response:
            if not code:
                return self._error(request, "This workspace link is incomplete.", 400)
            try:
                launch_data = await self.worker.exchange_launch(code)
            except WorkspaceWorkerError as exc:
                return self._error(request, str(exc), 401)
            existing = self._session(request)
            user_id = str(launch_data["user_id"])
            guild_id = str(launch_data["guild_id"])
            existing_guilds = (
                existing.guild_ids if existing and existing.user_id == user_id else ()
            )
            guild_ids = tuple(dict.fromkeys((*existing_guilds, guild_id)))
            session = WorkspaceSession(
                user_id=user_id,
                display_name=str(launch_data.get("display_name") or "Discord member"),
                guild_ids=guild_ids,
                csrf=new_csrf_token(),
            )
            proposal_id = str(launch_data.get("proposal_id") or "")
            destination = (
                f"/app/proposals/{proposal_id}?guild={guild_id}"
                if proposal_id
                else f"/app?guild={guild_id}"
            )
            response = RedirectResponse(destination, status_code=303)
            self._set_session(response, session)
            return response

        @router.post("/logout")
        async def logout(request: Request, csrf: str = Form("")) -> Response:
            session = self._require_session(request)
            self._check_csrf(session, csrf)
            response = RedirectResponse("/", status_code=303)
            response.delete_cookie(SESSION_COOKIE, path="/")
            return response

        @router.get("/app", response_class=HTMLResponse)
        async def dashboard(
            request: Request, guild: str = "", before: int = 0
        ) -> Response:
            session, guild_id, access, configs = await self._page_access(request, guild)
            config = await self.repository.get_guild_config(guild_id)
            visible = set(str(item) for item in access["visible_channel_ids"])
            active = []
            history = []
            next_before = 0
            if config:
                active = [
                    item
                    for item in await self.repository.list_active(guild_id)
                    if item.output_channel_id in visible
                ]
                cursor = (
                    datetime.fromtimestamp(before / 1_000_000, UTC)
                    if before > 0
                    else None
                )
                page = list(
                    await self.repository.list_guild_proposals(
                        guild_id, limit=51, before=cursor
                    )
                )
                visible_page = [
                    item
                    for item in page[:50]
                    if item.output_channel_id in visible
                    and item.status is not ProposalStatus.ACTIVE
                ]
                history = visible_page
                if len(page) > 50:
                    next_before = int(page[49].created_at.timestamp() * 1_000_000)
            return self.templates.TemplateResponse(
                request,
                "dashboard.html",
                self._context(
                    session,
                    guild_id,
                    access,
                    configs,
                    config=config,
                    active=active,
                    history=history,
                    next_before=next_before,
                ),
            )

        @router.get("/app/proposals/new", response_class=HTMLResponse)
        async def new_proposal(request: Request, guild: str = "") -> Response:
            session, guild_id, access, configs = await self._page_access(request, guild)
            config = await self.repository.get_guild_config(guild_id)
            if not config:
                return RedirectResponse(
                    f"/app/settings?guild={guild_id}", status_code=303
                )
            types = await self.repository.list_templates(guild_id)
            return self.templates.TemplateResponse(
                request,
                "proposal_form.html",
                self._context(
                    session,
                    guild_id,
                    access,
                    configs,
                    config=config,
                    proposal_types=types,
                ),
            )

        @router.post("/app/proposals")
        async def create_proposal(
            request: Request,
            guild_id: str = Form(...),
            csrf: str = Form(...),
            title: str = Form(...),
            proposal_type: str = Form("builtin:general"),
            context: str = Form(""),
        ) -> Response:
            session = self._require_session(request)
            self._check_csrf(session, csrf)
            await self._authorize(session, guild_id)
            title = " ".join(title.split())
            context = context.strip()
            if not 1 <= len(title) <= 100:
                raise HTTPException(422, "Titles must contain 1 to 100 characters.")
            if len(context) > 1000:
                raise HTTPException(422, "Context cannot exceed 1,000 characters.")
            return await self._enqueue(
                session,
                guild_id,
                "create",
                {
                    "title": title,
                    "context": context,
                    "type_id": proposal_type,
                },
            )

        @router.get("/app/proposals/{proposal_id}", response_class=HTMLResponse)
        async def proposal_detail(
            request: Request, proposal_id: str, guild: str = ""
        ) -> Response:
            session = self._require_session(request)
            proposal = await self.repository.get_proposal(proposal_id)
            if not proposal:
                raise HTTPException(404, "Proposal not found.")
            guild_id = guild or proposal.guild_id
            session, guild_id, access, configs = await self._page_access(
                request, guild_id
            )
            if proposal.guild_id != guild_id or proposal.output_channel_id not in set(
                access["visible_channel_ids"]
            ):
                raise HTTPException(404, "Proposal not found.")
            return self.templates.TemplateResponse(
                request,
                "proposal_detail.html",
                self._context(
                    session,
                    guild_id,
                    access,
                    configs,
                    proposal=proposal,
                    discord_url=_proposal_link(proposal),
                ),
            )

        @router.post("/app/proposals/{proposal_id}/delete")
        async def delete_proposal(
            request: Request,
            proposal_id: str,
            guild_id: str = Form(...),
            csrf: str = Form(...),
        ) -> Response:
            session = self._require_session(request)
            self._check_csrf(session, csrf)
            access = await self._authorize(session, guild_id)
            self._require_admin(access, session.user_id)
            return await self._enqueue(
                session, guild_id, "delete", {"proposal_id": proposal_id}
            )

        @router.get("/app/preferences", response_class=HTMLResponse)
        async def preferences(request: Request, guild: str = "") -> Response:
            session, guild_id, access, configs = await self._page_access(request, guild)
            subscribed = await self.repository.get_guild_subscription(
                guild_id, session.user_id
            )
            nudges = await self.repository.get_nudges_enabled(guild_id, session.user_id)
            return self.templates.TemplateResponse(
                request,
                "preferences.html",
                self._context(
                    session,
                    guild_id,
                    access,
                    configs,
                    subscribed=subscribed,
                    nudges=nudges,
                ),
            )

        @router.post("/app/preferences")
        async def update_preferences(
            request: Request,
            guild_id: str = Form(...),
            csrf: str = Form(...),
            new_proposals: str = Form(""),
            nudges: str = Form(""),
        ) -> Response:
            session = self._require_session(request)
            self._check_csrf(session, csrf)
            await self._authorize(session, guild_id)
            return await self._enqueue(
                session,
                guild_id,
                "preferences",
                {
                    "new_proposals": new_proposals == "on",
                    "nudges": nudges == "on",
                },
            )

        @router.get("/app/settings", response_class=HTMLResponse)
        async def settings_page(request: Request, guild: str = "") -> Response:
            session, guild_id, access, configs = await self._page_access(request, guild)
            self._require_admin(access, session.user_id)
            config = await self.repository.get_guild_config(guild_id)
            return self.templates.TemplateResponse(
                request,
                "settings.html",
                self._context(
                    session,
                    guild_id,
                    access,
                    configs,
                    config=config,
                    output_channels=access["output_channels"],
                ),
            )

        @router.post("/app/settings")
        async def update_settings(
            request: Request,
            guild_id: str = Form(...),
            csrf: str = Form(...),
            channel_id: str = Form(...),
            duration_minutes: int = Form(...),
        ) -> Response:
            session = self._require_session(request)
            self._check_csrf(session, csrf)
            access = await self._authorize(session, guild_id)
            self._require_admin(access, session.user_id)
            if not 1 <= duration_minutes <= 10080:
                raise HTTPException(
                    422, "Duration must be between 1 and 10,080 minutes."
                )
            return await self._enqueue(
                session,
                guild_id,
                "configure",
                {
                    "channel_id": channel_id,
                    "duration_minutes": duration_minutes,
                },
            )

        @router.get("/app/types", response_class=HTMLResponse)
        async def types_page(request: Request, guild: str = "") -> Response:
            session, guild_id, access, configs = await self._page_access(request, guild)
            self._require_admin(access, session.user_id)
            types = await self.repository.list_templates(guild_id)
            return self.templates.TemplateResponse(
                request,
                "types.html",
                self._context(
                    session,
                    guild_id,
                    access,
                    configs,
                    proposal_types=types,
                ),
            )

        @router.post("/app/types")
        async def save_type(
            request: Request,
            guild_id: str = Form(...),
            csrf: str = Form(...),
            name: str = Form(...),
            description: str = Form(...),
            type_id: str = Form(""),
        ) -> Response:
            session = self._require_session(request)
            self._check_csrf(session, csrf)
            access = await self._authorize(session, guild_id)
            self._require_admin(access, session.user_id)
            return await self._enqueue(
                session,
                guild_id,
                "type_save",
                {
                    "type_id": type_id,
                    "name": name,
                    "description": description,
                },
            )

        @router.post("/app/types/{type_id}/delete")
        async def delete_type(
            request: Request,
            type_id: str,
            guild_id: str = Form(...),
            csrf: str = Form(...),
        ) -> Response:
            session = self._require_session(request)
            self._check_csrf(session, csrf)
            access = await self._authorize(session, guild_id)
            self._require_admin(access, session.user_id)
            return await self._enqueue(
                session, guild_id, "type_delete", {"type_id": type_id}
            )

        @router.get("/app/jobs/{job_id}", response_class=HTMLResponse)
        async def job_status(
            request: Request, job_id: str, guild: str = ""
        ) -> Response:
            session = self._require_session(request)
            job = await self.repository.get_workspace_job(job_id)
            if job and (
                job.requester_hash != self.codec.fingerprint(session.user_id)
                or job.guild_id not in session.guild_ids
            ):
                raise HTTPException(404, "Job not found.")
            status = job.status if job else "queued"
            return self.templates.TemplateResponse(
                request,
                "job.html",
                {
                    "title": "Saving",
                    "session": session,
                    "job": job,
                    "status": status,
                    "guild_id": guild or (job.guild_id if job else ""),
                },
            )

    async def _enqueue(
        self,
        session: WorkspaceSession,
        guild_id: str,
        action: str,
        data: dict[str, Any],
    ) -> Response:
        job_id = secrets.token_hex(16)
        await self.tasks.enqueue_workspace(
            {
                "id": job_id,
                "action": action,
                "actor_user_id": session.user_id,
                "guild_id": guild_id,
                "requester_hash": self.codec.fingerprint(session.user_id),
                "data": data,
            }
        )
        return RedirectResponse(f"/app/jobs/{job_id}?guild={guild_id}", status_code=303)

    async def _page_access(
        self, request: Request, guild_id: str
    ) -> tuple[
        WorkspaceSession,
        str,
        dict[str, Any],
        list[GuildConfig],
    ]:
        session = self._require_session(request)
        selected = guild_id or (session.guild_ids[0] if session.guild_ids else "")
        if not selected or selected not in session.guild_ids:
            raise HTTPException(403, "Open Vite-oh from this Discord server first.")
        access = await self._authorize(session, selected)
        configs = list(
            await self.repository.list_guild_configs(list(session.guild_ids))
        )
        if not any(item.guild_id == selected for item in configs):
            configs.append(
                GuildConfig(
                    guild_id=selected,
                    guild_name=str(access["guild_name"]),
                    output_channel_id="",
                    proposal_timeout_seconds=0,
                    configured_by="",
                    created_at=_epoch(),
                    updated_at=_epoch(),
                )
            )
        return session, selected, access, configs

    async def _authorize(
        self, session: WorkspaceSession, guild_id: str
    ) -> dict[str, Any]:
        if guild_id not in session.guild_ids:
            raise HTTPException(403, "Open Vite-oh from this Discord server first.")
        try:
            access = await self.worker.get_access(guild_id, session.user_id)
        except WorkspaceWorkerError as exc:
            logger.warning(
                "Workspace authorization denied",
                extra={"guild_id": guild_id},
            )
            raise HTTPException(403, str(exc)) from exc
        if not access.get("is_member"):
            logger.warning(
                "Workspace authorization denied",
                extra={"guild_id": guild_id},
            )
            raise HTTPException(403, "You are no longer a member of this server.")
        return access

    def _session(self, request: Request) -> WorkspaceSession | None:
        token = request.cookies.get(SESSION_COOKIE, "")
        if not token:
            return None
        try:
            claims = self.codec.decode(token, "session")
            return WorkspaceSession(
                user_id=str(claims["user_id"]),
                display_name=str(claims.get("display_name") or "Discord member"),
                guild_ids=tuple(str(item) for item in claims.get("guild_ids") or []),
                csrf=str(claims["csrf"]),
            )
        except (InvalidToken, KeyError, TypeError):
            return None

    def _require_session(self, request: Request) -> WorkspaceSession:
        session = self._session(request)
        if not session:
            raise HTTPException(
                401, "Run `/proposal` in Discord to open your private workspace."
            )
        return session

    def _set_session(self, response: Response, session: WorkspaceSession) -> None:
        token = self.codec.encode(
            "session",
            {
                "user_id": session.user_id,
                "display_name": session.display_name,
                "guild_ids": list(session.guild_ids),
                "csrf": session.csrf,
            },
            ttl_seconds=self.settings.workspace_session_ttl_seconds,
        )
        response.set_cookie(
            SESSION_COOKIE,
            token,
            max_age=self.settings.workspace_session_ttl_seconds,
            httponly=True,
            secure=self.settings.secure_cookies,
            samesite="lax",
            path="/",
        )

    @staticmethod
    def _check_csrf(session: WorkspaceSession, supplied: str) -> None:
        if not secrets.compare_digest(session.csrf, supplied):
            raise HTTPException(403, "This form has expired. Refresh and try again.")

    def _context(
        self,
        session: WorkspaceSession,
        guild_id: str,
        access: dict[str, Any],
        configs: list[GuildConfig],
        **extra: Any,
    ) -> dict[str, Any]:
        return {
            "title": extra.pop("title", "Vite-oh"),
            "session": session,
            "guild_id": guild_id,
            "guild_name": access["guild_name"],
            "can_manage": bool(
                access.get("can_manage")
                or session.user_id == self.settings.discord_owner_user_id
            ),
            "guilds": configs,
            **extra,
        }

    def _require_admin(self, access: dict[str, Any], user_id: str) -> None:
        if not access.get("can_manage") and (
            not self.settings.discord_owner_user_id
            or user_id != self.settings.discord_owner_user_id
        ):
            raise HTTPException(403, "You need Manage Server permission.")

    def _error(self, request: Request, message: str, status: int) -> Response:
        return self.templates.TemplateResponse(
            request,
            "error.html",
            {"title": "Workspace unavailable", "session": None, "message": message},
            status_code=status,
        )


def _proposal_link(proposal: Proposal) -> str:
    return (
        f"https://discord.com/channels/{proposal.guild_id}/"
        f"{proposal.output_channel_id}/{proposal.message_id}"
    )


def _epoch() -> Any:
    from datetime import UTC, datetime

    return datetime(1970, 1, 1, tzinfo=UTC)
