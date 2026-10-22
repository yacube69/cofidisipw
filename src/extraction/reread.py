"""Targeted re-read of the cells the accounting checks doubt (CIPW-11).

After the checks, the cells of every failed check, and cells the parser left empty on a line it found, are read
again from the page: a crop of the cell at higher resolution, digits only, at most two preprocessing variants.
A reading is taken only when the checks support it: fewer checks fail and none that passed fails, or an empty
cell makes a check run and pass. A taken reading is never silent: the value keeps a note, its old reading stays
among the candidates and the result lists every change (`RereadReport`).

What the re-read cannot settle stays flagged for review with every reading as a candidate, plus the value that
would make the failed check pass (a proposal derived from the sum, never applied). The analyst picks one in the
app (CIPW-42).
"""

from __future__ import annotations

import statistics
import time
from dataclasses import dataclass

import cv2
import numpy as np
import pymupdf
import pytesseract
from pydantic import BaseModel, Field

from src.extraction.numbers import parse_number
from src.extraction.ocr import binarize, remove_table_lines, to_gray
from src.extraction.types import Word
from src.schema import BBox, FinancialStatements, Period, Value
from src.validation.checks import EQUALITY_CHECKS, SUM_CHECKS, validate
from src.validation.models import ValidationReport
from src.validation.review import periods_of

PERIODS: tuple[Period, ...] = ("current", "prior")
PAD_X, PAD_Y = 3.0, 2.0  # points around the cell
MAX_CHANGES = 12


@dataclass(frozen=True)
class Attempt:
    name: str
    scale: float  # of the re-read resolution
    binarize: str
    block: int = 51
    offset: int = 20


# Chosen by reading the misread cells of the ground truth scans with 36 settings: the grey crop (Tesseract
# thresholds it itself) found 18 of 25, a finer crop with adaptive thresholding added 2 more.
ATTEMPTS = (
    Attempt("digits, grey", scale=1.0, binarize="none"),
    Attempt("digits, 1.5x finer, adaptive", scale=1.5, binarize="adaptive"),
)
WHITELIST = "0123456789-()"


class Reading(BaseModel):
    attempt: str
    text: str
    value: float | None
    conf: float | None


class Change(BaseModel):
    key: str
    period: Period
    old: float | None
    new: float
    old_text: str | None
    new_text: str
    attempt: str


class RereadReport(BaseModel):
    """What the re-read did, for the result, the run log and the evaluation (CIPW-19)."""

    dpi: int
    cells: int = 0
    readings: int = 0
    changes: list[Change] = Field(default_factory=list)
    proposals: int = Field(0, description="Values derived from a failed sum, offered as candidates only.")
    failed_before: int = 0
    failed_after: int = 0
    seconds: float = 0.0


@dataclass
class Cell:
    key: str
    period: Period
    page: int
    box: BBox
    readings: list[Reading]


def _checked_keys() -> set[str]:
    keys: set[str] = set()
    for _, total, parts, _ in SUM_CHECKS:
        keys |= {total, *(p if isinstance(p, str) else p[0] for p in parts)}
    for _, left, right, _, _ in EQUALITY_CHECKS:
        keys |= {k for k, _ in left + right}
    return keys


def _items(fs: FinancialStatements):
    yield from fs.balance_sheet.items()
    yield from fs.income_statement.items()


def _columns(fs: FinancialStatements) -> dict[tuple[int, Period], tuple[float, float]]:
    """(page, period) -> left and right edge of the column, from the values read in it (right-aligned numbers)."""
    boxes: dict[tuple[int, Period], list[BBox]] = {}
    for _, item in _items(fs):
        for period in PERIODS:
            v = getattr(item, period)
            if v is not None and v.bbox is not None and v.page is not None and v.value is not None:
                boxes.setdefault((v.page, period), []).append(v.bbox)
    return {
        k: (min(b.x0 for b in bs), statistics.median(b.x1 for b in bs)) for k, bs in boxes.items() if len(bs) >= 3
    }


Target = tuple[str, Period, int, BBox, bool]  # key, period, page, the box to read, doubted by a check


