# Vite-oh

Vite-oh is a single-server Discord bot for anonymous veto voting. A proposal
passes at a fixed deadline unless a server member anonymously vetoes it.

This version uses Discord's HTTP Interaction Endpoint instead of a Gateway
WebSocket. It is stateless on Cloud Run: Firestore owns proposal state, Cloud
Tasks delivers exact deadline work with retries, and Cloud Scheduler repairs
missing tasks every five minutes.

## Architecture

```text
Discord
   │ signed interaction
   ▼
Public Cloud Run receiver (min 1)
   │ deferred response + deterministic task
   ▼
Cloud Tasks ───────────────► Private Cloud Run worker (scales to 0)
   │                                  │
   │ scheduled deadline               ├── Firestore transactions
   └──────────────────────────────────►├── Discord REST messages/DMs
                                      └── fixed proposal state transitions

Cloud Scheduler ── every 5 minutes ──► reconciliation endpoint
```

Only `/interactions` is public. Cloud Tasks and Scheduler invoke the worker
with a Google-signed OIDC token, and Cloud Run IAM rejects every other caller.

## Commands

- `/new <name>` creates a 48-hour proposal.
- `/sub` and `/unsub` control notifications for new proposals.
- `/view` lists active proposals and their deadlines.
- `/delete <name>` deletes a proposal and is restricted to the configured owner.
- `/help` shows command help.

Proposal messages provide **Veto** and **Subscribe** buttons. Veto confirmation
is ephemeral; the identity of the vetoing member is neither stored nor shown.

## Local development

Python 3.13 and [uv](https://docs.astral.sh/uv/) are required.

```bash
uv sync --all-groups --frozen
uv run ruff check .
uv run ruff format --check .
uv run mypy viteoh
uv run pytest
uv run pip-audit --skip-editable
```

Copy `.env.example` to `.env` only for local execution. The production receiver
and worker are separate services selected by `SERVICE_ROLE`. Local repository
tests use an in-memory implementation; Google integration testing should run
with the Firestore emulator:

```bash
gcloud emulators firestore start --host-port=127.0.0.1:8085
export FIRESTORE_EMULATOR_HOST=127.0.0.1:8085
```

Run a local receiver with:

```bash
SERVICE_ROLE=receiver uv run uvicorn viteoh.app:app --reload
```

## Google Cloud bootstrap

Infrastructure is defined in [`infra/`](infra). All production resources use
`northamerica-northeast1` (Montréal). The default Firestore database is created
in that location permanently, so confirm the project does not already have a
default database elsewhere.

1. Create a Google Cloud project and a globally unique GCS bucket for Terraform
   state.
2. Copy `infra/terraform.tfvars.example` to `infra/terraform.tfvars` and fill in
   the non-secret IDs. Never put the Discord bot token in Terraform.
3. Bootstrap the APIs, registry, secret container, and GitHub identity:

   ```bash
   cd infra
   terraform init -backend-config="bucket=YOUR_STATE_BUCKET"
   terraform apply \
     -target=google_project_service.apis \
     -target=google_artifact_registry_repository.app \
     -target=google_secret_manager_secret.discord_bot_token \
     -target=google_iam_workload_identity_pool.github \
     -target=google_iam_workload_identity_pool_provider.github \
     -target=google_service_account.deployer \
     -target=google_service_account_iam_member.github_wif \
     -target=google_project_iam_member.deployer_roles \
     -target=google_project_iam_member.deployer_service_account_user
   ```

4. Add the bot token directly to Secret Manager:

   ```bash
   printf '%s' "$DISCORD_BOT_TOKEN" | \
     gcloud secrets versions add viteoh-discord-bot-token --data-file=-
   ```

5. Configure the GitHub `production` environment:

   Variables:

   - `GCP_PROJECT_ID`
   - `GCP_WORKLOAD_IDENTITY_PROVIDER` from `terraform output`
   - `GCP_DEPLOYER_SERVICE_ACCOUNT` from `terraform output`
   - `TERRAFORM_STATE_BUCKET`
   - `DISCORD_APPLICATION_ID`
   - `DISCORD_GUILD_ID`
   - `DISCORD_OUTPUT_CHANNEL_ID`
   - `DISCORD_OWNER_USER_ID`

   Secret:

   - `DISCORD_PUBLIC_KEY`

6. Push to `main`. CI builds an immutable commit-SHA image, applies Terraform,
   deploys both services, and runs the command-registration Cloud Run job.

Terraform intentionally creates the Secret Manager secret but no secret
version, ensuring the bot token never enters source control, Terraform state,
or GitHub.

## Discord cutover

1. Finish or close active proposals in the legacy SQLite bot. This release
   intentionally starts with an empty Firestore database.
2. Let the production workflow deploy and register the guild commands.
3. Confirm the interaction endpoint responds to `/healthz`.
4. Copy `terraform output -raw interaction_endpoint_url` to the Discord
   Developer Portal's **Interactions Endpoint URL**. Discord will validate its
   signature and PING handling.
5. Stop the old Gateway/WebSocket process.
6. Smoke-test `/help`, `/sub`, `/new`, Veto, Subscribe, `/view`, and the
   owner-only `/delete`.

After Firestore receives live data, recover by rolling forward. The legacy
SQLite bot cannot consume the new state safely.

## Reliability guarantees

- Deadlines are absolute UTC timestamps and never move because a process stops.
- Firestore transactions serialize veto, delete, and pass transitions.
- Finalization before a deadline is rejected; veto at or after a deadline is
  rejected.
- Cloud Tasks retries failed work and deterministic task names deduplicate
  repeated interactions.
- Reconciliation repairs deleted/missing deadline tasks and finalizes overdue
  active proposals.
- Discord message edits are idempotent. Notification delivery is recorded per
  proposal, event, and subscriber; permanent closed-DM failures do not block a
  proposal transition.

Terminal proposal records are retained for audit and idempotency. The vetoing
user is not part of those records.

See the complete [modernization audit](docs/MODERNIZATION_AUDIT.md) and
[production acceptance runbook](docs/PRODUCTION_ACCEPTANCE.md).
