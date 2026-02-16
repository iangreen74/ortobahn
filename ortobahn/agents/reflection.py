"""Reflection Agent - analyzes past performance and creates structured memories."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from ortobahn.agents.base import BaseAgent
from ortobahn.llm import parse_json_response
from ortobahn.memory import MemoryStore
from ortobahn.models import (
    AgentMemory,
    MemoryCategory,
    MemoryType,
    ReflectionReport,
)

logger = logging.getLogger("ortobahn.agents.reflection")


class ReflectionAgent(BaseAgent):
    name = "reflection"
    prompt_file = "reflection.txt"
    thinking_budget = 8_000

    def run(self, run_id: str, client_id: str = "default", **kwargs: Any) -> ReflectionReport:
        # --- Step 1: Gather data (no LLM) ---
        posts = self.db.get_recent_posts_with_metrics(limit=50, client_id=client_id)

        if not posts:
            self.log_decision(
                run_id=run_id,
                input_summary="No published posts to reflect on",
                output_summary="Empty reflection report (no data)",
            )
            return ReflectionReport(summary="No published posts to analyze yet.")

        memory_store = MemoryStore(self.db)
        existing_memories = memory_store.recall(self.name, client_id, limit=15)
        memory_context = self.get_memory_context(client_id)

        active_experiments = self._get_active_experiments(client_id)
        goals = self._get_goals(client_id)
        strategy = self.db.get_active_strategy(client_id)

        # --- Step 2: Compute confidence calibration (pure math) ---
        calibration = self._compute_calibration(posts, run_id, client_id)

        # --- Step 3: One LLM call with analysis ---
        user_message = self._build_llm_input(
            posts=posts,
            calibration=calibration,
            memory_context=memory_context,
            active_experiments=active_experiments,
            goals=goals,
            strategy=strategy,
        )

        response = self.call_llm(user_message)
        report = parse_json_response(response.text, ReflectionReport)

        # Overlay calibration data computed in step 2
        report.confidence_accuracy = calibration["mean_absolute_error"]
        report.confidence_bias = calibration["bias"]

        # --- Step 4: Store new memories (no LLM) ---
        stored_memories = []
        for mem in report.new_memories:
            mem.agent_name = self.name
            mem.client_id = client_id
            mem.source_run_id = run_id
            try:
                memory_store.remember(mem)
                stored_memories.append(mem)
            except Exception as e:
                logger.warning(f"Failed to store memory: {e}")

        # Store calibration as a memory too
        if calibration["sample_size"] >= 3:
            cal_memory = AgentMemory(
                agent_name=self.name,
                client_id=client_id,
                memory_type=MemoryType.OBSERVATION,
                category=MemoryCategory.CALIBRATION,
                content={
                    "summary": f"Creator confidence is {calibration['bias']} (MAE: {calibration['mean_absolute_error']:.3f}, n={calibration['sample_size']})",
                    "mean_absolute_error": calibration["mean_absolute_error"],
                    "bias": calibration["bias"],
                    "sample_size": calibration["sample_size"],
                },
                confidence=min(0.9, 0.4 + calibration["sample_size"] * 0.05),
                source_run_id=run_id,
            )
            try:
                memory_store.remember(cal_memory)
            except Exception as e:
                logger.warning(f"Failed to store calibration memory: {e}")

        memory_store.enforce_limits(self.name, client_id)

        # --- Step 5: Update goal tracking (no LLM) ---
        goal_progress = self._update_goals(posts, goals, client_id)
        report.goal_progress = goal_progress

        # --- Step 6: Evaluate mature A/B experiments (no LLM) ---
        ab_updates = self._evaluate_experiments(active_experiments, posts, client_id)
        report.ab_test_updates = ab_updates

        self.log_decision(
            run_id=run_id,
            input_summary=f"{len(posts)} posts, {len(existing_memories)} memories, {len(active_experiments)} experiments",
            output_summary=f"Calibration: {calibration['bias']} (MAE {calibration['mean_absolute_error']:.3f}), {len(stored_memories)} new memories, {len(report.recommendations)} recommendations",
            reasoning=report.summary[:200] if report.summary else "",
            llm_response=response,
        )

        return report

    # --- Confidence calibration (pure math) ---

    def _compute_calibration(self, posts: list[dict], run_id: str, client_id: str) -> dict:
        """Compare Creator's predicted confidence vs actual engagement percentiles."""
        # Filter to posts that have both confidence and engagement data
        scored_posts = []
        for p in posts:
            confidence = p.get("confidence")
            if confidence is None or confidence == 0:
                continue
            engagement = (p.get("like_count") or 0) + (p.get("repost_count") or 0) + (p.get("reply_count") or 0)
            scored_posts.append(
                {
                    "id": p.get("id", ""),
                    "confidence": confidence,
                    "engagement": engagement,
                }
            )

        if len(scored_posts) < 2:
            return {
                "mean_absolute_error": 0.0,
                "bias": "neutral",
                "sample_size": len(scored_posts),
                "details": [],
            }

        # Sort by actual engagement and assign percentiles
        scored_posts.sort(key=lambda x: x["engagement"])
        n = len(scored_posts)
        for i, sp in enumerate(scored_posts):
            sp["actual_percentile"] = (i + 0.5) / n  # midpoint percentile

        # Compute calibration error per post
        errors = []
        details = []
        for sp in scored_posts:
            error = sp["confidence"] - sp["actual_percentile"]
            errors.append(error)
            details.append(
                {
                    "post_id": sp["id"],
                    "predicted": round(sp["confidence"], 3),
                    "actual_percentile": round(sp["actual_percentile"], 3),
                    "error": round(error, 3),
                }
            )

            # Store to calibration table
            try:
                self.db.conn.execute(
                    """INSERT OR REPLACE INTO confidence_calibration
                    (id, post_id, client_id, predicted_confidence, actual_engagement,
                     engagement_percentile, calibration_error, run_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        str(uuid.uuid4())[:8],
                        sp["id"],
                        client_id,
                        sp["confidence"],
                        sp["engagement"],
                        sp["actual_percentile"],
                        error,
                        run_id,
                    ),
                )
            except Exception as e:
                logger.debug(f"Failed to store calibration row: {e}")

        try:
            self.db.conn.commit()
        except Exception:
            pass

        mae = sum(abs(e) for e in errors) / len(errors)
        mean_error = sum(errors) / len(errors)

        # Determine bias direction
        if mean_error > 0.1:
            bias = "overconfident"
        elif mean_error < -0.1:
            bias = "underconfident"
        else:
            bias = "neutral"

        return {
            "mean_absolute_error": round(mae, 4),
            "bias": bias,
            "sample_size": n,
            "details": details,
        }

    # --- LLM input builder ---

    def _build_llm_input(
        self,
        posts: list[dict],
        calibration: dict,
        memory_context: str,
        active_experiments: list[dict],
        goals: list[dict],
        strategy: dict | None,
    ) -> str:
        parts = []

        # Recent posts with metrics
        parts.append("## Recent Posts with Metrics")
        for p in posts[:30]:  # Cap to avoid token overflow
            engagement = (p.get("like_count") or 0) + (p.get("repost_count") or 0) + (p.get("reply_count") or 0)
            parts.append(
                f"- [{p.get('platform', 'generic')}] confidence={p.get('confidence', 0):.2f} "
                f"engagement={engagement} (L:{p.get('like_count', 0)} R:{p.get('repost_count', 0)} C:{p.get('reply_count', 0)}) "
                f'| "{p.get("text", "")[:120]}"'
            )

        # Calibration summary
        parts.append("\n## Confidence Calibration")
        parts.append(f"Sample size: {calibration['sample_size']}")
        parts.append(f"Mean absolute error: {calibration['mean_absolute_error']:.4f}")
        parts.append(f"Bias: {calibration['bias']}")
        if calibration["details"]:
            parts.append("Details (predicted vs actual percentile):")
            for d in calibration["details"][:15]:
                parts.append(
                    f"  - post {d['post_id'][:8]}: predicted={d['predicted']}, actual={d['actual_percentile']}, error={d['error']}"
                )

        # Strategy context
        if strategy:
            parts.append("\n## Active Strategy")
            parts.append(f"Themes: {json.dumps(strategy.get('themes', []))}")
            parts.append(f"Tone: {strategy.get('tone', 'N/A')}")
            parts.append(f"Goals: {json.dumps(strategy.get('goals', []))}")

        # Existing memories
        if memory_context:
            parts.append(f"\n{memory_context}")

        # Active experiments
        if active_experiments:
            parts.append("\n## Active A/B Experiments")
            for exp in active_experiments:
                parts.append(
                    f"- [{exp.get('id', '')[:8]}] {exp.get('variable', 'unknown')}: "
                    f'A="{exp.get("variant_a_description", "")}" vs B="{exp.get("variant_b_description", "")}" '
                    f"(pairs: {exp.get('pair_count', 0)}/{exp.get('min_pairs_required', 5)})"
                )

        # Goals
        if goals:
            parts.append("\n## Current Goals")
            for g in goals:
                parts.append(
                    f"- {g.get('metric_name', 'unknown')}: {g.get('current_value', 0):.1f}/{g.get('target_value', 0):.1f} "
                    f"(trend: {g.get('trend', 'stable')})"
                )

        return "\n".join(parts)

    # --- Data helpers (no LLM) ---

    def _get_active_experiments(self, client_id: str) -> list[dict]:
        """Retrieve active A/B experiments."""
        try:
            rows = self.db.conn.execute(
                "SELECT * FROM ab_experiments WHERE client_id = ? AND status = 'active'",
                (client_id,),
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []

    def _get_goals(self, client_id: str) -> list[dict]:
        """Retrieve agent goals."""
        try:
            rows = self.db.conn.execute(
                "SELECT * FROM agent_goals WHERE client_id = ?",
                (client_id,),
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []

    def _update_goals(self, posts: list[dict], goals: list[dict], client_id: str) -> list[dict]:
        """Update goal progress based on current post data. Returns progress list."""
        progress = []
        if not goals:
            return progress

        for goal in goals:
            metric = goal.get("metric_name", "")
            target = goal.get("target_value", 0)
            old_value = goal.get("current_value", 0)
            window_days = goal.get("measurement_window_days", 7)

            # Compute current value based on metric type
            current = self._compute_goal_metric(metric, posts, window_days)
            if current is None:
                continue

            # Determine trend
            if current > old_value * 1.05:
                trend = "rising"
            elif current < old_value * 0.95:
                trend = "falling"
            else:
                trend = "stable"

            # Update in DB
            try:
                self.db.conn.execute(
                    "UPDATE agent_goals SET current_value = ?, trend = ?, last_measured_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (current, trend, goal["id"]),
                )
                self.db.conn.commit()
            except Exception as e:
                logger.warning(f"Failed to update goal {goal.get('id')}: {e}")

            progress.append(
                {
                    "metric": metric,
                    "target": target,
                    "current": round(current, 2),
                    "previous": round(old_value, 2),
                    "trend": trend,
                    "on_track": current >= target * 0.8,
                }
            )

        return progress

    def _compute_goal_metric(self, metric: str, posts: list[dict], window_days: int) -> float | None:
        """Compute a goal metric value from post data."""
        if not posts:
            return None

        if metric == "avg_engagement":
            engagements = [
                (p.get("like_count") or 0) + (p.get("repost_count") or 0) + (p.get("reply_count") or 0) for p in posts
            ]
            return sum(engagements) / len(engagements) if engagements else 0.0

        if metric == "total_engagement":
            return float(
                sum(
                    (p.get("like_count") or 0) + (p.get("repost_count") or 0) + (p.get("reply_count") or 0)
                    for p in posts
                )
            )

        if metric == "posts_per_day":
            return len(posts) / max(window_days, 1)

        if metric == "avg_confidence":
            confs = [p.get("confidence", 0) for p in posts if p.get("confidence")]
            return sum(confs) / len(confs) if confs else 0.0

        if metric == "like_count":
            return float(sum(p.get("like_count") or 0 for p in posts))

        return None

    def _evaluate_experiments(self, experiments: list[dict], posts: list[dict], client_id: str) -> list[dict]:
        """Evaluate mature A/B experiments using engagement data. Returns updates."""
        updates = []

        for exp in experiments:
            exp_id = exp.get("id", "")
            min_pairs = exp.get("min_pairs_required", 5)

            # Find posts belonging to this experiment
            a_posts = [p for p in posts if p.get("ab_pair_id") and p.get("ab_group") == "A"]
            b_posts = [p for p in posts if p.get("ab_pair_id") and p.get("ab_group") == "B"]

            # Match pairs by ab_pair_id
            pair_ids = set(p.get("ab_pair_id") for p in a_posts) & set(p.get("ab_pair_id") for p in b_posts)
            pair_count = len(pair_ids)

            # Update pair count
            try:
                self.db.conn.execute(
                    "UPDATE ab_experiments SET pair_count = ? WHERE id = ?",
                    (pair_count, exp_id),
                )
                self.db.conn.commit()
            except Exception:
                pass

            if pair_count < min_pairs:
                updates.append(
                    {
                        "experiment_id": exp_id,
                        "variable": exp.get("variable", ""),
                        "status": "collecting",
                        "pairs_completed": pair_count,
                        "pairs_needed": min_pairs,
                    }
                )
                continue

            # Compute engagement for each group
            def _avg_engagement(post_list: list[dict], matched_pairs: set) -> float:
                eng = [
                    (p.get("like_count") or 0) + (p.get("repost_count") or 0) + (p.get("reply_count") or 0)
                    for p in post_list
                    if p.get("ab_pair_id") in matched_pairs
                ]
                return sum(eng) / len(eng) if eng else 0.0

            avg_a = _avg_engagement(a_posts, pair_ids)
            avg_b = _avg_engagement(b_posts, pair_ids)

            # Determine winner (need at least 20% difference to declare)
            if avg_a > avg_b * 1.2:
                winner = "A"
            elif avg_b > avg_a * 1.2:
                winner = "B"
            else:
                winner = "inconclusive"

            result_summary = f"A avg={avg_a:.1f}, B avg={avg_b:.1f}, winner={winner}"

            # Conclude experiment
            try:
                self.db.conn.execute(
                    """UPDATE ab_experiments SET status = 'concluded', winner = ?,
                       result_summary = ?, concluded_at = CURRENT_TIMESTAMP WHERE id = ?""",
                    (winner if winner != "inconclusive" else None, result_summary, exp_id),
                )
                self.db.conn.commit()
            except Exception as e:
                logger.warning(f"Failed to conclude experiment {exp_id}: {e}")

            updates.append(
                {
                    "experiment_id": exp_id,
                    "variable": exp.get("variable", ""),
                    "status": "concluded",
                    "winner": winner,
                    "avg_engagement_a": round(avg_a, 2),
                    "avg_engagement_b": round(avg_b, 2),
                    "result": result_summary,
                }
            )

        return updates