def doubted(fs: FinancialStatements, report: ValidationReport) -> set[tuple[str, Period]]:
    """The values of failed checks and empty cells on lines a check uses."""
    out: set[tuple[str, Period]] = set()
    for check in report.errors:
        for key in check.fields:
            out |= {(key, period) for period in periods_of(check) if period in PERIODS}
    checked = _checked_keys()
    for key, item in _items(fs):
        for period in PERIODS:
            v = getattr(item, period)
            if key in checked and (v is None or v.value is None):
                out.add((key, period))
    return out


def targets(fs: FinancialStatements, report: ValidationReport, ocr_pages: set[int]) -> list[Target]:
    """Every current and prior cell of the lines read on OCR pages, located by its row and its column."""
    columns = _columns(fs)
    doubt = doubted(fs, report)
    out = []
    for key, item in _items(fs):
        boxed = [getattr(item, p) for p in PERIODS if getattr(item, p) is not None and getattr(item, p).bbox]
        for period in PERIODS:
            v = getattr(item, period)
            anchor = v if v is not None and v.bbox is not None else next(iter(boxed), None)
            if anchor is None or anchor.page not in ocr_pages or (anchor.page, period) not in columns:
                continue
            left, right = columns[(anchor.page, period)]
            if v is not None and v.bbox is not None:
                right = max(right, v.bbox.x1)
            y0, y1 = anchor.bbox.y0, anchor.bbox.y1  # type: ignore[union-attr]
            box = BBox(x0=left - PAD_X, y0=y0 - PAD_Y, x1=right + PAD_X, y1=y1 + PAD_Y)
            out.append((key, period, anchor.page, box, (key, period) in doubt))
    return out


def read_strip(page: pymupdf.Page, x0: float, x1: float, dpi: int) -> list[Word]:
    """The value columns of a page read as numbers only: the right side of a statement holds no letters, so
    Tesseract may only answer digits, a minus and brackets ("U" is not a reading of 11, "o" not of 0)."""
    clip = pymupdf.Rect(x0, 0, x1, page.rect.height) & page.rect
    pix = page.get_pixmap(dpi=dpi, clip=clip, colorspace=pymupdf.csRGB, alpha=False)
    rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
    img = remove_table_lines(binarize(to_gray(rgb, True), "otsu"))
    config = f"--psm 6 -c tessedit_char_whitelist={WHITELIST} -c preserve_interword_spaces=1"
    data = pytesseract.image_to_data(img, lang="eng", config=config, output_type="dict")
    scale = 72.0 / dpi
    words = []
    for text, conf, left, top, width, height in zip(
        data["text"], data["conf"], data["left"], data["top"], data["width"], data["height"], strict=True
    ):
        if text.strip() and float(conf) >= 0:
            words.append(Word(text=text.strip(), conf=float(conf), x0=clip.x0 + left * scale, y0=clip.y0 + top * scale,
                              x1=clip.x0 + (left + width) * scale, y1=clip.y0 + (top + height) * scale))  # fmt: skip
    return words


def strip_reading(words: list[Word], box: BBox, unit: int) -> Reading | None:
    inside = sorted((w for w in words if box.x0 <= w.cx <= box.x1 and box.y0 <= w.cy <= box.y1), key=lambda w: w.x0)
    if not inside:
        return None
    text = " ".join(w.text for w in inside)
    number = parse_number(text)
    if number is None and len(inside) > 1:
        number = parse_number(text.replace(" ", ""))
    conf = min((w.conf for w in inside if w.conf is not None), default=None)
    value = None if number is None else number * unit
    return Reading(attempt="column strip, digits", text=text, value=value, conf=conf)


