# Ortobahn

Autonomous AI marketing engine. A Vaultscaler subsidiary.

AI agents autonomously create and publish marketing content to Bluesky -- no human in the loop.

## Architecture

Five AI agents orchestrated in a pipeline:

```
Analytics → CEO → Strategist → Creator → Publisher
    ↑                                        │
    └────────── next cycle ──────────────────┘
```

1. **Analytics Agent** - analyzes past post performance
2. **CEO Agent** - sets marketing strategy based on analytics + trends
3. **Strategist Agent** - turns strategy into specific content ideas
4. **Creator Agent** - writes Bluesky posts (≤300 chars)
5. **Publisher Agent** - posts to Bluesky with confidence filtering

## Quick Start

### Prerequisites

- Python 3.10+
- Anthropic API key
- Bluesky account + app password

### Setup

```bash
git clone <repo-url>
cd ortobahn
cp .env.example .env
# Edit .env with your API keys
make install
make healthcheck
```

### Usage

```bash
make dry-run                              # Test run without posting
make run                                  # Full pipeline cycle
make dashboard                            # View terminal dashboard
python3 -m ortobahn schedule --interval 6 # Run every 6 hours
python3 -m ortobahn status                # Quick status check
```

### Development

```bash
make install       # Install with dev dependencies
make test          # Run tests with coverage
make lint          # Check code style (ruff)
make lint-fix      # Auto-fix lint issues
make typecheck     # Run mypy
make validate      # Tests + health check
```

## Environment Variables

See `.env.example` for all options.

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | Yes | Claude API key |
| `BLUESKY_HANDLE` | Yes* | Bluesky handle (*not needed for `--dry-run`) |
| `BLUESKY_APP_PASSWORD` | Yes* | App password from Settings → App Passwords |
| `NEWSAPI_KEY` | No | Free key from newsapi.org |

## Project Structure

```
ortobahn/
  agents/         # AI agent implementations
  integrations/   # External API clients (Bluesky, NewsAPI, RSS, Google Trends)
  dashboard/      # Rich terminal dashboard
  prompts/        # LLM system prompts (editable .txt files)
  config.py       # Settings from .env
  db.py           # SQLite persistence
  llm.py          # Shared Claude API wrapper
  models.py       # Pydantic data contracts
  orchestrator.py # Pipeline coordinator
  healthcheck.py  # Dependency health checks
tests/            # Test suite
```

## Cost

~$0.10-0.15 per pipeline cycle using Claude Sonnet 4.5. At 4 cycles/day: ~$15/month.
