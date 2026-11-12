"""The AI assessment call (CIPW-16): assessment input -> LLM -> `AssessmentReport`.

The LLM gets the prompt `prompts/assessment_v6.md` as system prompt and, as user message, the assessment input
(`build_assessment_input`) plus the rating decided by the rules (`rate`). It answers through a forced tool call
(Anthropic, Mistral) or as JSON (Gemini). The answer is validated with Pydantic. The rating is not the model's: if it
differs from the rule's, the rule's value is written back and a warning says so (rating approach A of the ticket,
still a proposal until the team decides).

The provider is a parameter (`anthropic`, `mistral`, `gemini`), so changing it is a matter of configuration. The HTTP
clients, model names and key variables are those of the vision baseline (`src/extraction/vision.py`).
"""

from __future__ import annotations

import json
import os
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.extraction.vision import (
    DEFAULT_MODELS,
    MODEL_ENV,
    PROVIDERS,
    ApiError,
    cost_usd,
    make_client,
)
from src.report.models import AssessmentReport
from src.report.rating import RatingConfig, RatingDecision, rate, rules_table
from src.report.recommendation import RecommendationConfig, recommend
from src.report.tables import figures_markdown
from src.report.texts import texts
from src.version import file_version

ROOT = Path(__file__).resolve().parents[2]
PROMPT_PATH = ROOT / "prompts" / "assessment_v6.md"
TRANSLATION_PROMPT_PATH = ROOT / "prompts" / "translation_v2.md"
TOOL_NAME = "record_assessment"
DEFAULT_LANGUAGE = "en"
MAX_TOKENS = 4000
MAX_RETRIES = 2  # "busy" (503) and "too many requests" (429): every retry can count against a free quota
MAX_TOKENS_BY_PROVIDER = {"anthropic": MAX_TOKENS, "mistral": MAX_TOKENS, "gemini": 16000}  # Gemini counts thinking
NO_RATING_WORDS = ("none", "null", "")


@dataclass
class ReportRun:
    """One assessment call: the report (or the error), what was asked and what it cost."""

    file: str
    provider: str
    model: str
    language: str
    prompt_version: str
    rating: RatingDecision
    report: AssessmentReport | None = None
    raw: dict[str, Any] | None = None  # the model's answer exactly as returned
    input_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0
    stop_reason: str | None = None
    temperature_used: bool = True
    error: str | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.report is not None and self.error is None

    @property
    def cost_usd(self) -> float | None:
        return cost_usd(self.input_tokens, self.output_tokens)


# ---------------------------------------------------------------------------------------------- prompt and schema


def _pct(value: float) -> str:
    return f"{value:g}"


def build_prompt(language: str = DEFAULT_LANGUAGE, path: Path = PROMPT_PATH, worsening_pct: float = 20.0) -> str:
    """System prompt: the text below the first horizontal rule of the prompt file, with the placeholders filled in.

    `{{LANGUAGE}}` and, from prompt v4 on, `{{WORSENING_PCT}}` (v1 to v3 have the 20 written in the text).
    """
    text = path.read_text(encoding="utf-8")
    body = text.split("\n---\n", 1)[1] if "\n---\n" in text else text
    body = body.replace("{{LANGUAGE}}", language).replace("{{WORSENING_PCT}}", _pct(worsening_pct))
    if "{{" in body:
        raise ValueError(f"Unfilled placeholder in {path}")
    return body.strip() + "\n"


def prompt_version(path: Path = PROMPT_PATH) -> str:
    """Content hash, as `audit.prompt_version` in the pipeline result."""
    return file_version(path)


def _strings() -> dict[str, Any]:
    return {"type": "array", "items": {"type": "string"}}


def output_schema() -> dict[str, Any]:
    """JSON schema of the tool input; the same fields as `AssessmentReport` (a test keeps them equal)."""
    return {
        "type": "object",
        "properties": {
            "language": {"type": "string"},
            "summary": {"type": "string"},
            "strengths": _strings(),
            "risks": _strings(),
            "red_flags": _strings(),
            "rating": {
                "type": "object",
                "properties": {"value": {"type": ["string", "null"]}, "reasoning": {"type": "string"}},
                "required": ["value", "reasoning"],
            },
            "data_quality_note": {"type": "string"},
        },
        "required": ["language", "summary", "strengths", "risks", "red_flags", "rating", "data_quality_note"],
    }


