"""Abstract base agent class."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path
from string import Template
from typing import Any

from ortobahn.db import Database
from ortobahn.llm import LLMResponse, call_llm

logger = logging.getLogger("ortobahn.agents")

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


class BaseAgent(ABC):
    name: str = "base"
    prompt_file: str = ""
    thinking_budget: int = 0

    def __init__(self, db: Database, api_key: str, model: str = "claude-sonnet-4-5-20250929", max_tokens: int = 4096):
        self.db = db
        self.api_key = api_key
        self.model = model
        self.max_tokens = max_tokens
        self._system_prompt = ""

    @property
    def system_prompt(self) -> str:
        if not self._system_prompt and self.prompt_file:
            path = PROMPTS_DIR / self.prompt_file
            self._system_prompt = path.read_text()
        return self._system_prompt

    def format_prompt(self, **context: Any) -> str:
        """Format the system prompt with context variables using $-style substitution.

        Uses safe_substitute so missing keys are left as-is (backward compat).
        """
        return Template(self.system_prompt).safe_substitute(**context)

    @abstractmethod
    def run(self, run_id: str, **kwargs: Any) -> Any:
        """Execute this agent's task. Returns structured output."""
        ...

    def get_memory_context(self, client_id: str = "default") -> str:
        """Retrieve formatted memories for this agent to inject into prompts."""
        try:
            from ortobahn.memory import MemoryStore

            store = MemoryStore(self.db)
            return store.get_memory_context(self.name, client_id)
        except Exception:
            return ""

    def call_llm(self, user_message: str, system_prompt: str | None = None) -> LLMResponse:
        """Call Claude with this agent's system prompt (or a custom one)."""
        return call_llm(
            system_prompt=system_prompt or self.system_prompt,
            user_message=user_message,
            model=self.model,
            max_tokens=self.max_tokens,
            api_key=self.api_key,
            thinking_budget=self.thinking_budget,
        )

    def log_decision(
        self,
        run_id: str,
        input_summary: str,
        output_summary: str,
        reasoning: str = "",
        llm_response: LLMResponse | None = None,
    ):
        """Log this agent's decision to the database."""
        full_reasoning = reasoning
        if llm_response and llm_response.thinking:
            thinking_summary = llm_response.thinking[:200]
            full_reasoning = (
                f"{reasoning} | Thinking: {thinking_summary}" if reasoning else f"Thinking: {thinking_summary}"
            )
        self.db.log_agent(
            run_id=run_id,
            agent_name=self.name,
            input_summary=input_summary[:500],
            output_summary=output_summary[:500],
            reasoning=full_reasoning[:500],
            llm_model=llm_response.model if llm_response else "",
            input_tokens=llm_response.input_tokens if llm_response else 0,
            output_tokens=llm_response.output_tokens if llm_response else 0,
            raw_response=llm_response.text if llm_response else "",
        )
        logger.info(f"[{self.name}] {output_summary[:100]}")
