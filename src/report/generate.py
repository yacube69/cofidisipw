"""The AI assessment call (CIPW-16): assessment input -> LLM -> `AssessmentReport`.

The LLM gets the prompt `prompts/assessment_v8.md` as system prompt and, as user message, the assessment input
(`build_assessment_input`) plus the rating decided by the rules (`rate`). It answers with one JSON object, validated
with Pydantic. The rating is not the model's: if it differs from the rule's, the rule's value and reasoning are
written back and a warning says so (rating approach A of the ticket, still a proposal until the team decides).

The model is reached through `src.llm.ask_json` (CIPW-58): Mistral's API or a Mistral model on a server of Cofidis' own,
the address, model, timeout, retries and cost cap of `settings.llm`, the key from MISTRAL_API_KEY, and no call at all
in offline mode. There is no other provider here and none in the settings. The script `scripts/generate_reports.py`
passes its own `ask` function with its `--gemini` flag, to tune the prompt on the public ground truth with a free
key; that is for that script only, never for the pipeline or the app.
"""

from __future__ import annotations

import json
import re
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.llm import Reply, ask_json
from src.report.models import AssessmentReport
from src.report.rating import RatingConfig, RatingDecision, rate, rules_table
from src.report.recommendation import RecommendationConfig, recommend
from src.report.tables import figures_markdown
from src.report.texts import period_date, texts
from src.settings import Settings, get_settings
from src.version import file_version

ROOT = Path(__file__).resolve().parents[2]
PROMPT_PATH = ROOT / "prompts" / "assessment_v8.md"
TRANSLATION_PROMPT_PATH = ROOT / "prompts" / "translation_v4.md"
DEFAULT_LANGUAGE = "en"
MAX_TOKENS = 4000
NO_RATING_WORDS = ("none", "null", "")

Ask = Callable[[str, str], Reply]  # (system prompt, user message) -> the model's JSON answer with tokens and cost


@dataclass
class ReportRun:
    """One assessment call: the report (or the error), what was asked and what it cost."""

    file: str
    model: str
    language: str
    prompt_version: str
    rating: RatingDecision
    report: AssessmentReport | None = None
    raw: dict[str, Any] | None = None  # the model's answer exactly as returned
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0  # 0 when the prices are not set (settings.llm.price_*) or the key is a free one
    seconds: float = 0.0
    error: str | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.report is not None and self.error is None


def default_ask(settings: Settings | None = None) -> Ask:
    """The model of `settings.llm` through `src.llm.ask_json`: Mistral, own server, offline mode refused."""
    settings = settings or get_settings()
    return lambda system, user: ask_json(system, user, settings, max_tokens=MAX_TOKENS)


def _ask(run: ReportRun, ask: Ask, settings: Settings, system: str, user: str) -> dict[str, Any]:
    """One call: the tokens and the cost go into the run; a call above the cost cap is an error, not a report."""
    reply = ask(system, user)
    run.model = reply.model
    run.input_tokens, run.output_tokens = reply.usage.input_tokens, reply.usage.output_tokens
    run.cost_usd = reply.usage.cost_usd
    if reply.usage.cost_usd > settings.llm.max_cost_usd:
        raise ValueError(
            f"the call cost USD {reply.usage.cost_usd:.2f}, above llm.max_cost_usd ({settings.llm.max_cost_usd:.2f})"
        )
    return reply.data


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


def user_message(data: dict[str, Any], decision: RatingDecision) -> str:
    """The assessment input with the rule-based rating added, as JSON."""
    rating = {"value": decision.value, "rules": decision.rules, "reasoning": decision.reasoning}
    return json.dumps({**data, "rating": rating}, ensure_ascii=False, indent=1)


# ---------------------------------------------------------------------------------------------- the report


