"""Analytics Agent - reads engagement metrics and generates reports."""

from __future__ import annotations

import json

from ortobahn.agents.base import BaseAgent
from ortobahn.integrations.bluesky import BlueskyClient
from ortobahn.models import AnalyticsReport


class AnalyticsAgent(BaseAgent):
    name = "analytics"
    prompt_file = "analytics.txt"

    def __init__(
        self,
        db,
        api_key: str,
        model: str = "claude-sonnet-4-5-20250929",
        max_tokens: int = 4096,
        bluesky_client: BlueskyClient | None = None,
        twitter_client=None,
        linkedin_client=None,
        **kwargs,
    ):
        super().__init__(db, api_key, model, max_tokens, **kwargs)
        self.bluesky = bluesky_client
        self.twitter = twitter_client
        self.linkedin = linkedin_client

    def run(self, run_id: str) -> AnalyticsReport:
        # Build report from DB
        report = self.db.build_analytics_report()

        # If we have posts, refresh metrics from all platforms
        if report.total_posts > 0 and (self.bluesky or self.twitter or self.linkedin):
            self._refresh_metrics()

            # Rebuild report with fresh metrics
            report = self.db.build_analytics_report()

        # If no posts yet, return empty report
        if report.total_posts == 0:
            self.log_decision(
                run_id=run_id,
                input_summary="No posts to analyze",
                output_summary="Empty analytics report (first run)",
            )
            return report

        # Use LLM to generate narrative summary and recommendations
        posts_data = self.db.get_recent_posts_with_metrics(limit=20)
        user_message = f"""## Performance Data (last 7 days)
Total posts: {report.total_posts}
Total likes: {report.total_likes}
Total reposts: {report.total_reposts}
Total replies: {report.total_replies}
Avg engagement per post: {report.avg_engagement_per_post}

## Recent Posts with Metrics:
"""
        for p in posts_data:
            user_message += f'- "{p["text"][:100]}" | Likes: {p.get("like_count", 0)}, Reposts: {p.get("repost_count", 0)}, Replies: {p.get("reply_count", 0)}\n'

        response = self.call_llm(user_message)

        # Parse LLM analysis and merge into report
        try:
            analysis = json.loads(response.text.strip().strip("`").removeprefix("json").strip())
            report.top_themes = analysis.get("top_themes", [])
            report.summary = analysis.get("summary", report.summary)
            report.recommendations = analysis.get("recommendations", [])
        except (json.JSONDecodeError, KeyError):
            report.summary = response.text[:500]

        self.log_decision(
            run_id=run_id,
            input_summary=f"{report.total_posts} posts analyzed",
            output_summary=f"Avg engagement: {report.avg_engagement_per_post}, Summary: {report.summary[:100]}",
            reasoning=f"Recommendations: {report.recommendations}",
            llm_response=response,
        )
        return report

    def _refresh_metrics(self):
        """Fetch latest metrics from all platforms for recent posts."""
        posts = self.db.get_recent_published_posts(days=7)
        for post in posts:
            platform = post.get("platform", "generic")
            uri = post.get("platform_uri") or post.get("bluesky_uri")
            platform_id = post.get("platform_id") or post.get("bluesky_cid")
            if not uri and not platform_id:
                continue
            try:
                if platform == "bluesky" and self.bluesky and uri:
                    metrics = self.bluesky.get_post_metrics(uri)
                    self.db.save_metrics(
                        post_id=post["id"],
                        like_count=metrics.like_count,
                        repost_count=metrics.repost_count,
                        reply_count=metrics.reply_count,
                        quote_count=metrics.quote_count,
                    )
                elif platform == "twitter" and self.twitter and platform_id:
                    metrics = self.twitter.get_post_metrics(platform_id)
                    self.db.save_metrics(
                        post_id=post["id"],
                        like_count=metrics.like_count,
                        repost_count=metrics.retweet_count,
                        reply_count=metrics.reply_count,
                    )
                elif platform == "linkedin" and self.linkedin and platform_id:
                    metrics = self.linkedin.get_post_metrics(platform_id)
                    self.db.save_metrics(
                        post_id=post["id"],
                        like_count=metrics.like_count,
                        reply_count=metrics.comment_count,
                    )
            except Exception:
                pass
