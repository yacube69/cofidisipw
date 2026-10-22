"""Helpers for the parser (CIPW-9): Czech number formats, units, periods, scope and column detection.

Interface agreed with CIPW-32:
    parse_number(text)                 -> float | None
    detect_unit(text)                  -> multiplier (1, 1000, 1_000_000) or None
    detect_period(text)                -> (period_end, prior_period_end)
    detect_scope(text)                 -> "full" | "abbreviated" | None
    detect_columns(lines, kind)        -> [Column(name, x0, x1)], name in gross/adjustment/current/prior/row/extra
"""

from __future__ import annotations

import re
import statistics
import unicodedata
from dataclasses import dataclass
from datetime import date, timedelta
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

# month stems, so every case matches: "31. prosince", "k 31. prosinci", "končící 31. prosincem"
# (červenec before červen: the longer stem first)
_MONTHS = [
    ("led", 1), ("unor", 2), ("brez", 3), ("dub", 4), ("kvet", 5), ("cervenc", 7), ("cerv", 6),
    ("srp", 8), ("zari", 9), ("rij", 10), ("listopad", 11), ("prosin", 12),
]  # fmt: skip


def _month(word: str) -> int | None:
    return next((m for stem, m in _MONTHS if word.startswith(stem)), None)


def _plain(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c)).lower()


def detect_unit(text: str) -> int | None:
    t = _plain(text)
    # OCR variants seen in real scans: "vtiis. Kc", "celych tislcick"
    if re.search(r"(tisic|tis\s*\.|\btis\b|v\s*tis|t[il1]{1,2}s\s*\.|t[il1]s[il1]?c)", t):
        return 1000
    if re.search(r"(milion|\bmil\b|mil\s*\.)", t):
        return 1_000_000
    if re.search(r"(v\s*(celych\s*)?(kc|czk)\b|v\s*korunach)", t):
        return 1
    return None


def _date(y: str | int, m: str | int, d: str | int) -> date | None:
    try:
        return date(int(y), int(m), int(d))
    except ValueError:
        return None


def _dated(t: str) -> list[tuple[int, int, date]]:
    """(start, end, date) of every date in plain text `t`, with repetitions."""
    found: list[tuple[int, int, date | None]] = []
    for m in re.finditer(r"\b(\d{1,2})\s*[.,/]\s*(\d{1,2})\s*[.,/]+\s*((?:19|20)\d{2})\b", t):
        found.append((m.start(), m.end(), _date(m[3], m[2], m[1])))
    for m in re.finditer(r"\b(\d{1,2})\s*[.,]?\s*([a-z]+)\s+((?:19|20)\d{2})\b", t):
        month = _month(m[2])
        found.append((m.start(), m.end(), _date(m[3], month, m[1]) if month else None))
    # forms with one box per digit: "k . . . 3 . . 1 . . . 1 . . 2 . . . 2 . . 0 . . 2 . . 5"
    for m in re.finditer(r"\bke?(?:\s*dni)?((?:[\s.]+\d){8})\b", t):
        digits = re.sub(r"\D", "", m[1])
        found.append((m.start(1), m.end(), _date(digits[4:], digits[2:4], digits[:2])))
    return [(a, b, d) for a, b, d in found if d is not None]


def _dates(text: str) -> list[date]:
    """Every date in the text, with repetitions (how often a date is printed tells the balance sheet date)."""
    return [d for _, _, d in _dated(_plain(text))]


def _month_end(d: date) -> bool:
    return (d + timedelta(days=1)).day == 1


