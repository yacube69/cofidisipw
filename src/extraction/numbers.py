"""Helpers for the parser (CIPW-9): Czech number formats, units, periods, scope and column detection.

Interface agreed with CIPW-32:
    parse_number(text)                 -> float | None
    detect_unit(text)                  -> multiplier (1, 1000, 1_000_000) or None
    detect_period(text)                -> (period_end, prior_period_end)
    detect_scope(text)                 -> "full" | "abbreviated" | None
    detect_columns(lines, kind)        -> [Column(name, x0, x1)], name in gross/adjustment/current/prior/row
"""

from __future__ import annotations

import re
import statistics
import unicodedata
from dataclasses import dataclass
from datetime import date
from typing import Literal

from rapidfuzz import fuzz

from src.extraction.layout import Line, NumberToken, merge_number_tokens

# --------------------------------------------------------------------------------------------- numbers

# OCR confusions that are only safe to fix inside a token that is otherwise a number
_DIGIT_FIXES = str.maketrans({"O": "0", "o": "0", "D": "0", "Q": "0", "l": "1", "I": "1", "|": "1", "!": "1",
                              "i": "1", "S": "5", "s": "5", "B": "8", "Z": "2", "z": "2", "G": "6", "b": "6",
                              "g": "9", "q": "9", "T": "7", "A": "4"})  # fmt: skip
_CONFUSABLE = set("OoDQlI|!i")  # letters OCR typically returns instead of 0 and 1
_MINUS = "-−–—~«="
_DASH_ONLY = re.compile(r"^[-−–—]+$")


def is_dash(text: str) -> bool:
    """A lone dash in a numeric column means zero / nothing to report."""
    return bool(_DASH_ONLY.match(text.strip()))


def clean_numeric_text(text: str) -> str | None:
    """Fix OCR letter/digit confusions in a token that is mostly digits; None if it is not a number."""
    t = text.strip().replace(" ", " ")
    if not t:
        return None
    letters = sum(c.isalpha() and c not in _CONFUSABLE for c in t)
    digits = sum(c.isdigit() for c in t)
    if digits == 0 or letters > digits:
        return None
    return t.translate(_DIGIT_FIXES)


def parse_number(text: str) -> float | None:
    """'1 234 567' → 1234567, '-17 406 042' → -17406042, '(1 234)' → -1234, '12,5' → 12.5, '3a2 255' → None.

    Spaces, dots and commas used as thousands separators are removed; a single comma or dot followed by one or
    two digits is a decimal separator.
    """
    t = clean_numeric_text(text)
    if t is None:
        return None
    t = t.replace(" ", "")
    negative = False
    if t.startswith("(") and t.endswith(")"):
        negative, t = True, t[1:-1]
    elif t[0] in _MINUS:
        negative, t = True, t.lstrip(_MINUS + "+")  # OCR sometimes reads "-253" as "-+253"
    elif t[0] == "+":
        t = t[1:]
    t = t.rstrip(".")
    m = re.fullmatch(r"(\d{1,3}(?:[.,]\d{3})+)", t)
    if m:  # 1.234.567 or 1,234,567
        value = float(re.sub(r"[.,]", "", t))
    elif re.fullmatch(r"\d+[.,]\d{1,2}", t):
        value = float(t.replace(",", "."))
    elif re.fullmatch(r"\d+", t):
        value = float(t)
    else:
        return None
    return -value if negative else value


# ------------------------------------------------------------------------------------- unit and period

_MONTHS = {
    "ledna": 1, "unora": 2, "brezna": 3, "dubna": 4, "kvetna": 5, "cervna": 6,
    "cervence": 7, "srpna": 8, "zari": 9, "rijna": 10, "listopadu": 11, "prosince": 12,
}  # fmt: skip


def _plain(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c)).lower()


def detect_unit(text: str) -> int | None:
    t = _plain(text)
    if re.search(r"(tisic|tis\s*\.|\btis\b|v\s*tis)", t):
        return 1000
    if re.search(r"(milion|\bmil\b|mil\s*\.)", t):
        return 1_000_000
    if re.search(r"(v\s*(celych\s*)?(kc|czk)\b|v\s*korunach)", t):
        return 1
    return None


