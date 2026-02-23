# Ortobahn

Autonomous AI marketing engine. A Vaultscaler subsidiary.

AI agents autonomously create, publish, and optimize marketing content across social platforms — no human in the loop. Multi-tenant SaaS with self-monitoring, self-healing CI/CD, autonomous engineering, and a live public dashboard.

## Architecture

Eighteen AI agents orchestrated in a four-phase pipeline with closed-loop monitoring and cross-session learning:

```
┌───────────────────────────────────────────────────────────────────────────┐
│  PHASE 1: Intelligence Gathering                                         │
│  SRE → CIFix → Analytics → Reflection → Trends → Support → Security     │
│                                                          → Legal         │
│                                                                          │
│  PHASE 2: Executive Decision-Making                                      │
│  CEO (extended thinking) → Dynamic Cadence                               │
│                                                                          │
│  PHASE 3: Content Execution                                              │
│  Strategist → Creator → Publisher → Engagement → Post Feedback           │
│                    ↳ ArticleWriter → Medium / Substack / LinkedIn        │
│                                                                          │
│  PHASE 4: Operations & Learning                                          │
│  CFO → Ops → Marketing → Learning Engine → Meta-Learning                 │
│                                                                          │
│  AUTONOMOUS:                                                             │
│  CTO (engineering tasks → branch → test → PR → auto-merge)              │
│  Enrichment (profile auto-fill from website analysis)                    │
│  Watchdog (sense/decide/act/verify remediation loop)                     │
└───────────────────────────────────────────────────────────────────────────┘
```

### Eighteen Pipeline Agents

| Agent | Role |
|-------|------|
| **SRE** | System health monitoring, pre-flight checks, alert routing |
| **CI Fix** | Self-healing CI/CD — auto-fixes lint, format, and type errors from failed GitHub Actions runs |
| **Analytics** | Past post performance analysis (engagement rates, trends, per-platform metrics) |
| **Reflection** | Pattern analysis, cross-session memory building, confidence calibration (extended thinking) |
| **CEO** | Executive strategy and directive routing — ingests all reports to set themes and goals (extended thinking) |
| **Strategist** | Content planning and idea generation from trends, strategy, and client context (extended thinking) |
| **Creator** | Multi-platform content generation with built-in self-critique loop (extended thinking) |
| **Publisher** | Confidence-gated publishing with post verification and automatic retry with backoff |
| **CFO** | Cost analysis, budget enforcement, ROI estimation per post |
| **Ops** | Client operations management, onboarding pipeline |
| **Support** | Customer health monitoring, churn detection, at-risk client alerts |
| **Marketing** | Self-marketing — Ortobahn promoting itself |
| **Security** | Threat detection, access log analysis, credential health scanning (extended thinking) |
| **Legal** | Compliance gap analysis, Terms of Service and Privacy Policy generation (extended thinking) |
| **Engagement** | Autonomous reply and conversation participation on Bluesky |
| **ArticleWriter** | Long-form article generation for cross-platform publishing (extended thinking) |
| **Enrichment** | Profile auto-enrichment — scrapes client websites and fills brand fields via LLM |
| **CTO** | Autonomous engineering — picks up backlog tasks, writes code, runs tests, creates auto-merge PRs |

### Intelligence Systems

| System | Role |
|--------|------|
| **Learning Engine** | Confidence calibration, theme tracking, anomaly detection, A/B experiment conclusions (0 LLM calls) |
| **Meta-Learning** | Cross-client pattern promotion — insights reinforced across 3+ clients become shared knowledge (0 LLM calls) |
| **Memory Store** | Agent cross-cycle memory with confidence scoring, category tagging, and automatic pruning |
| **Predictive Timing** | Topic velocity tracking — detects emerging trends before they peak |
| **Content Serialization** | Multi-part series management — maintains narrative continuity across posts |
| **Style Evolution** | Organic voice development through A/B experimentation on tone and style |
| **Dynamic Cadence** | Posting frequency optimization based on engagement trends |
| **Post Feedback Loop** | Real-time engagement monitoring — checks post performance minutes after publishing |
| **Publish Recovery** | Automatic retry with exponential backoff on publish failures |
| **A/B Testing** | Causal inference experiments with statistical significance testing |
| **Prompt Tuner** | Performance-aware prompt optimization based on historical outcomes |
| **Preflight Intelligence** | Pre-cycle blocking checks (credentials, API health, budget, subscription status) |
| **Watchdog** | Sense/decide/act/verify loop — remediates stale runs, phantom posts, credential issues |

## Platform Support

### Social Posts
| Platform | Status | Features |
|----------|--------|----------|
| **Bluesky** | Production | Publish, verify, engagement replies, analytics |
| **Twitter/X** | Production | Publish, analytics |
| **LinkedIn** | Production | Publish, analytics |