def _apply_rules_rating(report: AssessmentReport, decision: RatingDecision, run: ReportRun) -> None:
    """The rating is the rules': a different value from the model is replaced and said, with the rules' reasoning
    (the model's paragraph would argue for its own rating)."""
    if report.rating.value != decision.value:
        run.warnings.append(
            f"the model gave rating {report.rating.value!r}, the rules give {decision.value!r}: kept the rules'"
        )
        report.rating.value = decision.value
        report.rating.reasoning = decision.reasoning


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
    language: str = DEFAULT_LANGUAGE,
    ask: Ask | None = None,
    settings: Settings | None = None,
    decision: RatingDecision | None = None,
    prompt_path: Path = PROMPT_PATH,
    worsening_pct: float = 20.0,
    rating_config: RatingConfig | None = None,
    recommendation_config: RecommendationConfig | None = None,
) -> ReportRun:
    """Ask the model and return the validated report. Errors are recorded in `run.error`, not raised.

    `ask` is `src.llm.ask_json` on `settings.llm` unless a script passes its own (the free-key flag of
    scripts/generate_reports.py, the fakes of the tests)."""
    settings = settings or get_settings()
    decision = decision or rate(data, rating_config)
    run = ReportRun(
        file=file,
        model=settings.llm.model,
        language=language,
        prompt_version=prompt_version(prompt_path),
        rating=decision,
    )
    started = time.perf_counter()
    try:
        run.raw = _ask(
            run,
            ask or default_ask(settings),
            settings,
            build_prompt(language, prompt_path, worsening_pct),
            user_message(data, decision),
        )
        run.report = AssessmentReport.model_validate(_clean(run.raw, language))
        _apply_rules_rating(run.report, decision, run)
        add_recommendation(run, data, recommendation_config, language)
    except Exception as exc:  # validation, API, network: kept in the run, the caller decides (e.g. stop on a quota)
        run.report, run.error = None, f"{type(exc).__name__}: {exc}"
    run.seconds = round(time.perf_counter() - started, 2)
    return run


# ---------------------------------------------------------------------------------------------- translation

# a minus sign counts when it is not glued to a word or a digit (so "2024-12-31" has no negative numbers)
_NUMBER = re.compile(r"(?:(?<![\w])[-\u2212])?\d+(?:[ \u00a0\u202f]\d{3})*(?:[.,]\d+)?")


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
            value = match.replace("\u00a0", "").replace("\u202f", "").replace(" ", "").replace(",", ".")
            found[value.replace("\u2212", "-")] += 1
    return found


def translation_user_message(report: AssessmentReport) -> str:
    return json.dumps(report.model_dump(exclude={"recommendation", "source"}), ensure_ascii=False, indent=1)


def translate_report(
    english: ReportRun,
    data: dict[str, Any],
    *,
    language: str,
    ask: Ask | None = None,
    settings: Settings | None = None,
    prompt_path: Path = TRANSLATION_PROMPT_PATH,
    recommendation_config: RecommendationConfig | None = None,
) -> ReportRun:
    """Translate an English report, so every language carries the same analysis. Errors stay in `run.error`.

    The rating and the recommendation are not translated by the model: the code sets them. Checks: same number of
    items in each list, and every number of the English text must be in the translation (a difference is a warning).
    """
    settings = settings or get_settings()
    run = ReportRun(
        file=english.file,
        model=english.model,
        language=language,
        prompt_version=f"{english.prompt_version}+{prompt_version(prompt_path)}",
        rating=english.rating,
    )
    started = time.perf_counter()
    try:
        if english.report is None:
            raise ValueError("there is no English report to translate")
        run.raw = _ask(
            run,
            ask or default_ask(settings),
            settings,
            build_prompt(language, prompt_path),
            translation_user_message(english.report),
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
        f"{t['period_ending']} {period_date(run.language, company['period_end'])}"
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

    rules_title, rules_intro, rules_header, rules_body = rule_rows(
        run.language, run.rating.partial, run.rating.disabled_rules
    )
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
            model=run.model, prompt=run.prompt_version, rules=run.rating.rules_version
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
    rows, pages, versions, partial, disabled = [], [], set(), False, set()
    for path in sorted(reports_dir.glob("*.json")):
        saved = json.loads(path.read_text(encoding="utf-8"))
        data, rating = saved["input"], saved["rating"]
        partial = partial or bool(rating.get("partial"))
        disabled |= set(rating.get("disabled_rules") or [])
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
        rules_table(partial, sorted(disabled)),
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