def _dates(text: str) -> list[date]:
    t = _plain(text)
    out: list[date] = []
    for d, m, y in re.findall(r"\b(\d{1,2})\s*[.,]\s*(\d{1,2})\s*[.,]+\s*((?:19|20)\d{2})\b", t):
        try:
            out.append(date(int(y), int(m), int(d)))
        except ValueError:
            pass
    for d, month, y in re.findall(r"\b(\d{1,2})\s*[.,]?\s*([a-z]+)\s+((?:19|20)\d{2})\b", t):
        if month in _MONTHS:
            try:
                out.append(date(int(y), _MONTHS[month], int(d)))
            except ValueError:
                pass
    return out


def detect_period(text: str) -> tuple[date | None, date | None]:
    """(current period end, prior period end) from 'Rozvahový den: 31. prosince 2025', '31.12.2025' columns etc."""
    dates = sorted(set(_dates(text)), reverse=True)
    # ignore dates after the balance sheet date (date of preparation, audit report)
    rozvahovy = re.search(r"rozvahov\w*\s+den\W*(.{0,30})", _plain(text))
    current = None
    if rozvahovy:
        found = _dates(rozvahovy.group(1))
        current = found[0] if found else None
    if current is None:
        year_ends = [d for d in dates if (d.month, d.day) in ((12, 31), (6, 30), (3, 31), (9, 30))]
        current = year_ends[0] if year_ends else (dates[0] if dates else None)
    if current is None:
        return None, None
    prior = next((d for d in dates if d < current and (current - d).days >= 300), None)
    if prior is None:
        try:
            prior = current.replace(year=current.year - 1)
        except ValueError:
            prior = None
    return current, prior


def detect_scope(text: str) -> Literal["full", "abbreviated"] | None:
    t = _plain(text)
    if "zkracen" in t or "mikro" in t:
        return "abbreviated"
    if "plnem rozsahu" in t:
        return "full"
    return None


def detect_ico(text: str) -> str | None:
    """IČO: the 8-digit number after 'IČ', 'IČO' or 'Identifikační číslo' (OCR-noisy labels allowed)."""
    m = re.search(r"(?:\bic\s*o?\b|\bi\w{4,12}\s+c\w{3,5})\W{0,5}(\d{8})\b", _plain(text))
    return m.group(1) if m else None


def detect_company(text: str) -> str | None:
    """Name after 'Firma:' / 'Obchodní firma:' (tolerant to OCR noise such as 'Fima:', 'Finna:')."""
    m = re.search(r"(?:Obchodn[ií]\s+)?\b[FfA][iíl1]?[rn]{0,2}[mn]?n?a\s*:\s*(.+)", text)
    if not m:
        return None
    name = re.split(r"\s{2,}|\n", m.group(1).strip())[0]
    return name.strip(" .,:;") or None


# ------------------------------------------------------------------------------------------- columns

ColumnName = Literal["gross", "adjustment", "current", "prior", "row"]
StatementKind = Literal["assets", "liabilities", "income_statement"]

ORDER: dict[StatementKind, list[ColumnName]] = {
    "assets": ["gross", "adjustment", "current", "prior"],
    "liabilities": ["current", "prior"],
    "income_statement": ["current", "prior"],
}

_HEADERS = {"BRUTTO": "gross", "KOREKCE": "adjustment", "NETTO": "net"}


@dataclass
class Column:
    name: ColumnName
    x0: float
    x1: float  # right edge of the numbers (they are right-aligned)
    count: int = 0

    @property
    def center(self) -> float:
        return (self.x0 + self.x1) / 2

    def contains(self, token: NumberToken, tolerance: float) -> bool:
        return self.x0 - tolerance <= token.cx <= self.x1 + tolerance


def _cluster(values: list[float], gap: float) -> list[list[float]]:
    clusters: list[list[float]] = []
    for v in sorted(values):
        if clusters and v - clusters[-1][-1] <= gap:
            clusters[-1].append(v)
        else:
            clusters.append([v])
    return clusters


