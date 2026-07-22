# Production acceptance

Perform this before switching the Discord Developer Portal to the new
Interaction Endpoint URL.

## Infrastructure

1. Confirm both Cloud Run services are healthy.
2. Confirm the bot has View Channel, Send Messages, Embed Links, and Read
   Message History in each configured proposal channel.
3. Confirm `viteoh-interactions` has one minimum instance and the worker has
   zero minimum instances.
4. Confirm the worker rejects an unauthenticated request to `/tasks/reconcile`.
5. Execute the command-registration job and confirm the single global
   `/proposal` command and all nested subcommands.
6. Confirm the Scheduler job can invoke reconciliation successfully.

## Short deadline smoke test

Run `/proposal configure channel:#test-output duration_minutes:1` in the
testing guild.

1. Create General and New member proposals and confirm their embeds contain the
   template, formatted title, context, deadline, and zero acknowledgements.
2. Create, edit, use, and delete a custom template. Confirm the created
   proposal retains its original template snapshot.
3. Enable new-proposal DMs in `/proposal preferences` and confirm a DM arrives.
4. Redeploy the worker or allow it to scale back to zero.
5. Confirm the proposal passes without its deadline changing and produces one
   outcome reply to the updated canonical embed.
6. Redeliver the same finalization task and confirm no duplicate transition or
   outcome reply.
7. Create another proposal, delete its deadline task, run reconciliation, and
   confirm a replacement task is recorded.
8. Create a third proposal and veto it with an optional reason just before
   expiry. Confirm the reason is public and the vetoing identity is absent.
9. Attempt a veto after expiry and confirm it is rejected.
10. Acknowledge from two members, confirm only the count is public, and confirm
   either member may still veto.
11. Use autocomplete to nudge one member, verify the neutral DM and message
    link, then confirm duplicate nudges and per-server opt-out are enforced.
12. Delete a proposal and confirm the ephemeral confirmation, gray canonical
    embed, and single moderator-deletion outcome reply.

Run `/proposal configure channel:#live-output duration_minutes:2880` in the live
guild. A later configuration change affects only proposals created afterward.

## Discord cutover

1. Close every proposal in the old SQLite bot.
2. Set the Terraform output `interaction_endpoint_url` as the application's
   Discord Interaction Endpoint URL.
3. Confirm Discord accepts its signed PING validation.
4. Stop the old Gateway process.
5. Install the same application in every guild using the `bot` and
   `applications.commands` scopes.
6. Exercise every `/proposal` subcommand, both built-in templates, one custom
   template, Acknowledge, Subscribe, Veto with reason, and each terminal outcome
   independently in test and live guilds.

## Recovery

Once real proposals exist in Firestore, do not restart the SQLite bot. Roll
forward to a corrected Cloud Run revision. If task delivery is impaired,
Firestore deadlines remain authoritative and the five-minute reconciliation
job completes overdue proposals when service returns.
