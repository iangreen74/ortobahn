"""Voice learning engine — learns user content preferences from review decisions."""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone

from ortobahn.config import Settings
from ortobahn.db import Database
from ortobahn.memory import MemoryStore
from ortobahn.models import AgentMemory, MemoryCategory, MemoryType

logger = logging.getLogger("ortobahn.voice_learning")

# Heuristic thresholds
MIN_REVIEWS_FOR_PATTERN = 3
EMOJI_PATTERN = re.compile(
    r"[\U0001f600-\U0001f64f\U0001f300-\U0001f5ff\U0001f680-\U0001f6ff\U0001f1e0-\U0001f1ff\U00002702-\U000027b0\U0000fe0f]"
)
HASHTAG_PATTERN = re.compile(r"#\w+")
QUESTION_PATTERN = re.compile(r"\?")


class VoiceLearner:
    """Learns user voice preferences from content review decisions.

    Two analysis modes:
    - Heuristic (inline, zero LLM calls): runs on every review, detects obvious patterns
    - Batch LLM (periodic): deeper voice analysis from accumulated reviews
    """

    def __init__(self, db: Database, memory_store: MemoryStore):
        self.db = db
        self.memory = memory_store

    def record_review(
        self,
        client_id: str,
        content_type: str,
        content_id: str,
        action: str,
        rejection_reason: str = "",
        content_snapshot: dict | None = None,
    ) -> str:
        """Record a user review and extract voice patterns.

        Called inline on every approve/reject/edit. Returns review ID.
        """
        review_id = str(uuid.uuid4())[:8]
        self.db.execute(
            "INSERT INTO content_reviews (id, client_id, content_type, content_id, "
            "action, rejection_reason, content_snapshot, reviewed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                review_id,
                client_id,
                content_type,
                content_id,
                action,
                rejection_reason,
                json.dumps(content_snapshot or {}),
                datetime.now(timezone.utc).isoformat(),
            ),
            commit=True,
        )
        logger.info(f"Recorded review {review_id}: {action} {content_type} {content_id}")

        try:
            self._heuristic_voice_analysis(client_id)
        except Exception as e:
            logger.warning(f"Heuristic voice analysis failed (non-fatal): {e}")

        try:
            self._update_voice_confidence(client_id)
        except Exception as e:
            logger.warning(f"Voice confidence update failed (non-fatal): {e}")

        return review_id

    def get_voice_context(self, client_id: str) -> str:
        """Build formatted voice preferences block for prompt injection."""
        memories = self.memory.recall(
            agent_name="voice_learner",
            client_id=client_id,
            category=MemoryCategory.VOICE_PREFERENCE,
            limit=10,
            min_confidence=0.3,
        )
        if not memories:
            return ""

        lines = ["## User Voice Preferences (from review history)"]
        for mem in memories:
            summary = mem.content.get("summary", "")
            if not summary:
                continue
            pref_type = mem.content.get("type", "note")
            label = "PREFER" if pref_type == "prefer" else "AVOID" if pref_type == "avoid" else "NOTE"
            reinforced = f", seen {mem.times_reinforced}x" if mem.times_reinforced > 1 else ""
            lines.append(f"- {label}: {summary} [confidence: {mem.confidence:.2f}{reinforced}]")

        return "\n".join(lines) if len(lines) > 1 else ""

    def analyze_review_batch(
        self,
        client_id: str,
        settings: Settings,
        run_id: str = "",
    ) -> dict:
        """Analyze recent reviews with a single LLM call for deeper voice insights.

        Called periodically (e.g., once per pipeline cycle). Returns analysis results.
        """
        from ortobahn.llm import call_llm

        # Fetch recent reviews not yet batch-analyzed (last 10)
        reviews = self.db.fetchall(
            "SELECT * FROM content_reviews WHERE client_id=? ORDER BY reviewed_at DESC LIMIT 10",
            (client_id,),
        )
        if len(reviews) < MIN_REVIEWS_FOR_PATTERN:
            return {"status": "insufficient_reviews", "count": len(reviews)}

        # Build context
        approved = []
        rejected = []
        for r in reviews:
            snapshot = json.loads(r.get("content_snapshot") or "{}")
            text = snapshot.get("text", "")[:300]
            platform = snapshot.get("platform", "unknown")
            entry = f"[{platform}] {text}"
            if r["action"] == "approved":
                approved.append(entry)
            elif r["action"] == "rejected":
                reason = r.get("rejection_reason") or "(no reason given)"
                rejected.append(f"{entry} — Reason: {reason}")

        if not approved and not rejected:
            return {"status": "no_actionable_reviews"}

        # Get existing voice preferences for context
        existing_context = self.get_voice_context(client_id)

        approved_text = "\n".join(f"{i + 1}. {a}" for i, a in enumerate(approved)) or "(none)"
        rejected_text = "\n".join(f"{i + 1}. {r}" for i, r in enumerate(rejected)) or "(none)"

        system_prompt = (
            "You analyze a user's content preferences based on their review decisions. "
            "Extract specific, actionable voice preferences — not vague platitudes."
        )
        user_message = f"""Analyze these content review decisions:

## Approved (user liked these):
{approved_text}

## Rejected (user did NOT like these):
{rejected_text}

## Existing Voice Profile:
{existing_context or "(none yet)"}

Extract 3-5 specific voice preferences. Be precise about what the user likes and dislikes.
Return JSON:
{{
  "preferences": [
    {{"summary": "...", "type": "prefer" or "avoid", "confidence": 0.5 to 0.8}}
  ]
}}"""

        try:
            response = call_llm(
                system_prompt=system_prompt,
                user_message=user_message,
                model=settings.claude_model,
                max_tokens=1024,
                api_key=settings.anthropic_api_key,
                use_bedrock=settings.use_bedrock,
            )
            # Extract JSON from response
            resp_text = response.text.strip()
            if "```json" in resp_text:
                resp_text = resp_text.split("```json", 1)[1].split("```", 1)[0].strip()
            elif "```" in resp_text:
                resp_text = resp_text.split("```", 1)[1].split("```", 1)[0].strip()
            parsed = json.loads(resp_text)
            preferences = parsed.get("preferences", [])
        except Exception as e:
            logger.warning(f"Voice batch LLM analysis failed: {e}")
            return {"status": "llm_error", "error": str(e)}

        # Store as PREFERENCE memories
        stored = 0
        for pref in preferences:
            summary = pref.get("summary", "")
            if not summary:
                continue
            confidence = min(0.8, max(0.3, pref.get("confidence", 0.5)))
            mem = AgentMemory(
                agent_name="voice_learner",
                client_id=client_id,
                memory_type=MemoryType.PREFERENCE,
                category=MemoryCategory.VOICE_PREFERENCE,
                content={
                    "summary": summary,
                    "type": pref.get("type", "prefer"),
                    "source": "batch_analysis",
                },
                confidence=confidence,
                source_run_id=run_id,
            )
            self.memory.remember(mem)
            stored += 1

        logger.info(f"Voice batch analysis: stored {stored} preferences for {client_id}")
        return {
            "status": "ok",
            "preferences_stored": stored,
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
        }

    # --- Heuristic analysis (zero LLM calls) ---

    def _heuristic_voice_analysis(self, client_id: str) -> None:
        """Extract voice patterns from review history using heuristics only."""
        reviews = self.db.fetchall(
            "SELECT * FROM content_reviews WHERE client_id=? ORDER BY reviewed_at DESC LIMIT 20",
            (client_id,),
        )
        if len(reviews) < MIN_REVIEWS_FOR_PATTERN:
            return

        approved_texts = []
        rejected_texts = []
        for r in reviews:
            snapshot = json.loads(r.get("content_snapshot") or "{}")
            text = snapshot.get("text", "")
            if r["action"] == "approved":
                approved_texts.append(text)
            elif r["action"] == "rejected":
                rejected_texts.append(text)

        if not approved_texts and not rejected_texts:
            return

        # Length preference
        self._check_length_preference(client_id, approved_texts, rejected_texts)

        # Emoji preference
        self._check_emoji_preference(client_id, approved_texts, rejected_texts)

        # Question hook preference
        self._check_question_preference(client_id, approved_texts, rejected_texts)

        # Hashtag preference
        self._check_hashtag_preference(client_id, approved_texts, rejected_texts)

    def _check_length_preference(self, client_id: str, approved: list[str], rejected: list[str]) -> None:
        """Detect if user prefers short or long posts."""
        if not approved or not rejected:
            return
        avg_approved_len = sum(len(t) for t in approved) / len(approved)
        avg_rejected_len = sum(len(t) for t in rejected) / len(rejected)

        # Significant difference (>30%)
        if avg_approved_len > 0 and avg_rejected_len > 0:
            ratio = avg_approved_len / avg_rejected_len
            if ratio < 0.7:
                self._store_heuristic(client_id, "Shorter, more concise posts", "prefer")
            elif ratio > 1.4:
                self._store_heuristic(client_id, "Longer, more detailed posts", "prefer")

    def _check_emoji_preference(self, client_id: str, approved: list[str], rejected: list[str]) -> None:
        """Detect if user likes or dislikes emojis."""
        approved_emoji_rate = sum(1 for t in approved if EMOJI_PATTERN.search(t)) / len(approved) if approved else 0
        rejected_emoji_rate = sum(1 for t in rejected if EMOJI_PATTERN.search(t)) / len(rejected) if rejected else 0

        if rejected_emoji_rate > 0.6 and approved_emoji_rate < 0.3:
            self._store_heuristic(client_id, "Posts without emojis", "prefer")
        elif approved_emoji_rate > 0.6 and rejected_emoji_rate < 0.3:
            self._store_heuristic(client_id, "Posts that include emojis", "prefer")

    def _check_question_preference(self, client_id: str, approved: list[str], rejected: list[str]) -> None:
        """Detect if user prefers question-driven hooks."""

        def starts_with_question(text: str) -> bool:
            first_line = text.split("\n")[0] if text else ""
            return bool(QUESTION_PATTERN.search(first_line))

        approved_q_rate = sum(1 for t in approved if starts_with_question(t)) / len(approved) if approved else 0
        rejected_q_rate = sum(1 for t in rejected if starts_with_question(t)) / len(rejected) if rejected else 0

        if approved_q_rate > 0.5 and rejected_q_rate < 0.2:
            self._store_heuristic(client_id, "Opening with a question hook", "prefer")
        elif rejected_q_rate > 0.5 and approved_q_rate < 0.2:
            self._store_heuristic(client_id, "Opening with a statement rather than a question", "prefer")

    def _check_hashtag_preference(self, client_id: str, approved: list[str], rejected: list[str]) -> None:
        """Detect if user likes or dislikes hashtags."""
        approved_ht_rate = sum(1 for t in approved if HASHTAG_PATTERN.search(t)) / len(approved) if approved else 0
        rejected_ht_rate = sum(1 for t in rejected if HASHTAG_PATTERN.search(t)) / len(rejected) if rejected else 0

        if rejected_ht_rate > 0.6 and approved_ht_rate < 0.3:
            self._store_heuristic(client_id, "Posts without hashtags", "prefer")
        elif approved_ht_rate > 0.6 and rejected_ht_rate < 0.3:
            self._store_heuristic(client_id, "Posts that include hashtags", "prefer")

    def _store_heuristic(self, client_id: str, summary: str, pref_type: str) -> None:
        """Store a heuristic-derived voice preference."""
        mem = AgentMemory(
            agent_name="voice_learner",
            client_id=client_id,
            memory_type=MemoryType.PREFERENCE,
            category=MemoryCategory.VOICE_PREFERENCE,
            content={
                "summary": summary,
                "type": pref_type,
                "source": "heuristic",
            },
            confidence=0.5,
        )
        self.memory.remember(mem)

    # --- Voice confidence ---

    def _update_voice_confidence(self, client_id: str) -> None:
        """Recompute voice_confidence from recent review history."""
        row = self.db.fetchone(
            "SELECT "
            "SUM(CASE WHEN action='approved' THEN 1 ELSE 0 END) as approvals, "
            "SUM(CASE WHEN action='rejected' THEN 1 ELSE 0 END) as rejections, "
            "COUNT(*) as total "
            "FROM (SELECT action FROM content_reviews "
            "WHERE client_id=? AND action IN ('approved','rejected') "
            "ORDER BY reviewed_at DESC LIMIT 20)",
            (client_id,),
        )
        if not row or not row["total"]:
            return

        approvals = row["approvals"] or 0
        total = row["total"] or 0
        approval_rate = approvals / total if total > 0 else 0

        # Data weight: need at least 10 reviews for full confidence
        data_weight = min(1.0, total / 10)
        voice_confidence = round(approval_rate * data_weight, 3)

        self.db.execute(
            "UPDATE clients SET voice_confidence=? WHERE id=?",
            (voice_confidence, client_id),
            commit=True,
        )
