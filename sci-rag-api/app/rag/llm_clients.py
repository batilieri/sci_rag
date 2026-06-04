"""Thin wrappers around Anthropic and DeepSeek for structured generation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx
import orjson
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


_TRANSIENT_EXCEPTIONS = (httpx.TimeoutException, httpx.NetworkError)


@dataclass(slots=True)
class LLMResponse:
    text: str
    model: str
    tokens_input: int
    tokens_output: int
    raw: dict[str, Any]


@dataclass(slots=True)
class LLMUsage:
    tokens_input: int
    tokens_output: int


def _anthropic() -> AsyncAnthropic:
    settings = get_settings()
    return AsyncAnthropic(api_key=settings.anthropic_api_key, timeout=60.0)


def _deepseek() -> AsyncOpenAI:
    settings = get_settings()
    return AsyncOpenAI(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        timeout=60.0,
    )


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, max=4),
    retry=retry_if_exception_type(_TRANSIENT_EXCEPTIONS),
    reraise=True,
)
async def call_claude(
    *,
    system: str,
    user: str,
    model: str | None = None,
    max_tokens: int = 1500,
    temperature: float = 0.0,
    json_mode: bool = True,
) -> LLMResponse:
    settings = get_settings()
    chosen_model = model or settings.anthropic_model
    client = _anthropic()
    msg = await client.messages.create(
        model=chosen_model,
        max_tokens=max_tokens,
        temperature=temperature,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    text = "".join(block.text for block in msg.content if getattr(block, "type", None) == "text")
    if json_mode:
        text = _strip_json_fences(text)
    usage = getattr(msg, "usage", None)
    tokens_in = getattr(usage, "input_tokens", 0) if usage else 0
    tokens_out = getattr(usage, "output_tokens", 0) if usage else 0
    return LLMResponse(
        text=text,
        model=chosen_model,
        tokens_input=tokens_in,
        tokens_output=tokens_out,
        raw=msg.model_dump() if hasattr(msg, "model_dump") else {},
    )


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, max=4),
    retry=retry_if_exception_type(_TRANSIENT_EXCEPTIONS),
    reraise=True,
)
async def call_deepseek(
    *,
    system: str,
    user: str,
    model: str | None = None,
    max_tokens: int = 1500,
    temperature: float = 0.0,
    json_mode: bool = True,
) -> LLMResponse:
    settings = get_settings()
    chosen_model = model or settings.deepseek_model
    client = _deepseek()
    kwargs: dict[str, Any] = {
        "model": chosen_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    completion = await client.chat.completions.create(**kwargs)
    text = completion.choices[0].message.content or ""
    if json_mode:
        text = _strip_json_fences(text)
    usage = completion.usage
    return LLMResponse(
        text=text,
        model=chosen_model,
        tokens_input=usage.prompt_tokens if usage else 0,
        tokens_output=usage.completion_tokens if usage else 0,
        raw=completion.model_dump() if hasattr(completion, "model_dump") else {},
    )


async def call_vision_claude(
    *,
    system: str,
    user_text: str,
    image_bytes: bytes,
    image_mime: str = "image/png",
    model: str | None = None,
    max_tokens: int = 2500,
    temperature: float = 0.0,
) -> LLMResponse:
    settings = get_settings()
    chosen_model = model or settings.anthropic_model
    client = _anthropic()
    import base64

    encoded = base64.b64encode(image_bytes).decode("ascii")
    msg = await client.messages.create(
        model=chosen_model,
        max_tokens=max_tokens,
        temperature=temperature,
        system=system,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": image_mime, "data": encoded},
                    },
                    {"type": "text", "text": user_text},
                ],
            }
        ],
    )
    text = "".join(block.text for block in msg.content if getattr(block, "type", None) == "text")
    text = _strip_json_fences(text)
    usage = getattr(msg, "usage", None)
    return LLMResponse(
        text=text,
        model=chosen_model,
        tokens_input=getattr(usage, "input_tokens", 0) if usage else 0,
        tokens_output=getattr(usage, "output_tokens", 0) if usage else 0,
        raw=msg.model_dump() if hasattr(msg, "model_dump") else {},
    )


def _strip_json_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def parse_json_or_raise(text: str) -> dict[str, Any]:
    try:
        return orjson.loads(text)
    except orjson.JSONDecodeError:
        return json.loads(text)


PRICING_USD_PER_1K_TOKENS: dict[str, tuple[float, float]] = {
    "claude-sonnet-4-5-20250929": (0.003, 0.015),
    "deepseek-chat": (0.00027, 0.0011),
    "deepseek-v4-pro": (0.0007, 0.0028),
}


def estimate_cost_usd(model: str, tokens_in: int, tokens_out: int) -> float:
    price_in, price_out = PRICING_USD_PER_1K_TOKENS.get(model, (0.001, 0.003))
    return round((tokens_in * price_in + tokens_out * price_out) / 1000, 6)