GEMINI_FORMAT_HINT = """Answer with one JSON object and nothing else, in exactly this shape:
{"language": "en", "summary": "...", "strengths": ["..."], "risks": ["..."], "red_flags": ["..."],
 "rating": {"value": "low" | "medium" | "high" | null, "reasoning": "..."}, "data_quality_note": "..."}"""


def user_message(data: dict[str, Any], decision: RatingDecision) -> str:
    """The assessment input with the rule-based rating added, as JSON."""
    rating = {"value": decision.value, "rules": decision.rules, "reasoning": decision.reasoning}
    return json.dumps({**data, "rating": rating}, ensure_ascii=False, indent=1)


# ---------------------------------------------------------------------------------------------- provider calls


def _call_anthropic(client: Any, model: str, system: str, user: str, max_tokens: int, run: ReportRun) -> dict[str, Any]:
    import anthropic

    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "tools": [
            {"name": TOOL_NAME, "description": "Record the assessment.", "input_schema": output_schema()},
        ],
        "tool_choice": {"type": "tool", "name": TOOL_NAME},
        "messages": [{"role": "user", "content": user}],
    }
    try:
        response = client.messages.create(temperature=0, **kwargs)
    except anthropic.BadRequestError as exc:
        if "temperature" not in str(exc).lower():
            raise
        run.temperature_used = False
        run.warnings.append("model rejected temperature=0; called without it")
        response = client.messages.create(**kwargs)
    run.input_tokens = response.usage.input_tokens
    run.output_tokens = response.usage.output_tokens
    run.stop_reason = response.stop_reason
    if response.stop_reason == "max_tokens":
        raise ValueError("answer cut off at max_tokens")
    block = next((b for b in response.content if b.type == "tool_use" and b.name == TOOL_NAME), None)
    if block is None:
        raise ValueError("the model did not call the tool")
    return dict(block.input)


def _call_mistral(client: Any, model: str, system: str, user: str, max_tokens: int, run: ReportRun) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": TOOL_NAME,
                    "description": "Record the assessment.",
                    "parameters": output_schema(),
                },
            }
        ],
        "tool_choice": "any",
    }
    try:
        data = client.chat({**payload, "temperature": 0})
    except ApiError as exc:
        if exc.status_code != 400 or "temperature" not in str(exc).lower():
            raise
        run.temperature_used = False
        run.warnings.append("model rejected temperature=0; called without it")
        data = client.chat(payload)
    usage = data.get("usage") or {}
    run.input_tokens = usage.get("prompt_tokens", 0)
    run.output_tokens = usage.get("completion_tokens", 0)
    choice = data["choices"][0]
    run.stop_reason = choice.get("finish_reason")
    if run.stop_reason == "length":
        raise ValueError("answer cut off at max_tokens")
    calls = (choice.get("message") or {}).get("tool_calls") or []
    call = next((c for c in calls if (c.get("function") or {}).get("name") == TOOL_NAME), None)
    if call is None:
        raise ValueError("the model did not call the tool")
    arguments = call["function"]["arguments"]
    return json.loads(arguments) if isinstance(arguments, str) else dict(arguments)


