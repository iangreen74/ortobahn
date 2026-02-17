"""Pipeline orchestrator - wires all agents together."""

from __future__ import annotations

import logging
import uuid

from ortobahn.agents.analytics import AnalyticsAgent
from ortobahn.agents.ceo import CEOAgent
from ortobahn.agents.cfo import CFOAgent
from ortobahn.agents.cifix import CIFixAgent
from ortobahn.agents.creator import CreatorAgent
from ortobahn.agents.marketing import MarketingAgent
from ortobahn.agents.ops import OpsAgent
from ortobahn.agents.publisher import PublisherAgent
from ortobahn.agents.reflection import ReflectionAgent
from ortobahn.agents.sre import SREAgent
from ortobahn.agents.strategist import StrategistAgent
from ortobahn.config import Settings
from ortobahn.db import create_database
from ortobahn.integrations.bluesky import BlueskyClient
from ortobahn.integrations.linkedin import LinkedInClient
from ortobahn.integrations.newsapi_client import get_trending_headlines
from ortobahn.integrations.rss import fetch_feeds
from ortobahn.integrations.trends import get_trending_searches
from ortobahn.integrations.twitter import TwitterClient
from ortobahn.learning import LearningEngine
from ortobahn.memory import MemoryStore
from ortobahn.models import Client, Platform, TrendingTopic

logger = logging.getLogger("ortobahn.pipeline")