def _is_row_code(text: str) -> bool:
    return bool(re.fullmatch(r"\d{2,3}", text)) and int(text) <= 400


def numeric_tokens(lines: list[Line]) -> list[NumberToken]:
    """Number tokens that can be table values: parseable, not a date, not a column-index row ('1 2 3 4'),
    not a lone single digit (označení parts like 'B. I. 2.' and column indexes produce those)."""
    out = []
    for line in lines:
        tokens = [t for t in merge_number_tokens(line.words) if isinstance(t, NumberToken)]
        if tokens and all(re.fullmatch(r"\d", t.text) for t in tokens):
            continue  # column numbering row
        for tok in tokens:
            if tok.text.count(".") >= 2 or re.fullmatch(r"\d[.,]?", tok.text) and tok.text != "0":
                continue
            if parse_number(tok.text) is None:
                continue
            out.append(tok)
    return out


def _header_anchors(lines: list[Line]) -> list[tuple[str, float]]:
    """('gross'|'adjustment'|'net', x-center) for Brutto/Korekce/Netto header words, left to right."""
    anchors = []
    for line in lines[:40]:
        for w in line.words:
            word = _plain(w.text).upper().strip(".:,")
            for header, name in _HEADERS.items():
                if len(word) >= 5 and fuzz.ratio(header, word) >= 80:
                    anchors.append((name, w.cx))
    return sorted(anchors, key=lambda a: a[1])


def detect_columns(lines: list[Line], kind: StatementKind, gap: float | None = None) -> list[Column]:
    """Find the numeric columns of a statement page from the right edges of its numbers."""
    tokens = numeric_tokens(lines)
    if not tokens:
        return []
    height = statistics.median(t.words[0].height for t in tokens)
    gap = gap or max(1.2 * height, 8.0)  # right edges of one column jitter by a few points only
    clusters = _cluster([t.x1 for t in tokens], gap)
    min_count = max(2, int(0.08 * len(tokens)))

    columns: list[Column] = []
    for cl in clusters:
        members = [t for t in tokens if cl[0] - 0.01 <= t.x1 <= cl[-1] + 0.01]
        if len(members) < min_count:
            continue
        lefts = sorted(t.x0 for t in members)
        x0 = lefts[len(lefts) // 10]  # left edge of the wider numbers, ignoring a few outliers
        columns.append(Column(name="current", x0=x0, x1=statistics.median(cl), count=len(members)))

    # row-number column ("řád."): made of 2–3 digit codes, left of the value columns
    for col in columns[:-1]:
        members = [t for t in tokens if col.contains(t, 2)]
        if members and sum(_is_row_code(t.text) for t in members) >= 0.6 * len(members):
            col.name = "row"
            columns = [c for c in columns if c.x0 >= col.x0]  # anything left of it is the label area
            break
    value_cols = [c for c in columns if c.name != "row"]

    names = ORDER[kind]
    anchors = _header_anchors(lines) if kind == "assets" else []
    if kind == "assets" and len(anchors) >= 3:
        # map Brutto/Korekce/Netto/Netto headers to names, then each column to its nearest header
        named, seen_net = [], False
        for name, x in anchors:
            if name == "net":
                named.append(("prior" if seen_net else "current", x))
                seen_net = True
            else:
                named.append((name, x))
        for col in value_cols:
            col.name = min(named, key=lambda a: abs(a[1] - col.center))[0]
    elif len(value_cols) == len(names):
        for col, name in zip(value_cols, names, strict=True):
            col.name = name
    elif kind == "assets" and len(value_cols) == 3:
        for col, name in zip(value_cols, ["gross", "current", "prior"], strict=True):  # empty Korekce column
            col.name = name
    else:
        # fewer columns than expected: assume the rightmost ones are current, prior (most common layouts)
        tail = names[-len(value_cols) :] if len(value_cols) <= len(names) else names
        for col, name in zip(value_cols[-len(tail) :], tail, strict=False):
            col.name = name
    return columns
