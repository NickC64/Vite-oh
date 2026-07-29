from viteoh.domain import Proposal, ProposalStatus, utcnow


def test_untyped_proposal_document_defaults_to_no_type() -> None:
    now = utcnow()
    proposal = Proposal.from_document(
        "proposal-id",
        {
            "guild_id": "guild",
            "guild_name": "Guild",
            "output_channel_id": "channel",
            "title": "Quiet hours",
            "normalized_title": "quiet hours",
            "reservation_id": "reservation",
            "status": ProposalStatus.PASSED.value,
            "created_at": now,
            "deadline_at": now,
        },
    )
    assert proposal.title == "Quiet hours"
    assert proposal.normalized_title == "quiet hours"
    assert proposal.context == ""
    assert proposal.type_id == ""
    assert proposal.type_name == ""
    assert proposal.acknowledgement_count == 0
    assert proposal.nudge_count == 0


def test_legacy_proposal_document_uses_name_fields() -> None:
    now = utcnow()
    proposal = Proposal.from_document(
        "legacy-proposal-id",
        {
            "guild_id": "guild",
            "guild_name": "Guild",
            "output_channel_id": "channel",
            "display_name": "Legacy member",
            "normalized_name": "legacy member",
            "reservation_id": "reservation",
            "status": ProposalStatus.PASSED.value,
            "created_at": now,
            "deadline_at": now,
        },
    )

    assert proposal.title == "Legacy member"
    assert proposal.normalized_title == "legacy member"
