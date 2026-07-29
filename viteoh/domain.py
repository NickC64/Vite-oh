from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

MIN_PROPOSAL_DURATION_MINUTES = 1
MAX_PROPOSAL_DURATION_MINUTES = 10080


class ProposalStatus(StrEnum):
    ACTIVE = "active"
    PASSED = "passed"
    VETOED = "vetoed"
    WITHDRAWN = "withdrawn"
    DELETED = "deleted"


@dataclass(frozen=True, slots=True)
class GuildConfig:
    guild_id: str
    guild_name: str
    output_channel_id: str
    proposal_timeout_seconds: int
    configured_by: str
    created_at: datetime
    updated_at: datetime
    custom_type_count: int = 0
    timezone: str = "UTC"
    timezone_configured: bool = False

    @classmethod
    def from_document(cls, document_id: str, data: dict[str, Any]) -> "GuildConfig":
        return cls(
            guild_id=document_id,
            guild_name=data["guild_name"],
            output_channel_id=data["output_channel_id"],
            proposal_timeout_seconds=int(data["proposal_timeout_seconds"]),
            configured_by=data["configured_by"],
            created_at=_utc(data["created_at"]),
            updated_at=_utc(data["updated_at"]),
            custom_type_count=int(data.get("custom_type_count", 0)),
            timezone=str(data.get("timezone") or "UTC"),
            timezone_configured="timezone" in data,
        )


@dataclass(frozen=True, slots=True)
class ProposalType:
    id: str
    guild_id: str
    name: str
    normalized_name: str
    description: str
    created_by: str
    updated_by: str
    created_at: datetime
    updated_at: datetime
    builtin: bool = False

    @classmethod
    def from_document(
        cls, guild_id: str, document_id: str, data: dict[str, Any]
    ) -> "ProposalType":
        return cls(
            id=document_id,
            guild_id=guild_id,
            name=data["name"],
            normalized_name=data["normalized_name"],
            description=data["description"],
            created_by=data["created_by"],
            updated_by=data["updated_by"],
            created_at=_utc(data["created_at"]),
            updated_at=_utc(data["updated_at"]),
        )


@dataclass(frozen=True, slots=True)
class Proposal:
    id: str
    guild_id: str
    guild_name: str
    output_channel_id: str
    title: str
    normalized_title: str
    context: str
    reservation_id: str
    status: ProposalStatus
    created_at: datetime
    deadline_at: datetime
    message_id: str | None = None
    deadline_task_name: str | None = None
    task_scheduled: bool = False
    terminal_at: datetime | None = None
    announcement_synced: bool = False
    effects_complete: bool = False
    acknowledgement_count: int = 0
    nudge_count: int = 0
    announcement_version: int = 0
    render_version: int = 0
    type_id: str = ""
    type_name: str = ""
    veto_reason: str = ""
    outcome_message_id: str | None = None
    archived: bool = False
    archived_at: datetime | None = None
    archived_by: str = ""
    source_kind: str = "automated"
    message_intentionally_removed: bool = False
    imported_at: datetime | None = None
    imported_by: str = ""
    source_url: str = ""
    provenance_note: str = ""
    historical_date_only: bool = False

    @classmethod
    def from_document(cls, document_id: str, data: dict[str, Any]) -> "Proposal":
        return cls(
            id=document_id,
            guild_id=data["guild_id"],
            guild_name=data["guild_name"],
            output_channel_id=data["output_channel_id"],
            title=data["title"],
            normalized_title=data["normalized_title"],
            context=data.get("context", ""),
            reservation_id=data["reservation_id"],
            status=ProposalStatus(data["status"]),
            created_at=_utc(data["created_at"]),
            deadline_at=_utc(data["deadline_at"]),
            message_id=data.get("message_id"),
            deadline_task_name=data.get("deadline_task_name"),
            task_scheduled=bool(data.get("task_scheduled", False)),
            terminal_at=_utc(data["terminal_at"]) if data.get("terminal_at") else None,
            announcement_synced=bool(data.get("announcement_synced", False)),
            effects_complete=bool(data.get("effects_complete", False)),
            acknowledgement_count=int(data.get("acknowledgement_count", 0)),
            nudge_count=int(data.get("nudge_count", 0)),
            announcement_version=int(data.get("announcement_version", 0)),
            render_version=int(data.get("render_version", 0)),
            type_id=data.get("type_id", ""),
            type_name=data.get("type_name", ""),
            veto_reason=data.get("veto_reason", ""),
            outcome_message_id=data.get("outcome_message_id"),
            archived=bool(data.get("archived", False)),
            archived_at=(
                _utc(data["archived_at"]) if data.get("archived_at") else None
            ),
            archived_by=str(data.get("archived_by", "")),
            source_kind=str(data.get("source_kind") or "automated"),
            message_intentionally_removed=bool(
                data.get("message_intentionally_removed", False)
            ),
            imported_at=(
                _utc(data["imported_at"]) if data.get("imported_at") else None
            ),
            imported_by=str(data.get("imported_by") or ""),
            source_url=str(data.get("source_url") or ""),
            provenance_note=str(data.get("provenance_note") or ""),
            historical_date_only=bool(data.get("historical_date_only", False)),
        )


@dataclass(frozen=True, slots=True)
class CreateProposalResult:
    proposal: Proposal | None
    duplicate_name: bool = False


@dataclass(frozen=True, slots=True)
class TransitionResult:
    proposal: Proposal | None
    changed: bool
    reason: str


@dataclass(frozen=True, slots=True)
class ProposalActionResult:
    proposal: Proposal | None
    changed: bool
    reason: str


@dataclass(frozen=True, slots=True)
class TypeMutationResult:
    proposal_type: ProposalType | None
    changed: bool
    reason: str


@dataclass(frozen=True, slots=True)
class WorkspaceJob:
    id: str
    guild_id: str
    requester_hash: str
    action: str
    status: str
    message: str
    proposal_id: str | None
    created_at: datetime
    updated_at: datetime
    expires_at: datetime

    @classmethod
    def from_document(cls, document_id: str, data: dict[str, Any]) -> "WorkspaceJob":
        return cls(
            id=document_id,
            guild_id=str(data["guild_id"]),
            requester_hash=str(data["requester_hash"]),
            action=str(data["action"]),
            status=str(data["status"]),
            message=str(data.get("message", "")),
            proposal_id=(str(data["proposal_id"]) if data.get("proposal_id") else None),
            created_at=_utc(data["created_at"]),
            updated_at=_utc(data["updated_at"]),
            expires_at=_utc(data["expires_at"]),
        )


@dataclass(frozen=True, slots=True)
class WorkspaceLaunch:
    user_id: str
    guild_id: str
    guild_ids: tuple[str, ...]
    proposal_id: str | None
    created_at: datetime
    expires_at: datetime
    consumed_at: datetime | None = None

    @classmethod
    def from_document(cls, data: dict[str, Any]) -> "WorkspaceLaunch":
        guild_id = str(data["guild_id"])
        return cls(
            user_id=str(data["user_id"]),
            guild_id=guild_id,
            guild_ids=tuple(
                str(item) for item in (data.get("guild_ids") or [guild_id])
            ),
            proposal_id=(str(data["proposal_id"]) if data.get("proposal_id") else None),
            created_at=_utc(data["created_at"]),
            expires_at=_utc(data["expires_at"]),
            consumed_at=(
                _utc(data["consumed_at"]) if data.get("consumed_at") else None
            ),
        )


def utcnow() -> datetime:
    return datetime.now(UTC)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
