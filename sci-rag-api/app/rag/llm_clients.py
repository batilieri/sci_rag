"""Resilient, pooled wrappers around DeepSeek and Anthropic for structured generation.

Design notes (built to serve many clients concurrently):
  * One shared async client per provider, reused across requests. Creating a client
    per call (the old behaviour) spins up a fresh httpx connection pool every time,
    which leaks sockets and defeats keep-alive under load.
  * A per-worker semaphore caps concurrent in-flight LLM calls so a burst of clients
    applies backpressure here instead of hammering the provider into 429s.
  * Retries (with jittered exponential backoff) cover the transient failures the
    provider SDKs actually raise: timeouts, connection drops, 429 rate limits and
    5xx. The SDK's own retry is disabled so all backoff/logging flows through here.
"""

from __future__ import annotations

import asyncio
import base64
import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, TypeVar

import anthropic
import httpx
import openai
import orjson
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_random_exponential,
)

from app.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

T = TypeVar("T")

# Transient failures worth retrying. The OpenAI/Anthropic SDKs wrap network and HTTP
# errors in their own exception types, so retrying bare httpx errors (the old code)
# never actually fired for these providers.
_OPENAI_TRANSIENT = (
    openai.APITimeoutError,
    openai.APIConnectionError,
    openai.RateLimitError,
    openai.InternalServerError,
)
_ANTHROPIC_TRANSIENT = (
    anthropic.APITimeoutError,
    anthropic.APIConnectionError,
    anthropic.RateLimitError,
    anthropic.InternalServerError,
)
_HTTPX_TRANSIENT = (httpx.TimeoutException, httpx.NetworkError)


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


# --------------------------------------------------------------------------------------
# Shared clients + concurrency control (lazy singletons, one set per worker process)
# --------------------------------------------------------------------------------------

_deepseek_client: AsyncOpenAI | None = None
_anthropic_client: AsyncAnthropic | None = None
_semaphore: asyncio.Semaphore | None = None


def _timeout() -> httpx.Timeout:
    s = get_settings()
    return httpx.Timeout(s.llm_timeout_seconds, connect=10.0)


def _limits() -> httpx.Limits:
    s = get_settings()
    return httpx.Limits(
        max_connections=s.llm_max_connections,
        max_keepalive_connections=s.llm_max_keepalive,
        keepalive_expiry=30.0,
    )


def get_semaphore() -> asyncio.Semaphore:
    """Per-event-loop concurrency gate for outbound LLM calls."""
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(get_settings().llm_max_concurrency)
    return _semaphore


def get_deepseek() -> AsyncOpenAI:
    global _deepseek_client
    if _deepseek_client is None:
        s = get_settings()
        _deepseek_client = AsyncOpenAI(
            api_key=s.deepseek_api_key,
            base_url=s.deepseek_base_url,
            timeout=_timeout(),
            max_retries=0,  # tenacity owns retries (unified backoff + logging)
            http_client=httpx.AsyncClient(timeout=_timeout(), limits=_limits()),
        )
    return _deepseek_client


def get_anthropic() -> AsyncAnthropic:
    global _anthropic_client
    if _anthropic_client is None:
        s = get_settings()
        _anthropic_client = AsyncAnthropic(
            api_key=s.anthropic_api_key,
            timeout=_timeout(),
            max_retries=0,
            http_client=httpx.AsyncClient(timeout=_timeout(), limits=_limits()),
        )
    return _anthropic_client


async def shutdown_llm_clients() -> None:
    """Close pooled clients on app shutdown so sockets are released cleanly."""
    global _deepseek_client, _anthropic_client
    if _deepseek_client is not None:
        await _deepseek_client.close()
        _deepseek_client = None
    if _anthropic_client is not None:
        await _anthropic_client.close()
        _anthropic_client = None


async def _resilient(
    fn: Callable[[], Awaitable[T]],
    *,
    transient: tuple[type[BaseException], ...],
    provider: str,
) -> T:
    """Run an LLM call under the concurrency gate with retry + jittered backoff."""
    s = get_settings()
    async for attempt in AsyncRetrying(
        stop=stop_after_attempt(s.llm_max_retries),
        wait=wait_random_exponential(multiplier=0.5, max=8),
        retry=retry_if_exception_type(transient),
        reraise=True,
    ):
        with attempt:
            if attempt.retry_state.attempt_number > 1:
                logger.warning(
                    "llm_retry",
                    provider=provider,
                    attempt=attempt.retry_state.attempt_number,
                )
            async with get_semaphore():
                return await fn()
    raise AssertionError("unreachable")  # pragma: no cover


# --------------------------------------------------------------------------------------
# Public call surface (signatures unchanged)
# --------------------------------------------------------------------------------------


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
    client = get_deepseek()
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

    async def _do() -> Any:
        return await client.chat.completions.create(**kwargs)

    completion = await _resilient(
        _do, transient=_OPENAI_TRANSIENT + _HTTPX_TRANSIENT, provider="deepseek"
    )
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
    client = get_anthropic()

    async def _do() -> Any:
        return await client.messages.create(
            model=chosen_model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
            messages=[{"role": "user", "content": user}],
        )

    msg = await _resilient(
        _do, transient=_ANTHROPIC_TRANSIENT + _HTTPX_TRANSIENT, provider="claude"
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
    client = get_anthropic()
    encoded = base64.b64encode(image_bytes).decode("ascii")

    async def _do() -> Any:
        return await client.messages.create(
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

    msg = await _resilient(
        _do, transient=_ANTHROPIC_TRANSIENT + _HTTPX_TRANSIENT, provider="claude-vision"
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
