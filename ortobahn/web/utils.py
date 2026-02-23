"""Shared web utilities used by glass.py, tenant_dashboard.py, and other route modules."""

from __future__ import annotations

PIPELINE_STEPS = [
    "sre",
    "cifix",
    "analytics",
    "reflection",
    "trends",
    "support",
    "security",
    "legal",
    "ceo",
    "strategist",
    "creator",
    "publisher",
    "cfo",
    "ops",
    "marketing",
    "learning",
]


def badge(status: str) -> str:
    """Return an HTML badge span for the given status."""
    return f'<span class="badge {status}">{status}</span>'


def step_index(agent_name: str) -> int:
    """Map agent name to pipeline step number (1-based)."""
    name = agent_name.lower().replace("_agent", "").replace("agent", "").strip()
    for i, step in enumerate(PIPELINE_STEPS):
        if step in name or name in step:
            return i + 1
    return 0


def escape(text: str) -> str:
    """Minimal HTML escaping."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
