"""Rule-based parser (CIPW-32): words with positions → FinancialStatements. Deterministic, no LLM.

For every statement page:
1. straighten the words (OCR skew) and group them into lines;
2. split balance-sheet pages into the assets and liabilities part (both can share a page);
3. detect the numeric columns of each part (CIPW-9);
4. split every line into označení (code), label, row number and values per column;
5. identify the line by combining three signals, each tolerant to OCR noise:
     - label: fuzzy match against the official label and variants (config/line_items.yaml)
     - code:  označení compared after mapping I/l/1/| to one symbol
     - row:   číslo řádku
   A weak or ambiguous match is flagged for review, never silently guessed.
6. keep page, bounding box (on the original page), OCR confidence and match score for every value.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from src.extraction.layout import Line, NumberToken, group_lines, merge_number_tokens, straighten, unstraighten
from src.extraction.numbers import (
    Column,
    StatementKind,
    clean_numeric_text,
    detect_columns,
    detect_company,
    detect_ico,
    detect_period,
    detect_scope,
    detect_unit,
    is_dash,
    parse_number,
)
from src.extraction.types import PageWords, Word
from src.line_items import LineDef, LineDictionary, load_dictionary
from src.schema import BBox, FinancialStatements, LineItem, Metadata, Value

ACCEPT = 78.0  # combined score needed to accept a line
REVIEW = 86.0  # below this an accepted value is flagged for review
LOW_CONF = 60.0  # OCR confidence below this flags the value

_CODE_TOKEN = re.compile(
    r"^(?:[A-L]|[IVXl1|]{1,4}|\d{1,2}|\*{1,3}|\+|[A-L][.,]?[IVXl1|]{0,4}|[A-L]\.?\+[A-L]|\+[A-L])[.,:;]?$"
)


# ------------------------------------------------------------------------------------------ helpers


def plain(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    text = re.sub(r"\(\s*[+-]\s*/\s*[+-]\s*\)|\(\s*[+-]\s*\)", " ", text)  # "(+/-)", "(-)"
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", text).split())


def norm_code(text: str) -> str:
    """'B. I. 2.' → 'B.I.2.', 'B. + C.' → 'B.+C.'; '**' stays."""
    t = re.sub(r"\s+", "", text.upper()).replace(",", ".").replace(":", ".").replace(";", ".")
    t = re.sub(r"\.+", ".", t)
    if t and not t.endswith((".", "*")):
        t += "."
    return t


def code_key(code: str) -> str:
    """Comparison form: OCR confuses I, l, 1 and | in Roman numerals, so they become one symbol."""
    return re.sub(r"[I1L|!]", "1", code.upper())


def code_score(found: str, expected: str) -> float | None:
    if not found:
        return None
    a, b = code_key(found), code_key(expected)
    if not b:
        return 30.0  # line has a code, the field has none (e.g. AKTIVA CELKEM)
    if a == b:
        return 100.0
    return float(fuzz.ratio(a, b))


@dataclass
class ParsedLine:
    page: int
    kind: StatementKind
    code: str
    label: str
    row: str | None
    values: dict[str, list[NumberToken | Word]] = field(default_factory=dict)
    words: list[Word] = field(default_factory=list)
    y: float = 0.0
    height: float = 8.0

    @property
    def has_values(self) -> bool:
        return any(self.values.values())


@dataclass
class Match:
    key: str
    score: float
    label_score: float
    code_score: float | None
    row_score: float | None


# ---------------------------------------------------------------------------------------- line split


def _split_sides(lines: list[Line]) -> list[tuple[StatementKind, list[Line]]]:
    """Balance-sheet page → [('assets', lines), ('liabilities', lines)] split at the PASIVA header."""
    for i, line in enumerate(lines):
        words = [plain(w.text) for w in line.words]
        if any(fuzz.ratio(w, "pasiva") >= 83 for w in words):
            if i <= 2 or not any("aktiva" in plain(x.text) for x in lines[:i] for x in x.words):
                return [("liabilities", lines)]
            return [("assets", lines[:i]), ("liabilities", lines[i:])]
    return [("assets", lines)]


def split_line(line: Line, columns: list[Column], page: int, kind: StatementKind) -> ParsedLine:
    value_cols = [c for c in columns if c.name != "row"]
    row_col = next((c for c in columns if c.name == "row"), None)
    first_col_x = min((c.x0 for c in columns), default=10_000.0)
    tol = 6.0

    parsed = ParsedLine(
        page=page, kind=kind, code="", label="", row=None, words=line.words, y=line.cy, height=line.height
    )
    label_words: list[Word] = []
    code_parts: list[str] = []
    tokens = merge_number_tokens(line.words)
    for tok in tokens:
        x0 = tok.x0
        cx = tok.cx
        text = tok.text
        if row_col is not None and row_col.x0 - tol <= cx <= row_col.x1 + tol:
            cleaned = clean_numeric_text(text) or ""
            digits = re.sub(r"\D", "", cleaned)
            if 2 <= len(digits) <= 3:
                parsed.row = digits.zfill(3) if len(digits) == 3 else digits
            continue
        if x0 >= first_col_x - tol or (isinstance(tok, NumberToken) and cx >= first_col_x - 2 * tol):
            col = _nearest_column(cx, tok.x1, value_cols, tol)
            if col is not None and (isinstance(tok, NumberToken) or is_dash(text) or text.strip() in ZERO_LIKE):
                parsed.values.setdefault(col.name, []).append(tok)
            continue
        # left part: označení first, then the label
        if not label_words and _CODE_TOKEN.match(text) and len(code_parts) < 5:
            code_parts.append(text)
        else:
            label_words.append(tok if isinstance(tok, Word) else tok.words[0])
    parsed.code = norm_code(" ".join(code_parts)) if code_parts else ""
    parsed.label = " ".join(w.text for w in label_words)
    return parsed


def _nearest_column(cx: float, x1: float, cols: list[Column], tol: float) -> Column | None:
    best, best_d = None, 1e9
    for c in cols:
        if c.x0 - 3 * tol <= cx <= c.x1 + tol or abs(x1 - c.x1) <= 2 * tol:
            d = abs(x1 - c.x1)
            if d < best_d:
                best, best_d = c, d
    return best


# -------------------------------------------------------------------------------------- identification


def _label_score(label: str, definition: LineDef) -> float:
    lab = plain(label)
    if len(lab) < 3:
        return 0.0
    return max(float(fuzz.ratio(lab, plain(v))) for v in definition.labels)


def identify(line: ParsedLine, candidates: list[LineDef], extra_label: str | None = None) -> list[Match]:
    out = []
    for d in candidates:
        label = _label_score(line.label, d)
        if extra_label:
            # the line above belongs to this one: either the first half of a wrapped label, or the readable
            # label of a shaded row whose own text is garbled. The second half alone must not win.
            combined = _label_score(f"{extra_label} {line.label}", d)
            label = combined if len(plain(line.label)) >= 8 else max(combined, _label_score(extra_label, d))
        code = code_score(line.code, d.code)
        row = None
        if line.row and d.row:
            row = 100.0 if line.row.lstrip("0") == d.row.lstrip("0") else 0.0
        # the label decides; code and row number confirm or contradict (both are often garbled by OCR)
        score = label
        if row == 100.0:
            score += 12
        elif row == 0.0:
            score -= 4
        if code is not None:
            score += 6 if code >= 90 else (-6 if code < 50 else 0)
        # a garbled label on a shaded row: row number + code agreeing is enough evidence
        if label < 50 and row == 100.0 and (code is None or code >= 60):
            score = 82.0 if code is not None and code >= 80 else 78.0
        score = min(score, 100.0)
        out.append(Match(key=d.key, score=score, label_score=label, code_score=code, row_score=row))
    return sorted(out, key=lambda m: m.score, reverse=True)


# --------------------------------------------------------------------------------------------- values


ZERO_LIKE = {"o", "O", "0", "[]", "()", "D", "Q", "©", "®"}  # how OCR reads a lone 0 in a cell


def _value(tokens: list[NumberToken | Word], unit: int, pw: PageWords, score: float) -> Value | None:
    numbers = [t for t in tokens if isinstance(t, NumberToken)]
    if not numbers:
        zero = [t for t in tokens if t.text.strip() in ZERO_LIKE]
        if zero:
            w = zero[0]
            return Value(
                value=0.0,
                raw=w.text,
                page=pw.page,
                bbox=unstraighten(w.bbox, pw),
                source=pw.source,
                ocr_conf=w.conf,
                match_score=round(score, 1),
                needs_review=w.text.strip() not in "0oO",
            )
        if tokens and all(is_dash(t.text) for t in tokens):
            w = tokens[0]
            return Value(
                value=0.0,
                raw=w.text,
                page=pw.page,
                bbox=unstraighten(w.bbox, pw),
                source=pw.source,
                match_score=round(score, 1),
            )
        return None
    words = [w for t in numbers for w in t.words]
    raw = " ".join(t.text for t in numbers)
    number = parse_number(raw)
    candidates: list[float] = []
    if number is None and len(numbers) > 1:  # e.g. "24067 508" read as one value with a broken group
        number = parse_number(raw.replace(" ", ""))
    box = words[0].bbox
    for w in words[1:]:
        box = box.union(w.bbox)
    confs = [w.conf for w in words if w.conf is not None]
    conf = min(confs) if confs else None
    review = number is None or score < REVIEW or (conf is not None and conf < LOW_CONF)
    clean = re.fullmatch(r"-?\d{1,3}( \d{3})*", raw.replace("−", "-"))
    if number is not None and not clean and (pw.source == "ocr" or len(numbers) > 1):
        review = True  # OCR digits that do not form a clean thousands pattern: segmentation is unsure
    return Value(
        value=number * unit if number is not None else None,
        raw=raw,
        page=pw.page,
        bbox=unstraighten(_bbox(box), pw),
        ocr_conf=round(conf, 1) if conf is not None else None,
        match_score=round(score, 1),
        source=pw.source,
        needs_review=review,
        candidates=candidates,
    )


def _bbox(b: BBox) -> BBox:
    return BBox(x0=round(b.x0, 2), y0=round(b.y0, 2), x1=round(b.x1, 2), y1=round(b.y1, 2))


# ------------------------------------------------------------------------------------------------ main


@dataclass
class ParseLog:
    """What the parser saw: useful for the debug view and for extending the dictionary."""

    matched: list[tuple[int, str, str, float]] = field(default_factory=list)  # page, key, label, score
    unmatched: list[tuple[int, str, str, str | None]] = field(default_factory=list)  # page, code, label, row
    ambiguous: list[tuple[int, str, list[str]]] = field(default_factory=list)


def parse_statements(
    page_words: list[PageWords],
    pages: dict[str, list[int]],
    dictionary: LineDictionary | None = None,
    log: ParseLog | None = None,
) -> FinancialStatements:
    dictionary = dictionary or load_dictionary()
    log = log if log is not None else ParseLog()
    by_page = {pw.page: pw for pw in page_words}
    all_text = "\n".join(pw.text for pw in page_words)
    unit = detect_unit(all_text)
    current, prior = detect_period(all_text)
    fs = FinancialStatements(
        metadata=Metadata(
            company_name=detect_company(
                "\n".join(" ".join(w.text for w in ln.words) for pw in page_words for ln in group_lines(pw.words)[:12])
            ),
            ico=detect_ico(all_text),
            period_end=current,
            prior_period_end=prior,
            scope=detect_scope(all_text),
            unit_multiplier=unit or 1000,
            source_file=page_words[0].file if page_words else None,
            statement_pages={k: v for k, v in pages.items() if v},
            extraction_source={pw.page: pw.source for pw in page_words},
        )
    )
    multiplier = fs.metadata.unit_multiplier
    best_score: dict[str, float] = {}

    for statement, page_list in pages.items():
        for page_no in page_list:
            pw = by_page.get(page_no)
            if pw is None:
                continue
            lines = group_lines(straighten(pw))
            if statement == "balance_sheet":
                parts = _split_sides(lines)
            else:
                parts = [("income_statement", lines)]
            for kind, part in parts:
                columns = detect_columns(part, kind)
                if not [c for c in columns if c.name != "row"]:
                    continue
                side = None if kind == "income_statement" else kind
                candidates = dictionary.for_statement(statement, side)  # type: ignore[arg-type]
                target = fs.statement(statement)  # type: ignore[arg-type]
                _parse_part(part, columns, pw, kind, candidates, target, best_score, multiplier, log)

    full_only = {d.key for d in dictionary.lines if not d.abbreviated}
    if fs.metadata.scope is None and any(k in full_only for k in [*fs.balance_sheet, *fs.income_statement]):
        fs.metadata.scope = "full"
    return fs


def _parse_part(lines, columns, pw, kind, candidates, target, best_score, multiplier, log) -> None:
    first_col_x = min(c.x0 for c in columns)
    header_end = _header_end(lines, columns)
    pending: ParsedLine | None = None
    for line in lines:
        if line.cy <= header_end:
            continue
        parsed = split_line(line, columns, pw.page, kind)
        if not parsed.has_values:
            # a label without numbers: may be the first half of a wrapped label or a garbled shaded row label
            if parsed.label and any(w.x1 < first_col_x for w in line.words):
                pending = (
                    parsed if pending is None or line.cy - pending.y > 2.2 * line.height else _join(pending, parsed)
                )
            continue
        extra = None
        if pending is not None and parsed.y - pending.y <= 2.2 * max(parsed.height, pending.height):
            extra = pending.label
            if not parsed.code and pending.code:
                parsed.code = pending.code
        pending = None
        matches = identify(parsed, candidates, extra)
        if not matches or matches[0].score < ACCEPT:
            log.unmatched.append((pw.page, parsed.code, parsed.label, parsed.row))
            continue
        best = matches[0]
        if len(matches) > 1 and matches[1].score >= best.score - 2 and matches[1].key != best.key:
            log.ambiguous.append((pw.page, parsed.label, [best.key, matches[1].key]))
            score = best.score - 10  # flag everything on an ambiguous line
        else:
            score = best.score
        if best.key in target and best_score.get(best.key, 0) >= score:
            continue  # keep the earlier/better line (e.g. "Základní kapitál" A.I. before A.I.1.)
        item = LineItem(
            key=best.key, code=parsed.code or None, row=parsed.row, label=(extra + " " if extra else "") + parsed.label
        )
        for col_name, toks in parsed.values.items():
            value = _value(toks, multiplier, pw, score)
            if value is not None and col_name in ("gross", "adjustment", "current", "prior"):
                setattr(item, col_name, value)
        if item.values():
            target[best.key] = item
            best_score[best.key] = score
            log.matched.append((pw.page, best.key, item.label or "", round(score, 1)))


def _join(a: ParsedLine, b: ParsedLine) -> ParsedLine:
    a.label = f"{a.label} {b.label}".strip()
    a.code = a.code or b.code
    a.y = b.y
    return a


def _header_end(lines: list[Line], columns: list[Column]) -> float:
    """y where data rows start: below the last header line (Brutto/Netto/dates/'1 2 3 4') that precedes the
    first line carrying real values."""
    value_cols = [c for c in columns if c.name != "row"]
    end = 0.0
    for line in lines:
        values = [
            t
            for t in merge_number_tokens(line.words)
            if isinstance(t, NumberToken)
            and re.search(r"\d{3}", t.text)
            and not re.search(r"\d{1,2}\.\s*\d{1,2}\.", t.text)
            and any(c.x0 - 10 <= t.cx <= c.x1 + 10 for c in value_cols)
        ]
        if values:
            break
        text = plain(line.text)
        if (
            re.search(r"(brutto|netto|korekce|bezne|minule|skutecnost)", text)
            or re.search(r"\d{1,2}\.\s*\d{1,2}\.\s*(19|20)\d{2}", line.text)
            or re.fullmatch(r"[a-z0-9 ]{1,20}", text)
        ):
            end = max(end, line.y1)
    return end
