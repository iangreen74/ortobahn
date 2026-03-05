"""Engagement Agent — autonomous replies and conversation participation."""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from ortobahn.agents.base import BaseAgent
from ortobahn.circuit_breaker import CircuitOpenError, get_breaker
from ortobahn.llm import parse_json_response
from ortobahn.publish_recovery import ErrorCategory, PublishErrorClassifier

logger = logging.getLogger("ortobahn.agents")

# Platform-specific character limits
_PLATFORM_CHAR_LIMITS = {
    "bluesky": 300,
    "twitter": 280,
    "reddit": 10_000,
    "linkedin": 1_250,
}

# Default rate limits (per-client per-platform)
_DEFAULT_RATE_LIMITS = {
    "hourly": 3,
    "daily": 10,
}


@dataclass
class EngagementReply:
    """A single reply to draft/post."""

    notification_uri: str
    notification_text: str
    reply_text: str
    confidence: float
    reasoning: str


@dataclass
class EngagementResult:
    """Result of engagement agent run."""

    notifications_checked: int = 0
    replies_drafted: int = 0
    replies_posted: int = 0
    proactive_evaluated: int = 0
    proactive_posted: int = 0
    replies: list[EngagementReply] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class EngagementAgent(BaseAgent):
    name = "engagement"
    prompt_file = "engagement.txt"
    thinking_budget = 4_000

    def __init__(
        self,
        db,
        api_key,
        model="claude-sonnet-4-5-20250929",
        bluesky_client=None,
        twitter_client=None,
        linkedin_client=None,
        reddit_client=None,
        max_replies_per_cycle: int = 3,
        reply_confidence_threshold: float = 0.75,
        use_bedrock: bool = False,
        bedrock_region: str = "us-west-2",
    ):
        super().__init__(db, api_key, model, use_bedrock=use_bedrock, bedrock_region=bedrock_region)
        self.bluesky = bluesky_client
        self.twitter = twitter_client
        self.linkedin = linkedin_client
        self.reddit = reddit_client
        self.max_replies_per_cycle = max_replies_per_cycle
        self.reply_confidence_threshold = reply_confidence_threshold

    def _post_reply_with_retry(self, reply: EngagementReply, max_retries: int = 2) -> str | None:
        """Post a reply with retry logic for transient errors."""
        breaker = get_breaker("bluesky:engagement", failure_threshold=3, reset_timeout_seconds=300)

        for attempt in range(max_retries + 1):
            state = breaker.state
            if state.value == "open":
                raise CircuitOpenError(breaker.name, breaker._last_failure_time + breaker.reset_timeout)
            try:
                result = self._post_reply(reply)
                breaker.record_success()
                return result
            except CircuitOpenError:
                raise
            except Exception as e:
                category = PublishErrorClassifier.classify_error(e, "bluesky")
                if category == ErrorCategory.TRANSIENT and attempt < max_retries:
                    delay = 2**attempt
                    logger.info("[engagement] Transient error, retrying in %ds: %s", delay, e)
                    time.sleep(delay)
                    continue
                elif category == ErrorCategory.AUTH:
                    logger.warning("[engagement] Auth error, attempting re-login: %s", e)
                    try:
                        self.bluesky.login(force=True)
                        result = self._post_reply(reply)
                        breaker.record_success()
                        return result
                    except Exception:
                        breaker.record_failure()
                        raise
                else:
                    breaker.record_failure()
                    raise
        return None

    def run(self, run_id: str, client_id: str = "default", dry_run: bool = False, **kwargs) -> EngagementResult:
        """Check notifications and reply to relevant mentions.

        Respects the client's engagement_mode:
        - 'auto': post replies immediately if above confidence threshold
        - 'draft': save replies for manual review
        - 'off': skip engagement entirely
        """
        result = EngagementResult()

        # Check client engagement mode
        client_data = self.db.get_client(client_id)
        engagement_mode = (client_data.get("engagement_mode") or "auto") if client_data else "auto"
        if engagement_mode == "off":
            logger.info("[engagement] Engagement disabled for %s", client_id)
            return result

        # Phase A: REACTIVE — respond to mentions on our posts
        self._run_reactive(run_id, client_id, client_data, engagement_mode, dry_run, result)

        # Phase B: PROACTIVE — reply to queued discovered conversations
        proactive_enabled = client_data.get("proactive_engagement_enabled") if client_data else False
        if proactive_enabled:
            self._run_proactive(run_id, client_id, client_data, dry_run, result)

        self.log_decision(
            run_id=run_id,
            input_summary=f"Checked {result.notifications_checked} notifications, "
            f"{result.proactive_evaluated} proactive conversations",
            output_summary=f"Reactive: drafted {result.replies_drafted}, posted {result.replies_posted}. "
            f"Proactive: posted {result.proactive_posted}",
            reasoning=f"Reply confidence threshold: {self.reply_confidence_threshold}",
        )

        return result

    def _run_reactive(
        self,
        run_id: str,
        client_id: str,
        client_data: dict | None,
        engagement_mode: str,
        dry_run: bool,
        result: EngagementResult,
    ) -> None:
        """Phase A: respond to mentions/replies on our posts."""
        if not self.bluesky:
            logger.info("[engagement] No Bluesky client configured, skipping reactive")
            return

        # 1. Fetch recent notifications (mentions, replies)
        notifications = self._fetch_notifications()
        result.notifications_checked = len(notifications)

        if not notifications:
            logger.info("[engagement] No new notifications to process")
            return

        # 2. Filter out already-replied notifications
        notifications = self._filter_already_replied(notifications, client_id)

        if not notifications:
            logger.info("[engagement] All notifications already handled")
            return

        # 3. Draft replies using LLM
        brand_voice = client_data.get("brand_voice", "professional") if client_data else "professional"

        # Inject memory context
        memory_context = self.get_memory_context(client_id)

        replies = self._draft_replies(notifications, brand_voice, memory_context)
        result.replies_drafted = len(replies)

        # 4. Post or draft high-confidence replies
        for reply in replies[: self.max_replies_per_cycle]:
            if reply.confidence < self.reply_confidence_threshold:
                logger.info(f"[engagement] Skipping low-confidence reply ({reply.confidence:.2f})")
                continue

            if dry_run:
                logger.info(f"[engagement] DRY RUN would reply: {reply.reply_text[:60]}...")
                result.replies.append(reply)
                continue

            if engagement_mode == "draft":
                # Save as draft for manual review
                self._record_reply(run_id, client_id, reply, posted_uri="", status="draft")
                result.replies_drafted += 1
                result.replies.append(reply)
                logger.info("[engagement] Drafted reply for review: %s", reply.reply_text[:60])
                continue

            try:
                posted_uri = self._post_reply_with_retry(reply)
                if posted_uri:
                    self._record_reply(run_id, client_id, reply, posted_uri, status="posted")
                    result.replies_posted += 1
                    result.replies.append(reply)
                    logger.info("[engagement] Posted reply to %s", reply.notification_uri[:30])
            except CircuitOpenError:
                result.errors.append("Circuit breaker open for engagement, stopping replies")
                break
            except Exception as e:
                category = PublishErrorClassifier.classify_error(e, "bluesky")
                error_msg = f"Reply failed ({category.value}): {e}"
                result.errors.append(error_msg)
                logger.warning("[engagement] %s", error_msg)

    def _fetch_notifications(self) -> list[dict]:
        """Fetch recent Bluesky notifications (mentions and replies to our posts)."""
        try:
            self.bluesky.login()
            response = self.bluesky._call_with_retry(
                self.bluesky.client.app.bsky.notification.list_notifications,
                params={"limit": 25},
            )
            notifications = []
            for notif in response.notifications:
                if notif.reason in ("mention", "reply"):
                    # Extract the text from the notification's record
                    text = ""
                    if hasattr(notif, "record") and hasattr(notif.record, "text"):
                        text = notif.record.text
                    notifications.append(
                        {
                            "uri": notif.uri,
                            "cid": notif.cid,
                            "author_handle": notif.author.handle,
                            "author_display_name": getattr(notif.author, "display_name", notif.author.handle),
                            "text": text,
                            "reason": notif.reason,
                            "indexed_at": str(notif.indexed_at) if notif.indexed_at else "",
                            "parent_uri": "",
                        }
                    )
                    # If it's a reply, get the parent URI
                    if notif.reason == "reply" and hasattr(notif.record, "reply"):
                        notifications[-1]["parent_uri"] = (
                            str(notif.record.reply.parent.uri) if notif.record.reply else ""
                        )
            return notifications
        except Exception as e:
            logger.warning(f"[engagement] Failed to fetch notifications: {e}")
            return []

    def _filter_already_replied(self, notifications: list[dict], client_id: str) -> list[dict]:
        """Remove notifications we've already responded to."""
        if not notifications:
            return []
        uris = [n["uri"] for n in notifications]
        placeholders = ",".join("?" for _ in uris)
        existing = self.db.fetchall(
            f"SELECT notification_uri FROM engagement_replies WHERE notification_uri IN ({placeholders}) AND client_id = ?",
            uris + [client_id],
        )
        replied_uris = {r["notification_uri"] for r in existing}
        return [n for n in notifications if n["uri"] not in replied_uris]

    def _draft_replies(self, notifications: list[dict], brand_voice: str, memory_context: str) -> list[EngagementReply]:
        """Use LLM to draft replies to notifications."""
        parts = [
            f"## Brand Voice: {brand_voice}",
            "",
            "## Notifications to respond to:",
        ]

        for i, notif in enumerate(notifications[:10], 1):  # Max 10 at a time
            parts.append(f"\n### Notification {i}")
            parts.append(f"From: @{notif['author_handle']} ({notif['author_display_name']})")
            parts.append(f"Type: {notif['reason']}")
            parts.append(f"Text: {notif['text']}")

        if memory_context:
            parts.append(f"\n## Agent Memory\n{memory_context}")

        user_message = "\n".join(parts)
        response = self.call_llm(user_message)

        try:
            from pydantic import BaseModel, Field

            class ReplyDraft(BaseModel):
                notification_index: int
                reply_text: str
                confidence: float = Field(ge=0.0, le=1.0)
                reasoning: str

            class ReplyDrafts(BaseModel):
                replies: list[ReplyDraft]

            parsed = parse_json_response(response.text, ReplyDrafts)
            replies = []
            for draft in parsed.replies:
                idx = draft.notification_index - 1
                if 0 <= idx < len(notifications):
                    replies.append(
                        EngagementReply(
                            notification_uri=notifications[idx]["uri"],
                            notification_text=notifications[idx]["text"][:200],
                            reply_text=draft.reply_text,
                            confidence=draft.confidence,
                            reasoning=draft.reasoning,
                        )
                    )
            return replies
        except Exception as e:
            logger.warning(f"[engagement] Failed to parse reply drafts: {e}")
            return []

    def _post_reply(self, reply: EngagementReply) -> str | None:
        """Post a reply to Bluesky. Returns the reply URI or None."""
        try:
            from atproto import models as atproto_models

            # Parse the notification URI to get the reply reference
            # URI format: at://did:plc:xxx/app.bsky.feed.post/yyy
            parts = reply.notification_uri.split("/")
            if len(parts) >= 5:
                # Get the original post to build reply reference
                response = self.bluesky._call_with_retry(
                    self.bluesky.client.app.bsky.feed.get_posts,
                    params={"uris": [reply.notification_uri]},
                )

                if response.posts:
                    original = response.posts[0]
                    root_ref = None
                    parent_ref = atproto_models.ComAtprotoRepoStrongRef.Main(
                        uri=original.uri,
                        cid=original.cid,
                    )

                    # If the original post is itself a reply, use its root
                    if hasattr(original, "record") and hasattr(original.record, "reply") and original.record.reply:
                        root_ref = original.record.reply.root
                    else:
                        root_ref = parent_ref

                    reply_ref = atproto_models.AppBskyFeedPost.ReplyRef(
                        root=root_ref,
                        parent=parent_ref,
                    )

                    # Enforce 300 char limit for Bluesky
                    text = reply.reply_text[:300]

                    result = self.bluesky._call_with_retry(
                        self.bluesky.client.send_post,
                        text=text,
                        reply_to=reply_ref,
                    )
                    return result.uri
        except Exception as e:
            logger.warning(f"[engagement] Failed to post reply: {e}")
        return None

    def _record_reply(
        self,
        run_id: str,
        client_id: str,
        reply: EngagementReply,
        posted_uri: str,
        status: str = "posted",
        platform: str = "bluesky",
        engagement_type: str = "reactive",
        conversation_id: str = "",
    ) -> None:
        """Record the reply in the database."""
        self.db.execute(
            """INSERT INTO engagement_replies
                (id, run_id, client_id, notification_uri, notification_text,
                 reply_text, reply_uri, confidence, platform, status,
                 engagement_type, conversation_id, reasoning, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
            (
                str(uuid.uuid4())[:8],
                run_id,
                client_id,
                reply.notification_uri,
                reply.notification_text[:500],
                reply.reply_text[:500],
                posted_uri,
                reply.confidence,
                platform,
                status,
                engagement_type,
                conversation_id,
                reply.reasoning[:500],
            ),
            commit=True,
        )

    # ------------------------------------------------------------------
    # Proactive Engagement
    # ------------------------------------------------------------------

    def _run_proactive(
        self,
        run_id: str,
        client_id: str,
        client_data: dict | None,
        dry_run: bool,
        result: EngagementResult,
    ) -> None:
        """Phase B: find and reply to queued discovered conversations."""
        # Load queued conversations (from Listener Agent)
        conversations = self.db.fetchall(
            "SELECT * FROM discovered_conversations "
            "WHERE client_id=? AND status='queued' "
            "ORDER BY relevance_score DESC, engagement_score DESC "
            "LIMIT ?",
            (client_id, self.max_replies_per_cycle),
        )
        if not conversations:
            return

        result.proactive_evaluated = len(conversations)

        brand_voice = client_data.get("brand_voice", "professional") if client_data else "professional"
        memory_context = self.get_memory_context(client_id)

        for conv in conversations:
            platform = conv["platform"]

            # Rate limit check
            if not self._check_rate_limit(client_id, platform):
                logger.info("[engagement] Rate limit reached for %s/%s", client_id, platform)
                break

            # Anti-spam: don't reply to same author twice in 24h
            if self._replied_to_author_recently(client_id, conv["author_handle"], hours=24):
                self._mark_conversation_status(conv["id"], "skipped")
                continue

            # Draft the reply via LLM
            reply = self._draft_proactive_reply(conv, brand_voice, memory_context)
            if not reply or reply.confidence < self.reply_confidence_threshold:
                self._mark_conversation_status(conv["id"], "evaluated")
                continue

            if dry_run:
                logger.info("[engagement] DRY RUN proactive: %s", reply.reply_text[:60])
                result.proactive_posted += 1
                continue

            # Post the reply on the correct platform
            posted_uri = self._post_reply_multiplatform(
                platform, reply.reply_text, conv["external_id"], conv.get("external_uri", "")
            )
            if posted_uri:
                self._record_reply(
                    run_id,
                    client_id,
                    reply,
                    posted_uri,
                    status="posted",
                    platform=platform,
                    engagement_type="proactive",
                    conversation_id=conv["id"],
                )
                self._mark_conversation_status(conv["id"], "replied")
                self._record_rate_limit(client_id, platform)
                result.proactive_posted += 1
                logger.info("[engagement] Proactive reply on %s: %s", platform, reply.reply_text[:60])
            else:
                self._mark_conversation_status(conv["id"], "evaluated")
                result.errors.append(f"Proactive reply failed on {platform}")

    def _draft_proactive_reply(self, conv: dict, brand_voice: str, memory_context: str) -> EngagementReply | None:
        """Draft a reply to a discovered conversation via LLM."""
        char_limit = _PLATFORM_CHAR_LIMITS.get(conv["platform"], 300)
        parts = [
            "## PROACTIVE ENGAGEMENT — You are entering SOMEONE ELSE's conversation",
            "",
            f"Brand voice: {brand_voice}",
            f"Platform: {conv['platform']} (max {char_limit} chars)",
            "",
            "RULES FOR PROACTIVE REPLIES:",
            "- Be humble — you're a guest in their conversation",
            "- Add genuine value, never pitch or promote",
            "- If you can't add value, say so (set confidence to 0)",
            "- Match the platform's tone and norms",
            "- Higher bar than reactive: only reply if truly valuable",
            "",
            "## Conversation to respond to:",
            f"Author: @{conv['author_handle']}",
            f"Platform: {conv['platform']}",
            f"Text: {conv['text_content'][:800]}",
            f"Engagement: {conv.get('engagement_score', 0)} interactions",
        ]

        if memory_context:
            parts.append(f"\n## Agent Memory\n{memory_context}")

        parts.append('\nRespond with JSON: {"reply_text": "...", "confidence": 0.85, "reasoning": "..."}')

        user_message = "\n".join(parts)
        try:
            response = self.call_llm(user_message)
            text = response.text.strip().strip("`").removeprefix("json").strip()
            data = json.loads(text)
            confidence = max(0.0, min(1.0, data.get("confidence", 0.0)))
            reply_text = data.get("reply_text", "")[:char_limit]
            if not reply_text:
                return None
            return EngagementReply(
                notification_uri=conv["external_uri"],
                notification_text=conv["text_content"][:200],
                reply_text=reply_text,
                confidence=confidence,
                reasoning=data.get("reasoning", ""),
            )
        except Exception as e:
            logger.warning("[engagement] Failed to draft proactive reply: %s", e)
            return None

    def _post_reply_multiplatform(self, platform: str, text: str, target_id: str, target_uri: str) -> str | None:
        """Post a reply on the correct platform. Returns posted URI or None."""
        try:
            if platform == "bluesky" and self.bluesky:
                # Use existing Bluesky reply mechanism
                reply = EngagementReply(
                    notification_uri=target_uri,
                    notification_text="",
                    reply_text=text,
                    confidence=1.0,
                    reasoning="proactive",
                )
                return self._post_reply(reply)
            elif platform == "twitter" and self.twitter:
                url, reply_id = self.twitter.reply_to_tweet(target_id, text)
                return url
            elif platform == "reddit" and self.reddit:
                url, comment_id = self.reddit.reply_to_post(target_id, text)
                return url
            elif platform == "linkedin" and self.linkedin:
                comment_urn = self.linkedin.comment_on_post(target_id, text)
                return comment_urn
            else:
                logger.warning("[engagement] No client for platform %s", platform)
                return None
        except Exception as e:
            logger.warning("[engagement] Multi-platform reply failed (%s): %s", platform, e)
            return None

    def _mark_conversation_status(self, conversation_id: str, status: str) -> None:
        """Update status of a discovered conversation."""
        self.db.execute(
            "UPDATE discovered_conversations SET status=? WHERE id=?",
            (status, conversation_id),
            commit=True,
        )

    def _replied_to_author_recently(self, client_id: str, author_handle: str, hours: int = 24) -> bool:
        """Check if we've already replied to this author within the window."""
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        row = self.db.fetchone(
            "SELECT COUNT(*) as cnt FROM engagement_replies "
            "WHERE client_id=? AND notification_uri LIKE ? AND created_at > ? "
            "AND engagement_type='proactive'",
            (client_id, f"%{author_handle}%", cutoff),
        )
        return bool(row and row.get("cnt", 0) > 0)

    # ------------------------------------------------------------------
    # Rate Limiting
    # ------------------------------------------------------------------

    def _check_rate_limit(self, client_id: str, platform: str) -> bool:
        """Check if we're within rate limits for this client/platform. Returns True if OK."""
        now = datetime.now(timezone.utc)

        for period in ("hourly", "daily"):
            row = self.db.fetchone(
                "SELECT * FROM engagement_rate_limits WHERE client_id=? AND platform=? AND period=?",
                (client_id, platform, period),
            )
            if not row:
                # Create rate limit row
                self.db.execute(
                    """INSERT INTO engagement_rate_limits
                    (id, client_id, platform, period, max_count, current_count, period_start)
                    VALUES (?, ?, ?, ?, ?, 0, ?)""",
                    (str(uuid.uuid4()), client_id, platform, period, _DEFAULT_RATE_LIMITS[period], now.isoformat()),
                    commit=True,
                )
                continue

            # Check if period has expired and reset
            from ortobahn.db.core import to_datetime

            period_start = to_datetime(row["period_start"])
            if period_start:
                window = timedelta(hours=1) if period == "hourly" else timedelta(days=1)
                if now - period_start > window:
                    self.db.execute(
                        "UPDATE engagement_rate_limits SET current_count=0, period_start=? WHERE id=?",
                        (now.isoformat(), row["id"]),
                        commit=True,
                    )
                    continue

            if row["current_count"] >= row["max_count"]:
                return False

        return True

    def _record_rate_limit(self, client_id: str, platform: str) -> None:
        """Increment rate limit counters for this client/platform."""
        for period in ("hourly", "daily"):
            self.db.execute(
                "UPDATE engagement_rate_limits SET current_count = current_count + 1 "
                "WHERE client_id=? AND platform=? AND period=?",
                (client_id, platform, period),
                commit=True,
            )
