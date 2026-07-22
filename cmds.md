# Bot Commands

- `/proposal create [template]` — Open a guided proposal modal. Defaults to
  General; New member is also built in.
- `/proposal list` — View active proposals, template names, deadlines, and links.
- `/proposal nudge <proposal> <user>` — Anonymously notify one member.
- `/proposal preferences [new_proposals] [nudges]` — View or change your
  per-server notification preferences.
- `/proposal configure [channel] [duration_minutes]` — Configure this server
  (Manage Server).
- `/proposal delete <proposal>` — Confirm deletion of an active proposal
  (Manage Server).
- `/proposal template create [context_required]` — Create a guided template.
- `/proposal template edit <template> [context_required]` — Edit a custom
  template without changing existing proposals.
- `/proposal template delete <template>` — Confirm deletion of a custom template.
- `/proposal template list` — List built-in and custom templates.
- `/proposal help` — Explain consent voting and available commands.

Use **Veto** to submit an anonymous objection with an optional public reason,
**Acknowledge** to record privately that you saw the proposal, and **Subscribe**
to receive proposal updates. Passing, vetoing, and moderator deletion update the
canonical embed and post one new outcome reply.