def read_cell(page: pymupdf.Page, box: BBox, dpi: int, unit: int) -> list[Reading]:
    """The number in a cell, read with each attempt: a crop at `dpi`, digits only, one line."""
    clip = pymupdf.Rect(box.x0, box.y0, box.x1, box.y1) & page.rect
    if clip.is_empty:
        return []
    out = []
    for attempt in ATTEMPTS:
        pix = page.get_pixmap(dpi=round(dpi * attempt.scale), clip=clip, colorspace=pymupdf.csRGB, alpha=False)
        rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
        img = binarize(to_gray(rgb, False), attempt.binarize, block=attempt.block, offset=attempt.offset)
        img = cv2.copyMakeBorder(img, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=255)
        config = f"--psm 7 -c tessedit_char_whitelist={WHITELIST}"
        data = pytesseract.image_to_data(img, lang="eng", config=config, output_type="dict")
        words = [(t.strip(), float(c)) for t, c in zip(data["text"], data["conf"], strict=True) if t.strip()]
        text = " ".join(t for t, _ in words)
        number = parse_number(text) if text else None
        if number is None and len(words) > 1:
            number = parse_number(text.replace(" ", ""))
        conf = min((c for _, c in words if c >= 0), default=None)
        out.append(Reading(attempt=attempt.name, text=text, value=None if number is None else number * unit, conf=conf))
        bare = text.strip("-")  # a ruling read as a dash before or after the number
        if bare != text and (number := parse_number(bare)) is not None:
            out.append(Reading(attempt=f"{attempt.name}, without the dash", text=bare, value=number * unit, conf=conf))
    return out


def _state(fs: FinancialStatements) -> tuple[set[tuple[str, str | None]], set[tuple[str, str | None]]]:
    report = validate(fs)
    checks = [c for c in report.checks if c.severity == "error"]
    return {(c.name, c.period) for c in checks if not c.passed}, {(c.name, c.period) for c in checks if c.passed}


def _set(fs: FinancialStatements, key: str, period: Period, value: Value | None) -> Value | None:
    item = fs.balance_sheet.get(key) or fs.income_statement.get(key)
    old = getattr(item, period)
    setattr(item, period, value)
    return old


def _options(fs: FinancialStatements, cell: Cell) -> list[Reading]:
    """Readings of a cell that differ from its value, one per number."""
    old = fs.value(cell.key, cell.period)
    out: dict[float, Reading] = {}
    for reading in cell.readings:
        if reading.value is not None and (old is None or old.value != reading.value):
            out.setdefault(reading.value, reading)
    return list(out.values())


def _try(fs: FinancialStatements, moves: list[tuple[Cell, Reading]]) -> tuple[set, set]:
    olds = []
    for cell, reading in moves:
        old = fs.value(cell.key, cell.period)
        olds.append(old)
        _set(fs, cell.key, cell.period, (old or Value()).model_copy(update={"value": reading.value}))
    state = _state(fs)
    for (cell, _), old in zip(moves, olds, strict=True):
        _set(fs, cell.key, cell.period, old)
    return state


def _sharing_a_failed_check(fs: FinancialStatements, cells: list[Cell]) -> list[tuple[Cell, Cell]]:
    """Pairs of cells in the same failed check: two misread values in one sum are fixed only together."""
    by_value = {(c.key, c.period): c for c in cells}
    pairs: dict[tuple[int, int], tuple[Cell, Cell]] = {}
    for check in validate(fs).errors:
        members = [by_value[(k, p)] for k in check.fields for p in periods_of(check) if (k, p) in by_value]
        for i, a in enumerate(members):
            for b in members[i + 1 :]:
                pairs.setdefault((id(a), id(b)), (a, b))
    return list(pairs.values())


def resolve(fs: FinancialStatements, cells: list[Cell], report: RereadReport) -> None:
    """Take readings while they make the checks agree better, never one that breaks a check that held: first one
    cell at a time, then two cells of the same failed check together."""
    failed, passed = _state(fs)
    report.failed_before = len(failed)
    open_cells = [c for c in cells if _options(fs, c)]
    for _ in range(MAX_CHANGES):
        moves: list[list[tuple[Cell, Reading]]] = [[(c, r)] for c in open_cells for r in _options(fs, c)]
        best = _best(fs, moves, failed, passed)
        if best is None:
            pairs = _sharing_a_failed_check(fs, open_cells)
            moves = [[(a, ra), (b, rb)] for a, b in pairs for ra in _options(fs, a) for rb in _options(fs, b)]
            best = _best(fs, moves, failed, passed)
        if best is None:
            break
        for cell, reading in best:
            _apply(fs, cell, reading, report)
            open_cells.remove(cell)
        failed, passed = _state(fs)
    report.failed_after = len(failed)