def _call_gemini(client: Any, model: str, system: str, user: str, max_tokens: int, run: ReportRun) -> dict[str, Any]:
    config: dict[str, Any] = {"maxOutputTokens": max_tokens, "responseMimeType": "application/json"}
    body: dict[str, Any] = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [
            {"role": "user", "parts": [{"text": user + "\n\nWrite the assessment.\n\n" + GEMINI_FORMAT_HINT}]}
        ],
    }
    try:
        data = client.generate(model, {**body, "generationConfig": {**config, "temperature": 0}})
    except ApiError as exc:
        if exc.status_code != 400 or "temperature" not in str(exc).lower():
            raise
        run.temperature_used = False
        run.warnings.append("model rejected temperature=0; called without it")
        data = client.generate(model, {**body, "generationConfig": config})
    usage = data.get("usageMetadata") or {}
    run.input_tokens = usage.get("promptTokenCount", 0)
    run.output_tokens = usage.get("candidatesTokenCount", 0) + usage.get("thoughtsTokenCount", 0)
    candidates = data.get("candidates") or []
    if not candidates:
        raise ValueError(f"no answer from the model: {str(data.get('promptFeedback') or data)[:300]}")
    run.stop_reason = candidates[0].get("finishReason")
    if run.stop_reason == "MAX_TOKENS":
        raise ValueError("answer cut off at max_tokens")
    parts = (candidates[0].get("content") or {}).get("parts", [])
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    if not text.strip():
        raise ValueError(f"the model returned no text (finish reason {run.stop_reason})")
    answer = json.loads(text)
    if not isinstance(answer, dict):
        raise ValueError("the answer is not a JSON object")
    return answer


CALLS = {"anthropic": _call_anthropic, "mistral": _call_mistral, "gemini": _call_gemini}


# ---------------------------------------------------------------------------------------------- the report


def _apply_rules_rating(report: AssessmentReport, decision: RatingDecision, run: ReportRun) -> None:
    """The rating is the rules': a different value from the model is replaced and said."""
    if report.rating.value != decision.value:
        run.warnings.append(
            f"the model gave rating {report.rating.value!r}, the rules give {decision.value!r}: kept the rules'"
        )
        report.rating.value = decision.value


def add_recommendation(
    run: ReportRun, data: dict[str, Any], config: RecommendationConfig | None = None, language: str | None = None
) -> None:
    """The recommendation of the rating, with its criteria (code, not the model); also for a saved report."""
    if run.report is not None:
        run.report.recommendation = recommend(data, run.rating, config, language or run.language)


def _tidy(text: Any) -> Any:
    """A sentence from the model with the spaces cut off and a capital first letter."""
    if not isinstance(text, str):
        return text
    text = text.strip()
    return text[:1].upper() + text[1:]


def _clean(raw: dict[str, Any], language: str) -> dict[str, Any]:
    """The model's answer ready for validation: our language, tidy sentences, 'none' / 'null' as no rating."""
    answer = {**raw, "language": language}
    for key in ("summary", "data_quality_note"):
        answer[key] = _tidy(answer.get(key))
    for key in ("strengths", "risks", "red_flags"):
        if isinstance(answer.get(key), list):
            answer[key] = [_tidy(item) for item in answer[key]]
    rating = answer.get("rating")
    if isinstance(rating, dict):
        value = rating.get("value")
        if isinstance(value, str):
            value = None if value.strip().lower() in NO_RATING_WORDS else value.strip().lower()
        answer["rating"] = {**rating, "value": value, "reasoning": _tidy(rating.get("reasoning"))}
    return answer


def generate_report(
    data: dict[str, Any],
    *,
    file: str = "",
    provider: str = "gemini",
    model: str | None = None,
    language: str = DEFAULT_LANGUAGE,
    client: Any = None,
    max_tokens: int | None = None,
    decision: RatingDecision | None = None,
    prompt_path: Path = PROMPT_PATH,
    worsening_pct: float = 20.0,
    rating_config: RatingConfig | None = None,
    recommendation_config: RecommendationConfig | None = None,
) -> ReportRun:
    """Call the LLM and return the validated report. Errors are recorded in `run.error`, not raised."""
    if provider not in PROVIDERS:
        raise ValueError(f"unknown provider {provider!r}, use one of {PROVIDERS}")
    model = model or os.getenv(MODEL_ENV[provider]) or DEFAULT_MODELS[provider]
    decision = decision or rate(data, rating_config)
    run = ReportRun(
        file=file,
        provider=provider,
        model=model,
        language=language,
        prompt_version=prompt_version(prompt_path),
        rating=decision,
    )
    started = time.perf_counter()
    try:
        client = client or make_client(provider)
        if hasattr(client, "max_retries"):
            client.max_retries = min(client.max_retries, MAX_RETRIES)
        if hasattr(client, "retry_statuses"):  # a "busy" (503) answer may count against a free quota: no retry
            client.retry_statuses = tuple(code for code in client.retry_statuses if code != 503)
        run.raw = CALLS[provider](
            client,
            model,
            build_prompt(language, prompt_path, worsening_pct),
            user_message(data, decision),
            max_tokens or MAX_TOKENS_BY_PROVIDER[provider],
            run,
        )
        run.report = AssessmentReport.model_validate(_clean(run.raw, language))
        _apply_rules_rating(run.report, decision, run)
        add_recommendation(run, data, recommendation_config, language)
    except Exception as exc:  # validation, API, network: kept in the run, the caller decides (e.g. stop on a quota)
        run.report, run.error = None, f"{type(exc).__name__}: {exc}"
    run.seconds = round(time.perf_counter() - started, 2)
    return run


