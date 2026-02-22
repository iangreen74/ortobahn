"""CEO Agent - sets strategy, assesses business health, issues executive directives."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from ortobahn.agents.base import BaseAgent
from ortobahn.db import to_datetime
from ortobahn.llm import parse_json_response
from ortobahn.models import (
    AnalyticsReport,
    CEOReport,
    CFOReport,
    Client,
    ExecutiveDirective,
    LegalReport,
    OpsReport,
    Platform,
    ReflectionReport,
    SecurityReport,
    SREReport,
    Strategy,
    SupportReport,
)

if TYPE_CHECKING:
    from ortobahn.db import Database

logger = logging.getLogger("ortobahn.ceo")


def _compute_engagement_trend(db: Database, client_id: str) -> dict | None:
    """Compare average engagement this week vs last week.

    Returns a dict with direction ('rising'/'falling'/'stable') and percentage change,
    or None if there are fewer than 3 posts in either period.
    """
    now = datetime.now(timezone.utc)
    this_week_start = (now - timedelta(days=7)).isoformat()
    last_week_start = (now - timedelta(days=14)).isoformat()
    this_week_end = now.isoformat()
    last_week_end = this_week_start

    this_week_rows = db.fetchall(
        """SELECT COALESCE(m.like_count, 0) + COALESCE(m.repost_count, 0) + COALESCE(m.reply_count, 0) AS engagement
           FROM posts p
           LEFT JOIN metrics m ON p.id = m.post_id
               AND m.measured_at = (SELECT MAX(m2.measured_at) FROM metrics m2 WHERE m2.post_id = p.id)
           WHERE p.status = 'published' AND p.client_id = ? AND p.published_at > ? AND p.published_at <= ?""",
        (client_id, this_week_start, this_week_end),
    )

    last_week_rows = db.fetchall(
        """SELECT COALESCE(m.like_count, 0) + COALESCE(m.repost_count, 0) + COALESCE(m.reply_count, 0) AS engagement
           FROM posts p
           LEFT JOIN metrics m ON p.id = m.post_id
               AND m.measured_at = (SELECT MAX(m2.measured_at) FROM metrics m2 WHERE m2.post_id = p.id)
           WHERE p.status = 'published' AND p.client_id = ? AND p.published_at > ? AND p.published_at <= ?""",
        (client_id, last_week_start, last_week_end),
    )

    if len(this_week_rows) < 3 or len(last_week_rows) < 3:
        return None

    this_avg = sum(r["engagement"] for r in this_week_rows) / len(this_week_rows)
    last_avg = sum(r["engagement"] for r in last_week_rows) / len(last_week_rows)

    if last_avg == 0:
        if this_avg > 0:
            return {"direction": "rising", "percentage": 100.0}
        return {"direction": "stable", "percentage": 0.0}

    pct_change = ((this_avg - last_avg) / last_avg) * 100

    if pct_change > 5:
        direction = "rising"
    elif pct_change < -5:
        direction = "falling"
    else:
        direction = "stable"

    return {"direction": direction, "percentage": round(pct_change, 1)}


class CEOAgent(BaseAgent):
    name = "ceo"
    prompt_file = "ceo.txt"
    thinking_budget = 10_000

    def run(
        self,
        run_id: str,
        analytics_report: AnalyticsReport | None = None,
        trending: list | None = None,
        client: Client | None = None,
        performance_insights: str = "",
        reflection_report: ReflectionReport | None = None,
        sre_report: SREReport | None = None,
        support_report: SupportReport | None = None,
        cfo_report: CFOReport | None = None,
        ops_report: OpsReport | None = None,
        security_report: SecurityReport | None = None,
        legal_report: LegalReport | None = None,
        **kwargs,
    ) -> CEOReport:
        client_id = client.id if client else "default"

        # Check for existing valid strategy scoped to client
        existing = self.db.get_active_strategy(client_id=client_id)
        if existing:
            strategy = Strategy(
                themes=existing["themes"],
                tone=existing["tone"],
                goals=existing["goals"],
                content_guidelines=existing["content_guidelines"],
                posting_frequency=existing["posting_frequency"],
                valid_until=to_datetime(existing["valid_until"]),
                client_id=client_id,
            )
            self.log_decision(
                run_id=run_id,
                input_summary=f"Reusing active strategy for {client_id} (still valid)",
                output_summary=f"Themes: {', '.join(strategy.themes)}",
                reasoning="Existing strategy has not expired yet",
            )
            return CEOReport(strategy=strategy)

        # Format prompt with client context
        if client:
            system_prompt = self.format_prompt(
                client_name=client.name,
                client_description=client.description,
                client_industry=client.industry,
                client_target_audience=client.target_audience,
                client_brand_voice=client.brand_voice,
                client_website=client.website,
                client_products=client.products or "Not specified",
                client_competitive_positioning=client.competitive_positioning or "Not specified",
                client_content_pillars=client.content_pillars or "Not specified",
                client_company_story=client.company_story or "Not specified",
                available_platforms=", ".join(p.value for p in Platform if p != Platform.BLUESKY),
            )
        else:
            system_prompt = None

        # Build the user message for the LLM
        parts = [f"Current date: {datetime.now(timezone.utc).isoformat()}"]
        parts.append(f"Strategy should be valid until: {(datetime.now(timezone.utc) + timedelta(days=7)).isoformat()}")

        if analytics_report and analytics_report.total_posts > 0:
            parts.append(f"\n## Recent Performance\n{analytics_report.model_dump_json(indent=2)}")
        else:
            parts.append("\nThis is the FIRST RUN. No previous analytics. Bootstrap a strong initial strategy.")

        if trending:
            parts.append("\n## Current Trending Topics")
            for t in trending[:10]:
                parts.append(f"- [{t.source}] {t.title}: {t.description or ''}")

        if performance_insights:
            parts.append(f"\n{performance_insights}")

        # Inject engagement trend (week-over-week)
        try:
            trend = _compute_engagement_trend(self.db, client_id)
            if trend:
                parts.append(f"\nEngagement trend: {trend['direction']} ({trend['percentage']}% change week-over-week)")
        except Exception as e:
            logger.warning(f"Could not compute engagement trend: {e}")

        # Inject reflection report insights
        if reflection_report:
            parts.append("\n## Reflection Insights")
            parts.append(f"Confidence accuracy: {reflection_report.confidence_accuracy:.2f}")
            parts.append(f"Confidence bias: {reflection_report.confidence_bias}")
            if reflection_report.content_patterns:
                cp = reflection_report.content_patterns
                if cp.winning_attributes:
                    parts.append(f"Winning attributes: {', '.join(cp.winning_attributes)}")
                if cp.losing_attributes:
                    parts.append(f"Losing attributes: {', '.join(cp.losing_attributes)}")
            if reflection_report.recommendations:
                parts.append("Recommendations:")
                for rec in reflection_report.recommendations:
                    parts.append(f"- {rec}")

        # === NEW: Inject department reports ===

        if sre_report:
            parts.append("\n## System Health (SRE Department)")
            parts.append(f"Health status: {sre_report.health_status}")
            parts.append(f"Pipeline success rate: {sre_report.pipeline_success_rate:.0%}")
            parts.append(f"Estimated 24h cost: ${sre_report.estimated_cost_24h:.2f}")
            if sre_report.alerts:
                parts.append("Alerts:")
                for a in sre_report.alerts:
                    parts.append(f"- [{a.severity}] {a.component}: {a.message}")

        if support_report:
            parts.append("\n## Customer Health (Support Department)")
            parts.append(f"Clients checked: {support_report.total_clients_checked}")
            if support_report.at_risk_clients:
                parts.append(f"At-risk clients: {', '.join(support_report.at_risk_clients)}")
            if support_report.tickets:
                parts.append("Open tickets:")
                for t in support_report.tickets[:5]:
                    parts.append(f"- [{t.severity}] {t.client_id}: {t.summary}")

        if cfo_report:
            parts.append("\n## Financial (CFO Department)")
            parts.append(f"Cost per post: ${cfo_report.cost_per_post:.4f}")
            parts.append(f"ROI: {cfo_report.roi_estimate:.1f} engagements/$")
            parts.append(f"Budget status: {cfo_report.budget_status}")

        if security_report:
            parts.append("\n## Security (CISO Department)")
            parts.append(f"Threat level: {security_report.threat_level}")
            if security_report.threats_detected:
                parts.append(f"Threats: {len(security_report.threats_detected)} detected")
                for t in security_report.threats_detected[:3]:
                    parts.append(f"- [{t.severity}] {t.threat_type}: {t.details[:100]}")
            if security_report.recommendations:
                parts.append("Security recommendations:")
                for r in security_report.recommendations[:3]:
                    parts.append(f"- [{r.priority}] {r.area}: {r.recommendation[:100]}")

        if legal_report:
            parts.append("\n## Legal (General Counsel)")
            parts.append(f"Documents generated: {len(legal_report.documents_generated)}")
            if legal_report.compliance_gaps:
                parts.append("Compliance gaps:")
                for g in legal_report.compliance_gaps:
                    parts.append(f"- [{g.severity}] {g.area}: {g.description[:100]}")
            parts.append(f"Summary: {legal_report.summary[:200]}")

        if ops_report:
            parts.append("\n## Operations")
            parts.append(f"Active clients: {ops_report.active_clients}")
            parts.append(f"Pending: {ops_report.pending_clients}")
            if ops_report.actions_taken:
                parts.append(f"Actions taken: {len(ops_report.actions_taken)}")

        # Inject memory context
        memory_context = self.get_memory_context(client_id)
        if memory_context:
            parts.append(f"\n## Agent Memory\n{memory_context}")

        user_message = "\n".join(parts)
        response = self.call_llm(user_message, system_prompt=system_prompt)

        # Parse response — try CEOReport first, fall back to plain Strategy
        report = self._parse_response(response.text, client_id, run_id)

        # Save strategy to DB
        strategy_data = report.strategy.model_dump()
        strategy_data["valid_until"] = report.strategy.valid_until.isoformat()
        self.db.save_strategy(strategy_data, run_id, raw_response=response.text, client_id=client_id)

        self.log_decision(
            run_id=run_id,
            input_summary=(
                f"Analytics: {analytics_report.total_posts if analytics_report else 0} posts, "
                f"Trends: {len(trending or [])}, Client: {client_id}, "
                f"Reports: SRE={'yes' if sre_report else 'no'}, "
                f"Support={'yes' if support_report else 'no'}, "
                f"Security={'yes' if security_report else 'no'}, "
                f"Legal={'yes' if legal_report else 'no'}"
            ),
            output_summary=(
                f"Strategy: themes={report.strategy.themes}, "
                f"Directives: {len(report.directives)}, "
                f"Risks: {len(report.risk_flags)}"
            ),
            reasoning=report.business_assessment[:200]
            if report.business_assessment
            else f"Tone: {report.strategy.tone}",
            llm_response=response,
        )
        return report

    def _parse_response(self, text: str, client_id: str, run_id: str) -> CEOReport:
        """Parse LLM response as CEOReport, with fallback to plain Strategy."""
        # Try parsing as CEOReport (new format)
        try:
            report = parse_json_response(text, CEOReport)
            report.strategy.client_id = client_id
            return report
        except Exception:
            pass

        # Fallback: try parsing as plain Strategy (backward compat)
        try:
            strategy = parse_json_response(text, Strategy)
            strategy.client_id = client_id
            return CEOReport(strategy=strategy)
        except Exception:
            pass

        # Last resort: try raw JSON parsing
        try:
            raw = text.strip().strip("`").removeprefix("json").strip()
            data = json.loads(raw)

            # If it has a "strategy" key, extract it
            if "strategy" in data:
                strategy = Strategy(**data["strategy"])
                strategy.client_id = client_id
                directives = [ExecutiveDirective(**d) for d in data.get("directives", [])]
                return CEOReport(
                    strategy=strategy,
                    directives=directives,
                    business_assessment=data.get("business_assessment", ""),
                    risk_flags=data.get("risk_flags", []),
                )

            # Otherwise assume it's a flat strategy
            strategy = Strategy(**data)
            strategy.client_id = client_id
            return CEOReport(strategy=strategy)
        except Exception as e:
            logger.error(f"Failed to parse CEO response: {e}")
            # Return a minimal report so the pipeline doesn't crash
            return CEOReport(
                strategy=Strategy(
                    themes=["general marketing"],
                    tone="professional",
                    goals=["maintain presence"],
                    content_guidelines="Post relevant, on-brand content",
                    posting_frequency="2-3 posts per day",
                    valid_until=datetime.now(timezone.utc) + timedelta(days=1),
                    client_id=client_id,
                ),
                business_assessment="CEO response parsing failed — using fallback strategy",
            )
