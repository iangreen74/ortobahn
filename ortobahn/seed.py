"""Seed data for known clients."""

from __future__ import annotations

from ortobahn.db import Database

VAULTSCALER_CLIENT = {
    "id": "vaultscaler",
    "name": "Vaultscaler",
    "description": "Autonomous engineering company building self-operating software systems. "
    "Flagship product Lev is an autonomous engineering platform that handles the engineering. "
    "Radix is a GPU orchestration platform for AI workloads at scale.",
    "industry": "Software Engineering / AI",
    "target_audience": "CTOs, VP Engineering, tech leads, developers building complex systems, "
    "AI/ML engineers needing GPU infrastructure",
    "brand_voice": "technically authoritative, direct, zero-bullshit engineering credibility",
    "website": "https://vaultscaler.com",
    "products": "Lev: Autonomous engineering platform — handles software engineering tasks autonomously. "
    "Radix: GPU orchestration platform — manages GPU clusters for AI/ML training and inference at scale.",
    "competitive_positioning": "Building autonomous systems for engineering teams. "
    "Where others offer copilots, Vaultscaler builds fully autonomous agents that own entire workflows.",
    "key_messages": "Autonomous engineering, AI-native infrastructure, GPU-at-scale, "
    "engineering without humans in the loop",
    "content_pillars": "Autonomous engineering, AI infrastructure, developer productivity, "
    "GPU computing, the future of software development",
    "company_story": "Vaultscaler was built on a simple thesis: software should build itself. "
    "Lev is the autonomous engineer — it doesn't assist, it executes. "
    "Radix is the compute layer — GPU orchestration that scales with demand. "
    "Together they represent the full stack of autonomous engineering.",
}

ORTOBAHN_CLIENT = {
    "id": "ortobahn",
    "name": "Ortobahn",
    "description": "Autonomous AI marketing engine that generates, optimizes, and publishes content "
    "across social media platforms using a multi-agent pipeline. No humans in the loop.",
    "industry": "AI / Marketing Technology",
    "target_audience": "Founders, marketing leaders, growth teams at startups and SMBs "
    "who want marketing that runs itself",
    "brand_voice": "Sharp, confident, technical but accessible. Shows don't tell. "
    "Let results speak. Never salesy, never generic.",
    "website": "https://ortobahn.com",
    "products": "Ortobahn: Autonomous marketing engine powered by a pipeline of specialized AI agents "
    "(Analytics, CEO, Strategist, Creator, Publisher, SRE, CFO, Ops).",
    "competitive_positioning": "Not a scheduling tool. Not a writing assistant. "
    "Ortobahn is a fully autonomous marketing company — strategy, creation, and publishing "
    "with no human in the loop. The marketing runs itself.",
    "key_messages": "Autonomous marketing, AI agents that strategize and create, "
    "confidence-gated publishing, multi-platform content at scale",
    "content_pillars": "AI marketing, autonomous systems, content at scale, marketing ROI, "
    "the death of manual social media management",
    "company_story": "Born from Vaultscaler's autonomous engineering DNA. "
    "If software can build itself, marketing can run itself. "
    "Ortobahn is living proof — it markets itself using its own pipeline.",
}


def seed_vaultscaler(db: Database) -> str:
    """Create the Vaultscaler client if it doesn't exist. Returns client_id."""
    existing = db.get_client("vaultscaler")
    if existing:
        db.execute("UPDATE clients SET internal=1 WHERE id='vaultscaler'", commit=True)
        return "vaultscaler"
    cid = db.create_client(VAULTSCALER_CLIENT)
    db.execute("UPDATE clients SET internal=1 WHERE id=?", (cid,), commit=True)
    return cid


def seed_ortobahn(db: Database) -> str:
    """Create the Ortobahn self-marketing client if it doesn't exist.

    Migration 001 creates a 'default' client named 'Ortobahn'. We update it
    in-place rather than creating a duplicate.
    """
    existing = db.get_client("ortobahn")
    if existing:
        db.execute("UPDATE clients SET internal=1 WHERE id='ortobahn'", commit=True)
        return "ortobahn"

    # Check if the 'default' client is already named Ortobahn (from migration 001)
    default = db.get_client("default")
    if default and default.get("name") == "Ortobahn":
        # Update the default client with full ortobahn profile
        for key in (
            "description",
            "industry",
            "target_audience",
            "brand_voice",
            "website",
            "products",
            "competitive_positioning",
            "key_messages",
            "content_pillars",
            "company_story",
        ):
            if key in ORTOBAHN_CLIENT:
                db.execute(f"UPDATE clients SET {key}=? WHERE id='default'", (ORTOBAHN_CLIENT[key],), commit=True)
        db.execute("UPDATE clients SET internal=1 WHERE id='default'", commit=True)
        return "default"

    cid = db.create_client(ORTOBAHN_CLIENT)
    db.execute("UPDATE clients SET internal=1 WHERE id=?", (cid,), commit=True)
    return cid