# ---------------------------------------------------------------------------------------------- translation

_NUMBER = re.compile(r"\d+(?:[ \u00a0]\d{3})*(?:[.,]\d+)?")


def _text_fields(report: AssessmentReport) -> list[str]:
    """All text of the report in a fixed order (the fields the model writes)."""
    return [
        report.summary,
        *report.strengths,
        *report.risks,
        *report.red_flags,
        report.rating.reasoning,
        report.data_quality_note,
    ]


def numbers_in(texts_: list[str]) -> Counter[str]:
    """The numbers of some text as values (`1 391,2` and `1391.2` are the same number)."""
    found: Counter[str] = Counter()
    for text in texts_:
        for match in _NUMBER.findall(text):
            found[match.replace("\u00a0", "").replace(" ", "").replace(",", ".")] += 1
    return found


def translation_user_message(report: AssessmentReport) -> str:
    return json.dumps(report.model_dump(exclude={"recommendation"}), ensure_ascii=False, indent=1)


def translate_report(
    english: ReportRun,
    data: dict[str, Any],
    *,
    language: str,
    model: str | None = None,
    client: Any = None,
    max_tokens: int | None = None,
    prompt_path: Path = TRANSLATION_PROMPT_PATH,
    recommendation_config: RecommendationConfig | None = None,
) -> ReportRun:
    """Translate an English report, so every language carries the same analysis. Errors stay in `run.error`.

    The rating and the recommendation are not translated by the model: the code sets them. Checks: same number of
    items in each list, and every number of the English text must be in the translation (a difference is a warning).
    """
    provider = english.provider
    model = model or english.model
    run = ReportRun(
        file=english.file,
        provider=provider,
        model=model,
        language=language,
        prompt_version=f"{english.prompt_version}+{prompt_version(prompt_path)}",
        rating=english.rating,
    )
    started = time.perf_counter()
    try:
        if english.report is None:
            raise ValueError("there is no English report to translate")
        client = client or make_client(provider)
        if hasattr(client, "max_retries"):
            client.max_retries = min(client.max_retries, MAX_RETRIES)
        if hasattr(client, "retry_statuses"):
            client.retry_statuses = tuple(code for code in client.retry_statuses if code != 503)
        run.raw = CALLS[provider](
            client,
            model,
            build_prompt(language, prompt_path),
            translation_user_message(english.report),
            max_tokens or MAX_TOKENS_BY_PROVIDER[provider],
            run,
        )
        run.report = AssessmentReport.model_validate(_clean(run.raw, language))
        run.report.rating.value = english.report.rating.value  # not the model's to change
        for name in ("strengths", "risks", "red_flags"):
            if len(getattr(run.report, name)) != len(getattr(english.report, name)):
                raise ValueError(f"the translation changed the number of items in {name}")
        missing = numbers_in(_text_fields(english.report)) - numbers_in(_text_fields(run.report))
        added = numbers_in(_text_fields(run.report)) - numbers_in(_text_fields(english.report))
        if missing or added:
            run.warnings.append(f"numbers differ from the English text: missing {sorted(missing)}, new {sorted(added)}")
        add_recommendation(run, data, recommendation_config, language)
    except Exception as exc:
        run.report, run.error = None, f"{type(exc).__name__}: {exc}"
    run.seconds = round(time.perf_counter() - started, 2)
    return run


# ---------------------------------------------------------------------------------------------- readable report


