from viteoh.domain import Proposal, ProposalStatus, utcnow


def test_legacy_proposal_document_is_backward_compatible() -> None:
    now = utcnow()
    proposal = Proposal.from_document(
        "proposal-id",
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
    assert proposal.context == ""
    assert proposal.acknowledgement_count == 0
    assert proposal.nudge_count == 0