def detect_period(text: str) -> tuple[date | None, date | None]:
    """(current period end, prior period end) from 'Rozvahový den: 31. prosince 2025', '31.12.2025' columns,
    'od 1.3.2024 - 28.2.2025' ranges etc."""
    t = _plain(text)
    # dates of preparation and signature ("Sestaveno dne: 17.4.2026", "podpis ... 30.06.2025") are no period ends
    found = [(a, b, d) for a, b, d in _dated(t) if not re.search(r"sestaven|podpis|\bdne\b", t[max(0, a - 40) : a])]
    every = [d for _, _, d in found]
    # period starts are not period ends: "od 1.3.2024", "1.3.2024 - 28.2.2025", or the day after a printed date
    starts = {
        d for a, b, d in found if re.search(r"\bod\s*$", t[max(0, a - 4) : a]) or re.match(r"\s*[-–]\s*\d", t[b:])
    }
    starts |= {d for d in every if d - timedelta(days=1) in every}
    range_ends = {d for a, _, d in found if re.search(r"\d\s*[-–]\s*$", t[max(0, a - 4) : a])}
    ends = sorted(set(every) - starts, reverse=True)

    current = None
    rozvahovy = re.search(r"rozvahov\w*\s+den\W*(.{0,30})", t)
    if rozvahovy:
        own = _dates(rozvahovy.group(1))
        current = own[0] if own else None
    if current is None:
        # the balance sheet date is the last day of a month (mostly a quarter end) and is printed in the headers
        # of every page; a date printed once is more likely the date of preparation or a maturity in a note
        quarter_ends = [d for d in ends if (d.month, d.day) in ((12, 31), (6, 30), (3, 31), (9, 30))]
        for candidates in (quarter_ends, [d for d in ends if _month_end(d)], [d for d in ends if d in range_ends]):
            if candidates:
                current = max(candidates, key=lambda d: (every.count(d), d))
                break
    if current is None:
        year = re.search(r"\bza\s+(?:ucetni\s+)?rok\s+((?:19|20)\d{2})\b", t)  # "účetní závěrka za rok 2024"
        if year is None:
            return None, None
        current = date(int(year[1]), 12, 31)
    prior = next((d for d in ends if d < current and (current - d).days >= 300), None)
    if prior is None:
        try:
            prior = current.replace(year=current.year - 1)
        except ValueError:  # 29 February
            prior = current.replace(year=current.year - 1, day=28)
    return current, prior


def detect_scope(text: str) -> Literal["full", "abbreviated"] | None:
    t = _plain(text)
    if "zkracen" in t or "mikro" in t:
        return "abbreviated"
    if "plnem rozsahu" in t:
        return "full"
    return None


def detect_ico(text: str) -> str | None:
    """IČO: the 8-digit number after 'IČ', 'IČO', 'IČ / DIČ' or 'Identifikační číslo' (OCR-noisy labels allowed,
    "1Č/DIČ")."""
    m = re.search(r"(?:\b[i1l]c\s*o?\b(?:\s*/\s*d[i1l]c\b)?|\bi\w{4,12}\s+c\w{3,5})\W{0,5}(\d{8})\b", _plain(text))
    return m.group(1) if m else None


# the label in front of the company name: "Obchodní firma:", "Firma:" (OCR: "Fima:", "Finna:"), "Daňový subjekt:",
# "Účetní jednotka:", "Název účetní jednotky:"
_COMPANY_LABEL = re.compile(
    r"(?:(?:Obchodn[ií]\s+)?\b[FfA][iíl1]?[rn]{0,2}[mn]?n?a|Da[nň]ov[ýy]\s+subjekt|[ÚU][čc]etn[ií]\s+jednotka"
    r"|N[áa]zev(?:\s+[úu][čc]etn[ií]\s+jednotky)?)\s*:\s*(.+)"
)


def detect_company(text: str) -> str | None:
    """Name after the company label; it ends at a column gap, a ruling ("|", "—") or the next label (IČ)."""
    m = _COMPANY_LABEL.search(text)
    if not m:
        return None
    name = re.split(r"\s{2,}|\n|\s[|—–]|\s[1Il]Č", m.group(1).strip())[0]
    return name.strip(" .,:;„“\"'") or None