def report_markdown(run: ReportRun, data: dict[str, Any]) -> str:
    """The report as a page for a human reviewer, with the review questions of the team at the end."""
    t = texts(run.language)
    company = data["company"]
    title = (
        f"{company['name'] or t['no_company']} (IČO {company['ico'] or '?'}), "
        f"{t['period_ending']} {company['period_end']}"
    )
    lines = [f"# {title}", ""]
    if run.report is None:
        lines += [f"**{t['no_report']}** {run.error}", ""]
        return "\n".join(lines)
    report = run.report
    rating = t["rating"][report.rating.value]
    quality = data["data_quality"]["overall"]
    lines += [
        f"**{t['rating_line'].format(rules=', '.join(run.rating.rules), rating=rating)}**",
        "",
        f"{t['data_quality_input']} {t['quality'].get(quality, quality)}",
        "",
        f"## {t['summary']}",
        report.summary,
        "",
    ]
    for key, items in (("strengths", report.strengths), ("risks", report.risks), ("red_flags", report.red_flags)):
        lines += [f"## {t[key]}"] + ([f"- {i}" for i in items] or [f"- {t['none']}"]) + [""]
    lines += [f"## {t['rating_reasoning']}", report.rating.reasoning, ""]
    if report.recommendation:
        lines += [
            f"## {t['recommendation']}",
            f"**{report.recommendation.label}**",
            "",
            f"*{t['recommendation_note']}*",
            "",
            t["criteria"],
            *[f"- {c}" for c in report.recommendation.criteria],
            "",
        ]
    lines += [f"## {t['data_quality_note']}", report.data_quality_note, ""]
    lines += figures_markdown(data, run.language)
    from src.report.recommendation import rule_rows

    rules_title, rules_intro, rules_header, rules_body = rule_rows(run.language, run.rating.partial)
    lines += [
        "",
        f"## {rules_title}",
        rules_intro,
        "",
        "| " + " | ".join(rules_header) + " |",
        "| " + " | ".join("---" for _ in rules_header) + " |",
        *["| " + " | ".join(row) + " |" for row in rules_body],
        "",
    ]
    lines += [
        "---",
        t["footer"].format(
            model=f"{run.provider}/{run.model}", prompt=run.prompt_version, rules=run.rating.rules_version
        ),
        "",
        f"## {t['review_title']}",
        *[f"- [ ] {item}" for item in t["review"]],
        f"- {t['comments']}",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------------------------- review pack


def review_pack(reports_dir: Path) -> str:
    """All saved reports (`<file>.md` and `<file>.json` of scripts/generate_reports.py) in one page for the team."""
    rows, pages, versions = [], [], set()
    for path in sorted(reports_dir.glob("*.json")):
        saved = json.loads(path.read_text(encoding="utf-8"))
        data, rating = saved["input"], saved["rating"]
        name = data["company"]["name"] or "unknown"
        status = "report" if saved.get("report") else "ERROR"
        version = saved.get("prompt_version") or "?"
        if saved.get("report"):
            versions.add(version)
        label = f"{rating['value']} risk" if rating["value"] else "no rating"
        recommendation = ((saved.get("report") or {}).get("recommendation") or {}).get("label", "–")
        rows.append(
            f"| {path.stem} | {name} | {label} ({','.join(rating['rules'])}) | {recommendation} "
            f"| {data['data_quality']['overall']} | {status} | {version} | |"
        )
        md = path.with_suffix(".md")
        if md.exists():
            pages.append(md.read_text(encoding="utf-8"))
    head = [
        "# AI assessment reports for review (CIPW-16)",
        "",
        "Each reviewer takes 2 to 3 companies and fills in the checklist at the end of each report.",
        "",
        rules_table(),
        "| file | company | risk rating (rules) | recommendation | input data quality | report | prompt | reviewer |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
        *rows,
        "",
    ]
    if len(versions) > 1:
        head += [
            f"**Warning: the reports come from {len(versions)} different prompt versions "
            f"({', '.join(sorted(versions))}).**",
            "",
        ]
    return "\n".join(head) + "\n\n---\n\n".join(pages)
