"""Intelligent test selection — run only tests affected by recent changes."""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger("ortobahn.test_selector")

# Maps source modules to their test files
MODULE_TEST_MAP: dict[str, list[str]] = {
    "ortobahn/agents/ceo.py": ["tests/test_agent_ceo.py"],
    "ortobahn/agents/creator.py": ["tests/test_agent_creator.py"],
    "ortobahn/agents/publisher.py": ["tests/test_agent_publisher.py"],
    "ortobahn/agents/analytics.py": ["tests/test_agent_analytics.py"],
    "ortobahn/agents/strategist.py": ["tests/test_agent_strategist.py"],
    "ortobahn/agents/cifix.py": ["tests/test_agent_cifix.py", "tests/test_cifix_playbook.py"],
    "ortobahn/agents/cfo.py": ["tests/test_agent_cfo.py"],
    "ortobahn/agents/marketing.py": ["tests/test_agent_marketing.py"],
    "ortobahn/agents/ops.py": ["tests/test_agent_ops.py"],
    "ortobahn/agents/sre.py": ["tests/test_agent_sre.py"],
    "ortobahn/agents/base.py": ["tests/test_agent_*.py"],  # Base affects all agents
    "ortobahn/orchestrator.py": ["tests/test_orchestrator.py", "tests/test_pipeline.py"],
    "ortobahn/db/": ["tests/test_db.py"],
    "ortobahn/config.py": ["tests/test_config.py"],
    "ortobahn/models.py": ["tests/test_models.py"],
    "ortobahn/llm.py": ["tests/test_llm.py"],
    "ortobahn/learning.py": ["tests/test_learning.py"],
    "ortobahn/memory.py": ["tests/test_memory.py"],
    "ortobahn/watchdog.py": ["tests/test_watchdog.py"],
    "ortobahn/web/": ["tests/test_web.py", "tests/test_onboard_api.py"],
    "ortobahn/integrations/bluesky.py": ["tests/test_integrations.py"],
    "ortobahn/integrations/twitter.py": ["tests/test_integration_twitter.py"],
    "ortobahn/integrations/linkedin.py": ["tests/test_integration_linkedin.py"],
    "ortobahn/integrations/slack.py": ["tests/test_slack.py"],
    "ortobahn/landing/": ["tests/test_landing_links.py"],
    "ortobahn/mcp_server.py": ["tests/test_mcp_server.py"],
    "ortobahn/migrations.py": ["tests/test_migrations.py", "tests/test_db.py"],
}

# Critical test files that always run
ALWAYS_RUN = [
    "tests/test_config.py",
    "tests/test_db.py",
]


def get_changed_files(base_ref: str = "HEAD~1") -> list[str]:
    """Get list of files changed since base_ref using git diff."""
    import subprocess

    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", base_ref, "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        return [f.strip() for f in result.stdout.strip().split("\n") if f.strip()]
    except Exception as e:
        logger.warning("Failed to get changed files: %s", e)
        return []


def select_tests(changed_files: list[str] | None = None, base_ref: str = "HEAD~1") -> list[str]:
    """Given changed files, return the list of test files to run.

    Returns empty list if ALL tests should run (e.g., too many changes or infrastructure files changed).
    """
    if changed_files is None:
        changed_files = get_changed_files(base_ref)

    if not changed_files:
        return []  # No changes detected, run all

    # If pyproject.toml, requirements, or CI config changed, run everything
    infra_patterns = ["pyproject.toml", "setup.py", "setup.cfg", ".github/", "Dockerfile", "Makefile"]
    for f in changed_files:
        for pattern in infra_patterns:
            if f.startswith(pattern) or f == pattern:
                logger.info("Infrastructure file changed (%s), running all tests", f)
                return []  # empty = run all

    selected: set[str] = set(ALWAYS_RUN)

    for changed_file in changed_files:
        # Direct test file change — include it
        if changed_file.startswith("tests/") and changed_file.endswith(".py"):
            selected.add(changed_file)
            continue

        # Check MODULE_TEST_MAP for exact and prefix matches
        for source_pattern, test_files in MODULE_TEST_MAP.items():
            if source_pattern.endswith("/"):
                # Directory prefix match
                if changed_file.startswith(source_pattern):
                    for tf in test_files:
                        if "*" in tf:
                            # Expand glob patterns
                            selected.update(str(p) for p in Path(".").glob(tf))
                        else:
                            selected.add(tf)
            else:
                # Exact file match
                if changed_file == source_pattern:
                    for tf in test_files:
                        if "*" in tf:
                            selected.update(str(p) for p in Path(".").glob(tf))
                        else:
                            selected.add(tf)

        # Fallback: if a changed .py file has no mapping, try convention
        if changed_file.endswith(".py") and changed_file.startswith("ortobahn/"):
            # ortobahn/foo.py -> tests/test_foo.py
            module_name = Path(changed_file).stem
            conventional_test = f"tests/test_{module_name}.py"
            if Path(conventional_test).exists():
                selected.add(conventional_test)

    # Filter to only existing files
    existing = [t for t in sorted(selected) if Path(t).exists()]

    # If selected tests are >60% of all tests, just run everything
    all_tests = list(Path("tests").glob("test_*.py"))
    if len(existing) > len(all_tests) * 0.6:
        logger.info("Selected %d/%d tests (>60%%), running all", len(existing), len(all_tests))
        return []

    logger.info("Selected %d/%d tests for changed files", len(existing), len(all_tests))
    return existing


def format_pytest_args(test_files: list[str]) -> str:
    """Format test files as pytest arguments string."""
    if not test_files:
        return ""  # empty = run all
    return " ".join(str(f) for f in test_files)
