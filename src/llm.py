"""The language model of the AI report (CIPW-16, CIPW-58): Mistral's chat completions API (docs/llm.md).

The same request works against Mistral's API and against a Mistral model on a server of Cofidis' own that offers the
chat completions interface (for example vLLM), so the statements need not leave Cofidis' network. `settings.llm` sets
the address, the model, the timeout and the token prices; the key comes from MISTRAL_API_KEY only.

    reply = ask_json(system_prompt, user_message)    # -> Reply(data: dict, usage: LLMUsage, model: str)

The answer must be one JSON object (`response_format: json_object`). The HTTP client, which waits and retries when
the service is busy (429, 503), is the one of the vision baseline (src/extraction/vision.py). Without a model
(offline mode, no key) the call is refused, and the pipeline uses the rule-based assessment instead (CIPW-55).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from src.extraction.vision import ApiError, MistralClient
from src.result import LLMUsage
from src.settings import Settings, get_settings


class LlmError(Exception):
    """The model could not be asked, or its answer is not the JSON object asked for."""


class Chat(Protocol):
    def chat(self, payload: dict[str, Any]) -> dict[str, Any]: ...


@dataclass
class Reply:
    data: dict[str, Any]
    usage: LLMUsage
    model: str


def client(settings: Settings) -> MistralClient:
    """The HTTP client for `settings.llm`. A server of one's own may need no key: it then ignores the header."""
    key = settings.llm.api_key.get_secret_value() if settings.llm.api_key else ""
    return MistralClient(
        api_key=key or "-",
        url=settings.llm.url.rstrip("/") + "/chat/completions",
        timeout=settings.llm.timeout,
        max_retries=settings.llm.max_retries,
    )


def cost_usd(input_tokens: int, output_tokens: int, settings: Settings) -> float:
    llm = settings.llm
    return round((input_tokens * llm.price_in_per_mtok + output_tokens * llm.price_out_per_mtok) / 1e6, 6)


def _text(message: dict[str, Any]) -> str:
    """The text of an answer: a string, or a list of chunks of which the text ones count (reasoning models)."""
    content = message.get("content")
    if isinstance(content, list):
        return "".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text")
    return content or ""


def ask_json(
    system: str, user: str, settings: Settings | None = None, max_tokens: int = 2000, chat: Chat | None = None
) -> Reply:
    """Ask the model once and return its answer as a JSON object, with the tokens used and their cost.
    `chat` replaces the HTTP client in tests."""
    settings = settings or get_settings()
    if not settings.llm_enabled:
        raise LlmError("no AI model connected: offline mode, or no MISTRAL_API_KEY for Mistral's API")
    payload = {
        "model": settings.llm.model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": 0,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }
    try:
        answer = (chat or client(settings)).chat(payload)
    except ApiError as exc:
        raise LlmError(f"{settings.llm.model} at {settings.llm.url}: {exc}") from exc
    except httpx.HTTPError as exc:  # timeout, connection refused, unknown host
        raise LlmError(f"{settings.llm.url} not reachable ({type(exc).__name__})") from exc
    try:
        choice = answer["choices"][0]
        if choice.get("finish_reason") == "length":
            raise LlmError(f"the answer of {settings.llm.model} was cut off at {max_tokens} tokens")
        text = _text(choice["message"])
        data = json.loads(text)
    except (KeyError, IndexError, TypeError, AttributeError, json.JSONDecodeError) as exc:
        raise LlmError(f"the answer of {settings.llm.model} is not a JSON object: {str(answer)[:300]}") from exc
    if not isinstance(data, dict):
        raise LlmError(f"the answer of {settings.llm.model} is JSON, but not an object: {text[:300]}")
    usage = answer.get("usage") or {}
    tokens_in, tokens_out = int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)
    return Reply(
        data=data,
        usage=LLMUsage(input_tokens=tokens_in, output_tokens=tokens_out,
                       cost_usd=cost_usd(tokens_in, tokens_out, settings)),  # fmt: skip
        model=answer.get("model") or settings.llm.model,
    )
