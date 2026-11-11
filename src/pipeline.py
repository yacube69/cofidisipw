"""End-to-end pipeline: PDF → Result (CIPW-40 skeleton, completed in CIPW-24).

    locate → read → parse → validate → reread → ratios → bankruptcy → benchmarks → report → check_numbers

Every stage is a function `stage(result, ctx) -> StageStatus`. A stage that is not implemented yet returns
"stub" and fills the `Result` with the mock data of one real company (data/fixtures/mock_26161516_2025.json,
Mondi 2025, whatever PDF is read), so the app and the CLI work end to end from day one. Track owners replace a
stub by pointing STAGES at the real module.

Settings (OCR, offline mode, paths) come from src/settings.py. Every run writes a JSON-lines log to
settings.paths.logs and fills `Result.audit` with what produced the result (CIPW-45).

A stage that cannot do its job raises StageFailed (expected problem, e.g. no statement pages found) or any other
exception (bug). Both mark the stage "error", and the stages that need its output (REQUIRES) are "skipped" instead
of running on empty data. The CLI exits with 1 when a stage of any file failed.

CLI:
    python -m src.pipeline data/raw/26161516_2025_statement.pdf --out outputs/ [--skip-report] [--mode ocr]
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
import time
import traceback
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from functools import cache
from pathlib import Path

import pymupdf

from src.extraction.locate import locate_one_set
from src.extraction.numbers import detect_format, detect_unit
from src.extraction.parallel import pmap
from src.extraction.parser import parse_statements
from src.extraction.reading import read_page
from src.extraction.reread import reread
from src.integrations.ares import lookup, name_differs
from src.line_items import load_dictionary
from src.ratios.bankruptcy import compute_bankruptcy
from src.ratios.benchmarks import SOURCE as BENCHMARK_SOURCE
from src.ratios.benchmarks import industry_benchmarks, industry_group, light
from src.ratios.engine import compute_ratios
from src.report.fallback import rule_based_report
from src.result import Audit, PipelineOptions, Result, StageRun, StageStatus
from src.runlog import RunLog
from src.settings import ROOT, Settings, get_settings
from src.validation.review import validate_and_review
from src.version import file_version, git_info, package_version, python_version, tesseract_version

MOCK_NAME = "mock_26161516_2025.json"  # in settings.paths.fixtures
FEW_LINES = 5  # fewer lines with a value than the smallest statement prints (a micro balance sheet has 9)
LOW_OCR_CONF = 60  # mean word confidence of the OCR pages below which a scan is hard to read
NOT_READ = {
    "ifrs": "the statements look like IFRS (výkaz o finanční pozici, úplný výsledek). Fides reads the Czech form "
    "of Vyhláška 500/2002 Sb.; IFRS lines are found only where their names match it, so values are missing",
    "bank": "these look like bank statements (Vyhláška 501/2002 Sb.), which Fides does not read; values are "
    "missing or on other lines",
}
REPORT_PROMPT = "report.md"  # in settings.paths.prompts (CIPW-16)


class StageFailed(Exception):
    """An expected failure of a stage (no pages found, no text read): reported without a traceback."""


@cache
def _mock_data() -> dict:
    return json.loads((get_settings().paths.fixtures / MOCK_NAME).read_text(encoding="utf-8"))


def mock(key: str):
    """A fresh copy of one section of the mock data, so a stage cannot change it for the next run."""
    return copy.deepcopy(_mock_data()[key])


@dataclass
class Context:
    pdf_path: Path
    options: PipelineOptions
    doc: pymupdf.Document
    settings: Settings = field(default_factory=get_settings)
    notes: dict[str, str] = field(default_factory=dict)  # stage -> note for a status without an exception
    extra: dict = field(default_factory=dict)


Stage = Callable[[Result, Context], StageStatus]


# ------------------------------------------------------------------------------------- stages (stubs)


def stage_locate(result: Result, ctx: Context) -> StageStatus:
    """CIPW-6: find the balance sheet and income statement pages (or take them from the options).

    Group annual reports hold an individual and a consolidated set of statements: only one set is read (the
    `scope` option, individual by default), so their numbers are never mixed."""
    if ctx.options.pages:
        n = ctx.doc.page_count
        outside = sorted({p for pages in ctx.options.pages.values() for p in pages if not 1 <= p <= n})
        if outside:
            raise StageFailed(f"pages {outside} out of range (the PDF has {n} pages)")
        result.pages = ctx.options.pages
    else:
        report: dict = {}
        result.pages, result.consolidation, other = locate_one_set(ctx.doc, ctx.options.scope, report)
        ctx.notes["locate"] = f"{report.get('locator', '')} locator"
        if result.consolidation != ctx.options.scope and result.consolidation is not None:
            result.warnings.append(
                f"locate: no {ctx.options.scope} statements found, read the {result.consolidation} ones"
            )
        elif any(other.values()):
            pages = sorted({p for ps in other.values() for p in ps})
            rest = "consolidated" if result.consolidation == "individual" else "individual"
            result.warnings.append(f"locate: the PDF also has {rest} statements (pages {pages}), not read")
    if not any(result.pages.values()):
        raise StageFailed("no balance sheet or income statement found; pass the pages manually")
    if not ctx.options.pages and not result.pages.get("balance_sheet"):
        result.warnings.append("locate: no balance sheet found; enter its pages if the PDF has one")
    if not ctx.options.pages and not result.pages.get("income_statement"):
        result.warnings.append(
            "locate: no income statement found (small companies may publish only the balance sheet); enter its "
            "pages if the PDF has one"
        )
    return "ok"


def stage_read(result: Result, ctx: Context) -> StageStatus:
    """CIPW-7: text layer for digital pages, Tesseract OCR for scans."""
    config = ctx.settings.ocr.config()
    if ctx.options.ocr_dpi:
        config = replace(config, dpi=ctx.options.ocr_dpi)
    pages = sorted({p for pages in result.pages.values() for p in pages})
    # pages side by side (CIPW-50): OCR of a scan takes ~15 s a page
    result.page_words = pmap(lambda p: read_page(ctx.doc, p, ctx.options.read_mode, config, ctx.pdf_path.name), pages)
    empty = [pw.page for pw in result.page_words if not pw.words]
    if empty:
        hint = " (read mode 'text' on a scan? use --mode auto or ocr)" if ctx.options.read_mode == "text" else ""
        if len(empty) == len(result.page_words):
            raise StageFailed(f"no text on pages {empty}{hint}")
        result.warnings.append(f"read: no text on pages {empty}{hint}")
    return "ok"


def stage_parse(result: Result, ctx: Context) -> StageStatus:
    """CIPW-32: rule-based parser, words with positions -> FinancialStatements."""
    result.statements = parse_statements(result.page_words, result.pages)
    meta = result.statements.metadata
    meta.source_file = ctx.pdf_path.name
    parts = ctx.pdf_path.stem.split("_")  # ICO_year_type.pdf
    if meta.ico is None and parts[0].isdigit() and len(parts[0]) == 8:
        meta.ico = parts[0]
    if detect_unit("\n".join(pw.text for pw in result.page_words)) is None:
        result.warnings.append("parse: unit not printed on the statement pages, assumed tis. Kč")
    if meta.period_end is None and len(parts) > 1 and re.fullmatch(r"(19|20)\d{2}", parts[1]):
        year = int(parts[1])
        meta.period_end, meta.prior_period_end = date(year, 12, 31), date(year - 1, 12, 31)
        result.warnings.append(f"parse: balance sheet date not found, assumed 31.12.{year} from the file name")
    result.warnings.extend(f"parse: {note}" for note in _few_values(result))
    return "ok"


def _few_values(result: Result) -> list[str]:
    """Why a statement gave few or no values: a format Fides does not read, a scan too poor to read, or pages
    that are not the statements. Said once, so the user does not take an empty result for a clean one."""
    form = detect_format("\n".join(pw.text for pw in result.page_words))
    if form:
        return [NOT_READ[form]]
    confs = [w.conf for pw in result.page_words if pw.source == "ocr" for w in pw.words
             if w.conf is not None and w.conf >= 0]  # fmt: skip
    if confs and sum(confs) / len(confs) < LOW_OCR_CONF:
        return [f"the scan is hard to read (mean OCR confidence {sum(confs) / len(confs):.0f} %): values may be "
                "missing or misread"]  # fmt: skip
    st = result.statements
    lines = sum(any(v.value is not None for v in item.values()) for s in (st.balance_sheet, st.income_statement)
                for item in s.values())  # fmt: skip
    if lines < FEW_LINES:
        pages = sorted({p for ps in result.pages.values() for p in ps})
        return [f"only {lines} lines found on pages {pages}; if these are not the statements, enter the pages"]
    return []


def stage_company(result: Result, ctx: Context) -> StageStatus:
    """CIPW-44: name, legal form, address, industry (CZ-NACE) and size of the company from ARES by its IČO, and a
    warning when ARES does not know the IČO or names another company than the statement (a misread IČO)."""
    meta = result.statements.metadata
    if not meta.ico:
        ctx.notes["company"] = "no IČO on the statement or in the file name"
        return "skipped"
    result.company, note = lookup(meta.ico, ctx.settings)
    ctx.notes["company"] = note
    if result.company is None and ("check digit" in note or "not in ARES" in note):
        result.warnings.append(f"company: {note}; enter the right IČO")
    elif result.company is not None and name_differs(meta.company_name, result.company.name):
        result.warnings.append(
            f"company: the statement names {meta.company_name!r}, ARES {result.company.name!r} for IČO {meta.ico}; "
            "check the IČO"
        )
    return "ok"


def stage_validate(result: Result, ctx: Context) -> StageStatus:
    """CIPW-10: accounting identity checks, plausibility and OCR-quality warnings; the checks restore lost minus
    signs and decide which values need review (src/validation/review.py)."""
    result.validation, notes = validate_and_review(result.statements)
    result.warnings.extend(f"validate: {note}" for note in notes)
    return "ok"


def stage_reread(result: Result, ctx: Context) -> StageStatus:
    """CIPW-11: read the cells the checks doubt again at higher resolution; take readings the checks support."""
    if not ctx.settings.reread.enabled:
        ctx.notes["reread"] = "disabled in the settings"
        return "skipped"
    result.reread = reread(ctx.doc, result.statements, result.validation, ctx.settings.reread.dpi)
    if not result.reread.cells:
        ctx.notes["reread"] = "nothing to re-read"
        return "skipped"
    result.validation, notes = validate_and_review(result.statements)
    result.warnings.extend(f"reread: {c.key} {c.period}: {c.old_text!r} read again as {c.new_text!r}"
                           for c in result.reread.changes)  # fmt: skip
    result.warnings.extend(f"validate: {note}" for note in notes)
    r = result.reread
    ctx.notes["reread"] = f"{r.cells} cells, {len(r.changes)} corrected, failing {r.failed_before}→{r.failed_after}"
    return "ok"


def stage_ratios(result: Result, ctx: Context) -> StageStatus:
    """CIPW-12: ratios for both years; values in failed checks or to review are flagged unreliable."""
    result.ratios = compute_ratios(result.statements, result.validation)
    return "ok"


def stage_bankruptcy(result: Result, ctx: Context) -> StageStatus:
    """CIPW-13: IN05 and Altman Z' for both years, on the ratio engine's definitions."""
    result.bankruptcy = compute_bankruptcy(result.statements, result.validation)
    return "ok"



def stage_benchmarks(result: Result, ctx: Context) -> StageStatus:
    """CIPW-14: a traffic light for every ratio (temporary project limits) and the MPO values of the company's
    industry for reference (src/ratios/benchmarks.py)."""
    result.benchmarks = {
        "mock": False,
        "industry_group": industry_group(result.company),
        "industry_benchmarks": industry_benchmarks(result.company),
        "nace": result.company.nace if result.company else None,
        "nace_2008": result.company.nace_2008 if result.company else None,
        "source": BENCHMARK_SOURCE,
        "ratios": {
            r.key: {
                "value": r.current.value,
                "light": light(r.key, r.current.value, r.current.flag),
                "prior_value": r.prior.value,
                "prior_light": light(r.key, r.prior.value, r.prior.flag),
            }
            for r in result.ratios.ratios
        },
    }
    return "ok"


def stage_report(result: Result, ctx: Context) -> StageStatus:
    """CIPW-16: the assessment. Without an AI model (offline mode, no API key), and until the LLM report is built,
    a rule-based assessment from the same numbers (src/report/fallback.py), marked `source: "rules"`."""
    if ctx.options.skip_report:
        return "skipped"
    result.report = rule_based_report(result, ctx.settings.report.language)
    if ctx.settings.offline:
        ctx.notes["report"] = "offline mode: no LLM call, rule-based assessment"
    elif not ctx.settings.llm_enabled:
        ctx.notes["report"] = "no AI model connected (no API key): rule-based assessment"
    else:
        ctx.notes["report"] = "rule-based assessment (the LLM report of CIPW-16 is not built yet)"
    return "ok"


def stage_check_numbers(result: Result, ctx: Context) -> StageStatus:
    """CIPW-17 (stub): number check of the AI report. A rule-based report takes its numbers from the data itself."""
    if ctx.options.skip_report or result.report is None:
        return "skipped"
    if result.report.get("source") == "rules":
        ctx.notes["check_numbers"] = "rule-based report: its numbers are the data's own"
        return "skipped"
    result.number_check = mock("number_check")
    return "stub"


STAGES: list[tuple[str, Stage]] = [
    ("locate", stage_locate),
    ("read", stage_read),
    ("parse", stage_parse),
    ("company", stage_company),
    ("validate", stage_validate),
    ("reread", stage_reread),
    ("ratios", stage_ratios),
    ("bankruptcy", stage_bankruptcy),
    ("benchmarks", stage_benchmarks),
    ("report", stage_report),
    ("check_numbers", stage_check_numbers),
]

# stages whose output a stage needs: if one of them failed (or was skipped for that reason), the stage is skipped
REQUIRES: dict[str, list[str]] = {
    "read": ["locate"],
    "parse": ["read"],
    "company": ["parse"],
    "validate": ["parse"],
    "reread": ["validate"],
    "ratios": ["validate"],
    "bankruptcy": ["ratios"],
    "benchmarks": ["ratios"],
    "report": ["ratios", "bankruptcy", "benchmarks"],
    "check_numbers": ["report"],
}


def _audit(run_id: str, settings: Settings, started: datetime, log: RunLog) -> Audit:
    dictionary = load_dictionary()
    git = git_info()
    return Audit(
        run_id=run_id,
        tool_version=package_version(),
        git_commit=git["commit"],  # type: ignore[arg-type]
        git_describe=git["describe"],  # type: ignore[arg-type]
        git_dirty=git["dirty"],  # type: ignore[arg-type]
        settings_hash=settings.hash(),
        settings=settings.result_settings(),
        dictionary_version=f"v{dictionary.version} {file_version(settings.paths.dictionary)}",
        offline=settings.offline,
        ocr_config=settings.ocr.config().describe(),
        tesseract_version=tesseract_version(),
        python_version=python_version(),
        started_at=started,
        log_file=_relative(log.path),
    )


def _relative(path: Path | None) -> str | None:
    if path is None:
        return None
    return path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else path.as_posix()


def run_pipeline(pdf_path: str | Path, options: PipelineOptions | None = None) -> Result:
    pdf_path = Path(pdf_path)
    options = options or PipelineOptions()
    settings = get_settings()
    run_id = uuid.uuid4().hex[:12]
    log = RunLog(run_id, pdf_path.name, settings.paths.logs if settings.log_runs else None)
    result = Result(file=pdf_path.name, options=options)
    result.audit = _audit(run_id, settings, datetime.now().astimezone(), log)
    log.event("run_start", file=pdf_path.name, options=options.model_dump(mode="json"),
              audit=result.audit.model_dump(mode="json", exclude={"corrections"}))  # fmt: skip
    unavailable: set[str] = set()  # stages that failed or were skipped because an input was missing
    with pymupdf.open(pdf_path) as doc:
        ctx = Context(pdf_path=pdf_path, options=options, doc=doc, settings=settings)
        for name, stage in STAGES:
            start = time.perf_counter()
            missing = [dep for dep in REQUIRES.get(name, []) if dep in unavailable]
            if missing:
                status, note = "skipped", f"needs {', '.join(missing)}"
            else:
                try:
                    status, note = stage(result, ctx), ctx.notes.get(name)
                except StageFailed as exc:
                    status, note = "error", str(exc)
                    result.errors.append(f"{name}: {note}")
                except Exception as exc:  # one failing stage must not hide what the others produced
                    status, note = "error", f"{type(exc).__name__}: {exc}"
                    result.errors.append(f"{name}: {note}")
                    result.errors.append(traceback.format_exc(limit=3))
            if status == "error" or missing:
                unavailable.add(name)
            run = StageRun(stage=name, status=status, seconds=round(time.perf_counter() - start, 3), note=note)
            result.stages.append(run)
            log.event("stage", **run.model_dump())

    report_by_llm = any(s.stage == "report" and s.status == "ok" for s in result.stages) and (
        (result.report or {}).get("source") != "rules"
    )
    if report_by_llm:  # stubs, skipped and rule-based reports called no model
        result.audit.model = settings.llm.model
        prompt = settings.paths.prompts / REPORT_PROMPT
        result.audit.prompt_version = file_version(prompt) if prompt.exists() else None
    result.audit.finished_at = datetime.now().astimezone()
    checks = result.validation.checks if result.validation else []
    log.event(
        "run_end",
        seconds=round((result.audit.finished_at - result.audit.started_at).total_seconds(), 3),
        failed=result.failed,
        pages=result.pages,
        values=sum(
            len(i.values())
            for st in (result.statements.balance_sheet, result.statements.income_statement)
            for i in st.values()
        ),
        checks_failed=sum(not c.passed and c.severity == "error" for c in checks),
        errors=[e.splitlines()[0] for e in result.errors],
        warnings=result.warnings,
        model=result.audit.model,
    )
    return result


def write_result(result: Result, out_dir: str | Path) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{Path(result.file).stem}.result.json"
    path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m src.pipeline", description="Fides: PDF statement → result.json")
    parser.add_argument("pdf", nargs="+", type=Path, help="PDF file(s) or folder(s)")
    parser.add_argument("--out", type=Path, default=None, help="Output folder (default: settings.paths.outputs)")
    parser.add_argument("--mode", choices=["auto", "text", "ocr"], default="auto", help="Reading mode")
    parser.add_argument("--dpi", type=int, default=None, help="OCR resolution (default: settings.ocr.dpi)")
    parser.add_argument("--skip-report", action="store_true", help="No LLM call (deterministic part only)")
    parser.add_argument(
        "--scope", choices=["individual", "consolidated"], default="individual", help="Statements to read"
    )
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):  # Windows consoles default to cp1250; never crash on output
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")

    files: list[Path] = []
    for p in args.pdf:
        files.extend(sorted(p.glob("*.pdf")) if p.is_dir() else [p])
    if not files:
        print("No PDF files found.", file=sys.stderr)
        return 2

    options = PipelineOptions(read_mode=args.mode, ocr_dpi=args.dpi, skip_report=args.skip_report, scope=args.scope)
    failed = 0
    for pdf in files:
        try:
            result = run_pipeline(pdf, options)
        except Exception as exc:  # batch mode: log and continue
            failed += 1
            print(f"FAILED {pdf.name}: {type(exc).__name__}: {exc}", file=sys.stderr)
            continue
        path = write_result(result, args.out or get_settings().paths.outputs)
        stages = " ".join(f"{s.stage}:{s.status}" for s in result.stages)
        if result.failed:
            failed += 1
        flag = "FAILED" if result.failed else "with warnings" if result.warnings else "ok"
        print(f"{pdf.name} -> {path} ({flag}) [{stages}]", flush=True)
        for msg in result.errors[:1] + result.warnings:
            print(f"  {msg.splitlines()[0]}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
