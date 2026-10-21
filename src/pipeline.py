"""End-to-end pipeline: PDF → Result (CIPW-40 skeleton, completed in CIPW-24).

    locate → read → parse → validate → reread → ratios → bankruptcy → benchmarks → report → check_numbers

Every stage is a function `stage(result, ctx) -> StageStatus`. A stage that is not implemented yet returns
"stub" and fills the `Result` with the mock data of one real company (data/fixtures/mock_26161516_2025.json,
Mondi 2025, whatever PDF is read), so the app and the CLI work end to end from day one. Track owners replace a
stub by pointing STAGES at the real module.

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
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date
from functools import cache
from pathlib import Path

import pymupdf

from src.extraction.locate import locate_statements
from src.extraction.numbers import detect_unit
from src.extraction.ocr import DEFAULT as DEFAULT_OCR
from src.extraction.parser import parse_statements
from src.extraction.reading import read_page
from src.ratios.models import RatioReport
from src.result import PipelineOptions, Result, StageRun, StageStatus
from src.validation.checks import validate

ROOT = Path(__file__).resolve().parents[1]
MOCK_FILE = ROOT / "data" / "fixtures" / "mock_26161516_2025.json"


class StageFailed(Exception):
    """An expected failure of a stage (no pages found, no text read): reported without a traceback."""


@cache
def _mock_data() -> dict:
    return json.loads(MOCK_FILE.read_text(encoding="utf-8"))


def mock(key: str):
    """A fresh copy of one section of the mock data, so a stage cannot change it for the next run."""
    return copy.deepcopy(_mock_data()[key])


@dataclass
class Context:
    pdf_path: Path
    options: PipelineOptions
    doc: pymupdf.Document
    extra: dict = field(default_factory=dict)


Stage = Callable[[Result, Context], StageStatus]


# ------------------------------------------------------------------------------------- stages (stubs)


def stage_locate(result: Result, ctx: Context) -> StageStatus:
    """CIPW-6: find the balance sheet and income statement pages (or take them from the options)."""
    if ctx.options.pages:
        n = ctx.doc.page_count
        outside = sorted({p for pages in ctx.options.pages.values() for p in pages if not 1 <= p <= n})
        if outside:
            raise StageFailed(f"pages {outside} out of range (the PDF has {n} pages)")
        result.pages = ctx.options.pages
    else:
        result.pages = locate_statements(ctx.doc)
    if not any(result.pages.values()):
        raise StageFailed("no balance sheet or income statement found; pass the pages manually")
    return "ok"


def stage_read(result: Result, ctx: Context) -> StageStatus:
    """CIPW-7: text layer for digital pages, Tesseract OCR for scans."""
    config = replace(DEFAULT_OCR, dpi=ctx.options.ocr_dpi)
    pages = sorted({p for pages in result.pages.values() for p in pages})
    result.page_words = [read_page(ctx.doc, p, ctx.options.read_mode, config, ctx.pdf_path.name) for p in pages]
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
    return "ok"


def stage_validate(result: Result, ctx: Context) -> StageStatus:
    """CIPW-10: accounting identity checks, plausibility and OCR-quality warnings."""
    result.validation = validate(result.statements)
    return "ok"


def stage_reread(result: Result, ctx: Context) -> StageStatus:
    return "stub"


def stage_ratios(result: Result, ctx: Context) -> StageStatus:
    """CIPW-12 (stub): mock ratios."""
    result.ratios = RatioReport.model_validate(mock("ratios"))
    return "stub"


def stage_bankruptcy(result: Result, ctx: Context) -> StageStatus:
    """CIPW-13 (stub): mock IN05 and Altman Z' scores."""
    result.bankruptcy = mock("bankruptcy")
    return "stub"


def stage_benchmarks(result: Result, ctx: Context) -> StageStatus:
    """CIPW-14 (stub): mock traffic lights."""
    result.benchmarks = mock("benchmarks")
    return "stub"


def stage_report(result: Result, ctx: Context) -> StageStatus:
    """CIPW-16 (stub): mock AI assessment, no LLM call."""
    if ctx.options.skip_report:
        return "skipped"
    result.report = mock("report")
    return "stub"


def stage_check_numbers(result: Result, ctx: Context) -> StageStatus:
    """CIPW-17 (stub): mock number check of the report."""
    if ctx.options.skip_report:
        return "skipped"
    result.number_check = mock("number_check")
    return "stub"


STAGES: list[tuple[str, Stage]] = [
    ("locate", stage_locate),
    ("read", stage_read),
    ("parse", stage_parse),
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
    "validate": ["parse"],
    "reread": ["validate"],
    "ratios": ["validate"],
    "bankruptcy": ["ratios"],
    "benchmarks": ["ratios"],
    "report": ["ratios", "bankruptcy", "benchmarks"],
    "check_numbers": ["report"],
}


def run_pipeline(pdf_path: str | Path, options: PipelineOptions | None = None) -> Result:
    pdf_path = Path(pdf_path)
    options = options or PipelineOptions()
    result = Result(file=pdf_path.name, options=options)
    unavailable: set[str] = set()  # stages that failed or were skipped because an input was missing
    with pymupdf.open(pdf_path) as doc:
        ctx = Context(pdf_path=pdf_path, options=options, doc=doc)
        for name, stage in STAGES:
            start = time.perf_counter()
            missing = [dep for dep in REQUIRES.get(name, []) if dep in unavailable]
            if missing:
                status, note = "skipped", f"needs {', '.join(missing)}"
            else:
                try:
                    status, note = stage(result, ctx), None
                except StageFailed as exc:
                    status, note = "error", str(exc)
                    result.errors.append(f"{name}: {note}")
                except Exception as exc:  # one failing stage must not hide what the others produced
                    status, note = "error", f"{type(exc).__name__}: {exc}"
                    result.errors.append(f"{name}: {note}")
                    result.errors.append(traceback.format_exc(limit=3))
            if status == "error" or missing:
                unavailable.add(name)
            result.stages.append(
                StageRun(stage=name, status=status, seconds=round(time.perf_counter() - start, 3), note=note)
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
    parser.add_argument("--out", type=Path, default=ROOT / "outputs", help="Output folder (default: outputs/)")
    parser.add_argument("--mode", choices=["auto", "text", "ocr"], default="auto", help="Reading mode")
    parser.add_argument("--dpi", type=int, default=300, help="OCR resolution")
    parser.add_argument("--skip-report", action="store_true", help="No LLM call (deterministic part only)")
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

    options = PipelineOptions(read_mode=args.mode, ocr_dpi=args.dpi, skip_report=args.skip_report)
    failed = 0
    for pdf in files:
        try:
            result = run_pipeline(pdf, options)
        except Exception as exc:  # batch mode: log and continue
            failed += 1
            print(f"FAILED {pdf.name}: {type(exc).__name__}: {exc}", file=sys.stderr)
            continue
        path = write_result(result, args.out)
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
