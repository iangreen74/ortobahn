"""Publisher Agent - posts to platforms. No LLM, purely mechanical."""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any

from ortobahn.agents.base import BaseAgent
from ortobahn.integrations.bluesky import BlueskyClient
from ortobahn.models import (
    DraftPosts,
    Platform,
    PreflightIssue,
    PreflightResult,
    PreflightSeverity,
    PublishedPost,
    PublishedPosts,
)

logger = logging.getLogger("ortobahn.publisher")


class PublisherAgent(BaseAgent):
    name = "publisher"
    prompt_file = ""  # No LLM needed

    def __init__(
        self,
        db,
        api_key: str = "",
        model: str = "",
        max_tokens: int = 0,
        bluesky_client: BlueskyClient | None = None,
        twitter_client=None,
        linkedin_client=None,
        confidence_threshold: float = 0.7,
        post_delay_seconds: int = 0,
    ):
        super().__init__(db, api_key, model, max_tokens)
        self.bluesky = bluesky_client
        self.twitter = twitter_client
        self.linkedin = linkedin_client
        self.confidence_threshold = confidence_threshold
        self.post_delay = post_delay_seconds

    def preflight(self, **kwargs: Any) -> PreflightResult:
        """Check that at least one platform client is available."""
        issues: list[PreflightIssue] = []
        if self.bluesky is None and self.twitter is None and self.linkedin is None:
            issues.append(
                PreflightIssue(
                    severity=PreflightSeverity.WARNING,
                    component="publisher",
                    message="No platform clients configured — posts will be saved as drafts only",
                    agent_name=self.name,
                )
            )
        passed = not any(i.severity == PreflightSeverity.BLOCKING for i in issues)
        return PreflightResult(passed=passed, issues=issues)

    def _get_publisher(self, platform: Platform):
        """Return the client for a platform, or None if not configured."""
        publishers = {
            Platform.BLUESKY: self.bluesky,
            Platform.TWITTER: self.twitter,
            Platform.LINKEDIN: self.linkedin,
        }
        return publishers.get(platform)

    def run(
        self,
        run_id: str,
        drafts: DraftPosts,
        strategy_id: str | None = None,
        dry_run: bool = False,
        client_id: str = "default",
    ) -> PublishedPosts:
        results = []
        published_count = 0

        for draft in drafts.posts:
            if draft.confidence < self.confidence_threshold:
                results.append(
                    PublishedPost(
                        text=draft.text,
                        status="skipped",
                        platform=draft.platform,
                        error=f"Confidence {draft.confidence:.2f} below threshold {self.confidence_threshold}",
                    )
                )
                logger.info(f"Skipped post (confidence {draft.confidence:.2f}): {draft.text[:50]}...")
                continue

            # Save draft to DB
            post_id = self.db.save_post(
                text=draft.text,
                run_id=run_id,
                strategy_id=strategy_id,
                source_idea=draft.source_idea,
                reasoning=draft.reasoning,
                confidence=draft.confidence,
                status="draft",
                client_id=client_id,
                platform=draft.platform.value if hasattr(draft.platform, "value") else str(draft.platform),
                content_type=draft.content_type.value
                if hasattr(draft.content_type, "value")
                else str(draft.content_type),
            )

            if dry_run:
                results.append(
                    PublishedPost(
                        text=draft.text,
                        status="draft",
                        platform=draft.platform,
                    )
                )
                logger.info(f"[DRY RUN] Would post ({draft.platform.value}): {draft.text[:50]}...")
                continue

            # Dispatch to platform publisher if configured
            publisher = self._get_publisher(draft.platform)
            if publisher is not None:
                try:
                    uri, platform_id = publisher.post(draft.text)
                    self.db.update_post_published(post_id, uri, platform_id)
                    results.append(
                        PublishedPost(
                            text=draft.text,
                            uri=uri,
                            cid=platform_id,
                            published_at=datetime.utcnow(),
                            status="published",
                            platform=draft.platform,
                        )
                    )
                    published_count += 1
                    logger.info(f"Published to {draft.platform.value}: {draft.text[:50]}...")
                    if self.post_delay > 0:
                        logger.info(f"Rate limiting: waiting {self.post_delay}s before next post")
                        time.sleep(self.post_delay)
                except Exception as e:
                    self.db.update_post_failed(post_id, str(e))
                    results.append(
                        PublishedPost(
                            text=draft.text,
                            status="failed",
                            platform=draft.platform,
                            error=str(e),
                        )
                    )
                    logger.error(f"Failed to publish to {draft.platform.value}: {e}")
            else:
                # No publisher for this platform — save as draft for manual use
                results.append(
                    PublishedPost(
                        text=draft.text,
                        status="draft",
                        platform=draft.platform,
                    )
                )
                logger.info(f"Saved draft ({draft.platform.value}): {draft.text[:50]}...")

        self.log_decision(
            run_id=run_id,
            input_summary=f"{len(drafts.posts)} drafts, threshold={self.confidence_threshold}",
            output_summary=f"Published {published_count}, drafts {len(drafts.posts) - published_count}",
            reasoning=f"Dry run: {dry_run}",
        )
        return PublishedPosts(posts=results)
