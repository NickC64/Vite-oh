import unicodedata
from datetime import UTC, datetime

from viteoh.domain import ProposalTemplate

MAX_CUSTOM_TEMPLATES = 20
_BUILTIN_TIME = datetime(2020, 1, 1, tzinfo=UTC)


BUILTIN_TEMPLATES: tuple[ProposalTemplate, ...] = (
    ProposalTemplate(
        id="builtin:general",
        guild_id="",
        name="General",
        normalized_name="general",
        description="Any consent-based proposal",
        subject_label="Proposal title",
        context_label="Context (optional)",
        title_format="{subject}",
        context_required=False,
        created_by="system",
        updated_by="system",
        created_at=_BUILTIN_TIME,
        updated_at=_BUILTIN_TIME,
        builtin=True,
    ),
    ProposalTemplate(
        id="builtin:new-member",
        guild_id="",
        name="New member",
        normalized_name="new member",
        description="Propose adding someone to the server",
        subject_label="Who should be added?",
        context_label="Relevant context (optional)",
        title_format="{subject}",
        context_required=False,
        created_by="system",
        updated_by="system",
        created_at=_BUILTIN_TIME,
        updated_at=_BUILTIN_TIME,
        builtin=True,
    ),
)


def builtin_template(template_id: str) -> ProposalTemplate | None:
    return next(
        (template for template in BUILTIN_TEMPLATES if template.id == template_id),
        None,
    )


def normalize_template_name(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()


def validate_template_fields(
    name: str,
    description: str,
) -> tuple[str, str, str]:
    name = " ".join(name.split())
    description = " ".join(description.split())
    normalized_name = normalize_template_name(name)
    if not 1 <= len(name) <= 50:
        raise ValueError("Proposal type names must contain 1 to 50 characters.")
    if not 1 <= len(description) <= 100:
        raise ValueError("Proposal type descriptions must contain 1 to 100 characters.")
    if normalized_name in {item.normalized_name for item in BUILTIN_TEMPLATES}:
        raise ValueError("Custom proposal types cannot use a built-in type name.")
    return (
        name,
        normalized_name,
        description,
    )
