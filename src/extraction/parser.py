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
from dataclasses import dataclass, field, replace

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


def _reads_as_liabilities(lines: list[Line], dictionary) -> bool:
    """A balance-sheet page without a readable PASIVA header: more of its lines name a liabilities row than an
    assets row ("Vlastní kapitál", "Základní kapitál", "Závazky" against "Stálá aktiva", "Zásoby")."""
    sides = {side: [plain(v) for d in dictionary.for_statement("balance_sheet", side) for v in d.labels]
             for side in ("assets", "liabilities")}  # fmt: skip
    votes = {"assets": 0, "liabilities": 0}
    for line in lines:
        text = plain(" ".join(w.text for w in line.words if not any(ch.isdigit() for ch in w.text)))
        if len(text) < 6:
            continue
        best = {side: max(fuzz.partial_ratio(v, text) for v in labels) for side, labels in sides.items()}
        if max(best.values()) >= 90 and best["assets"] != best["liabilities"]:
            votes[max(best, key=best.get)] += 1  # type: ignore[arg-type]
    return votes["liabilities"] > votes["assets"]


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


# a parenthesis that is a formula, not part of the name: "(A.+B.+C.+D.)", "(součet B.1. až B.4.)", "(ř. 83 + 104)"
_FORMULA = re.compile(r"\((?=[^()]*(?:\d|\+|součet|soucet|ř\.|až))[^()]*(?:\)|$)")  # also cut off at the line end
# an označení that OCR garbled so the parser kept it in the label: "C.m.2.1. —", "c.n2.a.", "AIM.", "C.H.8.3. |"
_LEAD_CODE = re.compile(r"^(?:[^\s]{1,12}?[.,](?=[\s|©!—–-])\s*(?:[|©!—–-]\s*)*)+(?=\S)")  # ends at a space
# an označení printed without its last dot: "A.3 Služby", "C.II.1 Dlouhodobé pohledávky", "D.2 Náklady na …"
_CODE_NO_LAST_DOT = re.compile(r"^[A-Za-z]{1,4}(?:[.,][A-Za-z0-9]{1,4})+(?=\s+\S)")


_UPPER = "A-ZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ"
# noise OCR puts in front of the označení: punctuation and pieces of rulings ("! D.", ": cI", "„Daň"), or a lone
# small letter before a capital ("i C.l1.2.", "j C.JI.4.")
_HEAD_NOISE = re.compile(rf"^(?:[^\w*]+|[a-z]\s+(?=[{_UPPER}]))+")
# an označení OCR read without its dots, before a capitalised name: "cI Zásoby", "CH Pohledávky", "RIr Dlouhodobý"
_SHORT_CODE = re.compile(rf"^[A-Za-z0-9|]{{1,3}}\.?\s+(?:[^\w\s]+\s+)*(?=[{_UPPER}])")


def lead_code(label: str) -> str:
    """The garbled označení at the start of a label, '' when the label starts with a word."""
    text = _HEAD_NOISE.sub("", label)
    m = _LEAD_CODE.match(text) or _CODE_NO_LAST_DOT.match(text)
    if not m:
        return ""
    code = m.group(0).strip(" |©!—–-")
    # an abbreviated word ("Ostat.") is not a code: an označení has at most 4 letters (C.II.2.1., A.IV., cC.n.)
    return code if sum(ch.isalpha() for ch in code) <= 4 else ""


def _noise(word: str) -> bool:
    """A word at the end of a label that is not part of the name: OCR specks and ruling pieces ("í", "nA", "A:",
    "|"). A parenthesis ("(+/-)", "(-)") and a number (a note reference in annual reports) are kept."""
    if word.startswith("("):
        return False
    letters = re.sub(r"\W", "", word)
    return len(letters) <= 2 and not letters.isdigit()


def clean_label(label: str) -> str:
    """The name alone: without noise and a garbled označení in front, formulas in parentheses and specks at the
    end."""
    text = _HEAD_NOISE.sub("", label)
    code = lead_code(text)
    text = _HEAD_NOISE.sub("", text[len(code) :] if code else text)
    text = _HEAD_NOISE.sub("", _SHORT_CODE.sub("", text))
    words = _FORMULA.sub(" ", text).split()
    while len(words) > 1 and _noise(words[-1]):
        words.pop()
    return " ".join(words)


