"""Pipeline orchestrator - wires all agents together."""

from __future__ import annotations

import logging
import uuid

from ortobahn.agents.analytics import AnalyticsAgent
from ortobahn.agents.ceo import CEOAgent
from ortobahn.agents.cfo import CFOAgent
from ortobahn.agents.creator import CreatorAgent
from ortobahn.agents.marketing import MarketingAgent
from ortobahn.agents.ops import OpsAgent
from ortobahn.agents.publisher import PublisherAgent
from ortobahn.agents.sre import SREAgent
from ortobahn.agents.strategist import StrategistAgent
from ortobahn.config import Settings
from ortobahn.db import Database
from ortobahn.integrations.bluesky import BlueskyClient
from ortobahn.integrations.linkedin import LinkedInClient
from ortobahn.integrations.newsapi_client import get_trending_headlines
from ortobahn.integrations.rss import fetch_feeds
from ortobahn.integrations.trends import get_trending_searches
from ortobahn.integrations.twitter import TwitterClient
from ortobahn.models import Client, Platform, TrendingTopic

logger = logging.getLogger("ortobahn.pipeline")


class Pipeline:
    def __init__(self, settings: Settings, dry_run: bool = False):
        self.settings = settings
        self.dry_run = dry_run
        self.db = Database(settings.db_path)

        # Platform clients (optional - only init if credentials configured)
        self.bluesky = None
        if settings.bluesky_handle and settings.bluesky_app_password:
            self.bluesky = BlueskyClient(settings.bluesky_handle, settings.bluesky_app_password)

        self.twitter = None
        if settings.has_twitter():
            self.twitter = TwitterClient(
                api_key=settings.twitter_api_key,
                api_secret=settings.twitter_api_secret,
                access_token=settings.twitter_access_token,
                access_token_secret=settings.twitter_access_token_secret,
            )

        self.linkedin = None
        if settings.has_linkedin():
            self.linkedin = LinkedInClient(
                access_token=settings.linkedin_access_token,
                person_urn=settings.linkedin_person_urn,
            )

        # Initialize agents
        self.analytics = AnalyticsAgent(
            db=self.db,
            api_key=settings.anthropic_api_key,
            model=settings.claude_model,
            bluesky_client=self.bluesky,
            twitter_client=self.twitter,
            linkedin_client=self.linkedin,
        )
        self.ceo = CEOAgent(
            db=self.db,
            api_key=settings.anthropic_api_key,
            model=settings.claude_model,
        )
        self.ceo.thinking_budget = settings.thinking_budget_ceo
        self.strategist = StrategistAgent(
            db=self.db,
            api_key=settings.anthropic_api_key,
            model=settings.claude_model,
        )
        self.strategist.thinking_budget = settings.thinking_budget_strategist
        self.creator = CreatorAgent(
            db=self.db,
            api_key=settings.anthropic_api_key,
            model=settings.claude_model,
        )
        self.creator.thinking_budget = settings.thinking_budget_creator
        self.publisher = PublisherAgent(
            db=self.db,
            bluesky_client=self.bluesky,
            twitter_client=self.twitter,
            linkedin_client=self.linkedin,
            confidence_threshold=settings.post_confidence_threshold,
            post_delay_seconds=settings.post_delay_seconds,
        )
        self.sre = SREAgent(
            db=self.db,
            api_key=settings.anthropic_api_key,
            model=settings.claude_model,
        )
        self.cfo = CFOAgent(
            db=self.db,
            api_key=settings.anthropic_api_key,
            model=settings.claude_model,
        )
        self.ops = OpsAgent(
            db=self.db,
            api_key=settings.anthropic_api_key,
            model=settings.claude_model,
        )
        self.marketing = MarketingAgent(
            db=self.db,
            api_key=settings.anthropic_api_key,
            model=settings.claude_model,
        )

    def gather_trends(self) -> list[TrendingTopic]:
        """Gather trending topics from all sources."""
        topics = []

        # NewsAPI
        for article in get_trending_headlines(self.settings.newsapi_key or ""):
            topics.append(
                TrendingTopic(
                    title=article.title,
                    source="newsapi",
                    description=article.description,
                    url=article.url,
                )
            )

        # Google Trends
        for term in get_trending_searches():
            topics.append(
                TrendingTopic(
                    title=term,
                    source="google_trends",
                )
            )

        # RSS
        for rss_item in fetch_feeds(self.settings.rss_feeds):
            topics.append(
                TrendingTopic(
                    title=rss_item.title,
                    source="rss",
                    description=rss_item.summary,
                    url=rss_item.link,
                )
            )

        logger.info(f"Gathered {len(topics)} trending topics")
        return topics

    def publish_approved_drafts(self, client_id: str = "default") -> int:
        """Publish any posts in 'approved' status. Returns count published."""
        approved = self.db.get_approved_posts(client_id=client_id)
        if not approved:
            return 0

        published_count = 0
        for post in approved:
            platform_str = post.get("platform", "generic")
            try:
                platform = Platform(platform_str)
            except ValueError:
                platform = Platform.GENERIC

            publisher_client = self.publisher._get_publisher(platform)
            if publisher_client is None:
                logger.info(f"No publisher for {platform_str}, skipping approved post {post['id'][:8]}")
                continue

            if self.dry_run:
                logger.info(f"[DRY RUN] Would publish approved post {post['id'][:8]}")
                continue

            try:
                uri, platform_id = publisher_client.post(post["text"])
                self.db.update_post_published(post["id"], uri, platform_id)
                published_count += 1
                logger.info(f"Published approved post {post['id'][:8]} to {platform_str}")
            except Exception as e:
                self.db.update_post_failed(post["id"], str(e))
                logger.error(f"Failed to publish approved post {post['id'][:8]}: {e}")

        return published_count

    def run_cycle(
        self,
        client_id: str = "default",
        target_platforms: list[Platform] | None = None,
        generate_only: bool | None = None,
    ) -> dict:
        """Execute one complete pipeline cycle.

        generate_only: None = defer to settings.autonomous_mode,
                       True = drafts only, False = publish.
        """
        if generate_only is None:
            generate_only = not self.settings.autonomous_mode
        run_id = str(uuid.uuid4())
        self.db.start_pipeline_run(run_id, mode="single", client_id=client_id)
        errors = []
        total_input_tokens = 0
        total_output_tokens = 0

        # Load client from DB
        client_data = self.db.get_client(client_id)
        client = Client(**client_data) if client_data else None
        platforms = target_platforms or [Platform.GENERIC]

        # Budget guard: skip paused clients
        if client_data and client_data.get("status") == "paused":
            logger.info(f"Client {client_id} is paused (budget exceeded). Skipping cycle.")
            self.db.complete_pipeline_run(run_id, posts_published=0, errors=["client_paused"])
            return {"run_id": run_id, "posts_published": 0, "total_drafts": 0, "input_tokens": 0, "output_tokens": 0, "errors": ["client_paused"]}

        logger.info(f"=== Pipeline cycle {run_id[:8]} started (client={client_id}) ===")

        # Backup database before cycle
        if self.settings.backup_enabled:
            from ortobahn.backup import backup_database

            backup_database(self.settings.db_path, self.settings.backup_dir, self.settings.backup_max_count)

        # Publish any previously approved drafts first
        approved_published = self.publish_approved_drafts(client_id=client_id)
        if approved_published:
            logger.info(f"Published {approved_published} previously approved drafts")

        try:
            # 0. SRE Agent (system health check - runs first)
            logger.info("[0/9] SRE Agent checking system health...")
            sre_report = self.sre.run(run_id, slack_webhook_url=self.settings.slack_webhook_url)
            logger.info(f"  -> Health: {sre_report.health_status}, Alerts: {len(sre_report.alerts)}")

            # 1. Analytics
            logger.info("[1/9] Analytics Agent analyzing past performance...")
            analytics_report = self.analytics.run(run_id)
            logger.info(f"  -> {analytics_report.total_posts} posts analyzed")

            # 2. Gather trends (parallel-safe, no LLM)
            logger.info("[2/9] Gathering trending topics...")
            trending = self.gather_trends()

            # 2.5. Performance insights for CEO (prompt tuner)
            from ortobahn.prompt_tuner import get_performance_insights

            performance_insights = get_performance_insights(self.db, client_id=client_id)

            # 3. CEO
            logger.info("[3/9] CEO Agent setting strategy...")
            strategy = self.ceo.run(
                run_id,
                analytics_report=analytics_report,
                trending=trending,
                client=client,
                performance_insights=performance_insights,
            )
            logger.info(f"  -> Themes: {strategy.themes}")

            # 4. Strategist
            logger.info("[4/9] Strategist Agent planning content...")
            content_plan = self.strategist.run(run_id, strategy=strategy, trending=trending, client=client)
            # Limit to max_posts_per_cycle
            content_plan.posts = content_plan.posts[: self.settings.max_posts_per_cycle]
            logger.info(f"  -> {len(content_plan.posts)} post ideas")

            # 5. Creator
            logger.info("[5/9] Creator Agent writing posts...")
            drafts = self.creator.run(
                run_id,
                content_plan=content_plan,
                strategy=strategy,
                client=client,
                target_platforms=platforms,
            )
            logger.info(f"  -> {len(drafts.posts)} drafts written")

            # 6. Publisher (skip if generate_only)
            posts_published = 0
            if generate_only:
                logger.info("[6/5] Saving drafts for review (generate-only mode)...")
                active_strategy = self.db.get_active_strategy(client_id=client_id)
                strategy_id = active_strategy["id"] if active_strategy else None
                for draft in drafts.posts:
                    if draft.confidence >= self.settings.post_confidence_threshold:
                        self.db.save_post(
                            text=draft.text,
                            run_id=run_id,
                            strategy_id=strategy_id,
                            source_idea=draft.source_idea,
                            reasoning=draft.reasoning,
                            confidence=draft.confidence,
                            status="draft",
                            client_id=client_id,
                            platform=draft.platform.value,
                            content_type=draft.content_type.value,
                        )
                logger.info(f"  -> {len(drafts.posts)} drafts saved for review")
            else:
                logger.info("[6/5] Publisher Agent posting...")
                active_strategy = self.db.get_active_strategy(client_id=client_id)
                strategy_id = active_strategy["id"] if active_strategy else None

                published = self.publisher.run(
                    run_id,
                    drafts=drafts,
                    strategy_id=strategy_id,
                    dry_run=self.dry_run,
                    client_id=client_id,
                )
                posts_published = sum(1 for p in published.posts if p.status == "published")
                logger.info(f"  -> {posts_published} posts published")

            # 7. CFO Agent (cost analysis)
            logger.info("[7/9] CFO Agent analyzing costs...")
            cfo_report = self.cfo.run(run_id)
            logger.info(f"  -> Cost/post: ${cfo_report.cost_per_post:.4f}, ROI: {cfo_report.roi_estimate:.1f}")

            # 8. Ops Agent (operations management)
            logger.info("[8/9] Ops Agent managing operations...")
            ops_report = self.ops.run(run_id)
            logger.info(f"  -> Actions: {len(ops_report.actions_taken)}, Pending clients: {ops_report.pending_clients}")

            # 9. Marketing Agent (only for Ortobahn self-marketing)
            if client_id == "ortobahn":
                logger.info("[9/9] Marketing Agent generating self-marketing content...")
                marketing_report = self.marketing.run(run_id)
                logger.info(
                    f"  -> Ideas: {len(marketing_report.content_ideas)}, Drafts: {len(marketing_report.draft_posts)}"
                )

        except Exception as e:
            logger.error(f"Pipeline error: {e}")
            errors.append(str(e))
            self.db.fail_pipeline_run(run_id, errors)
            raise

        # Calculate token usage from agent logs
        logs = self.db.get_recent_agent_logs(limit=10)
        for log in logs:
            if log.get("run_id") == run_id:
                total_input_tokens += log.get("input_tokens") or 0
                total_output_tokens += log.get("output_tokens") or 0

        self.db.complete_pipeline_run(
            run_id,
            posts_published=posts_published,
            errors=errors if errors else None,
            total_input_tokens=total_input_tokens,
            total_output_tokens=total_output_tokens,
        )

        logger.info(f"=== Pipeline cycle {run_id[:8]} completed: {posts_published} posts published ===")

        return {
            "run_id": run_id,
            "posts_published": posts_published,
            "total_drafts": len(drafts.posts),
            "input_tokens": total_input_tokens,
            "output_tokens": total_output_tokens,
            "errors": errors,
        }

    def close(self):
        self.db.close()
