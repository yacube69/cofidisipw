"""Page locator (CIPW-6): find the balance sheet and income statement pages in a PDF.

Each page gets a score per statement from fuzzy keyword matches (titles and typical line labels, tolerant to
missing diacritics and OCR noise such as "R0ZVAHA" or "PASIVA C!LKEM") plus the share of numeric tokens.
Pages of other statements and of the notes (cash flow, changes in equity, auditor's report, Příloha) are
penalised, so a page that only *mentions* "rozvaha" in prose is not picked.

Text per page comes from the cheapest usable source: the PDF text layer (also the scanner's OCR layer, which
is noisy but good enough for keywords) or, for a scan without any text, a quick 150 DPI Tesseract pass.
"""

from __future__ import annotations

import re
import time
import unicodedata
from dataclasses import dataclass, field

import pymupdf
from rapidfuzz import fuzz

from src.extraction.ocr import QUICK, ocr_page

# (normalized keyword, weight)
BALANCE_SHEET = [
    ("ROZVAHA", 3.0),
    ("AKTIVA CELKEM", 3.0),
    ("PASIVA CELKEM", 3.0),
    ("STALA AKTIVA", 1.0),
    ("OBEZNA AKTIVA", 1.0),
    ("DLOUHODOBY HMOTNY MAJETEK", 1.0),
    ("VLASTNI KAPITAL", 1.0),
    ("CIZI ZDROJE", 1.0),
    ("KRATKODOBE ZAVAZKY", 1.0),
]
INCOME_STATEMENT = [
    ("VYKAZ ZISKU A ZTRATY", 3.0),
    ("TRZBY Z PRODEJE VYROBKU A SLUZEB", 1.5),
    ("VYKONOVA SPOTREBA", 1.5),
    ("OSOBNI NAKLADY", 1.0),
    ("UPRAVY HODNOT V PROVOZNI OBLASTI", 1.0),
    ("PROVOZNI VYSLEDEK HOSPODARENI", 1.5),
    ("NAKLADOVE UROKY", 1.0),
    ("VYSLEDEK HOSPODARENI PRED ZDANENIM", 1.5),
    ("VYSLEDEK HOSPODARENI ZA UCETNI OBDOBI", 1.0),
]
OTHER = [  # titles of pages that look like statements but are not ours
    ("PREHLED O PENEZNICH TOCICH", 6.0),
    ("PENEZNI TOKY Z", 3.0),
    ("PREHLED O ZMENACH VLASTNIHO KAPITALU", 6.0),
    ("ZPRAVA NEZAVISLEHO AUDITORA", 6.0),
    ("PRILOHA UCETNI ZAVERKY", 6.0),
    ("ZPRAVA O VZTAZICH", 4.0),
]

MIN_SCORE = 3.0
MIN_NUMBERS = 8
FUZZY = 86


def normalize(text: str) -> str:
    """Upper case, no diacritics, common OCR confusions in letters, single spaces."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c)).upper()
    text = text.translate(str.maketrans({"0": "O", "!": "E", "|": "I", "1": "I", "$": "S"}))
    return re.sub(r"[^A-Z ]+", " ", re.sub(r"\s+", " ", text)).strip()


def _matches(kw: str, line: str) -> bool:
    if " " not in kw:  # single word: compare with whole words, so ROZVAHA does not match ROZVAHOVY DEN
        return any(fuzz.ratio(kw, token) >= FUZZY for token in line.split())
    return len(line) >= len(kw) * 0.6 and fuzz.partial_ratio(kw, line) >= FUZZY


def keyword_score(lines: list[str], keywords: list[tuple[str, float]]) -> tuple[float, list[str]]:
    score, hits = 0.0, []
    for kw, weight in keywords:
        if any(_matches(kw, line) for line in lines):
            score += weight
            hits.append(kw)
    return score, hits


@dataclass
class PageScore:
    page: int
    source: str
    balance_sheet: float = 0.0
    income_statement: float = 0.0
    other: float = 0.0
    numbers: int = 0
    tokens: int = 0
    hits: list[str] = field(default_factory=list)

    @property
    def numeric_share(self) -> float:
        return self.numbers / self.tokens if self.tokens else 0.0

    def net(self, statement: str) -> float:
        return getattr(self, statement) - self.other


def page_text(page: pymupdf.Page) -> tuple[str, str]:
    text = page.get_text()
    if len(text.strip()) >= 20:
        return text, "text_layer"
    pw = ocr_page(page, config=QUICK)
    return "\n".join(_lines_from_words(pw.words)), "ocr"


def _lines_from_words(words) -> list[str]:
    rows: dict[int, list] = {}
    for w in words:
        rows.setdefault(round(w.cy / 6), []).append(w)
    return [" ".join(x.text for x in sorted(ws, key=lambda w: w.x0)) for _, ws in sorted(rows.items())]


def score_page(page: pymupdf.Page) -> PageScore:
    text, source = page_text(page)
    lines = [normalize(line) for line in text.splitlines()]
    lines = [line for line in lines if line]
    # Keywords may be split over two text-layer lines ("VÝKAZ ZISKU A" / "ZTRÁTY"): also test joined pairs.
    lines += [f"{a} {b}" for a, b in zip(lines, lines[1:], strict=False)]
    tokens = text.split()
    s = PageScore(
        page=page.number + 1,
        source=source,
        numbers=sum(1 for t in tokens if re.fullmatch(r"[-(]?\d[\d.,]*\)?", t)),
        tokens=len(tokens),
    )
    s.balance_sheet, h1 = keyword_score(lines, BALANCE_SHEET)
    s.income_statement, h2 = keyword_score(lines, INCOME_STATEMENT)
    s.other, h3 = keyword_score(lines, OTHER)
    s.hits = h1 + h2 + [f"-{h}" for h in h3]
    return s


def _pick(scores: list[PageScore], statement: str, rival: str) -> list[int]:
    candidates = [
        s
        for s in scores
        if s.net(statement) >= MIN_SCORE and s.numbers >= MIN_NUMBERS and s.net(statement) > s.net(rival)
    ]
    if not candidates:
        return []
    # contiguous runs; a statement spans 1–3 consecutive pages. Keep the strongest run.
    runs: list[list[PageScore]] = []
    for s in candidates:
        if runs and s.page == runs[-1][-1].page + 1:
            runs[-1].append(s)
        else:
            runs.append([s])
    best = max(runs, key=lambda run: sum(s.net(statement) for s in run))
    return [s.page for s in best[:3]]


def locate_statements(doc: pymupdf.Document, return_scores: bool = False):
    """{"balance_sheet": [20, 21], "income_statement": [22]} with 1-based pages (plus scores if asked)."""
    start = time.perf_counter()
    scores = [score_page(page) for page in doc]
    result = {
        "balance_sheet": _pick(scores, "balance_sheet", "income_statement"),
        "income_statement": _pick(scores, "income_statement", "balance_sheet"),
    }
    if return_scores:
        return result, scores, round(time.perf_counter() - start, 2)
    return result


if __name__ == "__main__":  # python -m src.extraction.locate file.pdf
    import sys

    with pymupdf.open(sys.argv[1]) as d:
        found, page_scores, secs = locate_statements(d, return_scores=True)
    for sc in page_scores:
        if sc.balance_sheet or sc.income_statement or sc.other:
            print(
                f"p{sc.page:>3} {sc.source:10} BS {sc.balance_sheet:4.1f} IS {sc.income_statement:4.1f} "
                f"other {sc.other:4.1f} nums {sc.numbers:4d}  {', '.join(sc.hits)}"
            )
    print(found, f"{secs}s")
