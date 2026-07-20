# Production acceptance

Perform this before switching the Discord Developer Portal to the new
Interaction Endpoint URL.

## Infrastructure

1. Confirm both Cloud Run services are healthy.
2. Confirm `viteoh-interactions` has one minimum instance and the worker has
   zero minimum instances.
3. Confirm the worker rejects an unauthenticated request to `/tasks/reconcile`.
4. Execute the command-registration job and confirm all six guild commands.
5. Confirm the Scheduler job can invoke reconciliation successfully.

## Short deadline smoke test

Temporarily set `proposal_timeout_seconds = 60` in the production Terraform
variables, apply, and wait for both Cloud Run revisions to become ready.

1. Create a proposal and confirm the public announcement contains a deadline.
2. Subscribe from another member and confirm a DM arrives.
3. Redeploy the worker or allow it to scale back to zero.
4. Confirm the proposal passes without its deadline changing.
5. Redeliver the same finalization task and confirm no duplicate transition or
   channel announcement.
6. Create another proposal, delete its deadline task, run reconciliation, and
   confirm a replacement task is recorded.
7. Create a third proposal and veto it just before expiry. Confirm the public
   message contains no vetoing identity.
8. Attempt a veto after expiry and confirm it is rejected.

Restore `proposal_timeout_seconds = 172800`, apply Terraform, and verify both
services are ready before creating real proposals.

## Discord cutover

1. Close every proposal in the old SQLite bot.
2. Set the Terraform output `interaction_endpoint_url` as the application's
   Discord Interaction Endpoint URL.
3. Confirm Discord accepts its signed PING validation.
4. Stop the old Gateway process.
5. Exercise `/help`, `/sub`, `/unsub`, `/new`, `/view`, Subscribe, Veto, and
   owner `/delete`.

## Recovery

Once real proposals exist in Firestore, do not restart the SQLite bot. Roll
forward to a corrected Cloud Run revision. If task delivery is impaired,
Firestore deadlines remain authoritative and the five-minute reconciliation
job completes overdue proposals when service returns.