def _label_score(label: str, definition: LineDef) -> float:
    variants = [plain(v) for v in definition.labels]
    best = 0.0
    for text in {label, clean_label(label)}:
        lab = plain(text)
        if len(lab) >= 3:
            best = max(best, *(float(fuzz.ratio(lab, v)) for v in variants))
    return best


def _parents(candidates: list[LineDef]) -> dict[str, str]:
    """key -> key of the line one level up by označení (C.II.2.1. -> C.II.2.), among the same candidates."""
    by_code = {d.code: d.key for d in candidates if d.code}
    out = {}
    for d in candidates:
        parts = [p for p in d.code.split(".") if p]
        if len(parts) > 1 and (up := ".".join(parts[:-1]) + ".") in by_code:
            out[d.key] = by_code[up]
    return out


def _last_index(items: list[str], value: str) -> int:
    return len(items) - 1 - items[::-1].index(value)


def _depth(code: str) -> int:
    return len([part for part in code.split(".") if part])


def _starts_small(label: str) -> bool:
    text = clean_label(label)
    return next((ch for ch in text if ch.isalpha()), "").islower()


def continues(line: ParsedLine, above: ParsedLine | None = None) -> bool:
    """A line that carries on the label of the line above: starts with a small letter ("dlouhodobý hmotný
    majetek") and has no označení of its own. New lines start with a code or a capital ("Osobní náklady",
    "c. Závazky"). Exception: when the line above has no code, the code may sit on this second line, vertically
    centred with the numbers ("Výsledek … bez menšinových" / "A.V. podílů (+/-) 25 801")."""
    if not _starts_small(line.label):
        return False
    has_code = bool(line.code or lead_code(line.label))
    return not has_code or (above is not None and not above.code and not lead_code(above.label))


def identify(
    line: ParsedLine, candidates: list[LineDef], extra_label: str | None = None, row_offset: int | None = 0
) -> list[Match]:
    """Score every candidate line for this printed line. `row_offset`: printed row number minus the decree's row
    around this line (another version of the form numbers its rows shifted); None when it is not known, then the
    printed row number is not used."""
    out = []
    for d in candidates:
        label = _label_score(line.label, d)
        if extra_label:
            # the line above belongs to this one: either the first half of a wrapped label, or the readable
            # label of a shaded row whose own text is garbled. The second half alone must not win.
            combined = _label_score(f"{extra_label} {line.label}", d)
            label = combined if len(plain(line.label)) >= 8 else max(combined, _label_score(extra_label, d))
        lead = norm_code(lead_code(line.label))
        # the označení in the label counts when the code column holds none, or holds less than the label (a side
        # letter "A" printed before "C.II.3 Časové rozlišení aktiv")
        use_lead = bool(lead) and (not line.code or _depth(lead) > _depth(line.code))
        printed = lead if use_lead else line.code
        field_code = d.code
        if printed and d.code and not re.search(r"[A-Za-z]", printed):
            # a sub-line printed with its own number only ("4.", "2.1."): compare with the end of the field's code
            field_code = ".".join([p for p in d.code.split(".") if p][-_depth(printed) :]) + "."
        code = code_score(printed, field_code)
        row = None
        if row_offset is not None and line.row and line.row.isdigit() and d.row and d.row.isdigit():
            row = 100.0 if int(line.row) - row_offset == int(d.row) else 0.0
            if row_offset and label >= ACCEPT:
                # a shift inferred from the lines around (another numbering) only helps to name a garbled label;
                # it drifts where the versions differ, so it never overrules a readable label and its code
                row = None
        # the label decides; code and row number confirm or contradict (both are often garbled by OCR)
        score = label
        if row == 100.0:
            score += 12
        elif row == 0.0:
            score -= 4
        if code is not None:
            score += 6 if code >= 90 else (-6 if code < 50 else 0)
        if use_lead and d.code and _depth(lead) > _depth(d.code):
            score -= 8  # a garbled označení deeper than the field's (B.4.3. for B.II.): a sub-line of it, not the line
        # a garbled label on a shaded row: row number + code agreeing is enough evidence
        if label < 50 and row == 100.0 and (code is None or code >= 60):
            score = 82.0 if code is not None and code >= 80 else 78.0
        elif label < 78 and row == 100.0 and row_offset and (code is None or code >= 60):
            # a garbled label (a stamp over it, a shaded row) on its row, the shift confirmed by readable lines
            score = max(score, 80.0)
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
    # "1 234" or "1234" (many statements print no thousands separator); "12 34" is a segmentation doubt
    clean = re.fullmatch(r"-?(\d{1,3}( \d{3})*|\d+)", raw.replace("−", "-"))
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
    templates: dict[str, tuple[float, list[Column]]] = {}  # kind -> (page width, columns of the fullest page)
    sections: dict[str, list[str]] = {}  # kind -> keys identified so far: a continuation page stays in its section

    for statement, page_list in pages.items():
        for page_no in page_list:
            pw = by_page.get(page_no)
            if pw is None:
                continue
            lines = group_lines(straighten(pw))
            if statement == "balance_sheet":
                parts = _split_sides(lines)
                if len(parts) == 1 and parts[0][0] == "assets" and _reads_as_liabilities(lines, dictionary):
                    parts = [("liabilities", lines)]  # PASIVA not read (a tilted scan): the rows tell the side
            else:
                parts = [("income_statement", lines)]
            for kind, part in parts:
                columns = _with_template(detect_columns(part, kind), templates.get(kind), pw.width)
                values = [c for c in columns if c.name not in ("row", "extra")]
                if not values:
                    continue
                template = templates.get(kind)
                if template is None or len(values) >= len([c for c in template[1] if c.name not in ("row", "extra")]):
                    templates[kind] = (pw.width, columns)
                side = None if kind == "income_statement" else kind
                candidates = dictionary.for_statement(statement, side)  # type: ignore[arg-type]
                target = fs.statement(statement)  # type: ignore[arg-type]
                seen = sections.setdefault(kind, [])
                _parse_part(part, columns, pw, kind, candidates, target, best_score, multiplier, log, seen)

    full_only = {d.key for d in dictionary.lines if not d.abbreviated}
    if fs.metadata.scope is None and any(k in full_only for k in [*fs.balance_sheet, *fs.income_statement]):
        fs.metadata.scope = "full"
    return fs


