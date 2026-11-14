"""AI assessment reports of the ground truth companies (CIPW-16, step 5).

    python scripts/generate_reports.py --dry-run                      # no call: prompt, input and rating per company
    python scripts/generate_reports.py                                # all files in data/ground_truth/, by `llm`
    python scripts/generate_reports.py 26161516_2025 --language cs
    python scripts/generate_reports.py --gemini                       # tuning the prompt with a free Gemini key
    python scripts/generate_reports.py --pack                         # no call: all saved reports in one page

The input of each company is built from the verified ground truth (`scripts/build_assessment_inputs.py`), the rating
comes from the rules (`src/report/rating.py`, a proposal), the LLM writes the text (`prompts/assessment_v8.md`).
By default the model is the one of `settings.llm` through `src.llm.ask_json` (Mistral's API or an own server, offline
mode refused, key from MISTRAL_API_KEY in the environment or .env, never from this repo).
`--gemini` is only for tuning the prompt on the PUBLIC ground truth with a free key (GEMINI_API_KEY): it is a flag of
this script, not a setting, and the pipeline and the app never use it.
Answers are saved in outputs/reports/cache (same prompt, input, model and language: no second call, no second
charge); --force calls again. Stops when a daily quota is used up.

With --pack: outputs/reports/review_pack.md, the saved reports of all companies in one page with a table (for the team).
Writes, per company, outputs/reports/<file>.md (for the team review) and outputs/reports/<file>.json (input, rating,
report, tokens), plus outputs/reports/summary.md. outputs/ is not in git.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from build_assessment_inputs import assessment_input  # noqa: E402

from src.extraction.vision import DEFAULT_MODELS, MODEL_ENV, ApiError, is_daily_quota, make_client  # noqa: E402
from src.llm import Reply  # noqa: E402
from src.report import export_docx, generate  # noqa: E402
from src.report.models import AssessmentReport  # noqa: E402
from src.report.rating import RatingDecision, rate  # noqa: E402
from src.result import LLMUsage  # noqa: E402
from src.settings import get_settings  # noqa: E402

GEMINI_MAX_TOKENS = 16000  # Gemini counts its thinking
MAX_RETRIES = 2  # "busy" (503) and "too many requests" (429): every retry can count against a free quota


def gemini_ask(model: str) -> generate.Ask:
    """The Gemini flag: a free key to tune the prompt on the public ground truth. Few retries, none for a 503."""
    client = make_client("gemini")
    client.max_retries = min(client.max_retries, MAX_RETRIES)
    client.retry_statuses = tuple(code for code in client.retry_statuses if code != 503)

    def ask(system: str, user: str) -> Reply:
        body: dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
        }
        config: dict[str, Any] = {"maxOutputTokens": GEMINI_MAX_TOKENS, "responseMimeType": "application/json"}
        try:
            data = client.generate(model, {**body, "generationConfig": {**config, "temperature": 0}})
        except ApiError as exc:
            if exc.status_code != 400 or "temperature" not in str(exc).lower():
                raise
            data = client.generate(model, {**body, "generationConfig": config})
        usage = data.get("usageMetadata") or {}
        candidates = data.get("candidates") or []
        if not candidates:
            raise ValueError(f"no answer from the model: {str(data.get('promptFeedback') or data)[:300]}")
        if candidates[0].get("finishReason") == "MAX_TOKENS":
            raise ValueError("answer cut off at max_tokens")
        parts = (candidates[0].get("content") or {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        if not text.strip():
            raise ValueError(f"the model returned no text (finish reason {candidates[0].get('finishReason')})")
        answer = json.loads(text)
        if not isinstance(answer, dict):
            raise ValueError("the answer is not a JSON object")
        tokens_out = usage.get("candidatesTokenCount", 0) + usage.get("thoughtsTokenCount", 0)
        return Reply(
            data=answer,
            usage=LLMUsage(input_tokens=usage.get("promptTokenCount", 0), output_tokens=tokens_out, cost_usd=0.0),
            model=model,
        )

    return ask


def cache_key(
    data: dict[str, Any],
    decision: RatingDecision,
    model: str,
    language: str,
    prompt: Path,
    worsening_pct: float = 20.0,
) -> str:
    system = generate.build_prompt(language, prompt, worsening_pct)
    text = system + generate.user_message(data, decision) + model + language
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def load_cached(path: Path, decision: RatingDecision, model: str, language: str) -> generate.ReportRun | None:
    if not path.exists():
        return None
    saved = json.loads(path.read_text(encoding="utf-8"))
    return generate.ReportRun(
        file=saved["file"],
        model=model,
        language=language,
        prompt_version=saved["prompt_version"],
        rating=decision,
        report=AssessmentReport.model_validate(saved["report"]),
        raw=saved.get("raw"),
        input_tokens=saved.get("input_tokens", 0),
        output_tokens=saved.get("output_tokens", 0),
        cost_usd=saved.get("cost_usd", 0.0),
        warnings=saved.get("warnings", []),
    )


def save_cache(path: Path, run: generate.ReportRun) -> None:
    assert run.report is not None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "file": run.file,
                "prompt_version": run.prompt_version,
                "report": run.report.model_dump(),
                "raw": run.raw,
                "input_tokens": run.input_tokens,
                "output_tokens": run.output_tokens,
                "cost_usd": run.cost_usd,
                "warnings": run.warnings,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def translation_key(english: generate.ReportRun, model: str, language: str, prompt: Path) -> str:
    assert english.report is not None
    system = generate.build_prompt(language, prompt)
    text = system + generate.translation_user_message(english.report) + model + language
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def write_files(out: Path, stem: str, run: generate.ReportRun, data: dict[str, Any]) -> None:
    """The page (.md) and the data (.json) of a report; an error never replaces a report that is already saved."""
    out.mkdir(parents=True, exist_ok=True)
    if not run.ok and (out / f"{stem}.md").exists():
        print("    (the saved report of this company is kept)")
        return
    (out / f"{stem}.md").write_text(generate.report_markdown(run, data), encoding="utf-8")
    (out / f"{stem}.json").write_text(
        json.dumps(
            {
                "input": data,
                "rating": run.rating.model_dump(),
                "report": run.report.model_dump() if run.report else None,
                "model": run.model,
                "language": run.language,
                "prompt_version": run.prompt_version,
                "tokens": [run.input_tokens, run.output_tokens],
                "warnings": run.warnings,
                "error": run.error,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


BUSY_STOP = 3  # "busy" (503) answers in a row after which the run stops: every attempt can count against a quota


def report_dir(language: str) -> str:
    """English reports stay in outputs/reports; another language has its own folder (outputs/reports_cs)."""
    return "reports" if language.lower()[:2] == "en" else f"reports_{language.lower()[:2]}"


def get_report(
    stem: str,
    data: dict[str, Any],
    decision: RatingDecision,
    language: str,
    args: argparse.Namespace,
    report_cfg: Any,
    model: str,
    prompt: Path,
    settings: Any,
    english: generate.ReportRun | None,
    ask: generate.Ask | None,
) -> tuple[generate.ReportRun, str]:
    """The report of one company in one language: from the saved answer, or by a model call.

    English is written by the assessment prompt; another language is the translation of the English report
    (`english`), so that every language carries the same analysis.
    """
    out = settings.paths.outputs / report_dir(language)
    if language == "en":
        key = cache_key(data, decision, model, "en", prompt, report_cfg.worsening_pct)
    else:
        assert english is not None
        key = translation_key(english, model, language, generate.TRANSLATION_PROMPT_PATH)
    cache = out / "cache" / f"{stem}_{key}.json"
    run = None if args.force else load_cached(cache, decision, model, language)
    status = "cached"
    if run is None:
        if language == "en":
            run = generate.generate_report(
                data,
                file=stem,
                language="en",
                ask=ask,
                settings=settings,
                decision=decision,
                prompt_path=prompt,
                worsening_pct=report_cfg.worsening_pct,
                recommendation_config=report_cfg.recommendation,
            )
        else:
            run = generate.translate_report(
                english, data, language=language, ask=ask, settings=settings,
                recommendation_config=report_cfg.recommendation,
            )  # fmt: skip
        status = "ok" if run.ok else "ERROR"
        if run.ok:
            save_cache(cache, run)
    else:  # the recommendation is not part of the model's answer: a changed table needs no new call
        generate.add_recommendation(run, data, report_cfg.recommendation, language)
    if language == "en" and args.language != "en":  # the English page is also kept
        write_files(out, stem, run, data)
    return run, status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("files", nargs="*", help="ground truth names (without .json); default: all")
    parser.add_argument(
        "--gemini",
        action="store_true",
        help="tune the prompt on the public ground truth with a free Gemini key (GEMINI_API_KEY); default: `llm`",
    )
    parser.add_argument("--model", help="with --gemini: the Gemini model (default: GEMINI_MODEL or the project's)")
    parser.add_argument("--language", help="report language, ISO 639-1; default: report.language in config/fides.yaml")
    parser.add_argument(
        "--prompt", default=generate.PROMPT_PATH.name, help="prompt file in prompts/ (to compare versions)"
    )
    parser.add_argument("--pack", action="store_true", help="no model call: one page with all saved reports")
    parser.add_argument("--dry-run", action="store_true", help="no model call")
    parser.add_argument(
        "--docx", action="store_true", help="also write one Word file with the reports (saved answers cost no call)"
    )
    parser.add_argument("--force", action="store_true", help="ignore saved answers")
    args = parser.parse_args(argv)
    if args.pack:
        language = args.language or get_settings().report.language
        out = get_settings().paths.outputs / report_dir(language)
        (out / "review_pack.md").write_text(generate.review_pack(out), encoding="utf-8")
        print(f"Written to {out / 'review_pack.md'}")
        return 0

    from dotenv import load_dotenv

    load_dotenv()  # the keys and model names of .env, as in scripts/run_vision_baseline.py

    settings = get_settings()
    report_cfg = settings.report
    args.language = args.language or report_cfg.language
    prompt = ROOT / "prompts" / args.prompt
    if args.gemini:
        model = args.model or os.getenv(MODEL_ENV["gemini"]) or DEFAULT_MODELS["gemini"]
    else:
        model = settings.llm.model
        if args.model:
            parser.error("--model is only for --gemini; the model of the report is `llm.model` (MISTRAL_MODEL)")
        if not args.dry_run and not settings.llm_enabled:
            print("No AI model connected: offline mode, or no MISTRAL_API_KEY. Set the key in .env, or use --gemini "
                  "on the public ground truth.")  # fmt: skip
            return 2
    ask = gemini_ask(model) if args.gemini and not args.dry_run else None
    gt_dir = settings.paths.ground_truth
    out = settings.paths.outputs / report_dir(args.language)
    paths = [gt_dir / f"{n}.json" for n in args.files] or sorted(
        p for p in gt_dir.glob("*.json") if not p.name.startswith("_")
    )
    print(
        f"model {model} ({'Gemini flag' if args.gemini else 'llm settings'}), language {args.language}, "
        f"prompt {args.prompt} ({generate.prompt_version(prompt)})"
    )
    if args.dry_run:
        system = generate.build_prompt(args.language, prompt, report_cfg.worsening_pct)
        print(f"system prompt: {len(system)} characters")

    busy_in_a_row = 0
    finished: list[tuple[generate.ReportRun, dict[str, Any]]] = []
    summary = [
        "| file | company | rating (rule) | status | tokens in/out | cost USD |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for path in paths:
        data = assessment_input(path)
        decision = rate(data, report_cfg.rating)
        name = (data["company"]["name"] or "")[:24]
        rating = f"{decision.value or 'none'} ({','.join(decision.rules)})"
        if args.dry_run:
            size = len(generate.user_message(data, decision))
            print(f"{path.stem:16} {name:25} {rating:14} input {size} characters")
            continue
        run, status = get_report(
            path.stem, data, decision, "en", args, report_cfg, model, prompt, settings, None, ask
        )
        if args.language != "en":
            english = run
            if english.ok:  # one analysis, translated: every language says the same
                run, status = get_report(
                    path.stem, data, decision, args.language, args, report_cfg, model, prompt, settings, english, ask
                )
            else:
                run, status = english, "ERROR"
        cost = run.cost_usd
        print(f"{path.stem:16} {name:25} {rating:14} {status:7} {run.input_tokens}/{run.output_tokens} tokens")
        for warning in run.warnings:
            print(f"    warning: {warning}")
        if run.error:
            print(f"    {run.error[:300]}")
        summary.append(
            f"| {path.stem} | {name} | {rating} | {status} | {run.input_tokens}/{run.output_tokens} | "
            f"{'-' if not cost else f'{cost:.4f}'} |"
        )
        write_files(settings.paths.outputs / report_dir(run.language), path.stem, run, data)
        if run.ok:
            finished.append((run, data))
        busy_in_a_row = busy_in_a_row + 1 if "HTTP 503" in (run.error or "") else 0
        if busy_in_a_row >= BUSY_STOP:
            print("STOP: the service is busy; try again later (finished answers are saved).")
            break
        if run.error and is_daily_quota(run.error):
            print("STOP: daily quota used up; run the same command tomorrow (finished answers are saved).")
            break
    if not args.dry_run:
        out.mkdir(parents=True, exist_ok=True)
        (out / "summary.md").write_text("\n".join(summary) + "\n", encoding="utf-8")
        print(f"\nWritten to {out}")
        if args.docx and finished:
            word = export_docx.build_docx(finished, out / f"Fides_AI_assessment_{args.language.upper()[:2]}.docx")
            print(f"Word file: {word}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
