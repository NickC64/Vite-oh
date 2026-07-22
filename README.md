# Vite-oh

Vite-oh is a multi-server Discord bot for anonymous veto voting. Each server
chooses its announcement channel and voting duration. A proposal passes at its
fixed deadline unless a server member anonymously vetoes it.

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

- `/proposal create [template]` opens a guided proposal form. General and New
  member templates are built in.
- `/proposal list` links to active proposals and their deadlines.
- `/proposal nudge <proposal> <user>` anonymously DMs one member.
- `/proposal preferences [new_proposals] [nudges]` controls per-server DMs.
- `/proposal configure [channel] [duration_minutes]` configures the server and
  requires Manage Server permission (or the bot-owner override).
- `/proposal delete <proposal>` confirms and deletes an active proposal.
- `/proposal template create|edit|delete|list` manages up to 20 guild-local
  guided templates. Template mutations require Manage Server permission.
- `/proposal help` explains the consent model and commands.

Proposal messages provide **Veto**, **Acknowledge**, and **Subscribe** buttons.
Acknowledgements expose only an aggregate count and never act as yes votes.
Veto opens an ephemeral form for an optional public reason; the vetoing
identity is neither stored nor shown. Announcements are rich embeds. Terminal
states update the canonical embed and create one reply so the channel receives
fresh activity without losing its clean source of truth.

Custom templates guide a subject and context field, optionally require context,
and format titles with exactly one `{subject}` token. Proposals snapshot the
template name, so later template edits or deletion never rewrite history.

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

## Google Cloud deployment

Terraform is split into two states under [`infra/`](infra):

- `bootstrap` is applied locally by a project administrator and owns APIs,
  Artifact Registry, Secret Manager, GitHub federation, deployer IAM, and
  state-bucket access.
- `application` is applied only by GitHub Actions and owns the runtime services,
  data infrastructure, queues, scheduler, and monitoring.

This keeps GitHub as the single source of truth for application configuration
and prevents the deployer from managing the identity provider it uses to
authenticate. See [`infra/README.md`](infra/README.md) for first-time bootstrap
and existing-state migration instructions.

Configure the GitHub `production` environment:

Variables:

- `GCP_PROJECT_ID`
- `GCP_WORKLOAD_IDENTITY_PROVIDER` from the bootstrap output
- `GCP_DEPLOYER_SERVICE_ACCOUNT` from the bootstrap output
- `TERRAFORM_STATE_BUCKET`
- `DISCORD_APPLICATION_ID`
- `DISCORD_OWNER_USER_ID`

Secret:

- `DISCORD_PUBLIC_KEY`

Push to `main`. CI builds an immutable commit-SHA image, applies only the
application stack, deploys both services, and runs the command-registration
Cloud Run job.

The bootstrap stack creates the Secret Manager container but no secret version,
ensuring the bot token never enters source control, Terraform state, or GitHub.

## Discord cutover

1. Finish or close active proposals in the legacy SQLite bot. This release
   intentionally starts with an empty Firestore database.
2. Let the production workflow deploy and register the global commands.
3. Confirm the interaction endpoint responds to `/health`.
4. Get the deployed endpoint:

   ```bash
   terraform -chdir=infra/application output -raw interaction_endpoint_url
   ```

   Copy it to the Discord Developer Portal's **Interactions Endpoint URL**.
   Discord will validate its signature and PING handling.
5. Stop the old Gateway/WebSocket process.
6. Install the same application in each server with the `bot` and
   `applications.commands` scopes.
7. Run `/proposal configure channel:#test-output duration_minutes:1` in the
   test server and `/proposal configure channel:#live-output
   duration_minutes:2880` in the live server.
8. Smoke-test both built-ins, a custom template, autocomplete, preferences,
   nudge, acknowledgement, an anonymous veto reason, terminal replies, and
   deletion independently in both servers.

The bot remains shown as offline because it uses HTTP interactions rather than
a Discord Gateway connection. Global command changes can take time to appear.

## Disposable single-guild cleanup

Before the first multi-guild deployment, remove the old test fixtures once:

```bash
export GOOGLE_CLOUD_PROJECT=mail-in-votes
export GOOGLE_CLOUD_LOCATION=northamerica-northeast1
scripts/cleanup-legacy.sh OLD_GUILD_ID OLD_CHANNEL_ID
```

The guarded command pauses the deadline queue and reconciliation, deletes old
proposal state, messages, tasks, and guild-scoped commands, and leaves timers
paused. Deploy, configure the guilds, then run the two resume commands printed
by the script. Do not use this cleanup after live multi-guild data exists.

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
- Terminal outcome replies use deterministic nonces and durable message IDs;
  reconciliation retries incomplete canonical or outcome delivery.

Terminal proposal records, public veto reasons, and template snapshots are
retained for audit and idempotency. The vetoing user is not part of those
records.

See the complete [modernization audit](docs/MODERNIZATION_AUDIT.md) and
[production acceptance runbook](docs/PRODUCTION_ACCEPTANCE.md).
