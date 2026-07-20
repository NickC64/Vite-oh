from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class ProposalStatus(StrEnum):
    ACTIVE = "active"
    PASSED = "passed"
    VETOED = "vetoed"
    DELETED = "deleted"


@dataclass(frozen=True, slots=True)
class Proposal:
    id: str
    display_name: str
    normalized_name: str
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

    @classmethod
    def from_document(cls, document_id: str, data: dict[str, Any]) -> "Proposal":
        return cls(
            id=document_id,
            display_name=data["display_name"],
            normalized_name=data["normalized_name"],
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


def utcnow() -> datetime:
    return datetime.now(UTC)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