# ------------------------------------------------------------------------------------------- columns

ColumnName = Literal["gross", "adjustment", "current", "prior", "row", "extra"]  # extra: not used
StatementKind = Literal["assets", "liabilities", "income_statement"]

ORDER: dict[StatementKind, list[ColumnName]] = {
    "assets": ["gross", "adjustment", "current", "prior"],
    "liabilities": ["current", "prior"],
    "income_statement": ["current", "prior"],
}

_HEADERS = {"BRUTTO": "gross", "KOREKCE": "adjustment", "NETTO": "net"}
_PERIOD_HEADERS = {"BEZNE": "current", "BEZNEM": "current", "MINULE": "prior", "MINULEM": "prior"}


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


def _row_like(col: Column, tokens: list[NumberToken]) -> bool:
    """Most tokens are 2–3 digit codes and they rise down the page (row numbers, unlike values or the
    references in formulas such as '(ř. 83 + 104 + 147)')."""
    members = sorted((t for t in tokens if col.contains(t, 2)), key=lambda t: t.words[0].cy)
    codes = [int(t.text) for t in members if _is_row_code(t.text)]
    if len(codes) < 3 or len(codes) < 0.6 * len(members):
        return False
    rising = sum(b > a for a, b in zip(codes, codes[1:], strict=False))
    return rising >= 0.6 * (len(codes) - 1)


def _merge_overlapping(columns: list[Column]) -> list[Column]:
    """OCR jitters the right edges of one column into two clusters whose x-ranges overlap; real columns are
    separated by white space."""
    out: list[Column] = []
    for col in sorted(columns, key=lambda c: c.x1):
        prev = out[-1] if out else None
        if prev is not None and col.x0 <= prev.x1:
            prev.x0, prev.x1, prev.count = min(prev.x0, col.x0), max(prev.x1, col.x1), prev.count + col.count
        else:
            out.append(col)
    return out


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


def _overlap(a: Column, b: Column) -> bool:
    return a.x0 <= b.x1 and b.x0 <= a.x1


def _within(anchors: list[tuple[str, float]], columns: list[Column]) -> list[tuple[str, float]]:
    """Header words above the numbers only: 'běžného' in a label (A.V.) is not a column header."""
    left = min(c.x0 for c in columns) - 30.0
    return [(name, x) for name, x in anchors if x >= left]


def _header_anchors(lines: list[Line], headers: dict[str, str]) -> list[tuple[str, float]]:
    """(name, x-center) of the header words in `headers` (fuzzy, OCR-tolerant), left to right."""
    anchors = []
    for line in lines[:40]:
        for w in line.words:
            word = _plain(w.text).upper().strip(".:,")
            for header, name in headers.items():
                if len(word) >= 5 and fuzz.ratio(header, word) >= 80:
                    # a text layer may hold the same word twice (fake bold): one anchor per position
                    if not any(n == name and abs(x - w.cx) < 3 for n, x in anchors):
                        anchors.append((name, w.cx))
                    break
    return sorted(anchors, key=lambda a: a[1])


def _name_by_anchors(value_cols: list[Column], anchors: list[tuple[str, float]]) -> None:
    """Each column takes the name of its nearest header. The first Netto (or Běžné) is the current period, the
    next one the prior period; further ones (a third year) and a second column claiming a header are 'extra'."""
    named: list[tuple[ColumnName, float]] = []
    nets = 0
    for name, x in anchors:
        if name == "net":
            nets += 1
            name = "current" if nets == 1 else "prior" if nets == 2 else "extra"
        if name != "extra" and any(n == name for n, _ in named):
            name = "extra"  # a header repeated on another line
        named.append((name, x))  # type: ignore[arg-type]
    for col in value_cols:
        col.name = min(named, key=lambda a: abs(a[1] - col.center))[0]
    for name in {c.name for c in value_cols if c.name != "extra"}:
        same = [c for c in value_cols if c.name == name]
        if len(same) > 1:  # keep the fullest column, then the one nearest its header
            x = next(x for n, x in named if n == name)
            keep = min(same, key=lambda c: (-c.count, abs(x - c.center)))
            for c in same:
                if c is not keep:
                    c.name = "extra"


