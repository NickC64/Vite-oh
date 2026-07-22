import re
import unicodedata
from datetime import UTC, datetime

from viteoh.domain import ProposalTemplate

MAX_CUSTOM_TEMPLATES = 20
_FORMAT_TOKEN = "{subject}"
_BRACES = re.compile(r"[{}]")
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
        title_format=_FORMAT_TOKEN,
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
        title_format="Add {subject} as a member",
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
    subject_label: str,
    context_label: str,
    title_format: str,
) -> tuple[str, str, str, str, str, str]:
    name = " ".join(name.split())
    description = " ".join(description.split())
    subject_label = " ".join(subject_label.split())
    context_label = " ".join(context_label.split())
    title_format = " ".join(title_format.split())
    normalized_name = normalize_template_name(name)
    if not 1 <= len(name) <= 50:
        raise ValueError("Template names must contain 1 to 50 characters.")
    if not 1 <= len(description) <= 100:
        raise ValueError("Template descriptions must contain 1 to 100 characters.")
    if not 1 <= len(subject_label) <= 45:
        raise ValueError("Subject prompts must contain 1 to 45 characters.")
    if not 1 <= len(context_label) <= 45:
        raise ValueError("Context prompts must contain 1 to 45 characters.")
    if not 1 <= len(title_format) <= 100:
        raise ValueError("Title formats must contain 1 to 100 characters.")
    if title_format.count(_FORMAT_TOKEN) != 1 or _BRACES.search(
        title_format.replace(_FORMAT_TOKEN, "")
    ):
        raise ValueError("Title formats must contain exactly one `{subject}` token.")
    if normalized_name in {item.normalized_name for item in BUILTIN_TEMPLATES}:
        raise ValueError("Custom templates cannot use a built-in template name.")
    return (
        name,
        normalized_name,
        description,
        subject_label,
        context_label,
        title_format,
    )


def format_title(template: ProposalTemplate, subject: str) -> str:
    subject = " ".join(subject.split())
    if not subject:
        raise ValueError("A proposal subject is required.")
    title = template.title_format.replace(_FORMAT_TOKEN, subject)
    if not 1 <= len(title) <= 100:
        raise ValueError("The formatted proposal title must be 1 to 100 characters.")
    return title


def subject_max_length(template: ProposalTemplate) -> int:
    return max(1, 100 - len(template.title_format) + len(_FORMAT_TOKEN))
