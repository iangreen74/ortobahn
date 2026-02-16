"""Pydantic data models - the contracts between agents."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

# --- Enums ---


class PostType(str, Enum):
    HOT_TAKE = "hot_take"
    INSIGHT = "insight"
    QUESTION = "question"
    THREAD_STARTER = "thread_starter"
    COMMENTARY = "commentary"


class Platform(str, Enum):
    BLUESKY = "bluesky"
    TWITTER = "twitter"
    LINKEDIN = "linkedin"
    GOOGLE_ADS = "google_ads"
    INSTAGRAM = "instagram"
    GENERIC = "generic"


class ContentType(str, Enum):
    SOCIAL_POST = "social_post"
    AD_HEADLINE = "ad_headline"
    AD_DESCRIPTION = "ad_description"


class ContentStatus(str, Enum):
    DRAFT = "draft"
    APPROVED = "approved"
    PUBLISHED = "published"
    REJECTED = "rejected"
    FAILED = "failed"
    SKIPPED = "skipped"


class SubscriptionStatus(str, Enum):
    NONE = "none"
    ACTIVE = "active"
    TRIALING = "trialing"
    PAST_DUE = "past_due"
    CANCELLED = "cancelled"


# --- Platform constraints (used by Creator to enforce limits) ---

PLATFORM_CONSTRAINTS: dict[str, dict] = {
    Platform.BLUESKY: {"max_chars": 300, "hashtags": False, "tone": "casual-professional"},
    Platform.TWITTER: {"max_chars": 280, "hashtags": True, "tone": "punchy"},
    Platform.LINKEDIN: {"max_chars": 3000, "hashtags": True, "tone": "professional"},
    Platform.GOOGLE_ADS: {"max_chars": 90, "hashtags": False, "tone": "action-oriented"},
    Platform.INSTAGRAM: {"max_chars": 2200, "hashtags": True, "tone": "visual-friendly"},
    Platform.GENERIC: {"max_chars": 500, "hashtags": False, "tone": "neutral"},
}


# --- Client model ---


class Client(BaseModel):
    id: str
    name: str
    description: str = ""
    industry: str = ""
    target_audience: str = ""
    brand_voice: str = ""
    website: str = ""
    active: bool = True
    products: str = ""
    competitive_positioning: str = ""
    key_messages: str = ""
    content_pillars: str = ""
    company_story: str = ""
    internal: bool = False
    subscription_status: str = "none"
    auto_publish: bool = False
    target_platforms: str = "bluesky"


# --- Trending data (fed into Strategist) ---


class TrendingTopic(BaseModel):
    title: str
    source: str  # "newsapi", "google_trends", "rss"
    description: str | None = None
    url: str | None = None


# --- CEO output ---


class Strategy(BaseModel):
    themes: list[str] = Field(..., min_length=1, max_length=5)
    tone: str
    goals: list[str]
    content_guidelines: str
    posting_frequency: str
    valid_until: datetime
    target_platforms: list[Platform] = Field(default_factory=lambda: [Platform.GENERIC])
    client_id: str = "default"


# --- Strategist output ---


class PostIdea(BaseModel):
    topic: str
    angle: str
    hook: str
    content_type: PostType
    priority: int = Field(ge=1, le=5)
    trending_source: str | None = None
    target_platforms: list[Platform] = Field(default_factory=lambda: [Platform.GENERIC])


class ContentPlan(BaseModel):
    posts: list[PostIdea]


# --- Creator output ---


class DraftPost(BaseModel):
    text: str
    source_idea: str
    reasoning: str
    confidence: float = Field(ge=0.0, le=1.0)
    platform: Platform = Platform.GENERIC
    content_type: ContentType = ContentType.SOCIAL_POST


class DraftPosts(BaseModel):
    posts: list[DraftPost]


# --- Publisher output ---


class PublishedPost(BaseModel):
    text: str
    uri: str | None = None
    cid: str | None = None
    published_at: datetime | None = None
    status: str  # "published", "failed", "skipped", "draft"
    error: str | None = None
    platform: Platform = Platform.GENERIC


class PublishedPosts(BaseModel):
    posts: list[PublishedPost]


# --- Analytics output ---


class PostPerformance(BaseModel):
    text: str
    uri: str
    like_count: int = 0
    repost_count: int = 0
    reply_count: int = 0
    total_engagement: int = 0


class AnalyticsReport(BaseModel):
    period: str = "last 7 days"
    total_posts: int = 0
    total_likes: int = 0
    total_reposts: int = 0
    total_replies: int = 0
    avg_engagement_per_post: float = 0.0
    best_post: PostPerformance | None = None
    worst_post: PostPerformance | None = None
    top_themes: list[str] = Field(default_factory=list)
    summary: str = "No data yet."
    recommendations: list[str] = Field(default_factory=list)


# --- SRE Agent output ---


class SREAlert(BaseModel):
    severity: str  # "critical", "warning", "info"
    component: str  # "pipeline", "platform_api", "database", "tokens"
    message: str


class SREReport(BaseModel):
    health_status: str = "unknown"  # "healthy", "degraded", "critical", "unknown"
    pipeline_success_rate: float = 0.0
    avg_confidence_trend: str = "stable"  # "rising", "falling", "stable"
    total_tokens_24h: int = 0
    estimated_cost_24h: float = 0.0
    platform_health: dict[str, str] = Field(default_factory=dict)
    alerts: list[SREAlert] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)


# --- CFO Agent output ---


class CFOReport(BaseModel):
    total_spend_24h: float = 0.0
    cost_per_post: float = 0.0
    cost_per_engagement: float = 0.0
    total_engagements_24h: int = 0
    roi_estimate: float = 0.0  # engagements per dollar
    budget_status: str = "within_budget"  # "within_budget", "over_budget", "under_utilized"
    recommendations: list[str] = Field(default_factory=list)
    summary: str = "No cost data yet."


# --- Ops Agent output ---


class OpsAction(BaseModel):
    action: str
    target: str
    status: str  # "completed", "pending", "failed"
    detail: str = ""


class OpsReport(BaseModel):
    pending_clients: int = 0
    active_clients: int = 0
    actions_taken: list[OpsAction] = Field(default_factory=list)
    pending_items: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    summary: str = "No operational data yet."


# --- Marketing Agent output ---


class MarketingIdea(BaseModel):
    angle: str
    hook: str
    target_platform: str = "bluesky"
    content_type: str = "social_post"


class MarketingReport(BaseModel):
    content_ideas: list[MarketingIdea] = Field(default_factory=list)
    draft_posts: list[str] = Field(default_factory=list)
    metrics_highlights: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    summary: str = "No marketing data yet."


# --- CTO Agent models ---


class TaskStatus(str, Enum):
    BACKLOG = "backlog"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"


class TaskCategory(str, Enum):
    FEATURE = "feature"
    BUGFIX = "bugfix"
    REFACTOR = "refactor"
    TEST = "test"
    INFRA = "infra"
    DOCS = "docs"


class EngineeringTask(BaseModel):
    id: str = ""
    title: str
    description: str
    priority: int = Field(default=3, ge=1, le=5)
    status: TaskStatus = TaskStatus.BACKLOG
    category: TaskCategory = TaskCategory.FEATURE
    estimated_complexity: str = "medium"


class CTOResult(BaseModel):
    task_id: str
    status: str  # "success", "failed", "skipped"
    branch_name: str = ""
    commit_sha: str = ""
    files_changed: list[str] = Field(default_factory=list)
    summary: str = ""
    error: str = ""


# --- Intelligence System models ---


class MemoryType(str, Enum):
    OBSERVATION = "observation"  # Raw fact: "posts about X got 2x engagement"
    LESSON = "lesson"  # Derived insight: "our audience prefers contrarian takes"
    PREFERENCE = "preference"  # Learned preference: "avoid corporate jargon"
    GOAL_STATE = "goal_state"  # Current KPI state: "engagement trending up 15%"


class MemoryCategory(str, Enum):
    CONTENT_PATTERN = "content_pattern"
    AUDIENCE_BEHAVIOR = "audience_behavior"
    TIMING = "timing"
    THEME_PERFORMANCE = "theme_performance"
    CALIBRATION = "calibration"
    PLATFORM_SPECIFIC = "platform_specific"


class AgentMemory(BaseModel):
    id: str = ""
    agent_name: str
    client_id: str = "default"
    memory_type: MemoryType
    category: MemoryCategory
    content: dict = Field(default_factory=dict)
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    source_run_id: str = ""
    source_post_ids: list[str] = Field(default_factory=list)
    times_reinforced: int = 1
    times_contradicted: int = 0


class ContentPatterns(BaseModel):
    high_performers: list[dict] = Field(default_factory=list)
    low_performers: list[dict] = Field(default_factory=list)
    winning_attributes: list[str] = Field(default_factory=list)
    losing_attributes: list[str] = Field(default_factory=list)


class ReflectionReport(BaseModel):
    confidence_accuracy: float = 0.0
    confidence_bias: str = "neutral"  # "overconfident", "underconfident", "neutral"
    strategy_effectiveness: dict = Field(default_factory=dict)
    content_patterns: ContentPatterns | None = None
    ab_test_updates: list[dict] = Field(default_factory=list)
    goal_progress: list[dict] = Field(default_factory=list)
    new_memories: list[AgentMemory] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    summary: str = ""


class DraftIteration(BaseModel):
    iteration: int
    text: str
    self_critique: str
    improvements_made: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class ABExperiment(BaseModel):
    id: str = ""
    client_id: str = "default"
    hypothesis: str
    variable: str
    variant_a_description: str
    variant_b_description: str
    status: str = "active"
    winner: str | None = None
    pair_count: int = 0
    min_pairs_required: int = 5


class AgentGoal(BaseModel):
    id: str = ""
    agent_name: str
    client_id: str = "default"
    metric_name: str
    target_value: float
    current_value: float = 0.0
    trend: str = "stable"
    measurement_window_days: int = 7


# --- CI/CD Self-Healing models ---


class CIFailureCategory(str, Enum):
    LINT = "lint"
    FORMAT = "format"
    TYPECHECK = "typecheck"
    TEST = "test"
    INSTALL = "install"
    UNKNOWN = "unknown"


class CIError(BaseModel):
    file_path: str = ""
    line: int | None = None
    column: int | None = None
    code: str = ""
    message: str = ""
    category: CIFailureCategory = CIFailureCategory.UNKNOWN


class CIFailure(BaseModel):
    gh_run_id: int = 0
    gh_run_url: str = ""
    job_name: str = ""
    category: CIFailureCategory = CIFailureCategory.UNKNOWN
    errors: list[CIError] = Field(default_factory=list)
    raw_logs: str = ""


class FixAttempt(BaseModel):
    strategy: str = ""
    files_changed: list[str] = Field(default_factory=list)
    llm_used: bool = False
    tokens_used: int = 0


class CIFixResult(BaseModel):
    failure: CIFailure | None = None
    status: str = "skipped"  # "fixed", "failed", "skipped", "no_failures"
    fix_attempt: FixAttempt | None = None
    branch_name: str = ""
    commit_sha: str = ""
    pr_url: str = ""
    validation_passed: bool = False
    error: str = ""
    summary: str = ""


# --- Preflight Intelligence models ---


class PreflightSeverity(str, Enum):
    BLOCKING = "blocking"
    WARNING = "warning"
    INFO = "info"


class PreflightIssue(BaseModel):
    severity: PreflightSeverity
    component: str
    message: str
    agent_name: str = ""


class PreflightResult(BaseModel):
    passed: bool = True
    issues: list[PreflightIssue] = Field(default_factory=list)
    checked_at: datetime = Field(default_factory=datetime.utcnow)
    duration_ms: float = 0.0

    @property
    def blocking_issues(self) -> list[PreflightIssue]:
        return [i for i in self.issues if i.severity == PreflightSeverity.BLOCKING]

    @property
    def warnings(self) -> list[PreflightIssue]:
        return [i for i in self.issues if i.severity == PreflightSeverity.WARNING]
