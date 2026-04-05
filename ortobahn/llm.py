from __future__ import annotations

import asyncio
import time
from enum import Enum
from typing import Any

import anthropic
from pydantic import BaseModel, Field


class ModelType(str, Enum):
    """Available Claude model types."""
    CLAUDE_3_5_SONNET = "claude-3-5-sonnet-20241022"
    CLAUDE_3_OPUS = "claude-3-opus-20240229"
    CLAUDE_3_SONNET = "claude-3-sonnet-20240229"
    CLAUDE_3_HAIKU = "claude-3-haiku-20240307"


class LLMRequest(BaseModel):
    """Data contract for LLM requests."""
    prompt: str
    model: ModelType = ModelType.CLAUDE_3_5_SONNET
    max_tokens: int = Field(default=1024, ge=1, le=4096)
    temperature: float = Field(default=1.0, ge=0.0, le=1.0)
    system_prompt: str | None = None


class LLMResponse(BaseModel):
    """Data contract for LLM responses."""
    content: str
    model: str
    usage: dict[str, int]
    stop_reason: str


class TokenBucket:
    """Token bucket for rate limiting API calls."""

    def __init__(self, rate: float, capacity: float) -> None:
        self.rate = rate
        self.capacity = capacity
        self.tokens = capacity
        self.last_update = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, tokens: float = 1.0) -> None:
        """Acquire tokens, waiting if necessary."""
        async with self._lock:
            while True:
                now = time.monotonic()
                elapsed = now - self.last_update
                self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
                self.last_update = now

                if self.tokens >= tokens:
                    self.tokens -= tokens
                    return

                await asyncio.sleep((tokens - self.tokens) / self.rate)


class LLMClient:
    """Async LLM client with rate limiting and retry logic."""

    def __init__(
        self,
        api_key: str,
        max_concurrent_requests: int = 5,
        requests_per_minute: float = 50.0,
        max_retries: int = 3,
    ) -> None:
        self.client = anthropic.AsyncAnthropic(api_key=api_key)
        self.semaphore = asyncio.Semaphore(max_concurrent_requests)
        self.token_bucket = TokenBucket(rate=requests_per_minute / 60.0, capacity=requests_per_minute)
        self.max_retries = max_retries

    async def generate(
        self,
        request: LLMRequest,
    ) -> LLMResponse:
        """Generate completion with rate limiting and retry logic."""
        async with self.semaphore:
            await self.token_bucket.acquire()
            return await self._generate_with_retry(request)

    async def _generate_with_retry(
        self,
        request: LLMRequest,
    ) -> LLMResponse:
        """Execute generation with exponential backoff retry."""
        last_exception = None

        for attempt in range(self.max_retries):
            try:
                return await self._execute_request(request)
            except anthropic.RateLimitError as e:
                last_exception = e
                if attempt < self.max_retries - 1:
                    wait_time = (2 ** attempt) * 1.0
                    await asyncio.sleep(wait_time)
            except anthropic.APITimeoutError as e:
                last_exception = e
                if attempt < self.max_retries - 1:
                    wait_time = (2 ** attempt) * 0.5
                    await asyncio.sleep(wait_time)
            except anthropic.APIConnectionError as e:
                last_exception = e
                if attempt < self.max_retries - 1:
                    wait_time = (2 ** attempt) * 0.5
                    await asyncio.sleep(wait_time)

        raise last_exception or Exception("Max retries exceeded")

    async def _execute_request(self, request: LLMRequest) -> LLMResponse:
        """Execute the actual API request."""
        kwargs: dict[str, Any] = {
            "model": request.model.value,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
            "messages": [{"role": "user", "content": request.prompt}],
        }

        if request.system_prompt:
            kwargs["system"] = request.system_prompt

        response = await self.client.messages.create(**kwargs)

        return LLMResponse(
            content=response.content[0].text,
            model=response.model,
            usage={
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            },
            stop_reason=response.stop_reason,
        )

    async def batch_generate(
        self,
        requests: list[LLMRequest],
    ) -> list[LLMResponse]:
        """Generate multiple completions concurrently."""
        tasks = [self.generate(request) for request in requests]
        return await asyncio.gather(*tasks)

    async def close(self) -> None:
        """Close the client and cleanup resources."""
        await self.client.close()