def _with_template(columns: list[Column], template: tuple[float, list[Column]] | None, width: float) -> list[Column]:
    """A statement printed over several pages keeps its columns. On a continuation page where the current year is
    mostly blank (zeros only in the prior year), too few numbers form a column, and the columns found are then
    named wrong. When every column found sits where a column of the previous page was, use that page's columns."""
    if template is None or abs(template[0] - width) > 2:
        return columns
    found = [c for c in columns if c.name not in ("row", "extra")]
    known = [c for c in template[1] if c.name not in ("row", "extra")]
    if len(found) >= len(known) or not all(any(abs(c.x1 - t.x1) <= 20 for t in template[1]) for c in found):
        return columns
    return [replace(c) for c in template[1]]


def row_offsets(parsed: list[ParsedLine], candidates: list[LineDef]) -> list[int | None]:
    """For each line, its printed row number minus the decree's row (None: unknown, do not use the row number).

    Forms come in versions that number their rows differently (PASIVA CELKEM is row 078 in one, 082 in another),
    and some software numbers the rows its own way. The lines whose label alone names one line of the dictionary
    are anchors: printed row minus that line's row is the shift around them. A line takes the shift of the two
    anchors nearest to it when they agree (one above and one below, or the two nearest on one side), so a
    garbled label (a stamp over it) is still identified by its row; where the anchors disagree (the numbering
    changes in between) or there are none nearby, the row number says nothing. A page without any anchor keeps
    the decree's numbering (shift 0), as before."""
    anchors: list[tuple[int, int]] = []  # (printed row, shift)
    for line in parsed:
        if not (line.row and line.row.isdigit()):
            continue
        scores = sorted(((_label_score(line.label, d), d) for d in candidates), key=lambda x: x[0], reverse=True)
        best = scores[0][1] if scores else None
        if not scores or scores[0][0] < 95 or (len(scores) > 1 and scores[1][0] >= 95):
            continue  # unclear, or a label printed in two sections
        if best is not None and best.row and best.row.isdigit():
            anchors.append((int(line.row), int(line.row) - int(best.row)))
    if not anchors:
        return [0] * len(parsed)
    anchors.sort()
    out: list[int | None] = []
    for line in parsed:
        if not (line.row and line.row.isdigit()):
            out.append(None)
            continue
        r = int(line.row)
        above = [a for a in anchors if a[0] <= r]
        below = [a for a in anchors if a[0] >= r]
        if above and below:
            pair = [above[-1], below[0]]
        else:
            pair = (above[-2:] if above else below[:2])
        shifts = {shift for _, shift in pair}
        out.append(shifts.pop() if len(shifts) == 1 and len(pair) == 2 or (len(pair) == 1 and pair[0][0] == r)
                   else None)  # fmt: skip
    return out


