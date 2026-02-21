# Ortobahn

Autonomous AI marketing engine. A Vaultscaler subsidiary.

AI agents autonomously create, publish, and optimize marketing content across social platforms — no human in the loop. Multi-tenant SaaS with self-monitoring, self-healing CI/CD, and a live public dashboard.

## Architecture

Twelve AI agents orchestrated in a pipeline with closed-loop monitoring:

```
┌─────────────────────────────────────────────────────────────┐
│  SRE → Analytics → Reflection → CEO → Strategist → Creator │
│                                                     ↓       │
│  Watchdog ← CFO ← Ops ← Support ← Marketing ← Publisher   │
│     ↓                                                       │
│  CIFix (self-healing CI/CD)                                 │
└─────────────────────────────────────────────────────────────┘
```

### Core Pipeline Agents

| Agent | Role |
|-------|------|
| **SRE** | System health monitoring and pre-flight checks |
| **Analytics** | Past post performance analysis (engagement, trends) |
| **Reflection** | Pattern analysis, memory building, confidence calibration |
| **CEO** | Strategic direction setting (extended thinking) |
| **Strategist** | Content planning and idea generation (extended thinking) |
| **Creator** | Multi-platform content generation with self-critique |
| **Publisher** | Confidence-gated publishing with post verification |
| **CFO** | Cost analysis and budget enforcement |
| **Ops** | Client operations management |
| **Support** | Customer health monitoring, churn detection |
| **Marketing** | Self-marketing for Ortobahn itself |

### Supporting Systems

| System | Role |
|--------|------|
| **Watchdog** | Sense/decide/act/verify loop — remediates stale runs, phantom posts, credential issues |
| **CIFix** | Self-healing CI/CD — auto-fixes lint, format, and type errors from failed GitHub Actions runs |
| **Learning Engine** | Confidence calibration, A/B testing, anomaly detection |
| **Memory Store** | Agent cross-cycle memory with confidence scoring and pruning |
| **Preflight** | Pre-cycle blocking checks (credentials, API health, budget) |

## Multi-Tenant Architecture

- Per-client data isolation (`client_id` on all tables)
- AWS Cognito authentication (email/password, JWT sessions)
- Per-tenant platform credentials (Bluesky, Twitter, LinkedIn) — encrypted at rest
- Stripe subscription management with free trial support
- Tenant self-service dashboard with live monitoring

## Web Dashboards

| Dashboard | URL | Purpose |
|-----------|-----|---------|
| Admin | `/` | Internal operations — all clients, content review, pipeline management |
| Tenant | `/my/dashboard` | Self-service — generate content, monitor health, manage settings |
| Glass | `/glass` | Public live dashboard showing Ortobahn's own internals |

All dashboards use HTMX for live-updating panels (no page reloads).

## Quick Start

### Prerequisites

- Python 3.10+
- Anthropic API key
- Platform credentials (Bluesky, Twitter, LinkedIn — optional)

### Setup

```bash
git clone <repo-url>
cd ortobahn
cp .env.example .env
# Edit .env with your API keys
make install-web
make healthcheck
```

### Usage

```bash
make web                                  # Start web dashboard (FastAPI)
make run                                  # Single pipeline cycle
make dry-run                              # Test run without posting
make dashboard                            # Terminal dashboard (Rich)
python3 -m ortobahn schedule --interval 6 # Scheduler (runs every N hours)
python3 -m ortobahn status                # Quick status check
python3 -m ortobahn seed                  # Seed default client data
```

### Development

```bash
make install-web   # Install with dev + web dependencies
make test          # Run tests with coverage
make lint          # Check code style (ruff check + format)
make lint-fix      # Auto-fix lint/format issues
make typecheck     # Run mypy
make validate      # Tests + health check
```

## Deployment

| Target | Command | Details |
|--------|---------|---------|
| Docker Compose | `make docker-up` | Local development |
| AWS ECS | `make deploy-ecs` | Production — builds, pushes to ECR, deploys web + scheduler services |
| AWS EC2 | `make deploy-ec2` | Fallback — deploys via SSM |
| Landing page | `make deploy-landing` | S3 + CloudFront at ortobahn.com |

**Production stack**: Docker → ECR → ECS (two services: `ortobahn-web-v2` + `ortobahn-scheduler-v2`), PostgreSQL on RDS, Cognito for auth.

## Environment Variables

See `.env.example` for all options. Key variables:

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | Yes | Claude API key |
| `BLUESKY_HANDLE` | For publishing | Bluesky handle (e.g. `you.bsky.social`) |
| `BLUESKY_APP_PASSWORD` | For publishing | App password from Bluesky Settings |
| `DB_PATH` | No | SQLite path (default: `data/ortobahn.db`) |
| `DATABASE_URL` | Production | PostgreSQL connection string |
| `ORTOBAHN_SECRET_KEY` | Yes | Encryption key for platform credentials |
| `COGNITO_USER_POOL_ID` | Production | AWS Cognito user pool |
| `COGNITO_CLIENT_ID` | Production | AWS Cognito app client |
| `STRIPE_SECRET_KEY` | For billing | Stripe API key |
| `STRIPE_PRICE_ID` | For billing | Stripe subscription price ID |
| `STRIPE_WEBHOOK_SECRET` | For billing | Stripe webhook signing secret |
| `NEWSAPI_KEY` | No | Free key from newsapi.org |

## Project Structure

```
ortobahn/
  agents/           # AI agent implementations (12 agents + base class)
  integrations/     # External API clients (Bluesky, Twitter, LinkedIn, NewsAPI, RSS, Trends)
  web/
    routes/         # FastAPI route modules (admin, tenant, glass, auth, payments, etc.)
    templates/      # Jinja2 HTML templates
    static/         # CSS and static assets
  prompts/          # LLM system prompts (editable .txt files)
  dashboard/        # Rich terminal dashboard
  config.py         # Settings from environment variables
  db.py             # Database layer (SQLite + PostgreSQL)
  llm.py            # Shared Claude API wrapper with caching and Bedrock support
  models.py         # Pydantic data contracts
  orchestrator.py   # Pipeline coordinator
  migrations.py     # Schema migration system
  watchdog.py       # Closed-loop self-monitoring
  learning.py       # Confidence calibration, A/B testing
  memory.py         # Agent cross-cycle memory store
  preflight.py      # Pre-cycle health checks
  healthcheck.py    # Dependency health checks
  auth.py           # Authentication (Cognito + API keys)
  credentials.py    # Encrypted credential storage
tests/              # Test suite (390+ tests)
CLAUDE.md           # Project conventions for AI assistants
INVARIANTS.md       # Stability protections
```

## Cost

~$0.10-0.15 per pipeline cycle using Claude Sonnet. Prompt caching reduces costs ~30-50%. At 4 cycles/day: ~$12-18/month per client.
