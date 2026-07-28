import hashlib
import logging
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from viteoh.config import Settings
from viteoh.domain import MAX_PROPOSAL_DURATION_MINUTES, Proposal, ProposalStatus
from viteoh.proposal_types import MAX_CUSTOM_TYPES
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
        self.templates.env.globals["asset_version"] = settings.asset_version
        self.router = APIRouter()
        self._member_search_times: dict[tuple[str, str], float] = {}
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
                status = 401 if exc.status_code in {400, 401, 403, 404} else 503
                return self._error(request, str(exc), status)
            existing = self._session(request)
            user_id = str(launch_data["user_id"])
            guild_id = str(launch_data["guild_id"])
            launched_guilds = tuple(
                str(item)
                for item in launch_data.get("guild_ids") or [guild_id]
                if str(item)
            )
            existing_guilds = (
                existing.guild_ids if existing and existing.user_id == user_id else ()
            )
            guild_ids = tuple(
                dict.fromkeys((*existing_guilds, *launched_guilds, guild_id))
            )
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
                    active_page="overview",
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
            types = await self.repository.list_types(guild_id)
            baseline_minutes = config.proposal_timeout_seconds // 60
            return self.templates.TemplateResponse(
                request,
                "proposal_form.html",
                self._context(
                    session,
                    guild_id,
                    access,
                    configs,
                    active_page="create",
                    config=config,
                    baseline_minutes=baseline_minutes,
                    baseline_duration=_format_duration(baseline_minutes),
                    maximum_duration=_format_duration(MAX_PROPOSAL_DURATION_MINUTES),
                    maximum_duration_minutes=MAX_PROPOSAL_DURATION_MINUTES,
                    proposal_types=types,
                ),
            )

        @router.get("/app/archive", response_class=HTMLResponse)
        async def proposal_archive(
            request: Request, guild: str = "", before: int = 0
        ) -> Response:
            session, guild_id, access, configs = await self._page_access(request, guild)
            self._require_admin(access, session.user_id)
            visible = set(str(item) for item in access["visible_channel_ids"])
            cursor = (
                datetime.fromtimestamp(before / 1_000_000, UTC) if before > 0 else None
            )
            page = list(
                await self.repository.list_guild_proposals(
                    guild_id,
                    archived=True,
                    limit=51,
                    before=cursor,
                )
            )
            proposals = [
                item for item in page[:50] if item.output_channel_id in visible
            ]
            next_before = (
                int(page[49].created_at.timestamp() * 1_000_000)
                if len(page) > 50
                else 0
            )
            return self.templates.TemplateResponse(
                request,
                "archive.html",
                self._context(
                    session,
                    guild_id,
                    access,
                    configs,
                    active_page="archive",
                    proposals=proposals,
                    next_before=next_before,
                ),
            )

        @router.post("/app/proposals")
        async def create_proposal(
            request: Request,
            guild_id: str = Form(...),
            csrf: str = Form(...),
            title: str = Form(...),
            proposal_type: str = Form(""),
            context: str = Form(""),
            duration_minutes: str = Form(""),
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
            config = await self.repository.get_guild_config(guild_id)
            if not config:
                raise HTTPException(409, "This server must be configured first.")
            baseline_minutes = config.proposal_timeout_seconds // 60
            try:
                selected_duration = int(duration_minutes or baseline_minutes)
            except ValueError as exc:
                raise HTTPException(422, "Choose a valid voting duration.") from exc
            if not (
                baseline_minutes <= selected_duration <= MAX_PROPOSAL_DURATION_MINUTES
            ):
                raise HTTPException(
                    422,
                    "Voting duration cannot be shorter than the server minimum "
                    f"of {baseline_minutes:,} minutes or longer than "
                    f"{MAX_PROPOSAL_DURATION_MINUTES:,} minutes.",
                )
            return await self._enqueue(
                session,
                guild_id,
                "create",
                {
                    "title": title,
                    "context": context,
                    "type_id": proposal_type.strip(),
                    "duration_minutes": selected_duration,
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
            action_context = await self._proposal_action_context(session, proposal)
            return self.templates.TemplateResponse(
                request,
                "proposal_detail.html",
                self._context(
                    session,
                    guild_id,
                    access,
                    configs,
                    active_page="archive" if proposal.archived else "overview",
                    **action_context,
                ),
            )

        @router.post("/app/proposals/{proposal_id}/acknowledge")
        async def acknowledge_proposal(
            request: Request,
            proposal_id: str,
            guild_id: str = Form(...),
            csrf: str = Form(...),
        ) -> Response:
            return await self._proposal_action(
                request,
                proposal_id,
                guild_id,
                csrf,
                "acknowledge",
                {},
            )

        @router.post("/app/proposals/{proposal_id}/subscription")
        async def subscribe_to_proposal(
            request: Request,
            proposal_id: str,
            guild_id: str = Form(...),
            csrf: str = Form(...),
            enabled: str = Form(...),
        ) -> Response:
            return await self._proposal_action(
                request,
                proposal_id,
                guild_id,
                csrf,
                "subscription",
                {"enabled": enabled == "true"},
            )

        @router.post("/app/proposals/{proposal_id}/nudge")
        async def nudge_about_proposal(
            request: Request,
            proposal_id: str,
            guild_id: str = Form(...),
            csrf: str = Form(...),
            target_user_id: str = Form(...),
        ) -> Response:
            return await self._proposal_action(
                request,
                proposal_id,
                guild_id,
                csrf,
                "nudge",
                {"target_user_id": target_user_id},
            )

        @router.post("/app/proposals/{proposal_id}/veto")
        async def veto_proposal(
            request: Request,
            proposal_id: str,
            guild_id: str = Form(...),
            csrf: str = Form(...),
            reason: str = Form(""),
        ) -> Response:
            reason = reason.strip()
            if len(reason) > 500:
                raise HTTPException(422, "A veto reason cannot exceed 500 characters.")
            return await self._proposal_action(
                request,
                proposal_id,
                guild_id,
                csrf,
                "veto",
                {"reason": reason},
            )

        @router.get("/app/guilds/{guild_id}/members")
        async def search_members(
            request: Request, guild_id: str, q: str = ""
        ) -> JSONResponse:
            session = self._require_session(request)
            await self._authorize(session, guild_id)
            query = " ".join(q.split())
            if not 2 <= len(query) <= 32:
                return JSONResponse({"members": []})
            throttle_key = (session.user_id, guild_id)
            now = time.monotonic()
            if now - self._member_search_times.get(throttle_key, 0.0) < 0.2:
                return JSONResponse(
                    {"detail": "Search a little more slowly."}, status_code=429
                )
            self._member_search_times[throttle_key] = now
            try:
                members = await self.worker.search_members(
                    guild_id, session.user_id, query
                )
            except WorkspaceWorkerError as exc:
                return JSONResponse({"detail": str(exc)}, status_code=exc.status_code)
            return JSONResponse({"members": members})

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

        @router.post("/app/proposals/{proposal_id}/archive")
        async def archive_proposal(
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
                session, guild_id, "archive", {"proposal_id": proposal_id}
            )

        @router.post("/app/proposals/{proposal_id}/purge")
        async def purge_proposal(
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
                session, guild_id, "purge", {"proposal_id": proposal_id}
            )

        @router.post("/app/proposals/{proposal_id}/restore")
        async def restore_proposal(
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
                session, guild_id, "restore", {"proposal_id": proposal_id}
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
                    active_page="preferences",
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
                    active_page="settings",
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
            types = await self.repository.list_types(guild_id)
            return self.templates.TemplateResponse(
                request,
                "types.html",
                self._context(
                    session,
                    guild_id,
                    access,
                    configs,
                    active_page="types",
                    proposal_types=types,
                    max_custom_types=MAX_CUSTOM_TYPES,
                    custom_type_count=sum(not item.builtin for item in types),
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
            request: Request,
            job_id: str,
            guild: str = "",
            inline: bool = False,
            proposal: str = "",
        ) -> Response:
            session = self._require_session(request)
            job = await self.repository.get_workspace_job(job_id)
            if job and (
                job.requester_hash != self.codec.fingerprint(session.user_id)
                or job.guild_id not in session.guild_ids
            ):
                raise HTTPException(404, "Job not found.")
            status = job.status if job else "queued"
            selected = guild or (job.guild_id if job else "")
            session, guild_id, access, configs = await self._page_access(
                request, selected
            )
            result_proposal = None
            if (
                job
                and job.status == "succeeded"
                and job.action == "create"
                and job.proposal_id
            ):
                created_proposal = await self.repository.get_proposal(job.proposal_id)
                visible_channels = {
                    str(item) for item in access.get("visible_channel_ids") or []
                }
                if (
                    created_proposal
                    and created_proposal.guild_id == guild_id
                    and created_proposal.output_channel_id in visible_channels
                ):
                    result_proposal = created_proposal
            if inline:
                action_proposal_id = (
                    job.proposal_id if job and job.proposal_id else proposal
                )
                action_context: dict[str, Any] = {}
                if (
                    job
                    and job.status == "succeeded"
                    and action_proposal_id
                    and job.action in {"acknowledge", "subscription", "nudge", "veto"}
                ):
                    updated = await self.repository.get_proposal(action_proposal_id)
                    if updated and updated.guild_id == guild_id:
                        action_context = await self._proposal_action_context(
                            session, updated
                        )
                return self.templates.TemplateResponse(
                    request,
                    "proposal_action_job.html",
                    {
                        "status": status,
                        "job": job,
                        "job_id": job_id,
                        "guild_id": guild_id,
                        "proposal_id": action_proposal_id,
                        "session": session,
                        "refresh_page": bool(
                            job and job.status == "succeeded" and job.action == "veto"
                        ),
                        **action_context,
                    },
                )
            return self.templates.TemplateResponse(
                request,
                "job.html",
                self._context(
                    session,
                    guild_id,
                    access,
                    configs,
                    title="Saving",
                    active_page="",
                    job=job,
                    status=status,
                    result_proposal=result_proposal,
                ),
            )

    async def _proposal_action(
        self,
        request: Request,
        proposal_id: str,
        guild_id: str,
        csrf: str,
        action: str,
        data: dict[str, Any],
    ) -> Response:
        session = self._require_session(request)
        self._check_csrf(session, csrf)
        await self._authorize(session, guild_id)
        proposal = await self.repository.get_proposal(proposal_id)
        if not proposal or proposal.guild_id != guild_id or proposal.archived:
            raise HTTPException(404, "Proposal not found.")
        payload = {"proposal_id": proposal_id, **data}
        job_id = secrets.token_hex(16)
        await self.tasks.enqueue_workspace(
            {
                "id": job_id,
                "action": action,
                "actor_user_id": session.user_id,
                "guild_id": guild_id,
                "requester_hash": self.codec.fingerprint(session.user_id),
                "data": payload,
            }
        )
        if request.headers.get("HX-Request") == "true":
            return self.templates.TemplateResponse(
                request,
                "proposal_action_job.html",
                {
                    "status": "queued",
                    "job": None,
                    "job_id": job_id,
                    "guild_id": guild_id,
                    "proposal_id": proposal_id,
                    "session": session,
                    "refresh_page": False,
                },
            )
        return RedirectResponse(
            (f"/app/jobs/{job_id}?guild={guild_id}&proposal={proposal_id}"),
            status_code=303,
        )

    async def _proposal_action_context(
        self, session: WorkspaceSession, proposal: Proposal
    ) -> dict[str, Any]:
        acknowledged = False
        subscribed = False
        if proposal.status is ProposalStatus.ACTIVE and not proposal.archived:
            acknowledged = await self.repository.has_acknowledged(
                proposal.id, session.user_id
            )
            subscribed = await self.repository.get_proposal_subscription(
                proposal.id, session.user_id
            )
        return {
            "proposal": proposal,
            "acknowledged": acknowledged,
            "proposal_subscribed": subscribed,
            "discord_url": _proposal_link(proposal),
        }

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
        list[dict[str, Any]],
    ]:
        session = self._require_session(request)
        selected = guild_id or (session.guild_ids[0] if session.guild_ids else "")
        if not selected or selected not in session.guild_ids:
            raise HTTPException(403, "Open Vite-oh from this Discord server first.")
        access = await self._authorize(session, selected)
        try:
            summaries = await self.worker.list_guild_summaries(
                session.guild_ids, session.user_id
            )
        except WorkspaceWorkerError:
            logger.warning(
                "Could not refresh workspace server list",
                extra={"guild_id": selected},
            )
            summaries = []
        if not any(str(item.get("guild_id")) == selected for item in summaries):
            summaries.append(
                {
                    "guild_id": selected,
                    "guild_name": str(access["guild_name"]),
                    "guild_icon_hash": str(access.get("guild_icon_hash") or ""),
                    "configured": (
                        await self.repository.get_guild_config(selected) is not None
                    ),
                    "can_manage": bool(access.get("can_manage")),
                }
            )
        guilds = sorted(
            (_guild_view(item) for item in summaries),
            key=lambda item: str(item["guild_name"]).casefold(),
        )
        return session, selected, access, guilds

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
            status = 503 if exc.status_code >= 500 else 403
            raise HTTPException(status, str(exc)) from exc
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
        guilds: list[dict[str, Any]],
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
            "guilds": guilds,
            "current_guild": next(
                (item for item in guilds if str(item.get("guild_id", "")) == guild_id),
                _guild_view(
                    {
                        "guild_id": guild_id,
                        "guild_name": access["guild_name"],
                        "guild_icon_hash": access.get("guild_icon_hash", ""),
                    }
                ),
            ),
            **extra,
        }

    def _require_admin(self, access: dict[str, Any], user_id: str) -> None:
        if not access.get("can_manage") and (
            not self.settings.discord_owner_user_id
            or user_id != self.settings.discord_owner_user_id
        ):
            raise HTTPException(403, "You need Manage Server permission.")

    def _error(self, request: Request, message: str, status: int) -> Response:
        return self.error_response(request, status, message)

    def error_response(
        self,
        request: Request,
        status: int,
        message: str,
        *,
        field: str = "",
    ) -> Response:
        heading = {
            400: "That request was not valid",
            401: "Your workspace session has expired",
            403: "You no longer have access",
            404: "That item could not be found",
            409: "That action conflicts with newer information",
            422: "Check the information you entered",
            503: "Discord is temporarily unavailable",
        }.get(status, "The workspace hit a problem")
        session = self._session(request)
        return self.templates.TemplateResponse(
            request,
            "error.html",
            {
                "title": heading,
                "session": None,
                "status": status,
                "heading": heading,
                "message": message,
                "field": field,
                "has_session": session is not None,
            },
            status_code=status,
        )


def _proposal_link(proposal: Proposal) -> str:
    return (
        f"https://discord.com/channels/{proposal.guild_id}/"
        f"{proposal.output_channel_id}/{proposal.message_id}"
    )


def _format_duration(minutes: int) -> str:
    for unit_minutes, singular in (
        (10080, "week"),
        (1440, "day"),
        (60, "hour"),
    ):
        if minutes >= unit_minutes and minutes % unit_minutes == 0:
            amount = minutes // unit_minutes
            return f"{amount:,} {singular}{'' if amount == 1 else 's'}"
    return f"{minutes:,} minute{'' if minutes == 1 else 's'}"


def _guild_view(item: dict[str, Any]) -> dict[str, Any]:
    guild_id = str(item.get("guild_id") or "")
    name = str(item.get("guild_name") or guild_id)
    icon_hash = str(item.get("guild_icon_hash") or "")
    digest = icon_hash[2:] if icon_hash.startswith("a_") else icon_hash
    valid_icon = (
        guild_id.isdigit()
        and bool(digest)
        and len(digest) <= 64
        and all(character in "0123456789abcdef" for character in digest.casefold())
    )
    initials = (
        "".join(word[0] for word in name.split() if word and word[0].isalnum())[
            :2
        ].upper()
        or "?"
    )
    hue = int(hashlib.sha256(guild_id.encode()).hexdigest()[:4], 16) % 360
    return {
        **item,
        "guild_id": guild_id,
        "guild_name": name,
        "guild_icon_url": (
            f"https://cdn.discordapp.com/icons/{guild_id}/{icon_hash}.webp?size=96"
            if valid_icon
            else ""
        ),
        "initials": initials,
        "fallback_tone": hue // 45,
    }