def _parse_part(lines, columns, pw, kind, candidates, target, best_score, multiplier, log, seen=None) -> None:
    first_col_x = min(c.x0 for c in columns)
    header_end = _header_end(lines, columns)
    body = [(line, split_line(line, columns, pw.page, kind)) for line in lines if line.cy > header_end]
    offsets = dict(zip([id(p) for _, p in body], row_offsets([p for _, p in body], candidates), strict=True))
    pending: ParsedLine | None = None
    parent = _parents(candidates)
    codes = {d.key: d.code for d in candidates}
    # keys identified on this side of the statement in page order, carried over to its next page (the current
    # section is the latest parent)
    seen = [] if seen is None else seen
    for line, parsed in body:
        if not parsed.has_values:
            # a label without numbers: may be the first half of a wrapped label or a garbled shaded row label
            if parsed.label and any(w.x1 < first_col_x for w in line.words):
                pending = (
                    parsed if pending is None or line.cy - pending.y > 2.2 * line.height else _join(pending, parsed)
                )
            continue
        extra = None
        close = pending is not None and parsed.y - pending.y <= 2.2 * max(parsed.height, pending.height)
        # the line above is the first half of this label only when this line carries it on; otherwise it was a
        # line without numbers of its own (an empty sub-line) and joining would spoil this line's label
        if close and pending is not None and (continues(parsed, pending) or len(plain(parsed.label)) < 3):
            extra = pending.label
            if not parsed.code and pending.code:
                parsed.code = pending.code
        pending = None
        matches = identify(parsed, candidates, extra, offsets[id(parsed)])
        if not matches or matches[0].score < ACCEPT:
            log.unmatched.append((pw.page, parsed.code, parsed.label, parsed.row))
            continue
        best = matches[0]
        # tied: nearly the same score, or the same name (C.I.9. and C.II.8. "Závazky ostatní") when the code does
        # not clearly name one of them (an OCR-garbled "c. LLM 8."): then the section decides, not the garbled code
        unsure_code = best.code_score is None or best.code_score < 90
        name_floor = max(ACCEPT, best.label_score - 2)
        # only parallel lines of two sections (C.I.9. and C.II.8.), not a line and a sub-line of another section
        # named alike (D. and C.II.3. "Časové rozlišení aktiv"): there the code depth still tells them apart
        same_name = [
            m
            for m in matches
            if unsure_code and m.label_score >= name_floor and _depth(codes[m.key]) == _depth(codes[best.key])
        ]
        ties = [m for m in matches if m.score >= best.score - 2 or m in same_name]
        if len(ties) > 1:
            log.ambiguous.append((pw.page, parsed.label, [m.key for m in ties]))
            # lines with the same label (trade receivables under long-term C.II.1. and under short-term C.II.2.):
            # the section decides, i.e. the tied field whose parent line came last; else the first field still free
            in_section = [m for m in ties if parent.get(m.key) in seen]
            if in_section:  # latest section first; within one section a field still free
                best = max(in_section, key=lambda m: (_last_index(seen, parent[m.key]), m.key not in target))
            else:
                # no section known: the first field still free in dictionary order (the common short-term field
                # before the rare long-term one, so a lone "Pohledávky z obchodních vztahů" is short-term)
                best = next((m for m in ties if m.key not in target), best)
            score = best.score - 10  # flag everything on an ambiguous line
        else:
            score = best.score
        seen.append(best.key)
        if best.key in parent and len(ties) == 1:
            # a sub-line with a name of its own ("Dlouhodobé přijaté zálohy", "Odložený daňový závazek") shows the
            # section even when the section's own line was not read; later lines named alike in C.I. and C.II.
            # ("Vydané dluhopisy", "Závazky - ovládaná nebo ovládající osoba") then go to that section
            seen.append(parent[best.key])
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
