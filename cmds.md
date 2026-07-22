# Bot Commands

- `/proposal create <title> [type] [context]` — Create a proposal directly from
  the command. The optional type appears above the unchanged title and defaults
  to General.
- `/proposal list` — View active proposals, type names, deadlines, and links.
- `/proposal nudge <proposal> <user>` — Anonymously notify one member.
- `/proposal preferences [new_proposals] [nudges]` — View or change your
  per-server notification preferences.
- `/proposal configure [channel] [duration_minutes]` — Configure this server
  (Manage Server).
- `/proposal delete <proposal>` — Confirm deletion of an active proposal
  (Manage Server).
- `/proposal type create` — Create a custom proposal type.
- `/proposal type edit <type>` — Edit a custom type without changing existing
  proposals.
- `/proposal type delete <type>` — Confirm deletion of a custom type.
- `/proposal type list` — List built-in and custom proposal types.
- `/proposal help` — Explain consent voting and available commands.

Use **Veto** to submit an anonymous objection with an optional public reason,
**Acknowledge** to record privately that you saw the proposal, and **Subscribe**
to receive proposal updates. Passing, vetoing, and moderator deletion update the
canonical embed and post one new outcome reply.
