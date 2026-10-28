"""Vision baseline (CIPW-8): page images -> multimodal LLM -> structured statements.

The alternative to our OCR + rules pipeline that many people try first: render the located pages, send the images to a
multimodal model and ask for the numbers as JSON. It exists to be compared with the parser (same ground truth, same
comparison, see scripts/run_vision_baseline.py), not to be used in production.

The model answers through a forced tool call whose input schema is the ground-truth format (values exactly as printed
plus the unit, see docs/schema.md). The answer is validated with Pydantic (`GroundTruth`) and converted to
`FinancialStatements`, so `src.evaluation.compare` scores it exactly like the parser output.
"""

from __future__ import annotations

import base64
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pymupdf

from src.line_items import LineDef, load_dictionary
from src.schema import FinancialStatements, GroundTruth

ROOT = Path(__file__).resolve().parents[2]
PROMPT_PATH = ROOT / "prompts" / "extraction_vision_v1.md"
DEFAULT_MODEL = "claude-sonnet-5-5"
DEFAULT_MODELS = {"anthropic": DEFAULT_MODEL, "mistral": "mistral-medium-3.5", "gemini": "gemini-3.6-flash"}
PROVIDERS = tuple(DEFAULT_MODELS)
MODEL_ENV = {"anthropic": "LLM_MODEL", "mistral": "MISTRAL_MODEL", "gemini": "GEMINI_MODEL"}
KEY_ENV = {"anthropic": "ANTHROPIC_API_KEY", "mistral": "MISTRAL_API_KEY", "gemini": "GEMINI_API_KEY"}
MISTRAL_URL = "https://api.mistral.ai/v1/chat/completions"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
DPI = 200
MAX_TOKENS = 8000
MAX_TOKENS_BY_PROVIDER = {"anthropic": MAX_TOKENS, "mistral": MAX_TOKENS, "gemini": 16000}  # Gemini counts thinking
TOOL_NAME = "record_statements"
ASSET_PERIODS = ("gross", "adjustment", "current", "prior")
OTHER_PERIODS = ("current", "prior")


@dataclass
class VisionResult:
    file: str
    pages: list[int]
    statements: FinancialStatements | None = None
    raw: dict[str, Any] | None = None  # the tool input exactly as the model returned it
    model: str = ""
    provider: str = "anthropic"
    prompt_version: str = PROMPT_PATH.stem
    dpi: int = DPI
    temperature_used: bool = True
    input_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0
    stop_reason: str | None = None
    error: str | None = None
    notes: str | None = None
    from_cache: bool = False
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.statements is not None and self.error is None


# ---------------------------------------------------------------------------------------------------------------
# Prompt and schema
# ---------------------------------------------------------------------------------------------------------------


def _periods(line: LineDef) -> tuple[str, ...]:
    return ASSET_PERIODS if line.side == "assets" else OTHER_PERIODS


def _line_table(statement: str) -> str:
    rows = []
    for line in load_dictionary().for_statement(statement):  # type: ignore[arg-type]
        row = f"`{line.key}` | {line.code or '-'} | {line.row or '-'} | {line.label} | {', '.join(_periods(line))}"
        rows.append(row)
    return "\n".join(rows)


def build_prompt(path: Path = PROMPT_PATH) -> str:
    """System prompt: the text below the first horizontal rule of the prompt file, with the line tables filled in."""
    text = path.read_text(encoding="utf-8")
    body = text.split("\n---\n", 1)[1] if "\n---\n" in text else text
    body = body.replace("{{BALANCE_SHEET_LINES}}", _line_table("balance_sheet"))
    body = body.replace("{{INCOME_STATEMENT_LINES}}", _line_table("income_statement"))
    if "{{" in body:
        raise ValueError(f"Unfilled placeholder in {path}")
    return body.strip() + "\n"


