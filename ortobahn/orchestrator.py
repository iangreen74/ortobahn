"""Pipeline orchestrator - wires all agents together."""

from __future__ import annotations

import logging
import time
import uuid

from ortobahn.agents.analytics import AnalyticsAgent
from ortobahn.agents.article_writer import ArticleWriterAgent
from ortobahn.agents.ceo import CEOAgent
from ortobahn.agents.cfo import CFOAgent
from ortobahn.agents.cifix import CIFixAgent
from ortobahn.agents.creator import CreatorAgent
from ortobahn.agents.cto import CTOAgent
from ortobahn.agents.engagement import EngagementAgent
from ortobahn.agents.legal import LegalAgent
from ortobahn.agents.marketing import MarketingAgent
from ortobahn.agents.ops import OpsAgent
from ortobahn.agents.publisher import PublisherAgent
from ortobahn.agents.reflection import ReflectionAgent
from ortobahn.agents.security import SecurityAgent
from ortobahn.agents.sre import SREAgent
from ortobahn.agents.strategist import StrategistAgent
from ortobahn.agents.support import SupportAgent
from ortobahn.cadence import CadenceOptimizer
from ortobahn.config import Settings
from ortobahn.db import create_database
from ortobahn.integrations.bluesky import BlueskyClient
from ortobahn.integrations.linkedin import LinkedInClient
from ortobahn.integrations.newsapi_client import get_trending_headlines
from ortobahn.integrations.reddit import RedditClient
from ortobahn.integrations.rss import fetch_feeds
from ortobahn.integrations.trends import get_trending_searches
from ortobahn.integrations.twitter import TwitterClient
from ortobahn.learning import LearningEngine
from ortobahn.memory import MemoryStore
from ortobahn.models import Client, DirectiveCategory, Platform, TrendingTopic
from ortobahn.post_feedback import PostFeedbackLoop
from ortobahn.predictive_timing import TopicVelocityTracker
from ortobahn.publish_recovery import ArticlePublishRecoveryManager, PublishRecoveryManager
from ortobahn.serialization import SeriesManager
from ortobahn.shared_insights import (
    CONTENT_TREND,
    COST_ANOMALY,
    DEPLOY_HEALTH,
    PLATFORM_ISSUE,
    SharedInsightBus,
)
from ortobahn.style_evolution import StyleEvolution
from ortobahn.webhooks import (
    EVENT_PIPELINE_COMPLETED,
    EVENT_PIPELINE_FAILED,
    EVENT_POST_FAILED,
    EVENT_POST_PUBLISHED,
    dispatch_event,
)

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

        self.reddit = None
        if settings.has_reddit():
            self.reddit = RedditClient(
                client_id=settings.reddit_client_id,
                client_secret=settings.reddit_client_secret,
                username=settings.reddit_username,
                password=settings.reddit_password,
            )

        # Common agent kwargs (includes Bedrock settings when enabled)
        _api_key = settings.anthropic_api_key
        _model = settings.claude_model
        _bedrock = settings.use_bedrock
        _region = settings.bedrock_region

        # Initialize agents
        self.analytics = AnalyticsAgent(
            self.db,
            _api_key,
            _model,
            bluesky_client=self.bluesky,
            twitter_client=self.twitter,
            linkedin_client=self.linkedin,
            reddit_client=self.reddit,
            use_bedrock=_bedrock,
            bedrock_region=_region,
        )
        self.ceo = CEOAgent(self.db, _api_key, _model, use_bedrock=_bedrock, bedrock_region=_region)
        self.ceo.thinking_budget = settings.thinking_budget_ceo
        self.strategist = StrategistAgent(self.db, _api_key, _model, use_bedrock=_bedrock, bedrock_region=_region)
        self.strategist.thinking_budget = settings.thinking_budget_strategist
        self.creator = CreatorAgent(self.db, _api_key, _model, use_bedrock=_bedrock, bedrock_region=_region)
        self.creator.thinking_budget = settings.thinking_budget_creator
        self.publisher = PublisherAgent(
            db=self.db,
            bluesky_client=self.bluesky,
            twitter_client=self.twitter,
            linkedin_client=self.linkedin,
            reddit_client=self.reddit,
            confidence_threshold=settings.post_confidence_threshold,
            post_delay_seconds=settings.post_delay_seconds,
        )
        self.sre = SREAgent(self.db, _api_key, _model, use_bedrock=_bedrock, bedrock_region=_region)
        self.cfo = CFOAgent(self.db, _api_key, _model, use_bedrock=_bedrock, bedrock_region=_region)
        self.ops = OpsAgent(self.db, _api_key, _model, use_bedrock=_bedrock, bedrock_region=_region)
        self.marketing = MarketingAgent(self.db, _api_key, _model, use_bedrock=_bedrock, bedrock_region=_region)
        self.support = SupportAgent(self.db, _api_key, _model, use_bedrock=_bedrock, bedrock_region=_region)
        self.reflection = ReflectionAgent(self.db, _api_key, _model, use_bedrock=_bedrock, bedrock_region=_region)
        self.reflection.thinking_budget = settings.thinking_budget_reflection
        self.cifix = (
            CIFixAgent(self.db, _api_key, _model, use_bedrock=_bedrock, bedrock_region=_region)
            if settings.cifix_enabled
            else None
        )
        self.cto = (
            CTOAgent(self.db, _api_key, _model, use_bedrock=_bedrock, bedrock_region=_region)
            if settings.cto_enabled
            else None
        )
        if self.cto:
            self.cto.thinking_budget = settings.thinking_budget_cto
        self.security = SecurityAgent(self.db, _api_key, _model, use_bedrock=_bedrock, bedrock_region=_region)
        self.security.thinking_budget = getattr(settings, "thinking_budget_security", 8000)
        self.legal = LegalAgent(self.db, _api_key, _model, use_bedrock=_bedrock, bedrock_region=_region)
        self.legal.thinking_budget = getattr(settings, "thinking_budget_legal", 10000)
        self.memory_store = MemoryStore(self.db)
        self.learning_engine = LearningEngine(self.db, self.memory_store)
        self.article_writer = ArticleWriterAgent(
            self.db, _api_key, _model, use_bedrock=_bedrock, bedrock_region=_region
        )
        self.article_writer.thinking_budget = settings.thinking_budget_article_writer

        # Innovation modules
        self.engagement = (
            EngagementAgent(
                self.db,
                _api_key,
                _model,
                bluesky_client=self.bluesky,
                max_replies_per_cycle=settings.engagement_max_replies,
                reply_confidence_threshold=settings.engagement_confidence_threshold,
                use_bedrock=_bedrock,
                bedrock_region=_region,
            )
            if settings.engagement_enabled
            else None
        )
        self.topic_tracker = TopicVelocityTracker(self.db) if settings.predictive_timing_enabled else None
        self.series_manager = SeriesManager(self.db) if settings.serialization_enabled else None
        self.style_evolution = StyleEvolution(self.db) if settings.style_evolution_enabled else None

        # Intelligence modules
        self.cadence_optimizer = CadenceOptimizer(self.db) if settings.dynamic_cadence_enabled else None
        self.post_feedback = (
            PostFeedbackLoop(
                self.db,
                self.memory_store,
                bluesky_client=self.bluesky,
                twitter_client=self.twitter,
                linkedin_client=self.linkedin,
                reddit_client=self.reddit,
            )
            if settings.post_feedback_enabled
            else None
        )
        self.publisher._recovery_manager = (
            PublishRecoveryManager(self.db, self.memory_store, max_retries=settings.publish_max_retries)
            if settings.publish_retry_enabled
            else None
        )

        # Cross-agent shared insight bus
        self.insight_bus = SharedInsightBus(self.db)

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

                # Verify the post actually exists on the platform
                # Returns True (found), False (not found), or None (inconclusive)
                # Brief delay for eventual consistency before verification
                time.sleep(2)
                verified = True
                if hasattr(publisher_client, "verify_post_exists") and uri:
                    result = publisher_client.verify_post_exists(uri)
                    if result is False:
                        # Retry once after a longer delay — platform may still be propagating
                        logger.info(f"Post not found on first check, retrying in 5s: {post['id'][:8]}")
                        time.sleep(5)
                        result = publisher_client.verify_post_exists(uri)
                    if result is False:
                        verified = False
                        logger.warning(f"Post verification failed after retry for approved post {post['id'][:8]}")
                    elif result is None:
                        logger.info(f"Post verification inconclusive for {post['id'][:8]}, trusting post succeeded")

                if verified:
                    self.db.update_post_published(post["id"], uri, platform_id)
                    published_count += 1
                    logger.info(f"Published approved post {post['id'][:8]} to {platform_str}")
                    dispatch_event(
                        self.db,
                        post.get("client_id", "default"),
                        EVENT_POST_PUBLISHED,
                        {"post_id": post["id"], "platform": platform_str, "text": post["text"][:500]},
                    )
                else:
                    self.db.update_post_failed(
                        post["id"],
                        "Post verification failed — not found on platform",
                    )
                    logger.error(f"Approved post {post['id'][:8]} failed verification")
                    dispatch_event(
                        self.db,
                        post.get("client_id", "default"),
                        EVENT_POST_FAILED,
                        {"post_id": post["id"], "error": "Post verification failed"},
                    )
            except Exception as e:
                self.db.update_post_failed(post["id"], str(e))
                logger.error(f"Failed to publish approved post {post['id'][:8]}: {e}")
                dispatch_event(
                    self.db,
                    post.get("client_id", "default"),
                    EVENT_POST_FAILED,
                    {"post_id": post["id"], "error": str(e)},
                )

        return published_count

    def publish_approved_articles(self, client_id: str = "default") -> int:
        """Publish any articles in 'approved' status. Returns count published."""
        approved = self.db.get_approved_articles(client_id=client_id)
        if not approved:
            return 0

        published_count = 0
        for article in approved:
            article_id = article["id"]
            art_client_id = article.get("client_id", client_id)

            if self.dry_run:
                logger.info(f"[DRY RUN] Would publish approved article {article_id[:8]}")
                continue

            try:
                pub_results = self._publish_article(article_id, art_client_id)
                if any(r["status"] == "published" for r in pub_results):
                    self.db.execute(
                        "UPDATE articles SET status='published', updated_at=CURRENT_TIMESTAMP WHERE id=?",
                        (article_id,),
                        commit=True,
                    )
                    published_count += 1
                    logger.info(f"Published approved article {article_id[:8]} to {len(pub_results)} platform(s)")
                elif not pub_results:
                    logger.info(f"No article platforms configured for article {article_id[:8]}, skipping")
                else:
                    logger.warning(f"All platforms failed for approved article {article_id[:8]}")
            except Exception as e:
                logger.error(f"Failed to publish approved article {article_id[:8]}: {e}")

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
        platforms_override: list[Platform] | None = None,
        _resume_run_id: str | None = None,
        _skip_phases: set[str] | None = None,
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
        errors = []
        total_input_tokens = 0
        total_output_tokens = 0
        articles_generated = 0

        # Load client from DB
        client_data = self.db.get_client(client_id)
        client = Client(**client_data) if client_data else None
        platforms = target_platforms or [Platform.GENERIC]

        # Per-platform scheduling: only publish to platforms that are due
        publish_platforms = platforms_override if platforms_override else platforms

        # Budget guard: skip paused or credential_issue clients (no run recorded)
        if client_data and client_data.get("status") in ("paused", "credential_issue"):
            reason = "budget exceeded" if client_data.get("status") == "paused" else "credential issue"
            logger.info(f"Client {client_id} is {client_data.get('status')} ({reason}). Skipping cycle.")
            return {
                "run_id": run_id,
                "posts_published": 0,
                "total_drafts": 0,
                "articles_generated": 0,
                "articles_published": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "errors": [f"client_{client_data.get('status')}"],
            }

        # Check trial expiry before subscription guard
        if client_data and not client_data.get("internal"):
            self.db.check_and_expire_trial(client_id)
            client_data = self.db.get_client(client_id)

        # Subscription guard: skip non-internal clients without active subscription (no run recorded)
        if (
            client_data
            and not client_data.get("internal")
            and client_data.get("subscription_status") not in ("active", "trialing")
        ):
            logger.info(
                f"Client {client_id} has no active subscription (status={client_data.get('subscription_status')}). Skipping."
            )
            return {
                "run_id": run_id,
                "posts_published": 0,
                "total_drafts": 0,
                "articles_generated": 0,
                "articles_published": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "errors": ["no_active_subscription"],
            }

        # Guards passed — record the pipeline run
        if _resume_run_id:
            run_id = _resume_run_id
            self.db.execute(
                "UPDATE pipeline_runs SET status = 'running', failed_phase = NULL WHERE id = ?",
                (run_id,),
                commit=True,
            )
        else:
            self.db.start_pipeline_run(run_id, mode="single", client_id=client_id)

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
            self.publisher.reddit = tenant_clients.get("reddit") or self.reddit
            self.analytics.reddit = tenant_clients.get("reddit") or self.reddit

        logger.info(f"=== Pipeline cycle {run_id[:8]} started (client={client_id}) ===")

        # Backup database before cycle (SQLite only — RDS has automated backups)
        if self.settings.backup_enabled and not self.settings.database_url:
            from ortobahn.backup import backup_database

            backup_database(self.settings.db_path, self.settings.backup_dir, self.settings.backup_max_count)

        # Publish any previously approved drafts first
        approved_published = self.publish_approved_drafts(client_id=client_id)
        if approved_published:
            logger.info(f"Published {approved_published} previously approved drafts")

        # Publish any previously approved articles
        articles_published = self.publish_approved_articles(client_id=client_id)
        if articles_published:
            logger.info(f"Published {articles_published} previously approved articles")

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
                    "articles_generated": 0,
                    "articles_published": articles_published,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "errors": [i.message for i in preflight_result.blocking_issues],
                }
            # Log warnings but continue
            for issue in preflight_result.warnings:
                logger.warning(f"Preflight warning: [{issue.component}] {issue.message}")

        try:
            # ═══════════════════════════════════════════════════════════════
            # PHASE 1: Intelligence Gathering
            # ═══════════════════════════════════════════════════════════════
            _skip_intelligence = bool(_skip_phases and "intelligence" in _skip_phases)
            if _skip_intelligence:
                logger.info("Skipping intelligence phase (resumed)")
            else:
                self.db.update_pipeline_phase(run_id, "intelligence")

            # 1.0 SRE Agent (system health check)
            logger.info("[1/14] SRE Agent checking system health...")
            sre_report = self.sre.run(run_id, slack_webhook_url=self.settings.slack_webhook_url)
            logger.info(f"  -> Health: {sre_report.health_status}, Alerts: {len(sre_report.alerts)}")

            # Publish SRE insights to shared bus
            if sre_report.health_status != "healthy":
                self.insight_bus.publish(
                    source_agent="sre",
                    insight_type=PLATFORM_ISSUE,
                    content=f"System health: {sre_report.health_status}. "
                    + "; ".join(a.message for a in sre_report.alerts[:5]),
                    confidence=0.8,
                    metadata={"health_status": sre_report.health_status, "alert_count": len(sre_report.alerts)},
                )

            # 1.1 CI Fix Agent (self-healing CI/CD)
            if self.cifix:
                logger.info("[1.5/14] CI Fix Agent checking for failures...")
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

            # 1.2 Analytics
            logger.info("[2/14] Analytics Agent analyzing past performance...")
            analytics_report = self.analytics.run(run_id)
            logger.info(f"  -> {analytics_report.total_posts} posts analyzed")

            # 1.3 Reflection Agent
            logger.info("[3/14] Reflection Agent analyzing patterns...")
            reflection_report = self.reflection.run(run_id, client_id=client_id)
            logger.info(
                f"  -> Calibration: {reflection_report.confidence_bias}, "
                f"{len(reflection_report.new_memories)} new memories, "
                f"{len(reflection_report.recommendations)} recommendations"
            )

            # 1.4 Gather trends (no LLM)
            logger.info("[4/14] Gathering trending topics...")
            trending = self.gather_trends(client_id)

            # 1.4a Predictive timing: record topics and surface emerging ones
            if self.topic_tracker:
                topic_dicts = [{"title": t.title, "source": t.source} for t in trending]
                self.topic_tracker.record_topics(topic_dicts, run_id)
                self.topic_tracker.detect_peaks()
                emerging = self.topic_tracker.get_emerging_topics()
                if emerging:
                    # Boost emerging topics by prepending them to trending list
                    for et in emerging[:5]:
                        trending.insert(
                            0,
                            TrendingTopic(
                                title=f"[EMERGING] {et['topic_title']}",
                                source=f"velocity:{et['velocity_score']:.0f}",
                                description=f"Seen {et['mention_count']}x, accelerating",
                            ),
                        )
                    logger.info(f"  -> {len(emerging)} emerging topics detected")

            # 1.5 Performance insights (prompt tuner)
            from ortobahn.prompt_tuner import get_performance_insights

            performance_insights = get_performance_insights(self.db, client_id=client_id)

            # 1.6 Support Agent (moved before CEO so report feeds into executive decisions)
            logger.info("[5/14] Support Agent checking client health...")
            support_report = self.support.run(run_id)
            logger.info(f"  -> Tickets: {len(support_report.tickets)}, At-risk: {len(support_report.at_risk_clients)}")

            # 1.7 Security Agent
            logger.info("[6/14] Security Agent assessing threats...")
            try:
                security_report = self.security.run(run_id)
                logger.info(
                    f"  -> Threat level: {security_report.threat_level}, Threats: {len(security_report.threats_detected)}"
                )
            except Exception as e:
                logger.warning(f"  -> Security agent error (non-fatal): {e}")
                security_report = None

            # 1.8 Legal Agent
            logger.info("[7/14] Legal Agent reviewing compliance...")
            try:
                legal_report = self.legal.run(run_id, client=client)
                logger.info(
                    f"  -> Docs: {len(legal_report.documents_generated)}, Gaps: {len(legal_report.compliance_gaps)}"
                )
            except Exception as e:
                logger.warning(f"  -> Legal agent error (non-fatal): {e}")
                legal_report = None

            if not _skip_intelligence:
                self.db.complete_pipeline_phase(run_id, "intelligence")

            # ═══════════════════════════════════════════════════════════════
            # PHASE 2: Executive Decision-Making
            # ═══════════════════════════════════════════════════════════════
            _skip_decision = bool(_skip_phases and "decision" in _skip_phases)
            if _skip_decision:
                logger.info("Skipping decision phase (resumed)")
            else:
                self.db.update_pipeline_phase(run_id, "decision")

            # Gather cross-agent insights for CEO
            shared_insights_summary = self.insight_bus.summarize()

            logger.info("[8/14] CEO Agent making executive decisions...")
            ceo_report = self.ceo.run(
                run_id,
                analytics_report=analytics_report,
                trending=trending,
                client=client,
                performance_insights=performance_insights,
                reflection_report=reflection_report,
                sre_report=sre_report,
                support_report=support_report,
                security_report=security_report,
                legal_report=legal_report,
                shared_insights=shared_insights_summary,
            )
            strategy = ceo_report.strategy
            logger.info(f"  -> Themes: {strategy.themes}, Directives: {len(ceo_report.directives)}")

            # Process executive directives
            if ceo_report.directives:
                self._process_directives(run_id, ceo_report.directives, client_id)

            # 2.1 Dynamic cadence (adjust max posts based on engagement trends)
            max_posts = self.settings.max_posts_per_cycle
            if self.cadence_optimizer:
                try:
                    max_posts = self.cadence_optimizer.calculate_optimal_posts(client_id, current_max=max_posts)
                    if max_posts != self.settings.max_posts_per_cycle:
                        logger.info(f"  -> Dynamic cadence: {self.settings.max_posts_per_cycle} -> {max_posts} posts")
                except Exception as e:
                    logger.warning(f"  -> Cadence optimizer error (non-fatal): {e}")

            if not _skip_decision:
                self.db.complete_pipeline_phase(run_id, "decision")

            # ═══════════════════════════════════════════════════════════════
            # PHASE 3: Content Execution
            # ═══════════════════════════════════════════════════════════════
            _skip_execution = bool(_skip_phases and "execution" in _skip_phases)
            if _skip_execution:
                logger.info("Skipping execution phase (resumed)")
            else:
                self.db.update_pipeline_phase(run_id, "execution")

            # 3.1 Strategist
            logger.info("[9/14] Strategist Agent planning content...")
            content_plan = self.strategist.run(run_id, strategy=strategy, trending=trending, client=client)
            content_plan.posts = content_plan.posts[:max_posts]
            logger.info(f"  -> {len(content_plan.posts)} post ideas")

            # 3.1a Style evolution: ensure experiment is running
            style_context = ""
            if self.style_evolution:
                self.style_evolution.ensure_active_experiment(client_id, run_id)
                style_context = self.style_evolution.get_experiment_context(client_id)

            # 3.1b Serialization: get series context
            series_context = ""
            if self.series_manager:
                series_context = self.series_manager.get_series_context(client_id)

            # Calibration feedback for Creator confidence scoring
            calibration_context = ""
            try:
                from ortobahn.calibration_adapter import get_calibration_context

                calibration_context = get_calibration_context(self.db, client_id)
            except Exception as e:
                logger.warning("  -> Calibration adapter error (non-fatal): %s", e)

            # 3.2 Creator
            logger.info("[10/14] Creator Agent writing posts...")
            drafts = self.creator.run(
                run_id,
                content_plan=content_plan,
                strategy=strategy,
                client=client,
                target_platforms=publish_platforms,
                enable_self_critique=self.settings.enable_self_critique,
                critique_threshold=self.settings.creator_critique_threshold,
                style_context=(style_context + "\n" + calibration_context).strip()
                if calibration_context
                else style_context,
                series_context=series_context,
            )
            logger.info(f"  -> {len(drafts.posts)} drafts written")

            # 3.2a Style evolution: tag A/B pairs in drafts
            if self.style_evolution and style_context:
                ab_a = [d for d in drafts.posts if d.ab_group == "A"]
                ab_b = [d for d in drafts.posts if d.ab_group == "B"]
                if ab_a and ab_b:
                    logger.info(f"  -> A/B variants detected: {len(ab_a)}A / {len(ab_b)}B")

            # 3.3 Publisher
            posts_published = 0
            if generate_only:
                logger.info("[11/14] Saving drafts for review (generate-only mode)...")
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
                            ab_group=draft.ab_group,
                            series_id=draft.series_id,
                        )
                logger.info(f"  -> {len(drafts.posts)} drafts saved for review")
            else:
                logger.info("[11/14] Publisher Agent posting...")
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

            # 3.3a Style evolution: tag A/B pairs in saved posts
            if self.style_evolution and style_context:
                try:
                    exp = self.style_evolution.get_active_experiment(client_id)
                    if exp:
                        run_posts = self.db.fetchall(
                            "SELECT id, ab_group FROM posts WHERE run_id = ? AND ab_group IS NOT NULL",
                            (run_id,),
                        )
                        a_posts = [p for p in run_posts if p["ab_group"] == "A"]
                        b_posts = [p for p in run_posts if p["ab_group"] == "B"]
                        pairs_tagged = min(len(a_posts), len(b_posts))
                        for i in range(pairs_tagged):
                            self.style_evolution.tag_post_pair(a_posts[i]["id"], b_posts[i]["id"], exp["id"])
                        if pairs_tagged:
                            logger.info(f"  -> Tagged {pairs_tagged} A/B pair(s) for experiment {exp['id']}")
                except Exception as e:
                    logger.warning(f"  -> A/B pair tagging error (non-fatal): {e}")

            # 3.3b Serialization: advance series for series-linked posts
            if self.series_manager:
                try:
                    series_posts = self.db.fetchall(
                        "SELECT id, series_id FROM posts WHERE run_id = ? AND series_id IS NOT NULL",
                        (run_id,),
                    )
                    for sp in series_posts:
                        self.series_manager.advance_series(sp["series_id"], sp["id"])
                except Exception as e:
                    logger.warning(f"  -> Series advancement error (non-fatal): {e}")

            # 3.4 Engagement Agent (autonomous replies)
            if self.engagement:
                logger.info("[11.5/14] Engagement Agent monitoring conversations...")
                try:
                    # Update bluesky client for tenant
                    self.engagement.bluesky = self.publisher.bluesky
                    engagement_result = self.engagement.run(
                        run_id,
                        client_id=client_id,
                        dry_run=self.dry_run,
                    )
                    logger.info(
                        f"  -> Checked {engagement_result.notifications_checked} notifications, "
                        f"posted {engagement_result.replies_posted} replies"
                    )
                except Exception as e:
                    logger.warning(f"  -> Engagement agent error (non-fatal): {e}")

            # 3.5 Post Feedback Loop (real-time engagement check)
            if self.post_feedback and not generate_only:
                try:
                    logger.info(
                        f"[11.7/14] Waiting {self.settings.post_feedback_delay_seconds}s for early engagement..."
                    )
                    time.sleep(self.settings.post_feedback_delay_seconds)
                    # Update platform clients for tenant
                    self.post_feedback.bluesky = self.publisher.bluesky
                    self.post_feedback.twitter = self.publisher.twitter
                    self.post_feedback.linkedin = self.publisher.linkedin
                    self.post_feedback.reddit = self.publisher.reddit
                    fb = self.post_feedback.check_recent_posts(run_id, client_id)
                    logger.info(
                        f"  -> Checked {fb['posts_checked']} posts: "
                        f"{fb['resonating']} resonating, {fb['silent']} silent, {fb['viral']} viral"
                    )
                except Exception as e:
                    logger.warning(f"  -> Post feedback error (non-fatal): {e}")

            # ═══════════════════════════════════════════════════════════════
            # PHASE 3.6: Article Generation & Publishing
            # ═══════════════════════════════════════════════════════════════
            if client_data and client_data.get("article_enabled"):
                try:
                    # Check article frequency — only generate if enough time has passed
                    should_generate = True
                    freq = client_data.get("article_frequency", "weekly")
                    freq_hours = {"daily": 24, "weekly": 168, "biweekly": 336, "monthly": 720}.get(freq, 168)
                    last_article = self.db.get_last_article_time(client_id)
                    if last_article:
                        from datetime import datetime as _dt
                        from datetime import timezone as _tz

                        from ortobahn.db import to_datetime as _to_dt

                        last_art_dt = _to_dt(last_article)
                        if last_art_dt.tzinfo is None:
                            last_art_dt = last_art_dt.replace(tzinfo=_tz.utc)
                        hours_since = (_dt.now(_tz.utc) - last_art_dt).total_seconds() / 3600
                        if hours_since < freq_hours:
                            should_generate = False
                            logger.info(
                                f"  -> Article generation skipped: {hours_since:.0f}h since last "
                                f"(frequency={freq}, threshold={freq_hours}h)"
                            )

                    if should_generate:
                        logger.info("[11.9/14] Article Writer generating long-form content...")
                        article_result = self.run_article_cycle(client_id=client_id)
                        if article_result["status"] == "success":
                            articles_generated = 1
                            logger.info(
                                f"  -> Article: '{article_result.get('title', '?')[:60]}' "
                                f"({article_result.get('word_count', 0)}w)"
                            )
                        elif article_result["status"] != "skipped":
                            logger.warning(f"  -> Article generation: {article_result.get('error', 'unknown')}")
                except Exception as e:
                    logger.warning(f"  -> Article generation error (non-fatal): {e}")

            if not _skip_execution:
                self.db.complete_pipeline_phase(run_id, "execution")

            # ═══════════════════════════════════════════════════════════════
            # PHASE 4: Operations & Learning
            # ═══════════════════════════════════════════════════════════════
            _skip_operations = bool(_skip_phases and "operations" in _skip_phases)
            if _skip_operations:
                logger.info("Skipping operations phase (resumed)")
            else:
                self.db.update_pipeline_phase(run_id, "operations")

            # 4.1 CFO Agent
            logger.info("[12/14] CFO Agent analyzing costs...")
            cfo_report = self.cfo.run(run_id)
            logger.info(f"  -> Cost/post: ${cfo_report.cost_per_post:.4f}, ROI: {cfo_report.roi_estimate:.1f}")

            # Publish cost anomaly insight if budget warning
            if cfo_report.budget_status not in ("within_budget", "no_data"):
                self.insight_bus.publish(
                    source_agent="cfo",
                    insight_type=COST_ANOMALY,
                    content=f"Budget status: {cfo_report.budget_status}. "
                    f"Cost/post: ${cfo_report.cost_per_post:.4f}, ROI: {cfo_report.roi_estimate:.1f}",
                    confidence=0.8,
                    metadata={
                        "budget_status": cfo_report.budget_status,
                        "cost_per_post": cfo_report.cost_per_post,
                        "roi": cfo_report.roi_estimate,
                    },
                )

            # 4.2 Ops Agent
            logger.info("[13/14] Ops Agent managing operations...")
            ops_report = self.ops.run(run_id)
            logger.info(f"  -> Actions: {len(ops_report.actions_taken)}, Pending clients: {ops_report.pending_clients}")

            # 4.3 Marketing Agent (only for Ortobahn self-marketing)
            if client_id == "ortobahn":
                marketing_report = self.marketing.run(run_id)
                logger.info(
                    f"  Marketing: {len(marketing_report.content_ideas)} ideas, {len(marketing_report.draft_posts)} drafts"
                )

            # 4.4 Learning Engine (pure computation, 0 LLM calls)
            logger.info("[14/14] Learning Engine processing outcomes...")
            learning_results = self.learning_engine.process_outcomes(run_id, client_id=client_id)
            logger.info(
                f"  -> Calibrations: {learning_results.get('calibrations', {}).get('new_records', 0)}, "
                f"Anomalies: {len(learning_results.get('anomalies', []))}, "
                f"Experiments concluded: {len(learning_results.get('experiments', []))}"
            )

            # Publish content trend insights for high performers
            anomalies = learning_results.get("anomalies", [])
            high_performers = [a for a in anomalies if a.get("type") == "high_performer"]
            if high_performers:
                self.insight_bus.publish(
                    source_agent="learning_engine",
                    insight_type=CONTENT_TREND,
                    content=f"{len(high_performers)} high-performing post(s) detected this cycle. "
                    + "; ".join(
                        f"post {hp.get('post_id', '?')[:8]} ({hp.get('engagement', 0)} eng)"
                        for hp in high_performers[:5]
                    ),
                    confidence=0.85,
                    metadata={"high_performer_count": len(high_performers)},
                )

            # Slack notification for anomalies
            if anomalies and self.settings.slack_webhook_url:
                try:
                    from ortobahn.integrations.slack import send_slack_message_deduped

                    high_performers = [a for a in anomalies if a.get("type") == "high_performer"]
                    if high_performers:
                        send_slack_message_deduped(
                            self.settings.slack_webhook_url,
                            f":chart_with_upwards_trend: Viral content detected! {len(high_performers)} post(s) performing above 3x average.",
                            fingerprint=f"anomaly-{client_id}-{run_id[:8]}",
                            cooldown_minutes=60,
                        )
                except Exception:
                    pass

            # Prune stale memories
            self.memory_store.prune(
                max_age_days=self.settings.memory_prune_days,
                min_confidence=0.2,
            )

            # Clean up old topic velocity data
            if self.topic_tracker:
                self.topic_tracker.cleanup_old_topics(max_age_days=30)

            # ═══════════════════════════════════════════
            # PHASE 4.5: Autonomous Engineering (CTO)
            # ═══════════════════════════════════════════
            if self.cto:
                pending_tasks = self.db.get_engineering_tasks(status="backlog", limit=1)
                if pending_tasks:
                    logger.info("[14.5/14] CTO Agent processing engineering tasks...")
                    try:
                        cto_result = self.cto.run(run_id)
                        logger.info(
                            "  -> CTO: %s (task=%s)",
                            cto_result.status,
                            getattr(cto_result, "task_id", "none")[:8]
                            if getattr(cto_result, "task_id", None)
                            else "none",
                        )
                    except Exception as e:
                        logger.warning("  -> CTO agent error (non-fatal): %s", e)
                else:
                    logger.info("[14.5/14] CTO Agent: no backlog tasks")

            if not _skip_operations:
                self.db.complete_pipeline_phase(run_id, "operations")

        except Exception as e:
            import traceback

            # Record which phase failed
            phase_row = self.db.fetchone("SELECT current_phase FROM pipeline_runs WHERE id = ?", (run_id,))
            failed_phase = phase_row["current_phase"] if phase_row and phase_row["current_phase"] else "unknown"
            self.db.fail_pipeline_phase(run_id, failed_phase, [str(e)])

            logger.error(f"Pipeline error: {e}\n{traceback.format_exc()}")
            errors.append(str(e))
            self.db.fail_pipeline_run(run_id, errors)
            dispatch_event(
                self.db,
                client_id,
                EVENT_PIPELINE_FAILED,
                {"run_id": run_id, "error": str(e)},
            )
            # Slack notification for pipeline failure
            if self.settings.slack_webhook_url:
                try:
                    from ortobahn.integrations.slack import send_slack_message_deduped

                    send_slack_message_deduped(
                        self.settings.slack_webhook_url,
                        f":rotating_light: Pipeline {run_id[:8]} FAILED: {str(e)[:200]}",
                        fingerprint=f"pipeline-fail-{client_id}",
                        cooldown_minutes=30,
                    )
                except Exception:
                    pass  # Slack notification is best-effort
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

        # Publish pipeline deploy health insight
        self.insight_bus.publish(
            source_agent="pipeline",
            insight_type=DEPLOY_HEALTH,
            content=f"Pipeline {run_id[:8]} completed: {posts_published} posts published, "
            f"{len(errors)} errors, {total_input_tokens + total_output_tokens} tokens used",
            confidence=0.9 if not errors else 0.5,
            metadata={
                "run_id": run_id,
                "posts_published": posts_published,
                "error_count": len(errors),
            },
        )

        dispatch_event(
            self.db,
            client_id,
            EVENT_PIPELINE_COMPLETED,
            {"run_id": run_id, "posts_published": posts_published},
        )

        # Slack notification for pipeline completion
        if self.settings.slack_webhook_url:
            try:
                from ortobahn.integrations.slack import send_slack_message

                send_slack_message(
                    self.settings.slack_webhook_url,
                    f":white_check_mark: Pipeline {run_id[:8]} completed: {posts_published} posts published",
                )
            except Exception:
                pass  # Slack notification is best-effort

        logger.info(f"=== Pipeline cycle {run_id[:8]} completed: {posts_published} posts published ===")

        return {
            "run_id": run_id,
            "posts_published": posts_published,
            "total_drafts": len(drafts.posts),
            "articles_generated": articles_generated,
            "articles_published": articles_published,
            "input_tokens": total_input_tokens,
            "output_tokens": total_output_tokens,
            "errors": errors,
        }

    def resume_cycle(self, client_id: str = "default") -> dict | None:
        """Resume a previously failed pipeline run, skipping completed phases."""
        import json

        resumable = self.db.get_resumable_run(client_id)
        if not resumable:
            return None

        run_id = resumable["id"]
        completed = json.loads(resumable["completed_phases"] or "[]")

        logger.info(f"Resuming pipeline run {run_id[:8]} from after phase: {completed[-1] if completed else 'start'}")

        # Reset status to running
        self.db.execute(
            "UPDATE pipeline_runs SET status = 'running', failed_phase = NULL WHERE id = ?",
            (run_id,),
            commit=True,
        )

        return self.run_cycle(
            client_id=client_id,
            _resume_run_id=run_id,
            _skip_phases=set(completed),
        )

    def _process_directives(self, run_id: str, directives: list, client_id: str) -> None:
        """Process CEO executive directives into actionable tasks."""
        max_directives = 5  # Rate limit to prevent directive storms
        processed = 0

        for directive in directives[:max_directives]:
            try:
                # Save to audit trail
                self.db.save_directive(run_id, client_id, directive.model_dump())

                # Route by category
                priority_map = {"critical": 1, "high": 2, "medium": 3, "low": 4}
                priority = priority_map.get(
                    directive.priority.value if hasattr(directive.priority, "value") else str(directive.priority), 3
                )

                category_label = (
                    directive.category.value if hasattr(directive.category, "value") else str(directive.category)
                )

                if directive.category in (DirectiveCategory.ENGINEERING, DirectiveCategory.SECURITY):
                    task_category = "infra" if directive.category == DirectiveCategory.SECURITY else "feature"
                    self.db.create_engineering_task(
                        {
                            "title": directive.directive[:200],
                            "description": f"{directive.directive}\n\nReasoning: {directive.reasoning}",
                            "priority": priority,
                            "category": task_category,
                            "created_by": "ceo_agent",
                        }
                    )
                    logger.info(f"  CEO directive -> CTO task [{category_label}]: {directive.directive[:80]}")

                elif directive.category == DirectiveCategory.LEGAL:
                    self.db.create_engineering_task(
                        {
                            "title": f"[Legal] {directive.directive[:180]}",
                            "description": f"{directive.directive}\n\nReasoning: {directive.reasoning}",
                            "priority": priority,
                            "category": "docs",
                            "created_by": "ceo_agent",
                        }
                    )
                    logger.info(f"  CEO directive -> Legal task: {directive.directive[:80]}")

                elif directive.category == DirectiveCategory.SUPPORT:
                    logger.info(f"  CEO support directive: {directive.directive[:100]}")

                elif directive.category == DirectiveCategory.OPERATIONS:
                    logger.info(f"  CEO ops directive: {directive.directive[:100]}")

                else:
                    logger.info(f"  CEO directive [{category_label}]: {directive.directive[:100]}")

                processed += 1
            except Exception as e:
                logger.warning(f"  Failed to process directive: {e}")

        if processed:
            logger.info(f"  -> Processed {processed}/{len(directives)} CEO directives")

    def run_article_cycle(self, client_id: str = "default") -> dict:
        """Generate a long-form article for a client. Returns result dict."""
        run_id = str(uuid.uuid4())

        client_data = self.db.get_client(client_id)
        if not client_data:
            return {"run_id": run_id, "status": "error", "error": "client_not_found"}

        if not client_data.get("article_enabled"):
            return {"run_id": run_id, "status": "skipped", "error": "articles_not_enabled"}

        # Subscription guard
        if not client_data.get("internal") and client_data.get("subscription_status") not in ("active", "trialing"):
            return {"run_id": run_id, "status": "skipped", "error": "no_active_subscription"}

        client = Client(**client_data)
        logger.info(f"=== Article cycle {run_id[:8]} started (client={client_id}) ===")

        # Reuse active CEO strategy
        strategy_data = self.db.get_active_strategy(client_id=client_id)
        if not strategy_data:
            # Generate a minimal strategy for article writing
            from datetime import datetime, timedelta
            from datetime import timezone as tz

            from ortobahn.models import Strategy

            strategy = Strategy(
                themes=["industry insights"],
                tone=client.brand_voice or "professional",
                goals=["thought leadership"],
                content_guidelines="Write insightful long-form content",
                posting_frequency="weekly",
                valid_until=datetime.now(tz.utc) + timedelta(days=7),
                client_id=client_id,
            )
        else:
            from ortobahn.models import Strategy

            strategy = Strategy(**strategy_data)

        # Gather context
        recent_articles = self.db.get_recent_articles(client_id, limit=10)
        top_posts = self.db.get_recent_posts_with_metrics(limit=10, client_id=client_id)

        try:
            article = self.article_writer.run(
                run_id=run_id,
                strategy=strategy,
                client=client,
                recent_articles=recent_articles,
                top_social_posts=top_posts,
            )

            # Save to DB
            article_id = self.db.save_article(
                {
                    "client_id": client_id,
                    "run_id": run_id,
                    "title": article.title,
                    "subtitle": article.subtitle,
                    "body_markdown": article.body_markdown,
                    "tags": article.tags,
                    "meta_description": article.meta_description,
                    "topic_used": article.topic_used,
                    "confidence": article.confidence,
                    "word_count": article.word_count,
                    "status": "draft",
                }
            )
            logger.info(
                f"  Article saved: '{article.title}' ({article.word_count}w, confidence={article.confidence:.2f})"
            )

            # Auto-publish if enabled and confidence is high enough
            if (
                client_data.get("auto_publish")
                and article.confidence >= self.settings.article_confidence_threshold
                and not self.dry_run
            ):
                pub_results = self._publish_article(article_id, client_id)
                if any(r["status"] == "published" for r in pub_results):
                    self.db.execute(
                        "UPDATE articles SET status='published', updated_at=CURRENT_TIMESTAMP WHERE id=?",
                        (article_id,),
                        commit=True,
                    )
                    logger.info(f"  Article auto-published to {len(pub_results)} platform(s)")

            # Update last_article_at
            self.db.update_client(client_id, {"last_article_at": "CURRENT_TIMESTAMP"})

            return {
                "run_id": run_id,
                "status": "success",
                "article_id": article_id,
                "title": article.title,
                "word_count": article.word_count,
                "confidence": article.confidence,
            }
        except Exception as e:
            logger.error(f"Article cycle failed: {e}")
            return {"run_id": run_id, "status": "error", "error": str(e)}

    def _publish_article(self, article_id: str, client_id: str) -> list[dict]:
        """Publish an article to all configured platforms. Returns list of results."""
        article = self.db.get_article(article_id)
        if not article:
            return []

        client_data = self.db.get_client(client_id)
        if not client_data:
            return []

        # Determine target platforms
        platforms_str = client_data.get("article_platforms", "")
        if not platforms_str:
            return []
        target_platforms = [p.strip() for p in platforms_str.split(",") if p.strip()]

        # Build article platform clients
        results = []
        if self.settings.secret_key:
            from ortobahn.credentials import build_article_clients

            article_clients = build_article_clients(self.db, client_id, self.settings.secret_key, self.settings)
        else:
            article_clients = {}

        from datetime import datetime
        from datetime import timezone as tz

        recovery = ArticlePublishRecoveryManager(self.db, max_retries=self.settings.publish_max_retries)

        for platform in target_platforms:
            # Map platform name to client key
            client_key = platform
            if platform == "linkedin":
                client_key = "linkedin_article"

            pub_client = article_clients.get(client_key)
            if not pub_client:
                logger.warning(f"No article client for platform '{platform}', skipping")
                pub_id = self.db.save_article_publication(
                    article_id, platform, status="skipped", error="no_credentials"
                )
                results.append({"platform": platform, "status": "skipped", "pub_id": pub_id})
                continue

            pub_id = self.db.save_article_publication(article_id, platform, status="pending")
            try:
                url, platform_id = pub_client.post(
                    title=article["title"],
                    body_markdown=article["body_markdown"],
                    tags=article.get("tags", []),
                )
                self.db.update_article_publication(
                    pub_id,
                    status="published",
                    published_url=url,
                    platform_id=platform_id,
                    published_at=datetime.now(tz.utc).isoformat(),
                )
                logger.info(f"  Published article to {platform}: {url}")
                results.append({"platform": platform, "status": "published", "url": url, "pub_id": pub_id})
            except Exception as e:
                logger.error(f"  Failed to publish article to {platform}: {e}")
                recovery_result = recovery.handle_failure(
                    pub_id=pub_id,
                    article=article,
                    platform=platform,
                    platform_client=pub_client,
                    exception=e,
                )
                if recovery_result["recovered"]:
                    logger.info(f"  Article recovery succeeded for {platform}: {recovery_result['action']}")
                    results.append(
                        {
                            "platform": platform,
                            "status": "published",
                            "url": recovery_result["url"],
                            "pub_id": pub_id,
                        }
                    )
                else:
                    results.append(
                        {
                            "platform": platform,
                            "status": "failed",
                            "error": str(e),
                            "pub_id": pub_id,
                        }
                    )

        return results

    def close(self):
        self.db.close()