def _best(fs: FinancialStatements, moves: list[list[tuple[Cell, Reading]]], failed: set, passed: set):
    best, best_gain = None, 0
    for move in moves:
        f2, p2 = _try(fs, move)
        if f2 - failed:
            continue  # breaks a check that held, or a check it makes run fails
        gain = len(failed - f2) + len(p2 - passed)
        if gain > best_gain:
            best, best_gain = move, gain
    return best


def _apply(fs: FinancialStatements, cell: Cell, reading: Reading, report: RereadReport) -> None:
    old = _set(fs, cell.key, cell.period, None)
    new = (old.model_copy() if old is not None else Value(page=cell.page, bbox=cell.box, source="ocr")).model_copy(
        update={"value": reading.value, "raw": reading.text, "ocr_conf": reading.conf}
    )
    printed = repr(old.raw) if old is not None and old.raw else "nothing"
    new.note = f"re-read at higher resolution ({reading.attempt}): {printed} read as {reading.text!r}, the checks agree"
    if old is not None and old.value is not None and old.value not in new.candidates:
        new.candidates = [*new.candidates, old.value]
    _set(fs, cell.key, cell.period, new)
    report.changes.append(
        Change(key=cell.key, period=cell.period, old=old.value if old is not None else None, new=reading.value,
               old_text=old.raw if old is not None else None, new_text=reading.text, attempt=reading.attempt)
    )  # fmt: skip


def propose(fs: FinancialStatements, cells: list[Cell], report: RereadReport) -> None:
    """Values still in doubt keep every reading as a candidate, plus the value a failed check would need."""
    by_key = {(c.key, c.period): c for c in cells}
    failed = validate(fs).errors
    for (key, period), cell in by_key.items():
        value = fs.value(key, period)
        if value is None or value.value is None:
            continue
        extra = [r.value for r in cell.readings if r.value is not None and r.value != value.value]
        value.candidates = list(dict.fromkeys([*value.candidates, *extra]))
    for check in failed:
        if check.difference is None:
            continue
        for key in check.fields:
            for period in periods_of(check):
                value = fs.value(key, period)
                if value is None or value.value is None:
                    continue
                original = value.value
                for guess in (original - check.difference, original + check.difference):
                    value.value = guess
                    checks = validate(fs).checks
                    holds = any(c.name == check.name and c.period == check.period and c.passed for c in checks)
                    value.value = original
                    if holds and guess not in value.candidates:
                        value.candidates = [*value.candidates, guess]
                        report.proposals += 1


def reread(doc: pymupdf.Document, fs: FinancialStatements, report: ValidationReport, dpi: int = 400) -> RereadReport:
    """Read the doubted cells again and take the readings the checks support. Changes `fs` in place."""
    start = time.perf_counter()
    out = RereadReport(dpi=dpi)
    ocr_pages = {page for page, source in fs.metadata.extraction_source.items() if source == "ocr"}
    unit = fs.metadata.unit_multiplier or 1
    cells = []
    found = targets(fs, report, ocr_pages)
    strips: dict[int, list[Word]] = {}
    for page in {t[2] for t in found}:
        boxes = [t[3] for t in found if t[2] == page]
        strips[page] = read_strip(doc[page - 1], min(b.x0 for b in boxes), max(b.x1 for b in boxes), dpi)
    for key, period, page, box, doubt in found:
        readings = [r for r in [strip_reading(strips[page], box, unit)] if r is not None]
        if doubt:
            readings += read_cell(doc[page - 1], box, dpi, unit)
        cells.append(Cell(key, period, page, box, readings))
        out.readings += len(readings)
    out.cells = len(cells)
    if cells:
        resolve(fs, cells, out)
    else:
        out.failed_before = out.failed_after = len(report.errors)
    propose(fs, cells, out)
    out.seconds = round(time.perf_counter() - start, 2)
    return out