def output_schema(simple: bool = False) -> dict[str, Any]:
    """JSON schema of the tool input: ground-truth format, one optional object per schema key.

    `simple=True` (used for Mistral) writes each number as plain `number` instead of `["number", "null"]`: a value
    that is not printed is left out, which the prompt already allows and which `GroundTruth` reads as missing.
    """
    d = load_dictionary()

    def statement_schema(statement: str) -> dict[str, Any]:
        props = {}
        for line in d.for_statement(statement):  # type: ignore[arg-type]
            props[line.key] = {
                "type": "object",
                "description": f"{line.label} ({line.code or 'total'})",
                "properties": {p: {"type": "number" if simple else ["number", "null"]} for p in _periods(line)},
                "additionalProperties": False,
            }
        return {"type": "object", "properties": props, "additionalProperties": False}

    unit: dict[str, Any] = {
        "type": "integer",
        "description": "1 = Kč, 1000 = tis. Kč, 1000000 = mil. Kč, from the header of the statements.",
    }
    if not simple:
        unit["enum"] = [1, 1000, 1000000]
    return {
        "type": "object",
        "properties": {
            "unit_multiplier": unit,
            "balance_sheet": statement_schema("balance_sheet"),
            "income_statement": statement_schema("income_statement"),
            "notes": {"type": "string", "description": "One short sentence about anything unusual."},
        },
        "required": ["unit_multiplier", "balance_sheet", "income_statement"],
        "additionalProperties": False,
    }


# ---------------------------------------------------------------------------------------------------------------
# Rendering and the call
# ---------------------------------------------------------------------------------------------------------------


def render_pages(pdf: str | Path, pages: list[int], dpi: int = DPI) -> list[tuple[int, bytes]]:
    """PNG bytes of the given 1-based pages."""
    out = []
    with pymupdf.open(pdf) as doc:
        for p in pages:
            if not 1 <= p <= len(doc):
                raise ValueError(f"Page {p} is not in {Path(pdf).name} ({len(doc)} pages)")
            out.append((p, doc[p - 1].get_pixmap(dpi=dpi).tobytes("png")))
    return out


def _content(images: list[tuple[int, bytes]]) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = []
    for page, png in images:
        content.append({"type": "text", "text": f"PDF page {page}:"})
        content.append(
            {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": base64.b64encode(png).decode()},
            }
        )
    content.append({"type": "text", "text": f"Transcribe the statements into the `{TOOL_NAME}` tool."})
    return content


def price_per_mtok() -> tuple[float | None, float | None]:
    """USD per million input/output tokens, from LLM_PRICE_IN_PER_MTOK / LLM_PRICE_OUT_PER_MTOK (not hard-coded)."""

    def read(name: str) -> float | None:
        raw = os.getenv(name)
        return float(raw) if raw else None

    return read("LLM_PRICE_IN_PER_MTOK"), read("LLM_PRICE_OUT_PER_MTOK")


def cost_usd(input_tokens: int, output_tokens: int) -> float | None:
    p_in, p_out = price_per_mtok()
    if p_in is None or p_out is None:
        return None
    return (input_tokens * p_in + output_tokens * p_out) / 1_000_000


def to_statements(raw: dict[str, Any], file: str, pages: list[int]) -> FinancialStatements:
    """Validate the model answer (unknown keys are rejected by `GroundTruth`) and convert to CZK."""
    gt = GroundTruth(
        file=file,
        unit_multiplier=raw["unit_multiplier"],
        balance_sheet=raw.get("balance_sheet") or {},
        income_statement=raw.get("income_statement") or {},
        notes=raw.get("notes"),
    )
    fs = gt.to_statements()
    fs.metadata.statement_pages = {"all": pages}
    for statement in (fs.balance_sheet, fs.income_statement):
        for item in statement.values():
            for v in item.values():
                v.source = None
                v.note = "vision baseline"
    return fs


class ApiError(Exception):
    """An HTTP error from a model API (Mistral or Gemini)."""

    def __init__(self, status_code: int, message: str):
        super().__init__(f"HTTP {status_code}: {message}")
        self.status_code = status_code


MistralError = ApiError  # name used by the first version of this module


class _HttpClient:
    """Plain HTTP client (no SDK). Waits and retries when the service says "too many requests" (429) or is busy."""

    retry_statuses = (429, 503)

    def __init__(self, key_env: str, api_key: str | None, timeout: float, max_retries: int):
        self.api_key = api_key or os.getenv(key_env)
        if not self.api_key:
            raise ValueError(f"{key_env} is not set")
        self.timeout, self.max_retries = timeout, max_retries
        self.sleep = time.sleep

    def _post(self, url: str, headers: dict[str, str], payload: dict[str, Any]) -> dict[str, Any]:
        import httpx

        for attempt in range(self.max_retries + 1):
            response = httpx.post(url, json=payload, headers=headers, timeout=self.timeout)
            if response.status_code in self.retry_statuses and attempt < self.max_retries:
                try:
                    wait = float(response.headers.get("retry-after", ""))
                except ValueError:
                    wait = min(2.0 * 2**attempt, 60.0)
                self.sleep(min(wait, 120.0))
                continue
            if response.status_code >= 400:
                raise ApiError(response.status_code, response.text[:500])
            return response.json()
        raise ApiError(429, "rate limit: retries exhausted")