def seed_vaultscaler_credentials(db: Database, settings) -> None:
    """Migrate Vaultscaler credentials from env vars to per-tenant storage."""
    if not settings.secret_key:
        return

    from ortobahn.credentials import get_platform_credentials, save_platform_credentials

    # Bluesky
    if settings.bluesky_handle and settings.bluesky_app_password:
        existing = get_platform_credentials(db, "vaultscaler", "bluesky", settings.secret_key)
        if not existing:
            save_platform_credentials(
                db,
                "vaultscaler",
                "bluesky",
                {"handle": settings.bluesky_handle, "app_password": settings.bluesky_app_password},
                settings.secret_key,
            )

    # Twitter
    if settings.has_twitter():
        existing = get_platform_credentials(db, "vaultscaler", "twitter", settings.secret_key)
        if not existing:
            save_platform_credentials(
                db,
                "vaultscaler",
                "twitter",
                {
                    "api_key": settings.twitter_api_key,
                    "api_secret": settings.twitter_api_secret,
                    "access_token": settings.twitter_access_token,
                    "access_token_secret": settings.twitter_access_token_secret,
                },
                settings.secret_key,
            )

    # LinkedIn
    if settings.has_linkedin():
        existing = get_platform_credentials(db, "vaultscaler", "linkedin", settings.secret_key)
        if not existing:
            save_platform_credentials(
                db,
                "vaultscaler",
                "linkedin",
                {"access_token": settings.linkedin_access_token, "person_urn": settings.linkedin_person_urn},
                settings.secret_key,
            )


CTO_BACKLOG_TASKS = [
    {
        "title": "Add health check endpoint for ALB",
        "description": "Add a /healthz endpoint that returns 200 OK with basic system status "
        "(db connectivity, uptime). This is needed for the AWS ALB health checks.",
        "priority": 1,
        "category": "infra",
        "estimated_complexity": "low",
    },
    {
        "title": "Add rate limiting to API endpoints",
        "description": "Implement rate limiting on the web API endpoints to prevent abuse. "
        "Use a sliding-window approach with configurable limits per client/IP. "
        "Store counters in the database or in-memory.",
        "priority": 2,
        "category": "feature",
        "estimated_complexity": "medium",
    },
    {
        "title": "Add password-based login",
        "description": "Implement password-based authentication for the web dashboard. "
        "Add user registration, login, and session management. "
        "Hash passwords with bcrypt. Add login/logout endpoints.",
        "priority": 2,
        "category": "feature",
        "estimated_complexity": "high",
    },
    {
        "title": "Add /api/content JSON endpoint",
        "description": "Add a public JSON API endpoint at /api/content that returns "
        "recent published content for a client. Support pagination, filtering by platform, "
        "and date range. Include engagement metrics where available.",
        "priority": 3,
        "category": "feature",
        "estimated_complexity": "low",
    },
    {
        "title": "Improve test coverage for auth module",
        "description": "Write comprehensive tests for ortobahn/auth.py covering "
        "API key generation, hashing, validation, and edge cases. "
        "Target at least 90% coverage for the auth module.",
        "priority": 3,
        "category": "test",
        "estimated_complexity": "medium",
    },
    {
        "title": "Add database backup to S3",
        "description": "Implement automated database backup to S3. Add a backup_to_s3() function "
        "that uploads the SQLite database file to a configured S3 bucket. "
        "Include configurable schedule and retention policy.",
        "priority": 3,
        "category": "infra",
        "estimated_complexity": "medium",
    },
    {
        "title": "Add OpenAPI documentation customization",
        "description": "Customize the FastAPI auto-generated OpenAPI docs with proper descriptions, "
        "examples, and tags for all endpoints. Add request/response examples "
        "and group endpoints by functionality.",
        "priority": 4,
        "category": "docs",
        "estimated_complexity": "low",
    },
]


def seed_cto_backlog(db: Database) -> list[str]:
    """Seed the CTO engineering backlog with initial tasks. Returns list of task IDs."""
    task_ids: list[str] = []
    existing = db.get_engineering_tasks(limit=100)
    existing_titles = {t["title"] for t in existing}

    for task_data in CTO_BACKLOG_TASKS:
        if task_data["title"] in existing_titles:
            continue
        tid = db.create_engineering_task(task_data)
        task_ids.append(tid)

    return task_ids


def seed_all(db: Database, settings=None) -> list[str]:
    """Seed all known clients and optionally migrate credentials."""
    ids = [seed_vaultscaler(db), seed_ortobahn(db)]
    if settings:
        seed_vaultscaler_credentials(db, settings)
    seed_cto_backlog(db)
    return ids
