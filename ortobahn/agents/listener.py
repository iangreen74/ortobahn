"""Social Listener Agent - scans platforms for relevant conversations."""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from ortobahn.agents.base import BaseAgent

logger = logging.getLogger("ortobahn.listener")

# Staleness thresholds per platform
_EXPIRY_HOURS = {
    "bluesky": 48,
    "twitter": 48,
    "reddit": 168,  # 7 days
}


@dataclass
class ListenerResult:
    rules_scanned: int = 0
    conversations_discovered: int = 0
    conversations_evaluated: int = 0
    conversations_queued: int = 0
    conversations_expired: int = 0
    errors: list[str] = field(default_factory=list)


class ListenerAgent(BaseAgent):
    name = "listener"
    prompt_file = "listener.txt"
    thinking_budget = 4_000

    def __init__(
        self,
        db: Any,
        api_key: str,
        model: str = "claude-sonnet-4-5-20250929",
        max_tokens: int = 4096,
        bluesky_client: Any = None,
        twitter_client: Any = None,
        reddit_client: Any = None,
        relevance_threshold: float = 0.6,
        max_conversations: int = 50,
        **kwargs: Any,
    ):
        super().__init__(db, api_key, model, max_tokens, **kwargs)
        self.bluesky = bluesky_client
        self.twitter = twitter_client
        self.reddit = reddit_client
        self.relevance_threshold = relevance_threshold
        self.max_conversations = max_conversations

    def run(
        self,
        run_id: str,
        client_id: str = "default",
        **kwargs: Any,
    ) -> ListenerResult:
        result = ListenerResult()

        # 1. Load active listening rules for this client, respecting cooldowns
        rules = self._get_active_rules(client_id)
        if not rules:
            logger.info("[listener] No active listening rules for client %s", client_id)
            return result

        # 2. Scan platforms per rule
        discovered = 0
        for rule in rules:
            try:
                posts = self._scan_for_rule(rule, client_id)
                stored = self._store_conversations(posts, rule, client_id)
                discovered += stored
                self._update_rule_scanned(rule["id"])
                result.rules_scanned += 1
            except Exception as e:
                msg = f"Rule {rule['id'][:8]} ({rule['platform']}/{rule['value']}): {e}"
                logger.warning("[listener] Scan error: %s", msg)
                result.errors.append(msg)

            if discovered >= self.max_conversations:
                break

        result.conversations_discovered = discovered

        # 3. Batch-evaluate relevance of 'new' conversations via LLM
        client_data = self.db.get_client(client_id)
        if discovered > 0 and client_data:
            evaluated, queued = self._evaluate_conversations(client_id, client_data)
            result.conversations_evaluated = evaluated
            result.conversations_queued = queued

        # 4. Expire stale conversations
        result.conversations_expired = self._expire_stale(client_id)

        self.log_decision(
            run_id=run_id,
            input_summary=f"{result.rules_scanned} rules scanned for client {client_id}",
            output_summary=(
                f"Discovered {result.conversations_discovered}, "
                f"evaluated {result.conversations_evaluated}, "
                f"queued {result.conversations_queued}, "
                f"expired {result.conversations_expired}"
            ),
        )
        return result

    # ------------------------------------------------------------------
    # Rule loading
    # ------------------------------------------------------------------

    def _get_active_rules(self, client_id: str) -> list[dict]:
        """Load active listening rules that are past their cooldown."""
        rows = self.db.fetchall(
            "SELECT * FROM listening_rules WHERE client_id=? AND active=1 ORDER BY priority",
            (client_id,),
        )
        now = datetime.now(timezone.utc)
        active = []
        for r in rows:
            last_scanned = r.get("last_scanned_at")
            cooldown = r.get("cooldown_minutes", 60)
            if last_scanned:
                from ortobahn.db.core import to_datetime

                last_dt = to_datetime(last_scanned)
                if last_dt and (now - last_dt) < timedelta(minutes=cooldown):
                    continue
            active.append(r)
        return active

    def _update_rule_scanned(self, rule_id: str) -> None:
        """Update last_scanned_at for a rule."""
        now = datetime.now(timezone.utc).isoformat()
        self.db.execute(
            "UPDATE listening_rules SET last_scanned_at=? WHERE id=?",
            (now, rule_id),
            commit=True,
        )

    # ------------------------------------------------------------------
    # Platform scanning
    # ------------------------------------------------------------------

    def _scan_for_rule(self, rule: dict, client_id: str) -> list[dict]:
        """Execute a search based on the rule's platform and type."""
        platform = rule["platform"]
        rule_type = rule["rule_type"]
        value = rule["value"]
        limit = rule.get("max_results_per_scan", 20)

        if platform == "bluesky" and self.bluesky:
            return self._scan_bluesky(value, rule_type, limit)
        elif platform == "reddit" and self.reddit:
            return self._scan_reddit(value, rule_type, limit)
        elif platform == "twitter" and self.twitter:
            return self._scan_twitter(value, rule_type, limit)
        else:
            return []

    def _scan_bluesky(self, value: str, rule_type: str, limit: int) -> list[dict]:
        """Search Bluesky for posts matching the rule."""
        query = value
        if rule_type == "hashtag" and not value.startswith("#"):
            query = f"#{value}"
        results = self.bluesky.search_posts(query=query, limit=limit)
        posts = []
        for r in results:
            posts.append(
                {
                    "platform": "bluesky",
                    "external_id": r["uri"],
                    "external_uri": r["uri"],
                    "author_handle": r["author_handle"],
                    "author_display_name": r.get("author_display_name", ""),
                    "text_content": r["text"],
                    "parent_external_id": r.get("parent_uri"),
                    "engagement_score": r.get("like_count", 0) + r.get("repost_count", 0) + r.get("reply_count", 0),
                    "metadata_json": json.dumps({"indexed_at": r.get("indexed_at", ""), "cid": r.get("cid", "")}),
                }
            )
        return posts

    def _scan_reddit(self, value: str, rule_type: str, limit: int) -> list[dict]:
        """Search Reddit based on rule type."""
        if rule_type == "subreddit":
            results = self.reddit.search_subreddit(subreddit=value, sort="hot", limit=limit)
        elif rule_type == "keyword":
            # Search across common subreddits
            results = self.reddit.search_subreddit(subreddit="all", query=value, sort="relevance", limit=limit)
        else:
            return []

        posts = []
        for r in results:
            posts.append(
                {
                    "platform": "reddit",
                    "external_id": r["post_id"],
                    "external_uri": r["url"],
                    "author_handle": r["author"],
                    "author_display_name": "",
                    "text_content": f"{r['title']}\n{r['text']}"[:2000],
                    "parent_external_id": None,
                    "engagement_score": r.get("score", 0) + r.get("num_comments", 0),
                    "metadata_json": json.dumps(
                        {
                            "subreddit": r.get("subreddit", ""),
                            "title": r.get("title", ""),
                            "created_utc": r.get("created_utc", 0),
                        }
                    ),
                }
            )
        return posts

    def _scan_twitter(self, value: str, rule_type: str, limit: int) -> list[dict]:
        """Search Twitter for recent tweets."""
        query = value
        if rule_type == "hashtag" and not value.startswith("#"):
            query = f"#{value}"
        results = self.twitter.search_recent(query=query, max_results=min(limit, 25))
        posts = []
        for r in results:
            posts.append(
                {
                    "platform": "twitter",
                    "external_id": r["tweet_id"],
                    "external_uri": r["url"],
                    "author_handle": r.get("author_handle", ""),
                    "author_display_name": r.get("author_display_name", ""),
                    "text_content": r["text"],
                    "parent_external_id": r.get("conversation_id"),
                    "engagement_score": r.get("like_count", 0) + r.get("retweet_count", 0) + r.get("reply_count", 0),
                    "metadata_json": json.dumps({"created_at": r.get("created_at", "")}),
                }
            )
        return posts

    # ------------------------------------------------------------------
    # Storage + dedup
    # ------------------------------------------------------------------

    def _store_conversations(self, posts: list[dict], rule: dict, client_id: str) -> int:
        """Store discovered conversations, deduplicating by UNIQUE index."""
        stored = 0
        for p in posts:
            conv_id = str(uuid.uuid4())
            try:
                self.db.execute(
                    """INSERT INTO discovered_conversations
                    (id, client_id, platform, source_type, source_query,
                     external_id, external_uri, author_handle, author_display_name,
                     text_content, parent_external_id, engagement_score,
                     status, metadata_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', ?)""",
                    (
                        conv_id,
                        client_id,
                        p["platform"],
                        rule["rule_type"],
                        rule["value"],
                        p["external_id"],
                        p["external_uri"],
                        p["author_handle"],
                        p.get("author_display_name", ""),
                        p["text_content"],
                        p.get("parent_external_id"),
                        p.get("engagement_score", 0),
                        p.get("metadata_json", "{}"),
                    ),
                    commit=True,
                )
                stored += 1
            except Exception:
                # UNIQUE constraint violation = already discovered, skip
                pass
        return stored

    # ------------------------------------------------------------------
    # Relevance evaluation
    # ------------------------------------------------------------------

    def _evaluate_conversations(self, client_id: str, client_data: dict) -> tuple[int, int]:
        """Batch-evaluate 'new' conversations for relevance via LLM."""
        rows = self.db.fetchall(
            "SELECT * FROM discovered_conversations "
            "WHERE client_id=? AND status='new' "
            "ORDER BY engagement_score DESC LIMIT 50",
            (client_id,),
        )
        if not rows:
            return 0, 0

        # Build prompt context
        system_prompt = self.format_prompt(
            client_name=client_data.get("name", "Unknown"),
            client_industry=client_data.get("industry", "General"),
            client_target_audience=client_data.get("target_audience", "General audience"),
            client_brand_voice=client_data.get("brand_voice", "Professional"),
            client_content_pillars=client_data.get("content_pillars", "Not specified"),
        )

        evaluated = 0
        queued = 0

        # Process in batches of 10
        for i in range(0, len(rows), 10):
            batch = rows[i : i + 10]
            parts = ["## Conversations to Evaluate\n"]
            for j, conv in enumerate(batch):
                parts.append(f"### [{j}] {conv['platform']} | @{conv['author_handle']}")
                parts.append(f"Engagement: {conv['engagement_score']}")
                parts.append(f"Text: {conv['text_content'][:500]}")
                parts.append("")

            user_message = "\n".join(parts)
            try:
                response = self.call_llm(user_message, system_prompt=system_prompt)
                evaluations = self._parse_evaluations(response.text)

                for ev in evaluations:
                    idx = ev.get("index", -1)
                    if 0 <= idx < len(batch):
                        conv = batch[idx]
                        score = max(0.0, min(1.0, ev.get("relevance_score", 0.0)))
                        new_status = "queued" if score >= self.relevance_threshold else "evaluated"
                        now = datetime.now(timezone.utc).isoformat()

                        self.db.execute(
                            "UPDATE discovered_conversations "
                            "SET relevance_score=?, status=?, evaluated_at=? "
                            "WHERE id=?",
                            (score, new_status, now, conv["id"]),
                            commit=True,
                        )
                        evaluated += 1
                        if new_status == "queued":
                            queued += 1

            except Exception as e:
                logger.warning("[listener] LLM evaluation batch failed: %s", e)

        return evaluated, queued

    def _parse_evaluations(self, text: str) -> list[dict]:
        """Parse LLM response into evaluation dicts."""
        cleaned = text.strip().strip("`").removeprefix("json").strip()
        try:
            data = json.loads(cleaned)
            return data.get("evaluations", [])
        except (json.JSONDecodeError, AttributeError):
            return []

    # ------------------------------------------------------------------
    # Expiry
    # ------------------------------------------------------------------

    def _expire_stale(self, client_id: str) -> int:
        """Mark old conversations as expired."""
        expired = 0
        now = datetime.now(timezone.utc)
        for platform, hours in _EXPIRY_HOURS.items():
            cutoff = (now - timedelta(hours=hours)).isoformat()
            self.db.execute(
                "UPDATE discovered_conversations "
                "SET status='expired' "
                "WHERE client_id=? AND platform=? AND status IN ('new', 'evaluated') "
                "AND discovered_at < ?",
                (client_id, platform, cutoff),
                commit=True,
            )
            # Count via separate query since execute may not return rowcount
            count = self.db.fetchone(
                "SELECT COUNT(*) as cnt FROM discovered_conversations "
                "WHERE client_id=? AND platform=? AND status='expired' "
                "AND discovered_at < ?",
                (client_id, platform, cutoff),
            )
            if count:
                expired += count.get("cnt", 0)
        return expired
