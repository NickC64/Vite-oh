# Production acceptance

Perform this before switching the Discord Developer Portal to the new
Interaction Endpoint URL.

## Infrastructure

1. Confirm the receiver, worker, and web Cloud Run services are healthy.
2. Confirm the bot has View Channel, Send Messages, Embed Links, and Read
   Message History in each configured proposal channel.
3. Confirm `viteoh-interactions` has one minimum instance and the worker has
   zero minimum instances.
4. Confirm the worker rejects an unauthenticated request to `/tasks/reconcile`.
5. Execute the command-registration job and confirm the only global command is
   `/proposal` with no options and that it is available in both servers and
   the bot's DMs.
6. Confirm the Scheduler job can invoke reconciliation successfully.

## Short deadline smoke test

Run `/proposal` in the testing guild. Confirm its ephemeral launch works once,
fails on reuse, and does not ask for another Discord login. In Workspace
Settings choose `#test-output` and one minute.

Also run `/proposal` in the bot's DMs and confirm the resulting workspace
switcher includes each configured mutual server and excludes inaccessible
servers.

1. Create one untyped proposal and one tagged **New member** proposal. Confirm
   their embeds contain the unchanged title, context, deadline, and zero
   acknowledgements, and that only the tagged proposal displays a type.
2. Create, edit, use, and delete a custom proposal type. Confirm the created
   proposal retains its original type snapshot.
3. Enable new-proposal DMs in Workspace Preferences and confirm a DM arrives.
4. Redeploy the worker or allow it to scale back to zero.
5. Confirm the proposal passes without its deadline changing and produces one
   outcome reply to the updated canonical embed.
6. Redeliver the same finalization task and confirm no duplicate transition or
   outcome reply.
7. Create another proposal, delete its deadline task, run reconciliation, and
   confirm a replacement task is recorded.
8. Archive a resolved proposal and confirm it leaves the main history. Restore
   it once, archive it again, then delete it permanently and confirm its Discord
   messages and private Firestore child records are removed.
8. Create a third proposal and veto it with an optional reason just before
   expiry. Confirm the reason is public and the vetoing identity is absent.
9. Attempt a veto after expiry and confirm it is rejected.
10. Acknowledge from two members, confirm only the count is public, and confirm
   either member may still veto.
11. Use the proposal's Nudge member picker, verify the neutral DM and message
    link, then confirm duplicate nudges and per-server opt-out are enforced.
12. Delete a proposal from its administrative web page and confirm the gray canonical
    embed, and single moderator-deletion outcome reply.

Open `/proposal` in the live guild and configure `#live-output` for 2,880
minutes. A later configuration change affects only proposals created afterward.

## Discord cutover

1. Close every proposal in the old SQLite bot.
2. Set the Terraform output `interaction_endpoint_url` as the application's
   Discord Interaction Endpoint URL.
3. Confirm Discord accepts its signed PING validation.
4. Stop the old Gateway process.
5. Install the same application in every guild using the `bot` and
   `applications.commands` scopes.
6. Exercise the workspace, an untyped proposal, the built-in New member type,
   one custom type, Nudge,
   Acknowledge, Subscribe, Veto with reason, and each terminal outcome
   independently in test and live guilds.

## Recovery

Once real proposals exist in Firestore, do not restart the SQLite bot. Roll
forward to a corrected Cloud Run revision. If task delivery is impaired,
Firestore deadlines remain authoritative and the five-minute reconciliation
job completes overdue proposals when service returns.
