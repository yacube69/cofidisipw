"""End-to-end pipeline: PDF → Result (CIPW-40 skeleton, completed in CIPW-24).

    locate → read → parse → validate → reread → ratios → bankruptcy → benchmarks → report → check_numbers

Every stage is a function `stage(result, ctx) -> StageStatus`. A stage that is not implemented yet returns
"stub" and fills the `Result` with mock data in the agreed schema, so the app and the CLI work end to end
from day one. Track owners replace a stub by pointing STAGES at the real module.

CLI:
    python -m src.pipeline data/raw/26161516_2025_statement.pdf --out outputs/ [--skip-report] [--mode ocr]
"""

from __future__ import annotations

import argparse
import sys
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path

import pymupdf

from src.extraction.ocr import DEFAULT as DEFAULT_OCR
from src.extraction.reading import read_page
from src.ratios.models import RatioReport
from src.result import PipelineOptions, Result, StageRun, StageStatus
from src.schema import FinancialStatements, GroundTruth
from src.validation.models import ValidationReport

ROOT = Path(__file__).resolve().parents[1]
GROUND_TRUTH_DIR = ROOT / "data" / "ground_truth"


@dataclass
class Context:
    pdf_path: Path
    options: PipelineOptions
    doc: pymupdf.Document
    extra: dict = field(default_factory=dict)


Stage = Callable[[Result, Context], StageStatus]


def _fixture_statements(pdf_path: Path) -> FinancialStatements | None:
    """Mock data for stubs: the ground truth of the same file, if someone transcribed it."""
    gt_file = GROUND_TRUTH_DIR / f"{pdf_path.stem.removesuffix('_statement')}.json"
    if not gt_file.exists():
        return None
    return GroundTruth.model_validate_json(gt_file.read_text(encoding="utf-8")).to_statements()


# ------------------------------------------------------------------------------------- stages (stubs)


def stage_locate(result: Result, ctx: Context) -> StageStatus:
    if ctx.options.pages:
        result.pages = ctx.options.pages
        return "ok"
    fixture = _fixture_statements(ctx.pdf_path)
    if fixture and fixture.metadata.statement_pages:
        result.pages = fixture.metadata.statement_pages
    else:
        result.pages = {"balance_sheet": list(range(1, ctx.doc.page_count + 1)), "income_statement": []}
    return "stub"


def stage_read(result: Result, ctx: Context) -> StageStatus:
    """CIPW-7: text layer for digital pages, Tesseract OCR for scans."""
    config = replace(DEFAULT_OCR, dpi=ctx.options.ocr_dpi)
    pages = sorted({p for pages in result.pages.values() for p in pages})
    result.page_words = [read_page(ctx.doc, p, ctx.options.read_mode, config, ctx.pdf_path.name) for p in pages]
    return "ok"


def stage_parse(result: Result, ctx: Context) -> StageStatus:
    fixture = _fixture_statements(ctx.pdf_path)
    result.statements = fixture or FinancialStatements()
    result.statements.metadata.source_file = ctx.pdf_path.name
    return "stub"


def stage_validate(result: Result, ctx: Context) -> StageStatus:
    result.validation = ValidationReport()
    return "stub"


def stage_reread(result: Result, ctx: Context) -> StageStatus:
    return "stub"


def stage_ratios(result: Result, ctx: Context) -> StageStatus:
    result.ratios = RatioReport()
    return "stub"


def stage_bankruptcy(result: Result, ctx: Context) -> StageStatus:
    return "stub"


def stage_benchmarks(result: Result, ctx: Context) -> StageStatus:
    return "stub"


def stage_report(result: Result, ctx: Context) -> StageStatus:
    if ctx.options.skip_report:
        return "skipped"
    return "stub"


def stage_check_numbers(result: Result, ctx: Context) -> StageStatus:
    if ctx.options.skip_report:
        return "skipped"
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


def run_pipeline(pdf_path: str | Path, options: PipelineOptions | None = None) -> Result:
    pdf_path = Path(pdf_path)
    options = options or PipelineOptions()
    result = Result(file=pdf_path.name, options=options)
    with pymupdf.open(pdf_path) as doc:
        ctx = Context(pdf_path=pdf_path, options=options, doc=doc)
        for name, stage in STAGES:
            start = time.perf_counter()
            try:
                status = stage(result, ctx)
                note = None
            except Exception as exc:  # one failing stage must not hide what the others produced
                status, note = "error", f"{type(exc).__name__}: {exc}"
                result.errors.append(f"{name}: {note}")
                result.errors.append(traceback.format_exc(limit=3))
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
        flag = "with errors" if result.errors else "ok"
        print(f"{pdf.name} -> {path} ({flag}) [{stages}]")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
