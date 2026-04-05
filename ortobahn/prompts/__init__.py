"""Versioned prompt storage and template engine."""

from __future__ import annotations

import datetime
import json
from enum import Enum
from typing import Any

from jinja2 import Environment, TemplateSyntaxError, meta
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import Column, DateTime, Integer, String, Text, UniqueConstraint, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

Base = declarative_base()


class PromptStatus(str, Enum):
    """Status of a prompt version."""

    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"


class PromptVersion(Base):
    """Database model for versioned prompts."""

    __tablename__ = "prompt_versions"
    __table_args__ = (UniqueConstraint("name", "version", name="uq_name_version"),)

    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    template = Column(Text, nullable=False)
    variables = Column(Text, nullable=False)  # JSON array of variable names
    status = Column(String(50), nullable=False, default=PromptStatus.DRAFT.value)
    created_at = Column(DateTime, nullable=False, default=datetime.datetime.utcnow)
    created_by = Column(String(255), nullable=True)
    comment = Column(Text, nullable=True)


class PromptTemplate(BaseModel):
    """Data contract for prompt templates."""

    name: str = Field(..., min_length=1, max_length=255)
    version: int = Field(..., ge=1)
    template: str = Field(..., min_length=1)
    variables: list[str] = Field(default_factory=list)
    status: PromptStatus = Field(default=PromptStatus.DRAFT)
    created_at: datetime.datetime = Field(default_factory=datetime.datetime.utcnow)
    created_by: str | None = None
    comment: str | None = None

    @field_validator("template")
    @classmethod
    def validate_template(cls, v: str) -> str:
        """Validate Jinja2 template syntax."""
        try:
            env = Environment()
            env.parse(v)
        except TemplateSyntaxError as e:
            raise ValueError(f"Invalid template syntax: {e}") from e
        return v


class PromptManager:
    """Manages versioned prompts with template rendering."""

    def __init__(self, db_url: str = "sqlite:///prompts.db"):
        """Initialize prompt manager with database."""
        self.engine = create_engine(db_url)
        Base.metadata.create_all(self.engine)
        self.session_maker = sessionmaker(bind=self.engine)
        self.jinja_env = Environment(autoescape=True)

    def create_prompt(self, prompt: PromptTemplate) -> int:
        """Create a new prompt version."""
        session = self.session_maker()
        try:
            # Extract variables from template
            ast = self.jinja_env.parse(prompt.template)
            variables = list(meta.find_undeclared_variables(ast))

            db_prompt = PromptVersion(
                name=prompt.name,
                version=prompt.version,
                template=prompt.template,
                variables=json.dumps(variables),
                status=prompt.status.value,
                created_by=prompt.created_by,
                comment=prompt.comment,
            )
            session.add(db_prompt)
            session.commit()
            return db_prompt.id
        finally:
            session.close()

    def get_prompt(self, name: str, version: int | None = None) -> PromptTemplate | None:
        """Get a specific prompt version or latest active."""
        session = self.session_maker()
        try:
            query = session.query(PromptVersion).filter(PromptVersion.name == name)
            if version is not None:
                query = query.filter(PromptVersion.version == version)
            else:
                query = query.filter(PromptVersion.status == PromptStatus.ACTIVE.value)
            query = query.order_by(PromptVersion.version.desc())
            db_prompt = query.first()

            if not db_prompt:
                return None

            return PromptTemplate(
                name=db_prompt.name,
                version=db_prompt.version,
                template=db_prompt.template,
                variables=json.loads(db_prompt.variables),
                status=PromptStatus(db_prompt.status),
                created_at=db_prompt.created_at,
                created_by=db_prompt.created_by,
                comment=db_prompt.comment,
            )
        finally:
            session.close()

    def render_prompt(self, name: str, context: dict[str, Any], version: int | None = None) -> str:
        """Render a prompt template with given context."""
        prompt = self.get_prompt(name, version)
        if not prompt:
            raise ValueError(f"Prompt '{name}' not found")

        template = self.jinja_env.from_string(prompt.template)
        return template.render(**context)

    def list_prompts(self, name: str | None = None) -> list[PromptTemplate]:
        """List all prompt versions, optionally filtered by name."""
        session = self.session_maker()
        try:
            query = session.query(PromptVersion)
            if name:
                query = query.filter(PromptVersion.name == name)
            query = query.order_by(PromptVersion.name, PromptVersion.version.desc())

            return [
                PromptTemplate(
                    name=p.name,
                    version=p.version,
                    template=p.template,
                    variables=json.loads(p.variables),
                    status=PromptStatus(p.status),
                    created_at=p.created_at,
                    created_by=p.created_by,
                    comment=p.comment,
                )
                for p in query.all()
            ]
        finally:
            session.close()

    def activate_prompt(self, name: str, version: int) -> None:
        """Activate a specific prompt version and archive others."""
        session = self.session_maker()
        try:
            session.query(PromptVersion).filter(
                PromptVersion.name == name, PromptVersion.status == PromptStatus.ACTIVE.value
            ).update({"status": PromptStatus.ARCHIVED.value})

            session.query(PromptVersion).filter(
                PromptVersion.name == name, PromptVersion.version == version
            ).update({"status": PromptStatus.ACTIVE.value})

            session.commit()
        finally:
            session.close()
