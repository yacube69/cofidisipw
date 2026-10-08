"""Field-level comparison of extracted statements with the ground truth (CIPW-32, reused by CIPW-19)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import pymupdf

from src.extraction.locate import locate_statements
from src.extraction.ocr import DEFAULT, OcrConfig
from src.extraction.parser import ParseLog, parse_statements
from src.extraction.reading import read_page
from src.extraction.types import PageWords
from src.schema import FinancialStatements, GroundTruth

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "outputs" / "cache" / "pagewords"

Outcome = Literal["correct", "wrong", "missing", "flagged_correct", "flagged_wrong"]


@dataclass
class FieldResult:
    statement: str
    key: str
    period: str
    expected: float
    actual: float | None
    raw: str | None = None
    page: int | None = None
    flagged: bool = False

    @property
    def correct(self) -> bool:
        return self.actual is not None and abs(self.actual - self.expected) < 0.5

    @property
    def outcome(self) -> Outcome:
        if self.actual is None:
            return "missing"
        if self.flagged:
            return "flagged_correct" if self.correct else "flagged_wrong"
        return "correct" if self.correct else "wrong"


@dataclass
class FileReport:
    file: str
    fields: list[FieldResult] = field(default_factory=list)
    pages_ok: bool = True
    log: ParseLog | None = None
    sources: set[str] = field(default_factory=set)

    def count(self, *outcomes: Outcome) -> int:
        return sum(1 for f in self.fields if f.outcome in outcomes)

    @property
    def accuracy(self) -> float:
        return sum(f.correct for f in self.fields) / len(self.fields) if self.fields else 0.0

    @property
    def silent_errors(self) -> int:
        """Wrong values that were NOT flagged for review: the dangerous kind for a lender."""
        return self.count("wrong")


def cached_read(doc: pymupdf.Document, file: str, page: int, config: OcrConfig = DEFAULT) -> PageWords:
    key = hashlib.sha1(f"{file}|{page}|{config.describe()}|{config.tesseract_config}".encode()).hexdigest()[:12]
    path = CACHE / f"{Path(file).stem}_p{page}_{key}.json"
    if path.exists():
        return PageWords.model_validate_json(path.read_text(encoding="utf-8"))
    pw = read_page(doc, page, "auto", config, file)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(pw.model_dump_json(), encoding="utf-8")
    return pw


def extract(pdf: Path, config: OcrConfig = DEFAULT, log: ParseLog | None = None) -> tuple[FinancialStatements, dict]:
    with pymupdf.open(pdf) as doc:
        pages = locate_statements(doc)
        wanted = sorted({p for ps in pages.values() for p in ps})
        page_words = [cached_read(doc, pdf.name, p, config) for p in wanted]
    return parse_statements(page_words, pages, log=log), pages


def compare(gt: GroundTruth, fs: FinancialStatements) -> list[FieldResult]:
    out = []
    for statement in ("balance_sheet", "income_statement"):
        for key, line in getattr(gt, statement).items():
            for period in ("gross", "adjustment", "current", "prior"):
                expected = getattr(line, period)
                if expected is None:
                    continue
                v = fs.value(key, period)  # type: ignore[arg-type]
                out.append(
                    FieldResult(
                        statement=statement,
                        key=key,
                        period=period,
                        expected=expected * gt.unit_multiplier,
                        actual=v.value if v else None,
                        raw=v.raw if v else None,
                        page=v.page if v else None,
                        flagged=bool(v and v.needs_review),
                    )
                )
    return out


def evaluate_file(gt_path: Path, raw_dir: Path = ROOT / "data" / "raw", config: OcrConfig = DEFAULT) -> FileReport:
    gt = GroundTruth.model_validate_json(gt_path.read_text(encoding="utf-8"))
    log = ParseLog()
    fs, pages = extract(raw_dir / gt.file, config, log)
    report = FileReport(file=gt.file, fields=compare(gt, fs), log=log)
    expected_pages = {k: v for k, v in gt.pages.items() if v}
    report.pages_ok = all(pages.get(k) == v for k, v in expected_pages.items())
    report.sources = set(fs.metadata.extraction_source.values())
    return report
