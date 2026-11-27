import json
import logging
import time

from openai import AsyncOpenAI

from ai_service.config import settings

logger = logging.getLogger("ai-service.llm")

_client: AsyncOpenAI | None = None


def get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        if not settings.llm_api_key:
            raise RuntimeError("LLM_API_KEY 未配置：先在 .env 里填上百炼 Key")
        _client = AsyncOpenAI(api_key=settings.llm_api_key, base_url=settings.llm_base_url)
    return _client


def log_llm_call(model: str, input_tokens: int, output_tokens: int, duration_ms: float) -> None:
    cost = None
    if settings.llm_price_input_per_1m > 0:
        cost = round(
            input_tokens / 1_000_000 * settings.llm_price_input_per_1m
            + output_tokens / 1_000_000 * settings.llm_price_output_per_1m,
            6,
        )
    logger.info(
        "llm_call model=%s input_tokens=%s output_tokens=%s duration_ms=%s cost=%s",
        model, input_tokens, output_tokens, round(duration_ms), cost,
    )


async def chat(messages: list[dict], temperature: float = 0.7, max_tokens: int = 1024) -> dict:
    client = get_client()
    start = time.monotonic()
    resp = await client.chat.completions.create(
        model=settings.llm_model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    duration_ms = (time.monotonic() - start) * 1000
    usage = resp.usage
    log_llm_call(settings.llm_model, usage.prompt_tokens, usage.completion_tokens, duration_ms)
    return {
        "reply": resp.choices[0].message.content,
        "model": resp.model,
        "input_tokens": usage.prompt_tokens,
        "output_tokens": usage.completion_tokens,
        "duration_ms": round(duration_ms),
    }


async def chat_stream(messages: list[dict], temperature: float = 0.7, max_tokens: int = 1024):
    """流式：每收到一片就 yield 一片文本；usage 在最后一片才到，随到随记日志。"""
    client = get_client()
    start = time.monotonic()
    stream = await client.chat.completions.create(
        model=settings.llm_model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        stream=True,
        stream_options={"include_usage": True},
    )
    async for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content
        if chunk.usage:
            log_llm_call(
                settings.llm_model,
                chunk.usage.prompt_tokens,
                chunk.usage.completion_tokens,
                (time.monotonic() - start) * 1000,
            )