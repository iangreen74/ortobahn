"""Shared Claude API wrapper used by all agents."""

import logging
import time
from dataclasses import dataclass

import anthropic

logger = logging.getLogger("ortobahn.llm")


@dataclass
class LLMResponse:
    text: str
    input_tokens: int
    output_tokens: int
    model: str
    thinking: str = ""


def call_llm(
    system_prompt: str,
    user_message: str,
    model: str = "claude-sonnet-4-5-20250929",
    max_tokens: int = 4096,
    api_key: str = "",
    retries: int = 3,
    thinking_budget: int = 0,
) -> LLMResponse:
    """Call Claude and return the response with token usage.

    When thinking_budget > 0, enables extended thinking which gives the model
    a scratchpad for deeper reasoning before producing its final answer.
    """
    client = anthropic.Anthropic(api_key=api_key)

    kwargs: dict = {
        "model": model,
        "max_tokens": max_tokens + thinking_budget if thinking_budget else max_tokens,
        "messages": [{"role": "user", "content": user_message}],
    }

    if thinking_budget > 0:
        kwargs["thinking"] = {
            "type": "enabled",
            "budget_tokens": thinking_budget,
        }
        # Extended thinking doesn't support system parameter; prepend to user message
        if system_prompt:
            kwargs["messages"] = [
                {"role": "user", "content": f"<system>\n{system_prompt}\n</system>\n\n{user_message}"},
            ]
    else:
        kwargs["system"] = system_prompt

    # Use streaming for extended thinking (required for long requests)
    use_stream = thinking_budget > 0

    for attempt in range(retries):
        try:
            if use_stream:
                with client.messages.stream(**kwargs) as stream:
                    response = stream.get_final_message()
            else:
                response = client.messages.create(**kwargs)

            # Parse response blocks
            text_parts = []
            thinking_parts = []
            for block in response.content:
                if block.type == "text":
                    text_parts.append(block.text)
                elif block.type == "thinking":
                    thinking_parts.append(block.thinking)

            return LLMResponse(
                text="\n".join(text_parts),
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                model=model,
                thinking="\n".join(thinking_parts),
            )
        except anthropic.RateLimitError:
            wait = 2**attempt * 5
            logger.warning(f"Rate limited, waiting {wait}s (attempt {attempt + 1}/{retries})")
            time.sleep(wait)
        except anthropic.APIError as e:
            if attempt == retries - 1:
                raise
            logger.warning(f"API error: {e}, retrying ({attempt + 1}/{retries})")
            time.sleep(2)

    raise RuntimeError("LLM call failed after all retries")


def parse_json_response(text: str, model_class):
    """Extract JSON from LLM response and parse into a Pydantic model."""
    cleaned = text.strip()

    # Strip markdown code fences
    if "```json" in cleaned:
        cleaned = cleaned.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in cleaned:
        cleaned = cleaned.split("```", 1)[1].split("```", 1)[0].strip()

    # Fallback: find JSON object/array boundaries if there's extra text
    if cleaned and cleaned[0] not in ("{", "["):
        start_obj = cleaned.find("{")
        start_arr = cleaned.find("[")
        if start_obj == -1 and start_arr == -1:
            raise ValueError(f"No JSON found in LLM response. First 300 chars: {text[:300]}")
        if start_arr != -1 and (start_obj == -1 or start_arr < start_obj):
            start = start_arr
            end = cleaned.rfind("]") + 1
        else:
            start = start_obj
            end = cleaned.rfind("}") + 1
        if end <= start:
            raise ValueError(f"Incomplete JSON in LLM response. First 300 chars: {text[:300]}")
        cleaned = cleaned[start:end]

    try:
        return model_class.model_validate_json(cleaned)
    except Exception as e:
        raise ValueError(
            f"Failed to parse LLM response as {model_class.__name__}: {e}\n"
            f"Cleaned text (first 300 chars): {cleaned[:300]}"
        ) from e
