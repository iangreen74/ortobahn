"""Learning Engine - connects outcomes to decisions with pure computation (0 LLM calls).

Runs at the end of each pipeline cycle to:
1. Track theme performance across strategies
2. Update confidence calibration records (predicted vs actual)
3. Detect anomalous posts (viral hits or silent failures)
4. Check and conclude mature A/B experiments
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

from ortobahn.ab_testing import get_ab_results
from ortobahn.db import Database
from ortobahn.memory import MemoryStore
from ortobahn.models import AgentMemory, MemoryCategory, MemoryType

logger = logging.getLogger("ortobahn.learning")


class LearningEngine:
    """Pure-computation learning loop.  Zero LLM calls."""

    def __init__(self, db: Database, memory_store: MemoryStore):
        self.db = db
        self.memory = memory_store

    def process_outcomes(self, run_id: str, client_id: str = "default") -> dict:
        """Called at the end of each pipeline cycle.  Returns summary of what was learned."""
        results: dict = {}
        results["calibrations"] = self._update_calibration_records(client_id, run_id)
        results["anomalies"] = self._detect_anomalies(client_id, run_id)
        results["theme_tracking"] = self._track_theme_performance(client_id, run_id)
        results["experiments"] = self._check_experiments(client_id, run_id)
        return results

    # ------------------------------------------------------------------
    # 1. Confidence calibration
    # ------------------------------------------------------------------

    def _update_calibration_records(self, client_id: str, run_id: str) -> dict:
        """Record predicted confidence vs actual engagement for recent published posts.

        Only processes posts that do not already have a calibration record.
        Returns a summary dict with count of new records and average calibration error.
        """
        # Get published posts that are missing calibration records
        rows = self.db.conn.execute(
            """
            SELECT p.id, p.confidence, p.text,
                   COALESCE(m.like_count, 0) + COALESCE(m.repost_count, 0)
                   + COALESCE(m.reply_count, 0) + COALESCE(m.quote_count, 0) AS engagement
            FROM posts p
            LEFT JOIN metrics m ON p.id = m.post_id
            WHERE p.status = 'published'
              AND p.client_id = ?
              AND p.id NOT IN (SELECT post_id FROM confidence_calibration WHERE client_id = ?)
            ORDER BY p.published_at DESC
            LIMIT 50
            """,
            (client_id, client_id),
        ).fetchall()

        if not rows:
            return {"new_records": 0, "avg_error": 0.0}

        posts = [dict(r) for r in rows]

        # Sort by engagement to compute percentiles
        sorted_posts = sorted(posts, key=lambda p: p["engagement"])
        n = len(sorted_posts)
        engagement_rank: dict[str, float] = {}
        for idx, p in enumerate(sorted_posts):
            engagement_rank[p["id"]] = (idx + 1) / n  # percentile in [1/n .. 1.0]

        now = datetime.now(timezone.utc).isoformat()
        total_error = 0.0

        for p in posts:
            percentile = engagement_rank[p["id"]]
            predicted = p["confidence"] or 0.0
            error = abs(predicted - percentile)
            total_error += error

            cal_id = str(uuid.uuid4())[:8]
            self.db.conn.execute(
                """
                INSERT INTO confidence_calibration
                    (id, post_id, client_id, predicted_confidence, actual_engagement,
                     engagement_percentile, calibration_error, measured_at, run_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cal_id,
                    p["id"],
                    client_id,
                    predicted,
                    p["engagement"],
                    round(percentile, 4),
                    round(error, 4),
                    now,
                    run_id,
                ),
            )

        self.db.conn.commit()

        avg_error = round(total_error / n, 4)
        logger.info(
            "Calibration: %d new records for client %s (avg error %.4f)",
            n,
            client_id,
            avg_error,
        )
        return {"new_records": n, "avg_error": avg_error}

    # ------------------------------------------------------------------
    # 2. Anomaly detection
    # ------------------------------------------------------------------

    def _detect_anomalies(self, client_id: str, run_id: str) -> list[dict]:
        """Detect posts with engagement significantly above or below the average.

        - 3x+ average engagement -> high_performer observation memory
        - 0 engagement when average > 2 -> low_performer observation memory

        Returns a list of anomaly dicts.
        """
        rows = self.db.conn.execute(
            """
            SELECT p.id, p.text, p.source_idea, p.confidence, p.platform,
                   COALESCE(m.like_count, 0) + COALESCE(m.repost_count, 0)
                   + COALESCE(m.reply_count, 0) + COALESCE(m.quote_count, 0) AS engagement
            FROM posts p
            LEFT JOIN metrics m ON p.id = m.post_id
            WHERE p.status = 'published' AND p.client_id = ?
            ORDER BY p.published_at DESC
            LIMIT 50
            """,
            (client_id,),
        ).fetchall()

        if not rows:
            return []

        posts = [dict(r) for r in rows]
        total_engagement = sum(p["engagement"] for p in posts)
        avg_engagement = total_engagement / len(posts) if posts else 0.0

        anomalies: list[dict] = []

        for p in posts:
            eng = p["engagement"]

            # High performer: 3x+ average
            if avg_engagement > 0 and eng >= avg_engagement * 3:
                anomaly = {
                    "type": "high_performer",
                    "post_id": p["id"],
                    "engagement": eng,
                    "average": round(avg_engagement, 2),
                    "ratio": round(eng / avg_engagement, 2),
                    "text_preview": (p["text"] or "")[:80],
                }
                anomalies.append(anomaly)

                self.memory.remember(
                    AgentMemory(
                        agent_name="creator",
                        client_id=client_id,
                        memory_type=MemoryType.OBSERVATION,
                        category=MemoryCategory.CONTENT_PATTERN,
                        content={
                            "summary": f"High performer ({eng} engagement, {anomaly['ratio']}x avg): {anomaly['text_preview']}",
                            "details": {
                                "engagement": eng,
                                "avg_engagement": round(avg_engagement, 2),
                                "ratio": anomaly["ratio"],
                                "source_idea": p.get("source_idea", ""),
                                "platform": p.get("platform", ""),
                            },
                        },
                        confidence=0.7,
                        source_run_id=run_id,
                        source_post_ids=[p["id"]],
                    )
                )
                logger.info(
                    "Anomaly: high performer post %s (%.1fx avg engagement)",
                    p["id"][:8],
                    anomaly["ratio"],
                )

            # Low performer: 0 engagement when average > 2
            elif avg_engagement > 2 and eng == 0:
                anomaly = {
                    "type": "low_performer",
                    "post_id": p["id"],
                    "engagement": 0,
                    "average": round(avg_engagement, 2),
                    "text_preview": (p["text"] or "")[:80],
                }
                anomalies.append(anomaly)

                self.memory.remember(
                    AgentMemory(
                        agent_name="creator",
                        client_id=client_id,
                        memory_type=MemoryType.OBSERVATION,
                        category=MemoryCategory.CONTENT_PATTERN,
                        content={
                            "summary": f"Low performer (0 engagement, avg was {round(avg_engagement, 2)}): {anomaly['text_preview']}",
                            "details": {
                                "engagement": 0,
                                "avg_engagement": round(avg_engagement, 2),
                                "source_idea": p.get("source_idea", ""),
                                "platform": p.get("platform", ""),
                            },
                        },
                        confidence=0.6,
                        source_run_id=run_id,
                        source_post_ids=[p["id"]],
                    )
                )
                logger.info("Anomaly: low performer post %s (0 engagement)", p["id"][:8])

        if anomalies:
            logger.info(
                "Detected %d anomalies for client %s (%d high, %d low)",
                len(anomalies),
                client_id,
                sum(1 for a in anomalies if a["type"] == "high_performer"),
                sum(1 for a in anomalies if a["type"] == "low_performer"),
            )
        return anomalies

    # ------------------------------------------------------------------
    # 3. Theme performance tracking
    # ------------------------------------------------------------------

    def _track_theme_performance(self, client_id: str, run_id: str) -> dict[str, float]:
        """Calculate average engagement per strategy theme.

        Joins strategies to their posts, extracts theme JSON, and aggregates
        engagement per theme.  Creates observation memories for standout themes.

        Returns dict mapping theme -> average engagement.
        """
        # Get recent strategies with their themes
        strategy_rows = self.db.conn.execute(
            """
            SELECT id, themes FROM strategies
            WHERE client_id = ?
            ORDER BY created_at DESC
            LIMIT 10
            """,
            (client_id,),
        ).fetchall()

        if not strategy_rows:
            return {}

        # Map strategy_id -> list of themes
        strategy_themes: dict[str, list[str]] = {}
        for row in strategy_rows:
            try:
                themes = json.loads(row["themes"])
            except (json.JSONDecodeError, TypeError):
                themes = []
            strategy_themes[row["id"]] = themes if isinstance(themes, list) else []

        strategy_ids = list(strategy_themes.keys())
        if not strategy_ids:
            return {}

        # Get posts linked to these strategies with engagement
        placeholders = ",".join("?" for _ in strategy_ids)
        post_rows = self.db.conn.execute(
            f"""
            SELECT p.strategy_id,
                   COALESCE(m.like_count, 0) + COALESCE(m.repost_count, 0)
                   + COALESCE(m.reply_count, 0) + COALESCE(m.quote_count, 0) AS engagement
            FROM posts p
            LEFT JOIN metrics m ON p.id = m.post_id
            WHERE p.status = 'published'
              AND p.client_id = ?
              AND p.strategy_id IN ({placeholders})
            """,
            [client_id] + strategy_ids,
        ).fetchall()

        if not post_rows:
            return {}

        # Aggregate engagement per theme
        theme_engagements: dict[str, list[int]] = {}
        for row in post_rows:
            sid = row["strategy_id"]
            eng = row["engagement"]
            for theme in strategy_themes.get(sid, []):
                theme_engagements.setdefault(theme, []).append(eng)

        # Calculate averages
        theme_avgs: dict[str, float] = {}
        for theme, engagements in theme_engagements.items():
            if engagements:
                theme_avgs[theme] = round(sum(engagements) / len(engagements), 2)

        if not theme_avgs:
            return {}

        # Find standout themes (above overall average)
        overall_avg = sum(theme_avgs.values()) / len(theme_avgs) if theme_avgs else 0
        for theme, avg in theme_avgs.items():
            if overall_avg > 0 and avg >= overall_avg * 1.5 and len(theme_engagements.get(theme, [])) >= 2:
                self.memory.remember(
                    AgentMemory(
                        agent_name="strategist",
                        client_id=client_id,
                        memory_type=MemoryType.OBSERVATION,
                        category=MemoryCategory.THEME_PERFORMANCE,
                        content={
                            "summary": f"Theme '{theme}' outperforms: {avg} avg engagement vs {round(overall_avg, 2)} overall",
                            "details": {
                                "theme": theme,
                                "avg_engagement": avg,
                                "overall_avg": round(overall_avg, 2),
                                "post_count": len(theme_engagements[theme]),
                            },
                        },
                        confidence=min(0.8, 0.5 + len(theme_engagements[theme]) * 0.05),
                        source_run_id=run_id,
                    )
                )
                logger.info(
                    "Theme '%s' outperforms: %.1f avg vs %.1f overall (%d posts)",
                    theme,
                    avg,
                    overall_avg,
                    len(theme_engagements[theme]),
                )

        return theme_avgs

    # ------------------------------------------------------------------
    # 4. A/B experiment checking
    # ------------------------------------------------------------------

    def _check_experiments(self, client_id: str, run_id: str) -> list[dict]:
        """Check active A/B experiments and conclude any that have enough data.

        For each active experiment, counts completed post pairs from the posts table.
        When completed pairs >= min_pairs_required, concludes the experiment and
        creates a lesson memory.

        Returns list of concluded experiment summaries.
        """
        experiments = self.db.conn.execute(
            "SELECT * FROM ab_experiments WHERE client_id = ? AND status = 'active'",
            (client_id,),
        ).fetchall()

        if not experiments:
            return []

        concluded: list[dict] = []

        for exp in experiments:
            exp = dict(exp)
            exp_id = exp["id"]
            min_pairs = exp["min_pairs_required"] or 5

            # Use get_ab_results to count completed pairs and determine winner
            ab_results = get_ab_results(self.db, client_id=client_id)
            completed_pairs = ab_results["completed_pairs"]

            if completed_pairs < min_pairs:
                logger.debug(
                    "Experiment %s: %d/%d pairs complete",
                    exp_id[:8],
                    completed_pairs,
                    min_pairs,
                )
                continue

            # Determine winner
            a_wins = ab_results["a_wins"]
            b_wins = ab_results["b_wins"]

            if a_wins > b_wins:
                winner = "A"
                winner_desc = exp["variant_a_description"]
            elif b_wins > a_wins:
                winner = "B"
                winner_desc = exp["variant_b_description"]
            else:
                winner = "tie"
                winner_desc = "No significant difference"

            result_summary = (
                f"After {completed_pairs} pairs: A won {a_wins}, B won {b_wins}, "
                f"{ab_results['ties']} ties. Winner: {winner}"
            )

            # Update experiment record
            now = datetime.now(timezone.utc).isoformat()
            self.db.conn.execute(
                """
                UPDATE ab_experiments
                SET status = 'concluded', winner = ?, pair_count = ?,
                    result_summary = ?, concluded_at = ?
                WHERE id = ?
                """,
                (winner, completed_pairs, result_summary, now, exp_id),
            )
            self.db.conn.commit()

            # Create a lesson memory from the result
            self.memory.remember(
                AgentMemory(
                    agent_name="creator",
                    client_id=client_id,
                    memory_type=MemoryType.LESSON,
                    category=MemoryCategory.CONTENT_PATTERN,
                    content={
                        "summary": f"A/B test concluded: {exp['hypothesis']} - winner is {winner} ({winner_desc})",
                        "details": {
                            "experiment_id": exp_id,
                            "variable": exp["variable"],
                            "hypothesis": exp["hypothesis"],
                            "winner": winner,
                            "winner_description": winner_desc,
                            "a_wins": a_wins,
                            "b_wins": b_wins,
                            "total_pairs": completed_pairs,
                        },
                    },
                    confidence=min(0.85, 0.5 + completed_pairs * 0.05),
                    source_run_id=run_id,
                )
            )

            summary = {
                "experiment_id": exp_id,
                "hypothesis": exp["hypothesis"],
                "winner": winner,
                "winner_description": winner_desc,
                "completed_pairs": completed_pairs,
                "result_summary": result_summary,
            }
            concluded.append(summary)

            logger.info(
                "Experiment %s concluded: %s (winner=%s, %d pairs)",
                exp_id[:8],
                exp["hypothesis"],
                winner,
                completed_pairs,
            )

        return concluded
