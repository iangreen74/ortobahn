# INVARIANTS.md - Stability Protections

Critical system behaviors that must not regress. Each invariant maps to an automated check.

## CI / Quality Gate

| ID | Invariant | Enforced By |
|----|-----------|------------|
| INV-001 | All tests pass before merge | GitHub Actions CI `test` job |
| INV-002 | Code passes linting and formatting | GitHub Actions CI `lint` job (`ruff check` + `ruff format --check`) |
| INV-003 | Type checking passes | GitHub Actions CI `typecheck` job (`mypy`) |

## Schema / Migrations

| ID | Invariant | Verification |
|----|-----------|-------------|
| INV-004 | Migration version consistent across code and tests | `test_version_after_init` and `test_idempotent` in `tests/test_migrations.py`; `test_migration_version` in `tests/test_agent_cifix.py`. When adding migration N+1, update MIGRATIONS dict AND both test files. |
| INV-005 | Migrations are idempotent | `test_idempotent` — running `run_migrations()` twice returns the same version |

## Pipeline Behavior

| ID | Invariant | Verification |
|----|-----------|-------------|
| INV-006 | No phantom pipeline runs for ineligible clients | `ortobahn/orchestrator.py` — paused clients and expired trials return before `start_pipeline_run()`. No pipeline_run record is created for skipped clients. |
| INV-007 | Auto-publish defaults to enabled for new clients | `ortobahn/db.py` `create_client()` passes `auto_publish=1`. Migration 019 backfilled existing clients. |
| INV-008 | Publisher verifies posts with delay before marking published | `ortobahn/agents/publisher.py` — 2s `time.sleep()` before `verify_post_exists()`. Failed verification marks post as `failed`, not `published`. |

## Data Integrity

| ID | Invariant | Verification |
|----|-----------|-------------|
| INV-009 | Health metrics are time-bounded (not all-time) | Dashboard health endpoint uses 24h window for published count and 7d window for productive runs. Never unbounded `SELECT COUNT(*)` for metrics. |
| INV-010 | Posts table shows both published AND failed statuses | `get_recent_posts_with_metrics()` in `db.py` filters `status IN ('published', 'failed')`. Failed posts display `error_message` in the dashboard. |
| INV-011 | Tenant data is client-scoped | All tenant dashboard queries include `WHERE client_id=?`. No cross-tenant data leakage in API responses. |

## Watchdog

| ID | Invariant | Verification |
|----|-----------|-------------|
| INV-012 | Watchdog remediates stale pipeline runs | `probe_stale_runs()` detects runs stuck in `running` status > N minutes. `_fix_stale_run()` marks them as `failed`. Verified by `_verify()`. |
| INV-013 | Watchdog catches phantom published posts | `probe_post_delivery()` verifies published posts exist on platform. `_fix_phantom_post()` marks missing posts as `failed`. |

## Subscription / Billing

| ID | Invariant | Verification |
|----|-----------|-------------|
| INV-014 | Trial expiry checked before pipeline run | `check_and_expire_trial()` called in orchestrator before subscription guard. Expired trials block pipeline execution. |
| INV-015 | Internal clients bypass subscription checks | `if not client_data.get("internal")` guard in orchestrator. Internal clients (e.g. Ortobahn default) always run regardless of subscription status. |

## Infrastructure

| ID | Invariant | Verification |
|----|-----------|-------------|
| INV-016 | All infrastructure values documented in `INFRASTRUCTURE.md` | Any change to URLs, AWS resources, service names, ARNs, or GitHub secrets must update `INFRASTRUCTURE.md`. This is the single source of truth — never hardcode infra values without documenting them here. |