### Long-Form Articles
| Platform | Status | Features |
|----------|--------|----------|
| **Medium** | Production | Article publishing via API |
| **Substack** | Production | Draft/publish via web API |
| **LinkedIn Articles** | Production | Long-form publishing via REST API |

### Data Sources
| Source | Usage |
|--------|-------|
| **NewsAPI** | Trending headlines + keyword search by client industry |
| **Google Trends** | Global trending searches |
| **RSS Feeds** | Per-client configurable feed monitoring |

## Self-Healing CI/CD

The CI Fix Agent runs every pipeline cycle and autonomously repairs broken builds:

1. **Detection** — Fetches failed GitHub Actions runs via `gh` CLI
2. **Diagnosis** — Categorizes failures (lint, format, type errors, test failures)
3. **Mechanical Fix** — Runs `ruff check --fix`, `ruff format`, auto-imports
4. **LLM Escalation** — For failures that can't be mechanically fixed, sends the error context to Claude for code-level patches
5. **Validation** — Runs the full test suite against the fix
6. **Ship** — Commits, pushes, creates PR with auto-merge enabled

The CTO Agent handles deeper engineering work: it picks up backlog tasks (created by CEO directives or manual entry), reads relevant source files, generates implementation code, writes tests, validates everything passes, and opens auto-merge PRs. Failed tests trigger automatic rollback.

## Multi-Tenant Architecture

- Per-client data isolation (`client_id` on all tables)
- AWS Cognito authentication (email/password, JWT sessions)
- Per-tenant platform credentials (Bluesky, Twitter, LinkedIn, Medium, Substack) — encrypted at rest with Fernet
- Stripe subscription management with free trial support and automatic expiry
- Tenant self-service dashboard with live pipeline monitoring
- Per-client configuration: news categories, RSS feeds, brand voice, target audience, posting cadence
- Webhook system for pipeline events (post published, pipeline completed/failed)

## Web Dashboards

| Dashboard | URL | Purpose |
|-----------|-----|---------|
| **Admin** | `/` | Internal operations — all clients, content review, pipeline management |
| **Tenant** | `/my/dashboard` | Self-service — generate content, monitor health, manage settings, connect platforms |
| **Glass** | `/glass` | Public live dashboard showing Ortobahn's own internals (pipeline steps, post history) |
| **SRE Panel** | `/sre` | System health — success rates, token usage, cost estimates, platform health |
| **Analytics** | `/my/analytics` | Per-tenant performance metrics, engagement trends, content analysis |

All dashboards use HTMX for live-updating panels (no page reloads). Templates rendered server-side with Jinja2.

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
  agents/           # AI agent implementations (18 agents + base class)
  integrations/     # External API clients (Bluesky, Twitter, LinkedIn, Medium, Substack, NewsAPI, RSS, Trends)
  web/
    routes/         # FastAPI route modules (admin, tenant, glass, SRE, auth, payments, legal, onboard, etc.)
    templates/      # Jinja2 HTML templates
    static/         # CSS and static assets
  prompts/          # LLM system prompts (editable .txt files — one per agent)
  dashboard/        # Rich terminal dashboard
  config.py         # Settings from environment variables
  db.py             # Database layer (SQLite + PostgreSQL)
  llm.py            # Shared Claude API wrapper with caching and Bedrock support
  models.py         # Pydantic data contracts
  orchestrator.py   # Pipeline coordinator (4-phase execution)
  migrations.py     # Schema migration system
  watchdog.py       # Closed-loop self-monitoring
  learning.py       # Confidence calibration, A/B testing, anomaly detection
  meta_learning.py  # Cross-client pattern promotion
  memory.py         # Agent cross-cycle memory store
  preflight.py      # Pre-cycle health checks
  predictive_timing.py  # Topic velocity tracking
  serialization.py  # Multi-part content series
  style_evolution.py    # Organic voice A/B experiments
  cadence.py        # Dynamic posting frequency
  post_feedback.py  # Real-time engagement monitoring
  publish_recovery.py   # Retry with exponential backoff
  ab_testing.py     # Causal inference experiments
  prompt_tuner.py   # Performance-aware prompt optimization
  healthcheck.py    # Dependency health checks
  auth.py           # Authentication (Cognito + API keys)
  credentials.py    # Encrypted credential storage (Fernet)
  git_utils.py      # Git operations for CTO agent
  webhooks.py       # Event dispatch system
  backup.py         # SQLite database backups
tests/              # Test suite (60+ test files)
CLAUDE.md           # Project conventions for AI assistants
INVARIANTS.md       # Stability protections
```

## Cost

~$0.10-0.15 per pipeline cycle using Claude Sonnet. Prompt caching reduces costs ~30-50%. At 4 cycles/day: ~$12-18/month per client.
