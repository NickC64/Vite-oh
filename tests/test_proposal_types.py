import pytest

from viteoh.domain import ProposalType, utcnow
from viteoh.proposal_types import (
    BUILTIN_TYPES,
    builtin_type,
    normalize_type_name,
    validate_type_fields,
)


def test_new_member_is_the_only_builtin_type() -> None:
    (new_member,) = BUILTIN_TYPES
    assert new_member.name == "New member"
    assert builtin_type("builtin:new-member") is new_member
    assert builtin_type("missing") is None


def test_custom_type_validation_normalizes_safe_fields() -> None:
    result = validate_type_fields(
        "  Policy   change ",
        " Guide policy proposals ",
    )
    assert result == (
        "Policy change",
        "policy change",
        "Guide policy proposals",
    )
    assert normalize_type_name("POLICY  Change") == "policy change"


def test_type_documents_contain_only_category_metadata() -> None:
    now = utcnow()
    proposal_type = ProposalType.from_document(
        "guild",
        "type-id",
        {
            "name": "Policy",
            "normalized_name": "policy",
            "description": "Changes to server policy",
            "created_by": "admin",
            "updated_by": "admin",
            "created_at": now,
            "updated_at": now,
        },
    )
    assert proposal_type.name == "Policy"
    assert proposal_type.description == "Changes to server policy"


def test_type_rejects_builtin_name() -> None:
    with pytest.raises(ValueError, match="built-in"):
        validate_type_fields("New member", "Description")
