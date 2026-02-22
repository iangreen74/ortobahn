# CLAUDE.md - Project Conventions

Ortobahn is an autonomous AI marketing platform. Multi-tenant SaaS with 12-agent pipeline, web dashboard, and closed-loop self-monitoring.

## Quick Commands

```bash
make install-web   # Install all dependencies (including web)
make test          # Run tests with coverage (pytest)
make lint          # Ruff check + format --check
make lint-fix      # Auto-fix lint/format
make typecheck     # mypy
make validate      # Tests + health check
make web           # Start FastAPI dev server
make deploy-ecs    # Build, push to ECR, deploy to ECS
```

## Code Conventions

### Imports
- `from __future__ import annotations` at top of every module
- stdlib, then third-party, then local (ruff isort order)
- Absolute imports: `from ortobahn.db import Database`
- Type unions: `str | None` (not `Optional[str]`) — Python 3.10+ target

### Database
- Backend-agnostic: use `?` placeholders (auto-converted to `%s` for PostgreSQL)
- Always pass `commit=True` for write operations
- UUID string primary keys via `str(uuid.uuid4())`
- Access: `db.execute()`, `db.fetchone()`, `db.fetchall()`
- Test with `test_db` fixture (fresh SQLite per test)

### Migrations (`ortobahn/migrations.py`)
- Sequential functions: `_migration_NNN_description(db: Database)`
- Register in `MIGRATIONS` dict at bottom of file
- Use `_safe_add_column()` for idempotent column additions
- Use `CREATE TABLE IF NOT EXISTS` for new tables
- After adding migration N+1:
  - Update `MIGRATIONS` dict
  - Update version assertions in `tests/test_migrations.py` and `tests/test_agent_cifix.py`
- Current version: check `len(MIGRATIONS)` in migrations.py

### Web / HTMX Pattern
- Full pages: Jinja2 templates extending `base.html`
- Live fragments: return `HTMLResponse` strings from `/api/*` endpoints
- HTMX polling: `hx-get="/path" hx-trigger="load, every Ns" hx-swap="innerHTML"`
- Tenant auth: `client: AuthClient` dependency (any authenticated user)
- Tenant routes: `/my/` prefix via `APIRouter(prefix="/my")`
- HTML escaping: use `_escape()` helper from `glass.py`

### Agents
- Extend `BaseAgent`, implement `run(run_id, **kwargs)` method
- System prompts in `ortobahn/prompts/{agent_name}.txt`
- Call LLM via `self.call_llm(user_message)` or `self.call_llm(user_message, system_prompt)`
- Log decisions via `self.log_decision(run_id, input_summary, output_summary, reasoning)`
- Extended thinking: set `thinking_budget` class attribute (0 = disabled)

### Testing
- pytest + pytest-asyncio for async route tests
- httpx `AsyncClient` with `ASGITransport` for API tests
- Mock LLM: `monkeypatch.setattr("ortobahn.agents.{module}.call_llm", mock_fn)`
- Key fixtures: `test_db`, `test_settings`, `mock_llm_response`, `test_api_key`, `auth_headers`
- Mark network tests: `@pytest.mark.network`
- All tests must pass before merge (CI enforced)

### CSS
- Single file: `ortobahn/web/static/style.css`
- PicoCSS v2 dark theme (`data-theme="dark"`)
- Badge classes: `.badge.completed`, `.badge.failed`, `.badge.running`, `.badge.draft`
- Glass dashboard classes: `.glass-status-card`, `.glass-pulse`, `.glass-stat`, `.glass-agent-card`
- Colors: indigo `#6366f1`, teal `#00d4aa`, green `#4caf50`, orange `#ffb74d`, red `#ef5350`

## Key Architecture

- **Pipeline**: Analytics → CEO → Strategist → Creator → Publisher (+ CFO, Ops, Support, Marketing, Reflection, SRE, CIFix, Watchdog)
- **Multi-tenant**: per-client data isolation via `client_id` on all tables
- **Auth**: AWS Cognito (prod) or API key (dev), JWT session cookies
- **Billing**: Stripe subscriptions with trial support
- **Storage**: SQLite (dev) / PostgreSQL (prod, RDS)
- **Deployment**: Docker → ECR → ECS (web + scheduler services)

## Infrastructure

Infrastructure values (URLs, AWS resources, service names, ARNs) are in `INFRASTRUCTURE.md`.
Quick reference is always loaded via MEMORY.md.
**Never guess infrastructure values** — always use the documented values.
Prod app: `https://app.ortobahn.com` | Landing: `https://ortobahn.com` | GitHub: `angreen74/ortobahn`

## Important Invariants

See `INVARIANTS.md` for the full list. Key ones:
- Migration version must be consistent between code and test assertions
- Auto-publish defaults to enabled for new clients
- No phantom pipeline runs for ineligible clients
- Health metrics use time-bounded queries (never unbounded)
- All tenant queries include `client_id=?` filter
