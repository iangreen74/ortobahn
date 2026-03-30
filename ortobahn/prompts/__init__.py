"""Versioned prompt templates with Jinja2, A/B testing, and injection detection."""

import re
from datetime import datetime
from typing import Any, Dict, List, Optional

import jinja2
from pydantic import BaseModel, Field
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import Session, relationship, sessionmaker

Base = declarative_base()


class PromptTemplate(Base):
    """Versioned prompt templates."""

    __tablename__ = "prompt_templates"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    template = Column(Text, nullable=False)
    description = Column(Text)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    created_by = Column(String(255))

    metrics = relationship("PromptMetric", back_populates="prompt")
    ab_tests = relationship("ABTest", back_populates="prompt")


class PromptMetric(Base):
    """Performance metrics for prompts."""

    __tablename__ = "prompt_metrics"

    id = Column(Integer, primary_key=True)
    prompt_id = Column(Integer, ForeignKey("prompt_templates.id"))
    execution_time = Column(Float)
    tokens_used = Column(Integer)
    success = Column(Boolean)
    error_message = Column(Text)
    user_rating = Column(Float)
    timestamp = Column(DateTime, default=datetime.utcnow)

    prompt = relationship("PromptTemplate", back_populates="metrics")


class ABTest(Base):
    """A/B testing metadata."""

    __tablename__ = "ab_tests"

    id = Column(Integer, primary_key=True)
    prompt_id = Column(Integer, ForeignKey("prompt_templates.id"))
    variant_name = Column(String(255), nullable=False)
    traffic_percentage = Column(Float, default=50.0)
    conversion_rate = Column(Float)
    sample_size = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)
    started_at = Column(DateTime, default=datetime.utcnow)
    ended_at = Column(DateTime)

    prompt = relationship("PromptTemplate", back_populates="ab_tests")


class PromptSchema(BaseModel):
    """Pydantic schema for prompt templates."""

    name: str
    version: int = 1
    template: str
    description: Optional[str] = None
    is_active: bool = True
    created_by: Optional[str] = None


class PromptInjectionDetector:
    """Detect prompt injection attacks."""

    INJECTION_PATTERNS = [
        r"ignore\s+(previous|above|prior)\s+instructions?",
        r"disregard\s+(previous|above|prior)",
        r"forget\s+(everything|all|previous)",
        r"you\s+are\s+now",
        r"new\s+instructions?",
        r"system\s*:\s*",
        r"<\s*\|.*?\|\s*>",
        r"\[\s*SYSTEM\s*\]",
    ]

    def __init__(self):
        self.patterns = [re.compile(p, re.IGNORECASE) for p in self.INJECTION_PATTERNS]

    def detect(self, text: str) -> Dict[str, Any]:
        """Detect potential injection attempts."""
        matches = []
        for pattern in self.patterns:
            if pattern.search(text):
                matches.append(pattern.pattern)

        return {
            "is_injection": len(matches) > 0,
            "matched_patterns": matches,
            "risk_score": min(len(matches) * 0.3, 1.0),
        }


class PromptManager:
    """Manage prompt templates with versioning and injection detection."""

    def __init__(self, db_url: str = "sqlite:///prompts.db"):
        self.engine = create_engine(db_url)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.jinja_env = jinja2.Environment(autoescape=True)
        self.injection_detector = PromptInjectionDetector()

    def create_prompt(self, prompt_data: PromptSchema, session: Session) -> PromptTemplate:
        """Create a new prompt template."""
        injection_result = self.injection_detector.detect(prompt_data.template)
        if injection_result["is_injection"]:
            raise ValueError(f"Injection detected: {injection_result['matched_patterns']}")

        prompt = PromptTemplate(**prompt_data.model_dump())
        session.add(prompt)
        session.commit()
        return prompt

    def render_prompt(self, name: str, version: Optional[int], context: Dict, session: Session) -> str:
        """Render a prompt template with context."""
        query = session.query(PromptTemplate).filter(PromptTemplate.name == name)
        if version:
            query = query.filter(PromptTemplate.version == version)
        else:
            query = query.filter(PromptTemplate.is_active == True).order_by(PromptTemplate.version.desc())

        prompt = query.first()
        if not prompt:
            raise ValueError(f"Prompt {name} v{version} not found")

        template = self.jinja_env.from_string(prompt.template)
        return template.render(**context)

    def rollback_version(self, name: str, target_version: int, session: Session) -> PromptTemplate:
        """Rollback to a previous version."""
        session.query(PromptTemplate).filter(PromptTemplate.name == name).update({"is_active": False})
        target = session.query(PromptTemplate).filter(
            PromptTemplate.name == name, PromptTemplate.version == target_version
        ).first()
        if not target:
            raise ValueError(f"Version {target_version} not found")
        target.is_active = True
        session.commit()
        return target
