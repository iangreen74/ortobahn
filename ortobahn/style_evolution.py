"""Style Evolution — A/B testing for content writing styles.

Activates the existing ab_experiments infrastructure to run experiments
on writing style: sentence structure, post length, tone variation,
hook styles, etc.

Works with:
- ab_experiments table (migration 010) for experiment tracking
- posts.ab_group / posts.ab_pair_id (migration 006) for variant tagging
- LearningEngine._check_experiments() for automatic conclusion
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from ortobahn.ab_testing import generate_pair_id
from ortobahn.db import Database

logger = logging.getLogger("ortobahn.style_evolution")

# Predefined experiment templates — the system auto-creates these
EXPERIMENT_TEMPLATES = [
    {
        "hypothesis": "Shorter posts (under 150 chars) get more engagement than longer ones",
        "variable": "post_length",
        "variant_a": "Short and punchy (under 150 characters)",
        "variant_b": "Detailed and informative (200-300 characters)",
    },
    {
        "hypothesis": "Posts starting with a question get more engagement",
        "variable": "hook_style",
        "variant_a": "Open with a direct question",
        "variant_b": "Open with a bold statement",
    },
    {
        "hypothesis": "Contrarian takes outperform consensus views",
        "variable": "tone",
        "variant_a": "Contrarian / provocative take",
        "variant_b": "Consensus / agreeable take",
    },
    {
        "hypothesis": "First-person narrative outperforms third-person analysis",
        "variable": "perspective",
        "variant_a": "First-person narrative (we/our experience)",
        "variant_b": "Third-person analytical (the industry/companies)",
    },
    {
        "hypothesis": "Data-driven posts outperform opinion-driven ones",
        "variable": "evidence_style",
        "variant_a": "Lead with specific data or numbers",
        "variant_b": "Lead with opinion or observation",
    },
]


class StyleEvolution:
    """Manage A/B style experiments for content evolution."""

    def __init__(self, db: Database):
        self.db = db

    def ensure_active_experiment(self, client_id: str, run_id: str = "") -> dict | None:
        """Ensure there's an active experiment for this client.

        If no active experiment exists, creates one from the next template.
        Returns the active experiment dict, or None if all templates exhausted.
        """
        active = self.get_active_experiment(client_id)
        if active:
            return active

        # Find which templates haven't been run yet
        concluded = self.db.fetchall(
            "SELECT variable FROM ab_experiments WHERE client_id = ? AND status IN ('concluded', 'active')",
            (client_id,),
        )
        used_variables = {r["variable"] for r in concluded}

        for template in EXPERIMENT_TEMPLATES:
            if template["variable"] not in used_variables:
                return self.create_experiment(
                    client_id=client_id,
                    hypothesis=template["hypothesis"],
                    variable=template["variable"],
                    variant_a=template["variant_a"],
                    variant_b=template["variant_b"],
                    run_id=run_id,
                )

        logger.info(f"All experiment templates exhausted for client {client_id}")
        return None

    def create_experiment(
        self,
        client_id: str,
        hypothesis: str,
        variable: str,
        variant_a: str,
        variant_b: str,
        min_pairs: int = 5,
        run_id: str = "",
    ) -> dict:
        """Create a new A/B experiment."""
        exp_id = str(uuid.uuid4())[:8]
        now = datetime.now(timezone.utc).isoformat()

        self.db.execute(
            """INSERT INTO ab_experiments
               (id, client_id, hypothesis, variable, variant_a_description,
                variant_b_description, status, pair_count, min_pairs_required,
                created_at, created_by_run_id)
            VALUES (?, ?, ?, ?, ?, ?, 'active', 0, ?, ?, ?)""",
            (exp_id, client_id, hypothesis, variable, variant_a, variant_b, min_pairs, now, run_id),
            commit=True,
        )
        logger.info(f"Created experiment '{hypothesis}' (id={exp_id}) for client {client_id}")
        return {
            "id": exp_id,
            "client_id": client_id,
            "hypothesis": hypothesis,
            "variable": variable,
            "variant_a_description": variant_a,
            "variant_b_description": variant_b,
            "status": "active",
            "pair_count": 0,
            "min_pairs_required": min_pairs,
        }

    def get_active_experiment(self, client_id: str) -> dict | None:
        """Get the current active experiment for a client."""
        row = self.db.fetchone(
            "SELECT * FROM ab_experiments WHERE client_id = ? AND status = 'active' ORDER BY created_at DESC LIMIT 1",
            (client_id,),
        )
        return dict(row) if row else None

    def get_experiment_context(self, client_id: str) -> str:
        """Build context string for Creator about the active experiment.

        Returns formatted instructions telling the Creator how to generate
        A/B variants for the current experiment.
        """
        exp = self.get_active_experiment(client_id)
        if not exp:
            return ""

        # Get past experiment results for context
        past = self.db.fetchall(
            "SELECT hypothesis, winner, variant_a_description, variant_b_description, result_summary "
            "FROM ab_experiments WHERE client_id = ? AND status = 'concluded' "
            "ORDER BY concluded_at DESC LIMIT 3",
            (client_id,),
        )

        lines = [
            "## A/B Style Experiment (ACTIVE)",
            f"Hypothesis: {exp['hypothesis']}",
            f"Variable being tested: {exp['variable']}",
            f"Variant A: {exp['variant_a_description']}",
            f"Variant B: {exp['variant_b_description']}",
            f"Pairs completed: {exp['pair_count']}/{exp['min_pairs_required']}",
            "",
            "IMPORTANT: For ONE of the post ideas, generate TWO versions:",
            "1. First version following Variant A style",
            "2. Second version following Variant B style",
            "Mark them with ab_group: 'A' or 'B' in your response.",
        ]

        if past:
            lines.append("\n## Past Experiment Results (apply these learnings):")
            for p in past:
                winner_desc = (
                    p["variant_a_description"]
                    if p["winner"] == "A"
                    else p["variant_b_description"]
                    if p["winner"] == "B"
                    else "No clear winner"
                )
                lines.append(f"- {p['hypothesis']}: Winner = {winner_desc}")

        return "\n".join(lines)

    def tag_post_pair(self, post_id_a: str, post_id_b: str, experiment_id: str) -> str:
        """Tag two posts as an A/B pair. Returns the pair_id."""
        pair_id = generate_pair_id()

        self.db.execute(
            "UPDATE posts SET ab_group = 'A', ab_pair_id = ? WHERE id = ?",
            (pair_id, post_id_a),
            commit=True,
        )
        self.db.execute(
            "UPDATE posts SET ab_group = 'B', ab_pair_id = ? WHERE id = ?",
            (pair_id, post_id_b),
            commit=True,
        )

        # Increment pair count on experiment
        self.db.execute(
            "UPDATE ab_experiments SET pair_count = pair_count + 1 WHERE id = ?",
            (experiment_id,),
            commit=True,
        )

        logger.info(f"Tagged A/B pair {pair_id} for experiment {experiment_id}")
        return pair_id

    def get_style_learnings(self, client_id: str) -> list[dict]:
        """Get concluded experiments and their winners for style guidance."""
        rows = self.db.fetchall(
            """SELECT hypothesis, variable, variant_a_description, variant_b_description,
                      winner, result_summary, concluded_at
               FROM ab_experiments
               WHERE client_id = ? AND status = 'concluded' AND winner IS NOT NULL
               ORDER BY concluded_at DESC""",
            (client_id,),
        )
        return [dict(r) for r in rows]
