import pytest

from viteoh.templates import (
    BUILTIN_TEMPLATES,
    builtin_template,
    normalize_template_name,
    validate_template_fields,
)


def test_builtins_are_general_and_new_member_types() -> None:
    general, new_member = BUILTIN_TEMPLATES
    assert general.name == "General"
    assert new_member.name == "New member"
    assert new_member.title_format == "{subject}"
    assert builtin_template("builtin:new-member") is new_member
    assert builtin_template("missing") is None


def test_custom_template_validation_normalizes_safe_fields() -> None:
    result = validate_template_fields(
        "  Policy   change ",
        " Guide policy proposals ",
    )
    assert result == (
        "Policy change",
        "policy change",
        "Guide policy proposals",
    )
    assert normalize_template_name("POLICY  Change") == "policy change"


def test_type_rejects_builtin_name() -> None:
    with pytest.raises(ValueError, match="built-in"):
        validate_template_fields("General", "Description")
