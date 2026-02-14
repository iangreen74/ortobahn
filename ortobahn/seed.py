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
        return "vaultscaler"
    return db.create_client(VAULTSCALER_CLIENT)


def seed_ortobahn(db: Database) -> str:
    """Create the Ortobahn self-marketing client if it doesn't exist."""
    existing = db.get_client("ortobahn")
    if existing:
        return "ortobahn"
    return db.create_client(ORTOBAHN_CLIENT)


def seed_all(db: Database) -> list[str]:
    """Seed all known clients."""
    return [seed_vaultscaler(db), seed_ortobahn(db)]