class MistralClient(_HttpClient):
    """Mistral chat completions API."""

    def __init__(
        self, api_key: str | None = None, url: str = MISTRAL_URL, timeout: float = 300.0, max_retries: int = 6
    ):
        super().__init__("MISTRAL_API_KEY", api_key, timeout, max_retries)
        self.url = url

    def chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post(self.url, {"Authorization": f"Bearer {self.api_key}"}, payload)


class GeminiClient(_HttpClient):
    """Google Gemini API (generateContent)."""

    def __init__(self, api_key: str | None = None, url: str = GEMINI_URL, timeout: float = 300.0, max_retries: int = 6):
        super().__init__("GEMINI_API_KEY", api_key, timeout, max_retries)
        self.url = url

    def generate(self, model: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._post(self.url.format(model=model), {"x-goog-api-key": self.api_key or ""}, body)


def make_client(provider: str) -> Any:
    """The client for `provider`, reading its key from the environment."""
    if provider == "mistral":
        return MistralClient()
    if provider == "gemini":
        return GeminiClient()
    import anthropic

    return anthropic.Anthropic()


def _mistral_content(images: list[tuple[int, bytes]]) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = []
    for page, png in images:
        content.append({"type": "text", "text": f"PDF page {page}:"})
        content.append({"type": "image_url", "image_url": "data:image/png;base64," + base64.b64encode(png).decode()})
    content.append({"type": "text", "text": f"Transcribe the statements into the `{TOOL_NAME}` tool."})
    return content


def _call_anthropic(
    client: Any, model: str, images: list[tuple[int, bytes]], max_tokens: int, result: VisionResult
) -> dict[str, Any]:
    import anthropic

    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "system": build_prompt(),
        "tools": [
            {
                "name": TOOL_NAME,
                "description": "Record the numbers transcribed from the pages.",
                "input_schema": output_schema(),
            }
        ],
        "tool_choice": {"type": "tool", "name": TOOL_NAME},
        "messages": [{"role": "user", "content": _content(images)}],
    }
    try:
        response = client.messages.create(temperature=0, **kwargs)
    except anthropic.BadRequestError as exc:
        if "temperature" not in str(exc).lower():
            raise
        result.temperature_used = False  # some models do not accept it: the run is then not strictly temperature 0
        result.warnings.append("model rejected temperature=0; called without it")
        response = client.messages.create(**kwargs)
    result.input_tokens = response.usage.input_tokens
    result.output_tokens = response.usage.output_tokens
    result.stop_reason = response.stop_reason
    if response.stop_reason == "max_tokens":
        raise ValueError("answer cut off at max_tokens: raise MAX_TOKENS")
    block = next((b for b in response.content if b.type == "tool_use" and b.name == TOOL_NAME), None)
    if block is None:
        raise ValueError("the model did not call the tool")
    return dict(block.input)


def _call_mistral(
    client: Any, model: str, images: list[tuple[int, bytes]], max_tokens: int, result: VisionResult
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": build_prompt()},
            {"role": "user", "content": _mistral_content(images)},
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": TOOL_NAME,
                    "description": "Record the numbers transcribed from the pages.",
                    "parameters": output_schema(simple=True),
                },
            }
        ],
        "tool_choice": "any",
    }
    try:
        data = client.chat({**payload, "temperature": 0})
    except MistralError as exc:
        if exc.status_code != 400 or "temperature" not in str(exc).lower():
            raise
        result.temperature_used = False
        result.warnings.append("model rejected temperature=0; called without it")
        data = client.chat(payload)
    usage = data.get("usage") or {}
    result.input_tokens = usage.get("prompt_tokens", 0)
    result.output_tokens = usage.get("completion_tokens", 0)
    choice = data["choices"][0]
    result.stop_reason = choice.get("finish_reason")
    if result.stop_reason == "length":
        raise ValueError("answer cut off at max_tokens: raise MAX_TOKENS")
    calls = (choice.get("message") or {}).get("tool_calls") or []
    call = next((c for c in calls if (c.get("function") or {}).get("name") == TOOL_NAME), None)
    if call is None:
        raise ValueError("the model did not call the tool")
    arguments = call["function"]["arguments"]
    return json.loads(arguments) if isinstance(arguments, str) else dict(arguments)