class Pipeline:
    def __init__(self, settings: Settings, dry_run: bool = False):
        self.settings = settings
        self.dry_run = dry_run
        self.db = create_database(settings)

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

        # Common agent kwargs (includes Bedrock settings when enabled)
        agent_kwargs = {
            "db": self.db,
            "api_key": settings.anthropic_api_key,
            "model": settings.claude_model,
            "use_bedrock": settings.use_bedrock,
            "bedrock_region": settings.bedrock_region,
        }

        # Initialize agents
        self.analytics = AnalyticsAgent(
            **agent_kwargs,
            bluesky_client=self.bluesky,
            twitter_client=self.twitter,
            linkedin_client=self.linkedin,
        )
        self.ceo = CEOAgent(**agent_kwargs)
        self.ceo.thinking_budget = settings.thinking_budget_ceo
        self.strategist = StrategistAgent(**agent_kwargs)
        self.strategist.thinking_budget = settings.thinking_budget_strategist
        self.creator = CreatorAgent(**agent_kwargs)
        self.creator.thinking_budget = settings.thinking_budget_creator
        self.publisher = PublisherAgent(
            db=self.db,
            bluesky_client=self.bluesky,
            twitter_client=self.twitter,
            linkedin_client=self.linkedin,
            confidence_threshold=settings.post_confidence_threshold,
            post_delay_seconds=settings.post_delay_seconds,
        )
        self.sre = SREAgent(**agent_kwargs)
        self.cfo = CFOAgent(**agent_kwargs)
        self.ops = OpsAgent(**agent_kwargs)
        self.marketing = MarketingAgent(**agent_kwargs)
        self.reflection = ReflectionAgent(**agent_kwargs)
        self.reflection.thinking_budget = settings.thinking_budget_reflection
        self.cifix = CIFixAgent(**agent_kwargs) if settings.cifix_enabled else None
        self.memory_store = MemoryStore(self.db)
        self.learning_engine = LearningEngine(self.db, self.memory_store)

    def gather_trends(self, client_id: str = "default") -> list[TrendingTopic]:
        """Gather trending topics from all sources, filtered by client's industry."""
        topics = []
        client = self.db.get_client(client_id)

        # NewsAPI: use client's category
        category = client.get("news_category", "technology") if client else "technology"
        for article in get_trending_headlines(self.settings.newsapi_key or "", category=category):
            topics.append(
                TrendingTopic(
                    title=article.title,
                    source="newsapi",
                    description=article.description,
                    url=article.url,
                )
            )

        # NewsAPI keyword search: use client's industry keywords
        keywords = client.get("news_keywords", "") if client else ""
        if keywords:
            from ortobahn.integrations.newsapi_client import search_news

            for article in search_news(self.settings.newsapi_key or "", query=keywords):
                topics.append(
                    TrendingTopic(
                        title=article.title,
                        source="newsapi_search",
                        description=article.description,
                        url=article.url,
                    )
                )

        # Google Trends (global — no keyword filtering available)
        for term in get_trending_searches():
            topics.append(
                TrendingTopic(
                    title=term,
                    source="google_trends",
                )
            )

        # RSS: use client's feeds if set, fall back to global defaults
        client_feeds = client.get("rss_feeds", "") if client else ""
        feed_urls = [f.strip() for f in client_feeds.split(",") if f.strip()] if client_feeds else []
        if not feed_urls:
            feed_urls = self.settings.rss_feeds
        for rss_item in fetch_feeds(feed_urls):
            topics.append(
                TrendingTopic(
                    title=rss_item.title,
                    source="rss",
                    description=rss_item.summary,
                    url=rss_item.link,
                )
            )

        logger.info(f"Gathered {len(topics)} trending topics for client {client_id}")
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

    def _run_agent_with_preflight(self, agent, run_id: str, **kwargs):
        """Check an agent's preflight before calling run(). Returns None on block."""
        pf = agent.preflight(**kwargs)
        if not pf.passed:
            for issue in pf.blocking_issues:
                logger.error(f"Agent {agent.name} preflight BLOCKED: {issue.message}")
            return None
        for issue in pf.warnings:
            logger.warning(f"Agent {agent.name} preflight warning: {issue.message}")
        return agent.run(run_id, **kwargs)

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
            # Per-client auto_publish overrides global setting
            if client_id and (cd := self.db.get_client(client_id)):
                generate_only = not cd.get("auto_publish", 0)
            else:
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
            return {
                "run_id": run_id,
                "posts_published": 0,
                "total_drafts": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "errors": ["client_paused"],
            }

        # Check trial expiry before subscription guard
        if client_data and not client_data.get("internal"):
            self.db.check_and_expire_trial(client_id)
            client_data = self.db.get_client(client_id)

        # Subscription guard: skip non-internal clients without active subscription
        if (
            client_data
            and not client_data.get("internal")
            and client_data.get("subscription_status") not in ("active", "trialing")
        ):
            logger.info(
                f"Client {client_id} has no active subscription (status={client_data.get('subscription_status')}). Skipping."
            )
            self.db.complete_pipeline_run(run_id, posts_published=0, errors=["no_active_subscription"])
            return {
                "run_id": run_id,
                "posts_published": 0,
                "total_drafts": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "errors": ["no_active_subscription"],
            }

        # Per-tenant credentials: resolve platform clients for this client
        if self.settings.secret_key:
            from ortobahn.credentials import build_platform_clients

            tenant_clients = build_platform_clients(self.db, client_id, self.settings.secret_key, self.settings)
            self.publisher.bluesky = tenant_clients["bluesky"] or self.bluesky
            self.publisher.twitter = tenant_clients["twitter"] or self.twitter
            self.publisher.linkedin = tenant_clients["linkedin"] or self.linkedin
            self.analytics.bluesky = tenant_clients["bluesky"] or self.bluesky
            self.analytics.twitter = tenant_clients["twitter"] or self.twitter
            self.analytics.linkedin = tenant_clients["linkedin"] or self.linkedin

        logger.info(f"=== Pipeline cycle {run_id[:8]} started (client={client_id}) ===")

        # Backup database before cycle (SQLite only — RDS has automated backups)
        if self.settings.backup_enabled and not self.settings.database_url:
            from ortobahn.backup import backup_database

            backup_database(self.settings.db_path, self.settings.backup_dir, self.settings.backup_max_count)

        # Publish any previously approved drafts first
        approved_published = self.publish_approved_drafts(client_id=client_id)
        if approved_published:
            logger.info(f"Published {approved_published} previously approved drafts")

        # --- Preflight Intelligence ---
        if self.settings.preflight_enabled:
            from ortobahn.preflight import run_pipeline_preflight

            preflight_result = run_pipeline_preflight(self.settings, self.db, client_id, check_apis=True)
            if not preflight_result.passed:
                for issue in preflight_result.blocking_issues:
                    logger.error(f"Preflight BLOCKER: [{issue.component}] {issue.message}")
                    if self.memory_store:
                        from ortobahn.models import (
                            AgentMemory,
                            MemoryCategory,
                            MemoryType,
                        )

                        self.memory_store.remember(
                            AgentMemory(
                                agent_name="preflight",
                                client_id=client_id,
                                memory_type=MemoryType.OBSERVATION,
                                category=MemoryCategory.CALIBRATION,
                                content={
                                    "component": issue.component,
                                    "message": issue.message,
                                },
                                confidence=1.0,
                                source_run_id=run_id,
                            )
                        )
                self.db.complete_pipeline_run(
                    run_id,
                    posts_published=0,
                    errors=[i.message for i in preflight_result.blocking_issues],
                )
                return {
                    "run_id": run_id,
                    "posts_published": 0,
                    "total_drafts": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "errors": [i.message for i in preflight_result.blocking_issues],
                }
            # Log warnings but continue
            for issue in preflight_result.warnings:
                logger.warning(f"Preflight warning: [{issue.component}] {issue.message}")

        try:
            # 0. SRE Agent (system health check - runs first)
            logger.info("[0/12] SRE Agent checking system health...")
            sre_report = self.sre.run(run_id, slack_webhook_url=self.settings.slack_webhook_url)
            logger.info(f"  -> Health: {sre_report.health_status}, Alerts: {len(sre_report.alerts)}")

            # 0.5 CI Fix Agent (self-healing CI/CD)
            if self.cifix:
                logger.info("[0.5/12] CI Fix Agent checking for failures...")
                try:
                    cifix_result = self.cifix.run(
                        run_id=run_id,
                        auto_pr=self.settings.cifix_auto_pr,
                    )
                    if cifix_result.status == "no_failures":
                        logger.info("  -> CI is green")
                    elif cifix_result.status == "fixed":
                        logger.info(f"  -> Fixed: {cifix_result.summary}")
                    elif cifix_result.status == "skipped":
                        logger.info(f"  -> Skipped: {cifix_result.summary or cifix_result.error}")
                    else:
                        logger.warning(f"  -> Fix failed: {cifix_result.error or cifix_result.summary}")
                except Exception as e:
                    logger.warning(f"  -> CI fix agent error (non-fatal): {e}")

            # 1. Analytics
            logger.info("[1/9] Analytics Agent analyzing past performance...")
            analytics_report = self.analytics.run(run_id)
            logger.info(f"  -> {analytics_report.total_posts} posts analyzed")

            # 2. Reflection Agent (analyze past performance, build memories)
            logger.info("[2/11] Reflection Agent analyzing patterns...")
            reflection_report = self.reflection.run(run_id, client_id=client_id)
            logger.info(
                f"  -> Calibration: {reflection_report.confidence_bias}, "
                f"{len(reflection_report.new_memories)} new memories, "
                f"{len(reflection_report.recommendations)} recommendations"
            )

            # 3. Gather trends (parallel-safe, no LLM)
            logger.info("[3/11] Gathering trending topics...")
            trending = self.gather_trends(client_id)

            # 3.5. Performance insights for CEO (prompt tuner)
            from ortobahn.prompt_tuner import get_performance_insights

            performance_insights = get_performance_insights(self.db, client_id=client_id)

            # 4. CEO
            logger.info("[4/11] CEO Agent setting strategy...")
            strategy = self.ceo.run(
                run_id,
                analytics_report=analytics_report,
                trending=trending,
                client=client,
                performance_insights=performance_insights,
                reflection_report=reflection_report,
            )
            logger.info(f"  -> Themes: {strategy.themes}")

            # 5. Strategist
            logger.info("[5/11] Strategist Agent planning content...")
            content_plan = self.strategist.run(run_id, strategy=strategy, trending=trending, client=client)
            # Limit to max_posts_per_cycle
            content_plan.posts = content_plan.posts[: self.settings.max_posts_per_cycle]
            logger.info(f"  -> {len(content_plan.posts)} post ideas")

            # 6. Creator
            logger.info("[6/11] Creator Agent writing posts...")
            drafts = self.creator.run(
                run_id,
                content_plan=content_plan,
                strategy=strategy,
                client=client,
                target_platforms=platforms,
                enable_self_critique=self.settings.enable_self_critique,
                critique_threshold=self.settings.creator_critique_threshold,
            )
            logger.info(f"  -> {len(drafts.posts)} drafts written")

            # 7. Publisher (skip if generate_only)
            posts_published = 0
            if generate_only:
                logger.info("[7/11] Saving drafts for review (generate-only mode)...")
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
                logger.info("[7/11] Publisher Agent posting...")
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

            # 8. CFO Agent (cost analysis)
            logger.info("[8/11] CFO Agent analyzing costs...")
            cfo_report = self.cfo.run(run_id)
            logger.info(f"  -> Cost/post: ${cfo_report.cost_per_post:.4f}, ROI: {cfo_report.roi_estimate:.1f}")

            # 9. Ops Agent (operations management)
            logger.info("[9/11] Ops Agent managing operations...")
            ops_report = self.ops.run(run_id)
            logger.info(f"  -> Actions: {len(ops_report.actions_taken)}, Pending clients: {ops_report.pending_clients}")

            # 10. Marketing Agent (only for Ortobahn self-marketing)
            if client_id == "ortobahn":
                logger.info("[10/11] Marketing Agent generating self-marketing content...")
                marketing_report = self.marketing.run(run_id)
                logger.info(
                    f"  -> Ideas: {len(marketing_report.content_ideas)}, Drafts: {len(marketing_report.draft_posts)}"
                )

            # 11. Learning Engine (pure computation, 0 LLM calls)
            logger.info("[11/11] Learning Engine processing outcomes...")
            learning_results = self.learning_engine.process_outcomes(run_id, client_id=client_id)
            logger.info(
                f"  -> Calibrations: {learning_results.get('calibrations', {}).get('new_records', 0)}, "
                f"Anomalies: {len(learning_results.get('anomalies', []))}, "
                f"Experiments concluded: {len(learning_results.get('experiments', []))}"
            )

            # Prune stale memories
            self.memory_store.prune(
                max_age_days=self.settings.memory_prune_days,
                min_confidence=0.2,
            )

        except Exception as e:
            logger.error(f"Pipeline error: {e}")
            errors.append(str(e))
            self.db.fail_pipeline_run(run_id, errors)
            raise

        # Calculate token usage from agent logs
        total_cache_creation = 0
        total_cache_read = 0
        logs = self.db.get_recent_agent_logs(limit=10)
        for log in logs:
            if log.get("run_id") == run_id:
                total_input_tokens += log.get("input_tokens") or 0
                total_output_tokens += log.get("output_tokens") or 0
                total_cache_creation += log.get("cache_creation_input_tokens") or 0
                total_cache_read += log.get("cache_read_input_tokens") or 0

        self.db.complete_pipeline_run(
            run_id,
            posts_published=posts_published,
            errors=errors if errors else None,
            total_input_tokens=total_input_tokens,
            total_output_tokens=total_output_tokens,
            total_cache_creation_tokens=total_cache_creation,
            total_cache_read_tokens=total_cache_read,
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
