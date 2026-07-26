import unicodedata
from datetime import UTC, datetime

from viteoh.domain import ProposalType

MAX_CUSTOM_TYPES = 20
_BUILTIN_TIME = datetime(2020, 1, 1, tzinfo=UTC)


BUILTIN_TYPES: tuple[ProposalType, ...] = (
    ProposalType(
        id="builtin:new-member",
        guild_id="",
        name="New member",
        normalized_name="new member",
        description="Propose adding someone to the server",
        created_by="system",
        updated_by="system",
        created_at=_BUILTIN_TIME,
        updated_at=_BUILTIN_TIME,
        builtin=True,
    ),
)


def builtin_type(type_id: str) -> ProposalType | None:
    return next(
        (
            proposal_type
            for proposal_type in BUILTIN_TYPES
            if proposal_type.id == type_id
        ),
        None,
    )


def normalize_type_name(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()


def validate_type_fields(
    name: str,
    description: str,
) -> tuple[str, str, str]:
    name = " ".join(name.split())
    description = " ".join(description.split())
    normalized_name = normalize_type_name(name)
    if not 1 <= len(name) <= 50:
        raise ValueError("Proposal type names must contain 1 to 50 characters.")
    if not 1 <= len(description) <= 100:
        raise ValueError("Proposal type descriptions must contain 1 to 100 characters.")
    if normalized_name in {item.normalized_name for item in BUILTIN_TYPES}:
        raise ValueError("Custom proposal types cannot use a built-in type name.")
    return (
        name,
        normalized_name,
        description,
    )
