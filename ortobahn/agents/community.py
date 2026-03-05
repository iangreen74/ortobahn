"""Community Intelligence Agent — tracks accounts, threads conversations, analyzes competitors."""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from ortobahn.agents.base import BaseAgent

logger = logging.getLogger("ortobahn.community")

# Minimum appearances to auto-discover an account
_AUTO_DISCOVER_THRESHOLD = 3

# Maximum tracked accounts per client
_MAX_TRACKED_ACCOUNTS = 100


@dataclass
class CommunityResult:
    accounts_updated: int = 0
    accounts_discovered: int = 0
    threads_created: int = 0
    threads_updated: int = 0
    insights_published: int = 0
    errors: list[str] = field(default_factory=list)


class CommunityAgent(BaseAgent):
    name = "community"
    prompt_file = "community.txt"
    thinking_budget = 6_000

    def __init__(
        self,
        db: Any,
        api_key: str,
        model: str = "claude-sonnet-4-5-20250929",
        max_tokens: int = 4096,
        bluesky_client: Any = None,
        twitter_client: Any = None,
        reddit_client: Any = None,
        linkedin_client: Any = None,
        **kwargs: Any,
    ):
        super().__init__(db, api_key, model, max_tokens, **kwargs)
        self.bluesky = bluesky_client
        self.twitter = twitter_client
        self.reddit = reddit_client
        self.linkedin = linkedin_client

    def run(self, run_id: str, client_id: str = "default", **kwargs: Any) -> CommunityResult:
        """Run community intelligence cycle."""
        result = CommunityResult()

        try:
            # 1. Auto-discover accounts from discovered conversations
            result.accounts_discovered = self._auto_discover_accounts(client_id)

            # 2. Update tracked account activity
            result.accounts_updated = self._update_account_activity(client_id)

            # 3. Thread conversations (group related ones)
            created, updated = self._thread_conversations(client_id)
            result.threads_created = created
            result.threads_updated = updated

            # 4. Analyze community and publish insights
            result.insights_published = self._analyze_and_publish(run_id, client_id)

        except Exception as e:
            logger.error("[community] Error in run: %s", e)
            result.errors.append(str(e))

        self.log_decision(
            run_id,
            f"Community intelligence for {client_id}",
            (
                f"accounts_discovered={result.accounts_discovered}, "
                f"accounts_updated={result.accounts_updated}, "
                f"threads={result.threads_created}+{result.threads_updated}, "
                f"insights={result.insights_published}"
            ),
            "Community intelligence cycle",
        )
        return result

    # ------------------------------------------------------------------
    # Auto-discover accounts
    # ------------------------------------------------------------------
    def _auto_discover_accounts(self, client_id: str) -> int:
        """Auto-discover accounts that appear frequently in discovered conversations."""
        # Find authors who appear >= threshold times but aren't tracked yet
        rows = self.db.fetchall(
            """SELECT author_handle, platform, COUNT(*) as cnt
               FROM discovered_conversations
               WHERE client_id = ? AND author_handle != ''
               GROUP BY author_handle, platform
               HAVING COUNT(*) >= ?""",
            (client_id, _AUTO_DISCOVER_THRESHOLD),
        )
        if not rows:
            return 0

        # Get existing tracked handles
        existing = self.db.fetchall(
            "SELECT account_handle, platform FROM tracked_accounts WHERE client_id = ?",
            (client_id,),
        )
        existing_set = {(r["account_handle"], r["platform"]) for r in existing}

        # Check limit
        if len(existing_set) >= _MAX_TRACKED_ACCOUNTS:
            return 0

        discovered = 0
        for row in rows:
            handle = row["author_handle"]
            platform = row["platform"]
            if (handle, platform) in existing_set:
                continue
            if len(existing_set) + discovered >= _MAX_TRACKED_ACCOUNTS:
                break

            self.db.execute(
                """INSERT INTO tracked_accounts
                   (id, client_id, platform, account_handle, account_type,
                    relevance_score, auto_discovered, created_at)
                   VALUES (?, ?, ?, ?, 'prospect', ?, 1, ?)""",
                (
                    str(uuid.uuid4()),
                    client_id,
                    platform,
                    handle,
                    min(1.0, row["cnt"] / 10.0),
                    datetime.now(timezone.utc).isoformat(),
                ),
                commit=True,
            )
            discovered += 1
            logger.info("[community] Auto-discovered account: %s on %s", handle, platform)

        return discovered

    # ------------------------------------------------------------------
    # Update account activity
    # ------------------------------------------------------------------
    def _update_account_activity(self, client_id: str) -> int:
        """Update activity snapshots for tracked accounts."""
        accounts = self.db.fetchall(
            "SELECT * FROM tracked_accounts WHERE client_id = ? AND active = 1",
            (client_id,),
        )
        if not accounts:
            return 0

        updated = 0
        cutoff_7d = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()

        for account in accounts:
            try:
                # Count recent conversations from this author
                row = self.db.fetchone(
                    """SELECT COUNT(*) as cnt, AVG(engagement_score) as avg_eng
                       FROM discovered_conversations
                       WHERE client_id = ? AND author_handle = ? AND platform = ?
                       AND discovered_at > ?""",
                    (client_id, account["account_handle"], account["platform"], cutoff_7d),
                )
                post_count = row["cnt"] if row else 0
                avg_engagement = row["avg_eng"] if row and row["avg_eng"] else 0.0

                # Upsert activity record
                self.db.execute(
                    """INSERT INTO account_activity
                       (id, tracked_account_id, client_id, post_count_7d,
                        avg_engagement_7d, recorded_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        str(uuid.uuid4()),
                        account["id"],
                        client_id,
                        post_count,
                        round(avg_engagement, 2),
                        datetime.now(timezone.utc).isoformat(),
                    ),
                    commit=True,
                )

                # Update last_checked_at
                self.db.execute(
                    "UPDATE tracked_accounts SET last_checked_at = ? WHERE id = ?",
                    (datetime.now(timezone.utc).isoformat(), account["id"]),
                    commit=True,
                )
                updated += 1
            except Exception as e:
                logger.warning("[community] Failed to update account %s: %s", account["account_handle"], e)

        return updated

    # ------------------------------------------------------------------
    # Thread conversations
    # ------------------------------------------------------------------
    def _thread_conversations(self, client_id: str) -> tuple[int, int]:
        """Group related conversations into threads."""
        # Find unthreaded conversations that have parent_external_id
        unthreaded = self.db.fetchall(
            """SELECT * FROM discovered_conversations
               WHERE client_id = ? AND (thread_id IS NULL OR thread_id = '')
               AND parent_external_id IS NOT NULL AND parent_external_id != ''
               ORDER BY discovered_at ASC LIMIT 100""",
            (client_id,),
        )

        created = 0
        updated = 0

        for conv in unthreaded:
            parent_id = conv["parent_external_id"]
            platform = conv["platform"]

            # Check if parent already has a thread
            parent_conv = self.db.fetchone(
                """SELECT thread_id FROM discovered_conversations
                   WHERE client_id = ? AND platform = ? AND external_id = ?""",
                (client_id, platform, parent_id),
            )

            thread_id = None
            if parent_conv and parent_conv.get("thread_id"):
                thread_id = parent_conv["thread_id"]
            else:
                # Check if parent is the root of an existing thread
                existing_thread = self.db.fetchone(
                    """SELECT id FROM conversation_threads
                       WHERE client_id = ? AND platform = ? AND root_conversation_id = ?""",
                    (client_id, platform, parent_id),
                )
                if existing_thread:
                    thread_id = existing_thread["id"]

            if thread_id:
                # Add to existing thread
                self.db.execute(
                    "UPDATE discovered_conversations SET thread_id = ? WHERE id = ?",
                    (thread_id, conv["id"]),
                    commit=True,
                )
                # Update thread stats
                self.db.execute(
                    """UPDATE conversation_threads
                       SET thread_depth = thread_depth + 1,
                           total_engagement = total_engagement + ?,
                           last_activity_at = ?
                       WHERE id = ?""",
                    (conv.get("engagement_score", 0), datetime.now(timezone.utc).isoformat(), thread_id),
                    commit=True,
                )
                updated += 1
            else:
                # Create new thread
                new_thread_id = str(uuid.uuid4())
                self.db.execute(
                    """INSERT INTO conversation_threads
                       (id, client_id, platform, root_conversation_id,
                        thread_depth, total_engagement, status, first_seen_at, last_activity_at)
                       VALUES (?, ?, ?, ?, 2, ?, 'active', ?, ?)""",
                    (
                        new_thread_id,
                        client_id,
                        platform,
                        parent_id,
                        conv.get("engagement_score", 0),
                        datetime.now(timezone.utc).isoformat(),
                        datetime.now(timezone.utc).isoformat(),
                    ),
                    commit=True,
                )
                # Link both parent and child
                self.db.execute(
                    """UPDATE discovered_conversations SET thread_id = ?
                       WHERE client_id = ? AND platform = ? AND external_id = ?""",
                    (new_thread_id, client_id, platform, parent_id),
                    commit=True,
                )
                self.db.execute(
                    "UPDATE discovered_conversations SET thread_id = ? WHERE id = ?",
                    (new_thread_id, conv["id"]),
                    commit=True,
                )
                created += 1

        return created, updated

    # ------------------------------------------------------------------
    # Analyze and publish insights
    # ------------------------------------------------------------------
    def _analyze_and_publish(self, run_id: str, client_id: str) -> int:
        """Analyze community data and publish insights to SharedInsightBus."""
        from ortobahn.shared_insights import (
            COMMUNITY_TREND,
            COMPETITOR_MOVE,
            ENGAGEMENT_PATTERN,
            SharedInsightBus,
        )

        bus = SharedInsightBus(self.db)
        insights_published = 0
        now = datetime.now(timezone.utc)
        cutoff_7d = (now - timedelta(days=7)).isoformat()

        # --- Community trends: top topics from recent conversations ---
        recent_convs = self.db.fetchall(
            """SELECT text_content, platform, engagement_score
               FROM discovered_conversations
               WHERE client_id = ? AND discovered_at > ?
               ORDER BY engagement_score DESC LIMIT 50""",
            (client_id, cutoff_7d),
        )

        if len(recent_convs) >= 5:
            # Use LLM to extract trends
            try:
                texts = [c["text_content"][:200] for c in recent_convs[:20]]
                prompt = (
                    "Analyze these recent social media posts and identify the top 3 community trends. "
                    "Return a JSON object with a 'trends' array, each item having 'topic' and 'description' fields.\n\n"
                    + "\n---\n".join(texts)
                )
                response = self.call_llm(prompt)
                trends = json.loads(response.text)
                if "trends" in trends:
                    for trend in trends["trends"][:3]:
                        bus.publish(
                            source_agent="community",
                            insight_type=COMMUNITY_TREND,
                            content=f"{trend['topic']}: {trend.get('description', '')}",
                            confidence=0.7,
                            metadata={"client_id": client_id, "run_id": run_id},
                        )
                        insights_published += 1
            except Exception as e:
                logger.warning("[community] Failed to extract trends: %s", e)

        # --- Competitor analysis: tracked competitor accounts ---
        competitors = self.db.fetchall(
            """SELECT ta.*, aa.post_count_7d, aa.avg_engagement_7d, aa.top_topics
               FROM tracked_accounts ta
               LEFT JOIN account_activity aa ON aa.tracked_account_id = ta.id
               WHERE ta.client_id = ? AND ta.account_type = 'competitor' AND ta.active = 1
               ORDER BY aa.recorded_at DESC""",
            (client_id,),
        )

        for comp in competitors:
            if comp.get("post_count_7d", 0) > 5:
                bus.publish(
                    source_agent="community",
                    insight_type=COMPETITOR_MOVE,
                    content=(
                        f"Competitor {comp['account_handle']} on {comp['platform']} "
                        f"posted {comp['post_count_7d']} times in 7d "
                        f"(avg engagement: {comp.get('avg_engagement_7d', 0):.1f})"
                    ),
                    confidence=0.6,
                    metadata={"client_id": client_id, "account_handle": comp["account_handle"]},
                )
                insights_published += 1

        # --- Engagement patterns: analyze reply effectiveness ---
        outcomes = self.db.fetchall(
            """SELECT platform, AVG(outcome_score) as avg_score, COUNT(*) as cnt
               FROM engagement_outcomes
               WHERE client_id = ? AND created_at > ?
               GROUP BY platform""",
            (client_id, cutoff_7d),
        )

        for outcome in outcomes:
            if outcome["cnt"] >= 3:
                bus.publish(
                    source_agent="community",
                    insight_type=ENGAGEMENT_PATTERN,
                    content=(
                        f"Engagement on {outcome['platform']}: "
                        f"avg score {outcome['avg_score']:.2f} from {outcome['cnt']} replies"
                    ),
                    confidence=0.65,
                    metadata={"client_id": client_id, "platform": outcome["platform"]},
                )
                insights_published += 1

        return insights_published