def _looks_like_notes(col: Column, tokens: list[NumberToken]) -> bool:
    """A 'Příloha' / 'Pozn.' column: small reference numbers, no amounts."""
    members = [t for t in tokens if col.contains(t, 2)]
    return bool(members) and sum(len(re.sub(r"\D", "", t.text)) <= 2 for t in members) >= 0.8 * len(members)


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
    sparse: list[Column] = []
    for cl in clusters:
        members = [t for t in tokens if cl[0] - 0.01 <= t.x1 <= cl[-1] + 0.01]
        lefts = sorted(t.x0 for t in members)
        x0 = lefts[len(lefts) // 10]  # left edge of the wider numbers, ignoring a few outliers
        col = Column(name="current", x0=x0, x1=statistics.median(cl), count=len(members))
        (columns if len(members) >= min_count else sparse).append(col)
    columns = _merge_overlapping(columns)
    if kind == "assets" and columns:
        # Korekce is often printed on a few lines only (the depreciated assets): when no column belongs to the
        # Korekce header, take the sparse one that does
        anchors = _within(_header_anchors(lines, _HEADERS), columns)

        def header_of(c: Column) -> str:
            return min(anchors, key=lambda a: abs(a[1] - c.center))[0]

        if any(n == "adjustment" for n, _ in anchors) and not any(header_of(c) == "adjustment" for c in columns):
            free = [c for c in sparse if header_of(c) == "adjustment" and not any(_overlap(c, d) for d in columns)]
            if free:
                columns = sorted([*columns, max(free, key=lambda c: c.count)], key=lambda c: c.x1)

    # row-number column ("řád."): the rightmost column of rising 2–3 digit codes that still has value columns
    # to its right; anything left of it is the label area (označení, text, formula references)
    for col in reversed(columns[:-1]):
        if _row_like(col, tokens):
            col.name = "row"
            columns = [c for c in columns if c.x0 >= col.x0]
            break
    value_cols = [c for c in columns if c.name != "row"]

    names = ORDER[kind]
    if not value_cols:
        return columns
    if kind == "assets":
        anchors = _within(_header_anchors(lines, _HEADERS), value_cols)
        by_header = len(anchors) >= 3 and len(value_cols) != len(names)  # the order is safer than OCR-garbled headers
    else:
        anchors = _within(_header_anchors(lines, _PERIOD_HEADERS), value_cols)
        by_header = len(value_cols) != len(names) and (
            {"current", "prior"} <= {n for n, _ in anchors} or (len(value_cols) < len(names) and bool(anchors))
        )
    if by_header:
        _name_by_anchors(value_cols, anchors)
        return columns
    if len(value_cols) > len(names):
        for col in value_cols:
            if _looks_like_notes(col, tokens):
                col.name = "extra"
        value_cols = [c for c in value_cols if c.name != "extra"]
    if len(value_cols) == len(names):
        for col, name in zip(value_cols, names, strict=True):
            col.name = name
    elif kind == "assets" and len(value_cols) == 3:
        for col, name in zip(value_cols, ["gross", "current", "prior"], strict=True):  # empty Korekce column
            col.name = name
    elif len(value_cols) > len(names):
        # more columns than expected and no usable header: the statement columns are the rightmost ones
        for col in value_cols[: -len(names)]:
            col.name = "extra"
        for col, name in zip(value_cols[-len(names) :], names, strict=True):
            col.name = name
    elif value_cols:
        # fewer columns than expected: assume the rightmost ones are current, prior (most common layouts)
        for col, name in zip(value_cols, names[-len(value_cols) :], strict=True):
            col.name = name
    return columns
