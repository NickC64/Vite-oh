import pytest

from viteoh.templates import (
    BUILTIN_TEMPLATES,
    builtin_template,
    format_title,
    normalize_template_name,
    subject_max_length,
    validate_template_fields,
)


def test_builtins_format_general_and_new_member_titles() -> None:
    general, new_member = BUILTIN_TEMPLATES
    assert format_title(general, "  Adopt quiet hours  ") == "Adopt quiet hours"
    assert format_title(new_member, "Alice") == "Add Alice as a member"
    assert subject_max_length(new_member) == 84
    assert builtin_template("builtin:new-member") is new_member
    assert builtin_template("missing") is None


def test_custom_template_validation_normalizes_safe_fields() -> None:
    result = validate_template_fields(
        "  Policy   change ",
        " Guide policy proposals ",
        "What changes?",
        "Why and what are the tradeoffs?",
        "Adopt {subject} as policy",
    )
    assert result == (
        "Policy change",
        "policy change",
        "Guide policy proposals",
        "What changes?",
        "Why and what are the tradeoffs?",
        "Adopt {subject} as policy",
    )
    assert normalize_template_name("POLICY  Change") == "policy change"


@pytest.mark.parametrize(
    "title_format",
    ["No token", "{subject} and {subject}", "{other}", "{subject} {other}"],
)
def test_custom_template_rejects_invalid_title_formats(title_format: str) -> None:
    with pytest.raises(ValueError, match="exactly one"):
        validate_template_fields(
            "Policy", "Description", "Subject", "Context", title_format
        )


def test_template_rejects_builtin_name_and_oversized_formatted_title() -> None:
    with pytest.raises(ValueError, match="built-in"):
        validate_template_fields(
            "General", "Description", "Subject", "Context", "{subject}"
        )
    with pytest.raises(ValueError, match="formatted"):
        format_title(BUILTIN_TEMPLATES[1], "x" * 100)
