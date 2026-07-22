# Modernization audit

Audit date: 2026-07-20

## Findings resolved

| Previous implementation | Risk | Replacement |
| --- | --- | --- |
| Python 3.9 container | Python 3.9 is end-of-life | Pinned Python 3.13 runtime |
| Discord Gateway and background thread | Requires an always-running process | Signed Discord HTTP interactions |
| `asyncio.sleep` for 48-hour deadlines | Lost on restart, deploy, or scale-down | Scheduled Cloud Tasks plus reconciliation |
| Global proposal/subscriber collections | Inconsistent across instances | Firestore is the only state authority |
| Local SQLite database and shutdown file | Ephemeral on serverless instances | Firestore Native Mode |
| Extending deadlines after downtime | User-visible deadline drift | Fixed absolute UTC deadlines |
| Disabled default TLS verification | Exposed every HTTPS call to interception | Standard verified TLS |
| Duplicate `discord` dependencies | Ambiguous and outdated environment | Removed Gateway libraries |
| Mixed synchronous/async SQLAlchemy | The `feat/async` branch could not execute correctly | Async Google clients and explicit interfaces |
| Hard-coded PostgreSQL placeholder | Invalid production credential handling | Application Default Credentials and Secret Manager |
| Deferred Discord response followed by a second initial response | Invalid Discord interaction lifecycle | Immediate defer followed by webhook edit |
| Channel lookup by name | Renames silently break announcements | Immutable Discord channel ID |
| Reused static component IDs | No stateless proposal routing | Proposal UUID encoded in each component ID |
| Flat, abbreviated command list | Poor discoverability as features grew | One nested `/proposal` command family |
| Plain edited announcements | Outcomes were easy to miss | Rich canonical embeds plus one terminal reply |
| GitHub Actions v3 and Black-only CI | Old runtime and no behavioral/security checks | Current pinned actions, Ruff, mypy, pytest, audit, Terraform, and container checks |
| Bootstrap and runtime resources in one Terraform state | CI had to administer the identity provider and APIs it depended on | Administrator-owned bootstrap state plus CI-owned application state |
| No tests | Timer and concurrency regressions were invisible | State-machine, interaction, REST adapter, and task tests |

## Dependency audit

Direct runtime and development dependencies were compared with their current
stable releases and locked in `uv.lock`. The first vulnerability scan found
`PYSEC-2026-3002` in PyNaCl 1.6.1; the project now uses patched PyNaCl 1.6.2.
The final `pip-audit` run reports no known vulnerabilities.

Dependabot checks Python, GitHub Actions, Docker, and Terraform dependencies
weekly. CI blocks merges on a future known Python vulnerability.

## Reliability model

The durable invariant is:

- `active` may become `vetoed` only when server time is before `deadline_at`.
- `active` may become `passed` only when server time is at or after
  `deadline_at`.
- `active` may become `deleted` only through the owner command.
- A terminal proposal never returns to `active`.

Firestore applies each transition transactionally and releases the active-name
reservation in that transaction. Task delivery is at-least-once, so repeated
workers read the terminal state and do not repeat the transition. Discord
effects have their own completion markers; reconciliation retries incomplete
effects without rolling back proposal state.

## Remaining deployment checks

The repository validates application behavior without cloud credentials.
Before cutover, the operator must still:

- Apply Terraform in the target project.
- Confirm the project has no default Firestore database in another location.
- Add the Discord bot token to Secret Manager.
- Configure GitHub production variables and Workload Identity Federation.
- Run the production acceptance procedure.
- Configure at least one Monitoring notification channel if alerts should
  notify a person rather than only appear as open incidents.