def _call_gemini(
    client: Any, model: str, images: list[tuple[int, bytes]], max_tokens: int, result: VisionResult
) -> dict[str, Any]:
    parts: list[dict[str, Any]] = []
    for page, png in images:
        parts.append({"text": f"PDF page {page}:"})
        parts.append({"inlineData": {"mimeType": "image/png", "data": base64.b64encode(png).decode()}})
    parts.append({"text": "Transcribe the statements into the JSON answer."})
    config: dict[str, Any] = {
        "maxOutputTokens": max_tokens,
        "responseMimeType": "application/json",
        "responseJsonSchema": output_schema(simple=True),
    }
    body: dict[str, Any] = {
        "systemInstruction": {"parts": [{"text": build_prompt()}]},
        "contents": [{"role": "user", "parts": parts}],
    }
    try:
        data = client.generate(model, {**body, "generationConfig": {**config, "temperature": 0}})
    except ApiError as exc:
        if exc.status_code != 400 or "temperature" not in str(exc).lower():
            raise
        result.temperature_used = False
        result.warnings.append("model rejected temperature=0; called without it")
        data = client.generate(model, {**body, "generationConfig": config})
    usage = data.get("usageMetadata") or {}
    result.input_tokens = usage.get("promptTokenCount", 0)
    # thinking tokens are billed as output
    result.output_tokens = usage.get("candidatesTokenCount", 0) + usage.get("thoughtsTokenCount", 0)
    candidates = data.get("candidates") or []
    if not candidates:
        raise ValueError(f"no answer from the model: {str(data.get('promptFeedback') or data)[:300]}")
    candidate = candidates[0]
    result.stop_reason = candidate.get("finishReason")
    if result.stop_reason == "MAX_TOKENS":
        raise ValueError("answer cut off at max_tokens: raise MAX_TOKENS_BY_PROVIDER")
    text = "".join(p.get("text", "") for p in (candidate.get("content") or {}).get("parts", []) if not p.get("thought"))
    if not text.strip():
        raise ValueError(f"the model returned no text (finish reason {result.stop_reason})")
    answer = json.loads(text)
    if not isinstance(answer, dict):
        raise ValueError("the answer is not a JSON object")
    return answer


def extract_with_vision(
    pdf: str | Path,
    pages: list[int],
    client: Any,
    model: str | None = None,
    dpi: int = DPI,
    max_tokens: int | None = None,
    provider: str = "anthropic",
) -> VisionResult:
    """One call: the images of `pages` in, statements out.

    `client` is an `anthropic.Anthropic` (provider "anthropic"), a `MistralClient` ("mistral") or a `GeminiClient`
    ("gemini"), or a test double with the same methods.
    """
    if provider not in PROVIDERS:
        raise ValueError(f"unknown provider {provider!r}, use one of {PROVIDERS}")
    model = model or os.getenv(MODEL_ENV[provider]) or DEFAULT_MODELS[provider]
    max_tokens = max_tokens or MAX_TOKENS_BY_PROVIDER[provider]
    pdf = Path(pdf)
    result = VisionResult(file=pdf.name, pages=pages, model=model, dpi=dpi, provider=provider)
    started = time.perf_counter()
    try:
        images = render_pages(pdf, pages, dpi)
        call = {"anthropic": _call_anthropic, "mistral": _call_mistral, "gemini": _call_gemini}[provider]
        result.raw = call(client, model, images, max_tokens, result)
        result.notes = result.raw.get("notes")
        result.statements = to_statements(result.raw, pdf.name, pages)
    except Exception as exc:  # a failed file is a result, not a crash: the run goes on and the failure is reported
        result.error = f"{type(exc).__name__}: {exc}"
    result.seconds = time.perf_counter() - started
    return result
