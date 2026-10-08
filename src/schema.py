"""Data model for extracted financial statements (CIPW-5).

Conventions (see docs/schema.md):
- Every amount is stored **in CZK** (`Value.value`), normalized from the unit printed in the header.
  The text exactly as printed is kept in `Value.raw` and the multiplier in `Metadata.unit_multiplier`.
- A line that is not printed or is empty is `None` (null), never 0. A printed "0" or "-" is 0.
- Losses and negative values are negative numbers.
- Balance-sheet assets have four amounts: gross, adjustment, net (current period) and prior (net, prior period).
  Liabilities and the income statement have current and prior.
- Every extracted amount keeps its source: page (1-based), bounding box in PDF points (top-left origin),
  OCR confidence (0–100) and parser match score (0–100).
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from src.line_items import Statement, load_dictionary

Source = Literal["text_layer", "ocr", "manual", "ground_truth"]
Scope = Literal["full", "abbreviated"]
Period = Literal["gross", "adjustment", "current", "prior"]


class BBox(BaseModel):
    """Rectangle in PDF points (1/72 inch), origin top-left of the page."""

    x0: float
    y0: float
    x1: float
    y1: float

    def union(self, other: BBox) -> BBox:
        return BBox(
            x0=min(self.x0, other.x0), y0=min(self.y0, other.y0), x1=max(self.x1, other.x1), y1=max(self.y1, other.y1)
        )


class Value(BaseModel):
    value: float | None = Field(None, description="Amount in CZK (normalized). None = not printed / not found.")
    raw: str | None = Field(None, description="Text exactly as read from the page.")
    page: int | None = Field(None, description="1-based page number in the source PDF.")
    bbox: BBox | None = None
    ocr_conf: float | None = Field(None, ge=0, le=100, description="Lowest OCR word confidence; None for text layer.")
    match_score: float | None = Field(None, ge=0, le=100, description="How sure the parser is about the line.")
    source: Source | None = None
    needs_review: bool = False
    candidates: list[float] = Field(default_factory=list, description="Alternative readings (CIPW-11).")
    note: str | None = None


class LineItem(BaseModel):
    key: str
    code: str | None = Field(None, description="Označení as read, normalized (e.g. 'C.II.').")
    row: str | None = Field(None, description="Číslo řádku as read.")
    label: str | None = Field(None, description="Label text as read.")
    gross: Value | None = None
    adjustment: Value | None = None
    current: Value | None = None
    prior: Value | None = None

    def amount(self, period: Period = "current") -> float | None:
        v = getattr(self, period)
        return v.value if v is not None else None

    def values(self) -> list[Value]:
        return [v for v in (self.gross, self.adjustment, self.current, self.prior) if v is not None]


class Metadata(BaseModel):
    company_name: str | None = None
    ico: str | None = Field(None, pattern=r"^\d{8}$")
    period_end: date | None = Field(None, description="Rozvahový den (balance sheet date) of the current period.")
    prior_period_end: date | None = None
    scope: Scope | None = None
    accounting_standard: str = "CZ GAAP (Vyhláška 500/2002 Sb.)"
    currency: str = "CZK"
    unit_multiplier: int | None = Field(None, description="1 = Kč, 1000 = tis. Kč, 1000000 = mil. Kč.")
    source_file: str | None = None
    statement_pages: dict[str, list[int]] = Field(default_factory=dict)
    extraction_source: dict[int, Source] = Field(default_factory=dict, description="Text layer or OCR per page.")


class FinancialStatements(BaseModel):
    metadata: Metadata = Field(default_factory=Metadata)
    balance_sheet: dict[str, LineItem] = Field(default_factory=dict)
    income_statement: dict[str, LineItem] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _known_keys(self) -> FinancialStatements:
        d = load_dictionary()
        for statement in ("balance_sheet", "income_statement"):
            allowed = set(d.keys(statement))
            items: dict[str, LineItem] = getattr(self, statement)
            unknown = set(items) - allowed
            if unknown:
                raise ValueError(f"Unknown {statement} keys: {sorted(unknown)} (see config/line_items.yaml)")
            for k, item in items.items():
                if item.key != k:
                    raise ValueError(f"Key mismatch: {k} != {item.key}")
        return self

    def statement(self, name: Statement) -> dict[str, LineItem]:
        return getattr(self, name)

    def get(self, key: str, period: Period = "current") -> float | None:
        """Amount in CZK for a field, from whichever statement holds it; None if missing."""
        for statement in (self.balance_sheet, self.income_statement):
            if key in statement:
                return statement[key].amount(period)
        return None

    def value(self, key: str, period: Period = "current") -> Value | None:
        for statement in (self.balance_sheet, self.income_statement):
            if key in statement:
                return getattr(statement[key], period)
        return None

    def flagged(self) -> list[tuple[str, Period, Value]]:
        out = []
        for statement in (self.balance_sheet, self.income_statement):
            for key, item in statement.items():
                for period in ("gross", "adjustment", "current", "prior"):
                    v = getattr(item, period)
                    if v is not None and v.needs_review:
                        out.append((key, period, v))
        return out


# ---------------------------------------------------------------------------------------------
# Ground truth: the simple, hand-filled format used in data/ground_truth/ (CIPW-3)
# ---------------------------------------------------------------------------------------------


class GroundTruthLine(BaseModel):
    gross: float | None = None
    adjustment: float | None = None
    current: float | None = None
    prior: float | None = None


class GroundTruth(BaseModel):
    """Values exactly as printed (in the unit of the statement, usually tis. Kč)."""

    file: str
    company_name: str | None = None
    ico: str | None = Field(None, pattern=r"^\d{8}$")
    period_end: date | None = None
    scope: Scope | None = None
    unit_multiplier: int = 1000
    pages: dict[str, list[int]] = Field(default_factory=dict)
    balance_sheet: dict[str, GroundTruthLine] = Field(default_factory=dict)
    income_statement: dict[str, GroundTruthLine] = Field(default_factory=dict)
    transcribed_by: str | None = None
    verified_by: str | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def _known_keys(self) -> GroundTruth:
        d = load_dictionary()
        for statement in ("balance_sheet", "income_statement"):
            unknown = set(getattr(self, statement)) - set(d.keys(statement))
            if unknown:
                raise ValueError(f"Unknown {statement} keys: {sorted(unknown)}")
        return self

    def to_statements(self) -> FinancialStatements:
        """Convert to the main model (values normalized to CZK, source = ground_truth)."""
        fs = FinancialStatements(
            metadata=Metadata(
                company_name=self.company_name,
                ico=self.ico,
                period_end=self.period_end,
                scope=self.scope,
                unit_multiplier=self.unit_multiplier,
                source_file=self.file,
                statement_pages=self.pages,
            )
        )
        for statement in ("balance_sheet", "income_statement"):
            target = fs.statement(statement)
            for key, line in getattr(self, statement).items():
                item = LineItem(key=key)
                for period in ("gross", "adjustment", "current", "prior"):
                    raw = getattr(line, period)
                    if raw is not None:
                        setattr(item, period, Value(value=raw * self.unit_multiplier, source="ground_truth"))
                target[key] = item
        return fs


def blank_ground_truth(file: str = "ICO_year_statement.pdf") -> dict:
    """Template with every schema field, used by scripts/export_schema.py."""
    d = load_dictionary()
    bs = {}
    for line in d.for_statement("balance_sheet"):
        periods = ["gross", "adjustment", "current", "prior"] if line.side == "assets" else ["current", "prior"]
        bs[line.key] = {p: None for p in periods}
    is_ = {line.key: {"current": None, "prior": None} for line in d.for_statement("income_statement")}
    return {
        "file": file,
        "company_name": None,
        "ico": None,
        "period_end": None,
        "scope": None,
        "unit_multiplier": 1000,
        "pages": {"balance_sheet": [], "income_statement": []},
        "balance_sheet": bs,
        "income_statement": is_,
        "transcribed_by": None,
        "verified_by": None,
        "notes": None,
    }
