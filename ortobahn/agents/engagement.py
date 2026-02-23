"""Engagement Agent — autonomous replies and conversation participation."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ortobahn.agents.base import BaseAgent
from ortobahn.llm import parse_json_response

logger = logging.getLogger("ortobahn.agents")


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
        max_replies_per_cycle: int = 3,
        reply_confidence_threshold: float = 0.75,
        use_bedrock: bool = False,
        bedrock_region: str = "us-west-2",
    ):
        super().__init__(db, api_key, model, use_bedrock=use_bedrock, bedrock_region=bedrock_region)
        self.bluesky = bluesky_client
        self.max_replies_per_cycle = max_replies_per_cycle
        self.reply_confidence_threshold = reply_confidence_threshold

    def run(self, run_id: str, client_id: str = "default", dry_run: bool = False, **kwargs) -> EngagementResult:
        """Check notifications and reply to relevant mentions."""
        result = EngagementResult()

        if not self.bluesky:
            logger.info("[engagement] No Bluesky client configured, skipping")
            return result

        # 1. Fetch recent notifications (mentions, replies)
        notifications = self._fetch_notifications()
        result.notifications_checked = len(notifications)

        if not notifications:
            logger.info("[engagement] No new notifications to process")
            self.log_decision(
                run_id=run_id,
                input_summary="Checked notifications",
                output_summary="No new notifications",
                reasoning="No mentions or replies found",
            )
            return result

        # 2. Filter out already-replied notifications
        notifications = self._filter_already_replied(notifications, client_id)

        if not notifications:
            logger.info("[engagement] All notifications already handled")
            return result

        # 3. Draft replies using LLM
        # Get client context for brand voice
        client_data = self.db.get_client(client_id)
        brand_voice = client_data.get("brand_voice", "professional") if client_data else "professional"

        # Inject memory context
        memory_context = self.get_memory_context(client_id)

        replies = self._draft_replies(notifications, brand_voice, memory_context)
        result.replies_drafted = len(replies)

        # 4. Post high-confidence replies
        for reply in replies[: self.max_replies_per_cycle]:
            if reply.confidence < self.reply_confidence_threshold:
                logger.info(f"[engagement] Skipping low-confidence reply ({reply.confidence:.2f})")
                continue

            if dry_run:
                logger.info(f"[engagement] DRY RUN would reply: {reply.reply_text[:60]}...")
                result.replies.append(reply)
                continue

            try:
                posted_uri = self._post_reply(reply)
                if posted_uri:
                    self._record_reply(run_id, client_id, reply, posted_uri)
                    result.replies_posted += 1
                    result.replies.append(reply)
                    logger.info(f"[engagement] Posted reply to {reply.notification_uri[:30]}")
            except Exception as e:
                error_msg = f"Failed to post reply: {e}"
                result.errors.append(error_msg)
                logger.warning(f"[engagement] {error_msg}")

        self.log_decision(
            run_id=run_id,
            input_summary=f"Checked {result.notifications_checked} notifications",
            output_summary=f"Drafted {result.replies_drafted}, posted {result.replies_posted} replies",
            reasoning=f"Reply confidence threshold: {self.reply_confidence_threshold}",
        )

        return result

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

    def _record_reply(self, run_id: str, client_id: str, reply: EngagementReply, posted_uri: str) -> None:
        """Record the reply in the database."""
        import uuid

        self.db.execute(
            """INSERT INTO engagement_replies
                (id, run_id, client_id, notification_uri, notification_text,
                 reply_text, reply_uri, confidence, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
            (
                str(uuid.uuid4())[:8],
                run_id,
                client_id,
                reply.notification_uri,
                reply.notification_text[:500],
                reply.reply_text[:500],
                posted_uri,
                reply.confidence,
            ),
            commit=True,
        )
