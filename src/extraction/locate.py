"""Page locator (CIPW-6): find the balance sheet and income statement pages in a PDF.

    locate_statements(doc) -> {"balance_sheet": [3, 4], "income_statement": [5]}   # 1-based page numbers

Text per page comes from the cheapest usable source: the PDF text layer when it is readable (of the two reading
orders, the one that shows more statement words is kept: some scanner OCR layers get shuffled by sort=True),
otherwise a quick 150 DPI Tesseract pass (lang="ces"; re-read at 300 DPI if it reads almost nothing; the top of the
page is re-read on the red channel when a title printed on a pink/red banner was lost).

1. Keyword pass: every page is scored with weighted keywords (titles count only at the top of the page; fuzzy
   matching for OCR text). A page with a title opens a statement; the next untitled pages are added while they look
   like its continuation (PASIVA without a title, bare table, column header, balance sheet row words), up to a limit.
2. Structural pass (Czech GAAP, vyhlaska 500/2002): the rows of the statements are fixed by law, so a statement is
   complete only when its first and last rows have been seen ("AKTIVA CELKEM" ... "PASIVA CELKEM" ... "Casove
   rozliseni pasiv"; "Trzby ..." ... "Vysledek hospodareni za ucetni obdobi"). It adds missing continuation pages,
   drops pages after a complete statement and finds statements whose title was not read. Statements without these
   rows (IFRS, foreign groups) keep the result of the keyword pass, plus a few IFRS-specific rules.

Consolidated and individual statements in the same PDF are returned together; locate_statements_split() separates
them. Measured on unseen filings (Czech companies, statements from 2022 on): 26/28 exact, 27/28 with one extra page.
"""

from __future__ import annotations

import re
import time
import unicodedata
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import pymupdf
import pytesseract
from PIL import Image
from rapidfuzz import fuzz

from src.extraction import ocr as _ocr  # noqa: F401  (points pytesseract at the configured Tesseract)

OCR_DPI = 150
OCR_RETRY_DPI = 300    # if the quick pass reads (almost) nothing, read the page again at this resolution
HEADER_FRACTION = 4    # top 1/4 of the page is re-read on the red channel if no title was found (pink/red banners)
OCR_MIN_CHARS = 150    # fewer readable characters than this = suspicious page
MIN_CHARS = 80          # below this, the page is treated as a scan
MIN_ALNUM_RATIO = 0.6   # below this, the text layer is garbage
MIN_DIGIT_RATIO = 0.08        # bare table pages (no keywords at all) must be this numeric
MIN_DIGIT_RATIO_TITLE = 0.03  # sparse tables (small companies): only used as a fallback
SPARSE_MIN_SCORE = 5          # minimum score for a sparse-table page to open a statement
HEAD_CHARS = 600        # titles must appear at the top of the page (not in running text)
LOOSE_CHARS = 250       # the loose keyword "zisku a ztrat" only counts at the very top of the page
EARLY_CHARS = 150       # a title this close to the top of the page is a real title, not running text
EARLY_MIN_SCORE = 3     # ...and then a lower score is enough (poor scans)
EARLY_MIN_DIGITS = 0.05 # ...and fewer digits
MIN_SCORE = 4           # minimum page score to count as a statement page
# max pages without their own title that may follow a titled page (e.g. PASIVA without ROZVAHA header)
ROW_STEMS = ("zavaz", "pohled", "zasob", "kapital", "dluhopis", "uver", "majetek", "rezerv")
ROW_MIN_DIGITS = 0.05   # a keyword-less page with balance-sheet row words may be this poor in digits
MAX_BRIDGE = 2          # up to 2 neutral pages (cover, stray table rows) may sit between ASSETS and LIABILITIES
TITLE_CONT_MIN_DIGITS = 0.01  # next page that starts with the statement title: accepted even if OCR read few digits
WEAK_MIN_DIGITS = 0.05   # a page with "aktiva"/"pasiva" right after the statement may have fewer digits (last page)
HDR_CONT_MIN_DIGITS = 0.005  # keyword-less page that starts with the column header of a statement table
MAX_BARE = 3            # pages WITHOUT any keyword are accepted only as the first 3 continuation pages
MAX_CONTINUATION = {"balance_sheet": 6, "income_statement": 2}
# pages that start with one of these are NOT statement pages: they end a continuation
STOP_PHRASES = [
    "o zmenach vlastniho kapitalu", "zmen vlastniho kapitalu", "o peneznich tocich", "penezniho toku",
    "zprava nezavisleho auditora", "o ostatnim uplnem vysledku", "prehled o zmenach",
    "vykaz ostatniho uplneho vysledku",   # IFRS: separate page for other comprehensive income (not the P&L)
    "pohybu ve vlastnim kapitalu",        # IFRS: "prehled pohybu ve vlastnim kapitalu" (statement of changes in equity)
    "(cash flow)", "prehled o penez",     # title split over two columns: "Prehled o penez-nich | tocich (cash flow)"
]
# phrases that must match exactly: a fuzzy match would also hit "vykaz zisku nebo ztraty a ostatniho uplneho vysledku"
EXACT_STOP_PHRASES = {"vykaz ostatniho uplneho vysledku"}
STOP_WORDS = ["priloha", "obsah", "poznamky"]

# (keyword, weight, is_phrase, head_only, group)
# head_only=True: the keyword counts only if it appears in the first HEAD_CHARS characters
# group: keywords of the same group are alternatives (only the best match counts, not the sum)
KEYWORDS = {
    "balance_sheet": [
        # Czech GAAP ("rozvaha") and IFRS ("vykaz o financni pozici/situaci") titles
        ("rozvaha", 3, False, True, "title"),
        ("vykaz o financni pozici", 3, True, True, "title"),
        ("vykaz o financni situaci", 3, True, True, "title"),
        ("vykaz financni pozice", 3, True, True, "title"),
        ("vykaz financni situace", 3, True, True, "title"),
        ("vykaz o financnim postaveni", 3, True, True, "title"),
        ("aktiva", 2, False, False, None),
        ("pasiva", 2, False, False, None),
        ("vlastni kapital a zavazky", 2, True, False, None),  # IFRS liabilities side
        ("v plnem rozsahu", 1, True, True, None),
        ("ve zkracenem rozsahu", 1, True, True, None),
    ],
    "income_statement": [
        # "vykaz zisku a ztrat" also matches the singular "...ztraty" (prefix match)
        ("vykaz zisku a ztrat", 4, True, True, "title"),
        ("vykaz zisku nebo ztraty", 4, True, True, "title"),
        ("vykaz o uplnem vysledku", 4, True, True, "title"),
        ("vykaz uplneho vysledku", 4, True, True, "title"),
        ("vykaz celkoveho vysledku", 4, True, True, "title"),
        ("vykaz o celkovem uplnem vysledku", 4, True, True, "title"),
        ("vykaz komplexniho vysledku", 4, True, True, "title"),
        ("vykaz o komplexnim vysledku", 4, True, True, "title"),
        ("v plnem rozsahu", 1, True, True, None),
        ("ve zkracenem rozsahu", 1, True, True, None),
    ],
}

# only used on OCR text: they survive damage to the first word of a title ("vykaz")
OCR_ONLY_KEYWORDS = {"income_statement": [("zisku a ztrat", 4, True, True, "title")]}


def _hide_quotes(text: str) -> str:
    """'ve vykazu zisku a ztraty' inside a sentence is a quotation, not a title."""
    # "Ciste zisky a ztraty z financnich nastroju" (plural) and a column "Rozvaha/Financovani" are not titles
    text = text.replace("zisky a ztrat", "#").replace("rozvaha/", "#/")
    text = re.sub(r"\bviz (?:vykaz\w*(?: zisku(?: a| nebo) ztrat\w*)?|rozvah\w*)", "viz #", text)
    text = re.sub(r"souhrnn[a-z]* (?:vykaz[a-z]*|rozvah[a-z]*)", "souhrnny #", text)
    for quoted, hidden in (("vykazu zisku a ztrat", "vykazu #"), ("polozka rozvahy", "polozka #"),
                           ("vykazu uplneho vysledku", "vykazu #"), ("vykazu rozvaha", "vykazu #")):
        text = text.replace(quoted, hidden)
    return text


def normalize(text: str) -> str:
    """Upper case, no diacritics, common OCR confusions in letters, single spaces (shared helper)."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c)).upper()
    text = text.translate(str.maketrans({"0": "O", "!": "E", "|": "I", "1": "I", "$": "S"}))
    return re.sub(r"[^A-Z ]+", " ", re.sub(r"\s+", " ", text)).strip()


def _norm(text: str) -> str:
    """Internal form used for matching: lowercase, no diacritics, single spaces. Digits and punctuation are kept
    (the share of digits tells tables from running text)."""
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"\s+", " ", text).strip()
    # letter-spaced text ("m o r a v i a") is joined so that it does not eat the head window
    return re.sub(r"\b(?:[^\W\d_] ){3,}[^\W\d_]\b", lambda m: m.group(0).replace(" ", ""), text)


def has_text_layer(text: str) -> bool:
    stripped = text.strip()
    if len(stripped) < MIN_CHARS:
        return False
    ok = sum(c.isalnum() or c.isspace() or c in ".,;:()-/%" for c in stripped)
    return ok / len(stripped) >= MIN_ALNUM_RATIO


def digit_ratio(text: str) -> float:
    return sum(c.isdigit() for c in text) / max(len(text), 1)


def _matches(keyword: str, is_phrase: bool, text: str, tokens: list[str], fuzzy: bool) -> bool:
    if is_phrase:
        if keyword in text:
            return True
        return fuzzy and fuzz.partial_ratio(keyword, text) >= 88
    # single word: whole-word match only ("aktiva" must not match "aktivace")
    if keyword in tokens:
        return True
    return fuzzy and any(fuzz.ratio(keyword, t) >= 80 for t in tokens)


def analyze_page(text: str, fuzzy: bool) -> tuple[dict[str, int], dict[str, bool]]:
    """Return (scores, has_title) per statement. A title match is required to *open* a statement."""
    norm = _hide_quotes(_norm(text))
    head = norm[:HEAD_CHARS]
    tokens = re.findall(r"[a-z0-9]+", norm)
    head_tokens = re.findall(r"[a-z0-9]+", head)
    scores, titles = {}, {}
    for stmt, kws in KEYWORDS.items():
        kws = kws + OCR_ONLY_KEYWORDS.get(stmt, [])
        total = 0
        title_hit = False
        best_in_group: dict[str, int] = {}
        for kw, w, ph, head_only, group in kws:
            where, where_tokens = (head, head_tokens) if head_only else (norm, tokens)
            if (kw, w, ph, head_only, group) in OCR_ONLY_KEYWORDS.get(stmt, []):
                where = _hide_quotes(where)[:LOOSE_CHARS]
                is_loose = True
            else:
                is_loose = False
            if _matches(kw, ph, where, where_tokens, fuzzy or is_loose):
                if group:
                    best_in_group[group] = max(best_in_group.get(group, 0), w)
                    if group == "title":
                        title_hit = True
                else:
                    total += w
        total += sum(best_in_group.values())
        scores[stmt] = total
        titles[stmt] = title_hit
    return scores, titles


def score_page(text: str, fuzzy: bool) -> dict[str, int]:
    return analyze_page(text, fuzzy)[0]


def is_stop_page(text: str, fuzzy: bool) -> bool:
    norm = _norm(text)
    head = norm[:HEAD_CHARS]
    head_tokens = re.findall(r"[a-z0-9]+", head)
    return any(_matches(p, True, head, head_tokens, fuzzy and p not in EXACT_STOP_PHRASES)
               for p in STOP_PHRASES) or any(
        _matches(w, False, head, head_tokens, False) for w in STOP_WORDS  # exact: fuzzy gave false STOPs
    )


def title_is_early(text: str, fuzzy: bool) -> dict[str, bool]:
    head = _hide_quotes(_norm(text))[:EARLY_CHARS]
    toks = re.findall(r"[a-z0-9]+", head)
    return {
        stmt: any(g == "title" and (_matches(kw, ph, _hide_quotes(head), toks, True)
                                    if (kw, w, ph, ho, g) in OCR_ONLY_KEYWORDS.get(stmt, [])
                                    else _matches(kw, ph, head, toks, fuzzy))
                  for kw, w, ph, ho, g in (kws + OCR_ONLY_KEYWORDS.get(stmt, [])))
        for stmt, kws in KEYWORDS.items()
    }


def is_toc_page(text: str, fuzzy: bool) -> bool:
    head = _norm(text)[:HEAD_CHARS]
    return _matches("obsah", False, head, re.findall(r"[a-z0-9]+", head), fuzzy)


SCOPE_CHARS = 300   # "konsolidovana" / "individualni" count only near the top of the page


def page_scope(text: str, fuzzy: bool) -> str | None:
    """'consolidated' / 'individual' if the top of the page says so, else None (the first one that appears wins)."""
    # a running header such as "Konsolidovana vyrocni zprava 2024" is on every page: it is not a title
    head = re.sub(r"(konsolidovan\w*|individualn\w*) vyrocn\w* zpra\w*", " ", _norm(text))[:SCOPE_CHARS]
    found = []
    for scope, word in (("consolidated", "konsolidovan"), ("individual", "individualn")):
        pos = head.find(word)
        if pos < 0 and fuzzy:
            for tok_start in range(0, max(len(head) - len(word), 0) + 1, 2):
                if fuzz.ratio(word, head[tok_start:tok_start + len(word)]) >= 85:
                    pos = tok_start
                    break
        if pos >= 0:
            found.append((pos, scope))
    return min(found)[1] if found else None


def _known_phrases(text: str) -> int:
    """How many statement words/rows can be read in the text (used to compare two readings of the same page)."""
    norm = _norm(text)
    words = ([kw for kws in KEYWORDS.values() for kw, *_ in kws] + BS_ASSETS + BS_LIAB + BS_END + IS_START + IS_END
             + STOP_PHRASES)   # the titles of the other statements count too (they end a statement)
    return sum(w in norm for w in words)


def _page_text(page: pymupdf.Page) -> tuple[str, bool]:
    """Return (text, used_ocr)."""
    text = page.get_text(sort=True)
    if has_text_layer(text):
        # some scanned PDFs carry a text layer whose blocks get shuffled by sort=True: keep the reading order
        # that shows more of the statement's words (ties keep the sorted text)
        plain = page.get_text()
        if _known_phrases(plain) > _known_phrases(text):
            return plain, False
        return text, False
    pix = page.get_pixmap(dpi=OCR_DPI)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    try:
        text = pytesseract.image_to_string(img, lang="ces")
        if len(_norm(text)) < OCR_MIN_CHARS:
            pix = page.get_pixmap(dpi=OCR_RETRY_DPI)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            retry = pytesseract.image_to_string(img, lang="ces")
            if len(_norm(retry)) > len(_norm(text)):
                text = retry
        if not any(analyze_page(text, True)[1].values()) and not is_stop_page(text, True):
            # titles printed on a red/pink banner are lost in the normal read: the red channel makes them black on white
            top = img.crop((0, 0, img.width, img.height // HEADER_FRACTION)).split()[0]
            head = pytesseract.image_to_string(top, lang="ces")
            if any(analyze_page(head, True)[1].values()) or is_stop_page(head, True):
                text = head + "\n" + text
        return text, True
    except pytesseract.TesseractNotFoundError:
        warnings.warn(
            f"Page {page.number + 1} has no text layer and Tesseract is not installed: page skipped.",
            stacklevel=2,
        )
        return "", False


def _runs(pages: list[int]) -> list[list[int]]:
    """Group sorted page numbers into runs of consecutive pages."""
    runs: list[list[int]] = []
    for p in pages:
        if runs and p == runs[-1][-1] + 1:
            runs[-1].append(p)
        else:
            runs.append([p])
    return runs


def _bridge(info: dict, stmt: str, first: int):
    """Skip up to MAX_BRIDGE neutral pages starting at `first` and return the next page if it looks like the
    untitled continuation of the statement (e.g. PASIVA after a cover page); otherwise None."""
    for target in range(first + 1, first + 1 + MAX_BRIDGE):
        if target not in info or info[target]["stop"]:
            return None
        if any(info[k]["stop"] or any(info[k]["titles"].values()) for k in range(first, target)):
            return None  # a skipped page is a real page of something else
        sc = info[target]["scores"]
        own = sc[stmt]
        other = max(v for k, v in sc.items() if k != stmt)
        if (own >= 2 and own > other and not any(info[target]["titles"].values())
                and info[target]["digits"] >= MIN_DIGIT_RATIO):
            return target
    return None


# ---------------------------------------------------------------------------------------------------------------
# Structural pass (Czech GAAP, vyhlaska 500/2002): the rows of the statements are fixed by law, so a statement is
# complete only when its first and last rows have been seen. Used to (1) add missing continuation pages,
# (2) drop pages that follow a statement that is already complete, (3) find a statement whose title was not read.
# Statements without these rows (IFRS, foreign groups) are left exactly as the keyword pass found them.
# ---------------------------------------------------------------------------------------------------------------
BS_ASSETS = ["aktiva celkem"]
BS_LIAB = ["pasiva celkem", "vlastni kapital a zavazky celkem", "celkem vlastni kapital a zavazky"]
BS_END = ["casove rozliseni pasiv", "vydaje pristich obdobi"]
# the assets side ends with almost the same words: a fuzzy match must prefer the liabilities wording
BS_END_RIVALS = {
    "casove rozliseni pasiv": "casove rozliseni aktiv",
    "vydaje pristich obdobi": "naklady pristich obdobi",
}
IS_START = ["trzby z prodeje vyrobku a sluzeb", "vykonova spotreba", "trzby za prodej zbozi",
            "trzby z prodeje vlastnich vyrobku a sluzeb"]   # older wording of the first row
IS_END = ["vysledek hospodareni za ucetni obdobi", "cisty obrat za ucetni obdobi"]
IS_LATE_ROWS = ["nakladove uroky", "vynosove uroky", "financni vysledek hospodareni", "ostatni financni naklady",
                "ostatni financni vynosy", "dan z prijmu"]
LOOKAHEAD = {"balance_sheet": 3, "income_statement": 2}   # max pages added to reach the last row
LOOKBACK = 3            # max pages added before the liabilities to reach "AKTIVA CELKEM"
ONE_PAGE_BS_MIN_DIGITS = 0.18   # an untitled one-page balance sheet is a dense table, not a report page with totals
STRUCT_MIN_DIGITS = 0.01   # OCR of the last (short) page of a statement may read few digits


def _has(info: dict, page: int, phrases: list[str]) -> bool:
    d = info.get(page)
    if not d:
        return False
    text = d["norm"]
    if any(ph in text for ph in phrases):
        return True
    # OCR (and garbled text layers) may damage a letter or two
    if len(text) > 20000:
        return False
    for ph in phrases:
        r = fuzz.partial_ratio(ph, text)
        if r >= 90 and (ph not in BS_END_RIVALS or r > fuzz.partial_ratio(BS_END_RIVALS[ph], text)):
            return True
    return False


def _blocked(info: dict, page: int, stmt: str) -> bool:
    """Page that can never be part of `stmt`: missing, end/notes/contents page, or the title of the other statement."""
    d = info.get(page)
    if d is None or d["stop"] or d["note"] or d["toc"]:
        return True
    other = [k for k in d["early"] if k != stmt]
    return any(d["early"][k] for k in other) and not d["early"][stmt]


def _groups(pages: list[int], gap: int) -> list[list[int]]:
    out: list[list[int]] = []
    for p in pages:
        if out and p - out[-1][-1] <= gap:
            out[-1].append(p)
        else:
            out.append([p])
    return out


def _markers(stmt: str):
    if stmt == "balance_sheet":
        return BS_ASSETS + BS_LIAB, BS_END
    return IS_START, IS_END


def _section_before_title(norm: str, has_title: bool = False) -> bool:
    """True if a numbered note section ("5.2. ostatni zmeny ...") starts before the statement title on the page.
    Pages without a title, row codes ("B.I. 2.1. software") and chapters that announce the statements themselves
    ("14.1 financni vykazy sestavene ...") do not count."""
    head = norm[:HEAD_CHARS]
    titles = [head.find(kw) for kws in KEYWORDS.values() for kw, _, _, _, g in kws if g == "title" and kw in head]
    if not titles and not has_title:
        return False
    before = head[:min(titles)] if titles else head   # OCR: title only found by fuzzy matching
    return _has_section_number(before)


def _has_section_number(before: str) -> bool:
    for m in re.finditer(r"\b\d{1,2}\.\d{1,2}\.? ([a-z][a-z0-9]{2})", before):
        if re.search(r"[a-z]\.\s*$", before[max(0, m.start() - 4):m.start()]):
            continue   # row code of a statement table
        if re.match(r"(?:konsolidovan\w* |individualn\w* |samostatn\w* )?(?:financni vykaz|ucetni zaverk)",
                    before[m.start(1):]):
            continue   # chapter heading that introduces the statements
        return True
    return False


def _strong(info: dict, g: list[int], stmt: str) -> bool:
    if stmt == "balance_sheet":
        return any(_has(info, p, BS_ASSETS) or _has(info, p, BS_LIAB) for p in g)
    return sum(any(_has(info, p, [m]) for p in g) for m in IS_START + IS_END) >= 2


def _structural_seeds(info: dict, stmt: str) -> list[int]:
    """Fallback when the keyword pass found nothing: pages that contain the fixed rows of the statement."""
    found = []
    for p, d in info.items():
        if d["toc"] or d["note"] or d["digits"] < MIN_DIGIT_RATIO:
            continue
        if d["stop"] and not d["early"][stmt]:
            continue
        if stmt == "balance_sheet" and (_has(info, p, BS_ASSETS) or _has(info, p, BS_LIAB)) and (
                _has(info, p, BS_END) or _has(info, p, ["stala aktiva", "obezna aktiva", "vlastni kapital"])):
            found.append(p)
        if stmt == "income_statement" and sum(_has(info, p, [m]) for m in IS_START + IS_END
                                               + ["osobni naklady", "provozni vysledek hospodareni"]) >= 3:
            found.append(p)
    return found


def _structure_pass(result: dict[str, list[int]], info: dict) -> dict[str, list[int]]:
    out = {}
    for stmt, pages in result.items():
        start_m, end_m = _markers(stmt)
        if not pages:
            pages = _structural_seeds(info, stmt)
        groups = _groups(sorted(pages), MAX_BRIDGE + 1)
        new_groups = []
        for g in groups:
            g = list(g)
            # (a) liabilities without assets before them: walk back to "AKTIVA CELKEM"
            if stmt == "balance_sheet" and not any(_has(info, p, BS_ASSETS) for p in g):
                for back in range(1, LOOKBACK + 1):
                    q = g[0] - back
                    if _blocked(info, q, stmt):
                        break
                    if _has(info, q, BS_ASSETS):
                        g = list(range(q, g[0])) + g
                        break
            # (a2) a page between two pages of the balance sheet that carries balance sheet rows is part of it
            #      (scans whose title was not read: assets page 2 is easily missed)
            if stmt == "balance_sheet" and len(g) > 1:
                filled = [g[0]]
                for p in range(g[0] + 1, g[-1] + 1):
                    d = info[p]
                    if p in g or (not _blocked(info, p, stmt) and d["rows"] and d["digits"] >= MIN_DIGIT_RATIO
                                  and not any(d["titles"].values())):
                        filled.append(p)
                g = sorted(set(filled) | set(g))
            # (b) statement not finished: walk forward to its last row
            needs = [BS_LIAB, BS_END] if stmt == "balance_sheet" else [IS_END]
            for need in needs:
                if any(_has(info, p, need) for p in g):
                    continue
                for ahead in range(1, LOOKAHEAD[stmt] + 1):
                    q = g[-1] + ahead
                    if q not in info or info[q]["digits"] < (STRUCT_MIN_DIGITS if not _has(info, q, need)
                                                              else HDR_CONT_MIN_DIGITS):
                        break
                    if _blocked(info, q, stmt) and not (
                            info[q]["stop"] and not info[q]["note"] and _has(info, q, need)
                            and info[q]["titles"][stmt]   # e.g. last page that repeats the title but whose footer
                                                          # lists the other statements (not a cash-flow page)
                            and not any(v for k, v in info[q]["early"].items() if k != stmt)):
                        break
                    if _has(info, q, need):
                        g = g + list(range(g[-1] + 1, q + 1))
                        break
            # (c) statement finished: pages after the last row without any row of the statement are not part of it
            if not any(_has(info, p, start_m) or _has(info, p, end_m) for p in g):
                new_groups.append((g, False))      # not a Czech GAAP statement (or rows not readable): unchanged
                continue
            ends = [p for p in g if _has(info, p, end_m)]
            if stmt == "balance_sheet":
                liab = [p for p in g if _has(info, p, BS_LIAB)]
                ends = [p for p in ends if liab and p >= liab[0]]
            if not ends and stmt == "balance_sheet" and any(_has(info, p, BS_ASSETS) for p in g):
                ends = [p for p in g if _has(info, p, BS_LIAB)]   # short forms / foreign layouts: no "D." rows
            if ends:
                # the income statement ends at its FIRST result row: the next pages that repeat it are other
                # statements (other comprehensive income, equity); the balance sheet ends at its last row
                last = ends[0] if stmt == "income_statement" else ends[-1]
                # some income statement forms print the result rows before the financial rows: those pages stay
                keep = start_m + end_m + (IS_LATE_ROWS if stmt == "income_statement" else [])
                g = [p for p in g if p <= last or _has(info, p, keep)
                     or (stmt == "income_statement" and info[p]["tablehdr"])]   # overflow rows under the same header
            new_groups.append((g, bool(ends) and _strong(info, g, stmt)))
        # (d) once a complete statement was found, a group without the statement's own rows is something else
        # (notes and reports also mention "casove rozliseni pasiv" or "trzby": the totals / several rows are needed)
        # only for the balance sheet: an income statement with other wording (IFRS, consolidated) is a real statement
        if stmt == "balance_sheet" and any(complete for _, complete in new_groups):
            new_groups = [(g, c) for g, c in new_groups
                          if c or _strong(info, g, stmt) or any(info[p]["early"][stmt] for p in g)]
        # a group whose pages all start with a numbered note section is a table quoted in the notes
        if any(not all(info[p]["section_first"] for p in g) for g, _ in new_groups):
            new_groups = [(g, c) for g, c in new_groups if not all(info[p]["section_first"] for p in g)]
        # a one-page balance sheet with both totals whose title was not read (scan) is added as a further statement
        if stmt == "balance_sheet" and new_groups:
            taken = {p for g, _ in new_groups for p in g}
            for p, d in info.items():
                if (p not in taken and p - 1 not in taken and p + 1 not in taken
                        and not (d["toc"] or d["note"] or d["stop"] or d["section_first"])
                        and d["digits"] >= ONE_PAGE_BS_MIN_DIGITS and not any(d["early"].values())
                        and not _has_section_number(d["norm"][:EARLY_CHARS])   # "7.3 konsolidovana struktura ..."
                        and _has(info, p, BS_ASSETS) and _has(info, p, BS_LIAB)):
                    new_groups.append(([p], True))
        out[stmt] = sorted({p for g, _ in new_groups for p in g})
    # (g) statements printed under the running header "Priloha v ucetni zaverce" (O2): such a balance sheet is kept
    # only if it shows its totals, and such an income statement only next to such a balance sheet; otherwise they are
    # tables quoted in the notes (only when the statement was found elsewhere too)
    def _under_note_hdr(g):
        return all(info[p].get("note_hdr") for p in g)
    bs_groups = _groups(out["balance_sheet"], 1)
    kept_bs = []
    for g in bs_groups:
        if _under_note_hdr(g) and len(bs_groups) > 1 and not _strong(info, g, "balance_sheet"):
            continue
        kept_bs.extend(g)
    out["balance_sheet"] = kept_bs
    is_groups = _groups(out["income_statement"], 1)
    kept_is = []
    for g in is_groups:
        if _under_note_hdr(g) and len(is_groups) > 1 and not any(
                abs(p - q) <= 2 for p in g for q in kept_bs if info[q].get("note_hdr")):
            continue
        kept_is.extend(g)
    out["income_statement"] = kept_is
    # (f) no income statement found but a balance sheet was (title not readable on a poor scan): take the page with the
    # last rows of the income statement right before or after the balance sheet, plus the untitled pages above it
    if not out["income_statement"] and out["balance_sheet"]:
        bs = out["balance_sheet"]
        lo, hi = bs[0] - 4, bs[-1] + 4
        for e in range(max(lo, 1), hi + 1):
            d = info.get(e)
            if (d is None or e in bs or d["stop"] or d["note"] or d["toc"] or d["digits"] < MIN_DIGIT_RATIO_TITLE
                    or not _has(info, e, IS_END)):
                continue
            g = [e]
            for back in (1, 2):
                q = e - back
                dq = info.get(q)
                if (dq is None or q in bs or dq["stop"] or dq["note"] or dq["toc"] or dq["digits"] < MIN_DIGIT_RATIO
                        or _has(info, q, BS_ASSETS + BS_LIAB + BS_END)):
                    break
                g.insert(0, q)
                if _has(info, q, IS_START):
                    break
            out["income_statement"] = g
            break
    # (e) no balance sheet found but an income statement was: in Czech filings the balance sheet comes right before
    # it. Pages just before it that lean towards the balance sheet (garbled text layer, title not readable) are taken.
    if not out["balance_sheet"] and out["income_statement"]:
        k = out["income_statement"][0]
        found = []
        for q in range(k - 1, k - 1 - LOOKBACK, -1):
            d = info.get(q)
            if (d is None or d["stop"] or d["note"] or d["toc"] or d["digits"] < MIN_DIGIT_RATIO
                    or d["scores"]["balance_sheet"] < 2
                    or d["scores"]["balance_sheet"] <= d["scores"]["income_statement"]):
                break
            found.insert(0, q)
        if found and any(info[q]["titles"]["balance_sheet"] for q in found):
            out["balance_sheet"] = found
    return out


@dataclass
class PageScore:
    """What the locator saw on one page (for debugging and tests)."""

    page: int
    source: str  # "text_layer" or "ocr"
    balance_sheet: float = 0.0
    income_statement: float = 0.0
    other: float = 0.0  # 1.0 if the page ends a statement (other statement, notes, auditor's report)
    numbers: int = 0
    tokens: int = 0
    hits: list[str] = field(default_factory=list)


def _page_score(page: int, d: dict) -> PageScore:
    tokens = d["norm"].split()
    hits = [k for k, v in d["titles"].items() if v]
    hits += [m for m in BS_ASSETS + BS_LIAB + BS_END + IS_START + IS_END if m in d["norm"]]
    return PageScore(
        page=page,
        source="ocr" if d["ocr"] else "text_layer",
        balance_sheet=float(d["scores"]["balance_sheet"]),
        income_statement=float(d["scores"]["income_statement"]),
        other=1.0 if d["stop"] else 0.0,
        numbers=sum(1 for t in tokens if re.fullmatch(r"[-(]?\d[\d.,]*\)?", t)),
        tokens=len(tokens),
        hits=hits,
    )


def locate_statements(doc: pymupdf.Document | str | Path, return_scores: bool = False):
    """{"balance_sheet": [20, 21], "income_statement": [22]} with 1-based pages (plus scores and seconds if asked).

    `doc` is an open PDF or a path. All pages of each statement are returned, consolidated and individual alike.
    """
    if not isinstance(doc, pymupdf.Document):
        with pymupdf.open(doc) as opened:
            return locate_statements(opened, return_scores)
    start = time.perf_counter()
    result, info = _locate(doc)
    if return_scores:
        scores = [_page_score(p, d) for p, d in sorted(info.items())]
        return result, scores, round(time.perf_counter() - start, 2)
    return result


def locate_statements_split(doc: pymupdf.Document | str | Path) -> dict[str, dict[str, list[int]]]:
    """Same pages, split by scope: {"balance_sheet": {"individual": [...], "consolidated": [...]}, ...}.

    A page without any marker takes the scope of the previous page of the statement; with no marker at all it is
    "individual".
    """
    if not isinstance(doc, pymupdf.Document):
        with pymupdf.open(doc) as opened:
            return locate_statements_split(opened)
    flat, info = _locate(doc)
    out: dict[str, dict[str, list[int]]] = {}
    for stmt, pages in flat.items():
        groups: dict[str, list[int]] = {"individual": [], "consolidated": []}
        prev_page, prev_scope = None, "individual"
        for p in pages:
            scope = info[p]["scope"]
            if scope is None:
                scope = prev_scope if prev_page is not None and p - prev_page <= MAX_BRIDGE + 1 else "individual"
            groups[scope].append(p)
            prev_page, prev_scope = p, scope
        out[stmt] = groups
    return out


Scope = Literal["individual", "consolidated"]


def locate_one_set(
    doc: pymupdf.Document | str | Path, prefer: Scope = "individual"
) -> tuple[dict[str, list[int]], Scope | None, dict[str, list[int]]]:
    """Pages of ONE set of statements, so the parser never mixes an individual and a consolidated statement.

    Returns (pages, scope used, pages of the other scope). The preferred scope is used when the PDF has any of its
    statement pages, the other one otherwise; scope is None when nothing was found.
    """
    split = locate_statements_split(doc)
    other: Scope = "consolidated" if prefer == "individual" else "individual"
    for scope, rest in ((prefer, other), (other, prefer)):
        pages = {stmt: groups[scope] for stmt, groups in split.items()}
        if any(pages.values()):
            return pages, scope, {stmt: groups[rest] for stmt, groups in split.items()}
    return {stmt: [] for stmt in split}, None, {stmt: [] for stmt in split}


def _title_after_header(text: str, fuzzy: bool) -> bool:
    """Some reports print 'Priloha v ucetni zaverce' as a running header on every page, statements included: a page
    whose statement title comes right after that header (no note number in front of it) is a statement page."""
    norm = _hide_quotes(_norm(text))
    if not any(title_is_early(text, fuzzy).values()) or digit_ratio(norm) < MIN_DIGIT_RATIO:
        return False
    head = norm[:EARLY_CHARS]
    for kws in KEYWORDS.values():
        for kw, _, _, _, g in kws:
            pos = head.find(kw)
            if g == "title" and pos >= 0:
                return re.search(r"(?:^|\s)\d{1,2}\.?\s*$", head[:pos]) is None
    return True


def _page_info(text: str, used_ocr: bool) -> dict:
    scores, titles = analyze_page(text, fuzzy=used_ocr)
    return {

        "scores": scores,
        "titles": titles,
        "digits": digit_ratio(_norm(text)),
        "stop": is_stop_page(text, fuzzy=True),  # long phrases only: text layers of scans are garbled too
        "toc": is_toc_page(text, fuzzy=used_ocr),
        "early": title_is_early(text, fuzzy=used_ocr),
        "rowhdr": all(w in _norm(text)[:300] for w in ("cislo", "radku")),
        "tablehdr": "bezne ucetni" in _norm(text)[:HEAD_CHARS] and "minule ucetni" in _norm(text)[:HEAD_CHARS],
        "scope": page_scope(text, fuzzy=used_ocr),
        "note": "priloha v ucetni zaverce" in _norm(text)[:80] and not _title_after_header(text, used_ocr),
        "note_hdr": "priloha v ucetni zaverce" in _norm(text)[:80],
        "rows": any(t.startswith(ROW_ST) for t in re.findall(r"[a-z]+", _norm(text)) for ROW_ST in ROW_STEMS),
        "norm": _norm(text),
        "section_first": _section_before_title(_norm(text), any(titles.values())),
        "ocr": used_ocr,
    }


def _locate(doc: pymupdf.Document):
    info: dict[int, dict] = {}
    for i, page in enumerate(doc, start=1):
        text, used_ocr = _page_text(page)
        info[i] = _page_info(text, used_ocr)

    result = {}
    for stmt in KEYWORDS:
        seeds = sorted(
            p for p, d in info.items()
            if d["titles"][stmt] and not d["toc"] and not d["note"]  # "Priloha v ucetni zaverce ..." = notes page
            and (not d["stop"] or d["early"][stmt])  # a note quoting a title in running text does not count
            and (
                (d["scores"][stmt] >= MIN_SCORE and d["digits"] >= MIN_DIGIT_RATIO)
                or (d["early"][stmt] and d["scores"][stmt] >= EARLY_MIN_SCORE
                    and d["digits"] >= EARLY_MIN_DIGITS and not all(d["titles"].values()))
            )
        )
        if not seeds:
            # sparse tables (mostly empty): admitted ONLY if no normal page was found for this
            # statement, and never for contents pages, notes/auditor pages or pages listing several titles
            seeds = sorted(
                p for p, d in info.items()
                if (d["scores"][stmt] >= SPARSE_MIN_SCORE or (d["early"][stmt] and d["scores"][stmt] >= MIN_SCORE))
                and d["titles"][stmt]
                and MIN_DIGIT_RATIO_TITLE <= d["digits"] < MIN_DIGIT_RATIO
                and not d["toc"] and not d["stop"] and not all(d["titles"].values())
            )
        if not seeds and stmt == "balance_sheet":
            # untitled balance sheet: the quick OCR lost the title (e.g. a shaded header), but the page starts with
            # the first row of the table, AKTIVA CELKEM, followed by balance-sheet rows
            seeds = sorted(
                p for p, d in info.items()
                if any(m in d["norm"][:EARLY_CHARS] for m in BS_ASSETS) and d["rows"]
                and d["digits"] >= ROW_MIN_DIGITS and not d["toc"] and not d["stop"] and not d["note"]
            )
        pages: set[int] = set()
        for run in _runs(seeds):
            cluster = list(run)
            # continuation pages of a sparse statement may be sparse too
            cont_min = MIN_DIGIT_RATIO_TITLE if info[run[-1]]["digits"] < MIN_DIGIT_RATIO else MIN_DIGIT_RATIO
            extra = 0
            while extra < MAX_CONTINUATION[stmt]:
                nxt = cluster[-1] + 1
                if nxt not in info or info[nxt]["stop"]:
                    break
                sc = info[nxt]["scores"]
                own = sc[stmt]
                other = max(v for k, v in sc.items() if k != stmt)
                # e.g. PASIVA without title
                weak_own = own > 0 and own > other and info[nxt]["digits"] >= min(cont_min, WEAK_MIN_DIGITS)
                bare_table = own == 0 and other == 0 and info[nxt]["digits"] >= cont_min and extra < MAX_BARE
                row_table = (stmt == "balance_sheet" and own == 0 and other == 0 and info[nxt]["rows"]
                             and info[nxt]["digits"] >= ROW_MIN_DIGITS)
                title_cont = (info[nxt]["early"][stmt] and not info[nxt]["note"] and not info[nxt]["toc"]
                              and own >= EARLY_MIN_SCORE and own > other
                              and info[nxt]["digits"] >= TITLE_CONT_MIN_DIGITS)
                hdr_cont = (own == 0 and other == 0 and info[nxt]["tablehdr"] and not info[nxt]["note"]
                            and info[nxt]["digits"] >= HDR_CONT_MIN_DIGITS)
                rowhdr_cont = (own == 0 and other == 0 and info[nxt]["rowhdr"] and not info[nxt]["note"]
                               and info[nxt]["digits"] >= HDR_CONT_MIN_DIGITS)
                if not (weak_own or bare_table or row_table or title_cont or hdr_cont or rowhdr_cont):
                    bridged = _bridge(info, stmt, nxt) if stmt == "balance_sheet" else None
                    if bridged is None:
                        break
                    cluster.append(bridged)   # the skipped pages are NOT part of the statement
                    extra += 1
                    continue
                cluster.append(nxt)
                extra += 1
            pages.update(cluster)
        result[stmt] = sorted(pages)
    result = _structure_pass(result, info)
    return _fill_gaps(result, info), info


def _fill_gaps(result: dict[str, list[int]], info: dict) -> dict[str, list[int]]:
    """A single page between two pages of the same statement belongs to it when it reads like that statement: its
    keywords outweigh the other statement's and it carries no other title. The quick OCR may find almost no digits
    on such a page (a liabilities page of a scan at 0.5 % digits), which the continuation rules need. A cover or
    divider page between the two sides has no statement keywords and stays out, as `_bridge` intends."""
    out = {}
    for stmt, pages in result.items():
        claimed = {p for other, ps in result.items() if other != stmt for p in ps}
        filled = list(pages)
        for before, after in zip(pages, pages[1:], strict=False):
            gap = before + 1
            if after - before != 2 or gap in claimed or gap not in info:
                continue
            d = info[gap]
            own = d["scores"][stmt]
            rival = max(v for k, v in d["scores"].items() if k != stmt)
            other_title = any(t for k, t in d["titles"].items() if k != stmt)
            if own >= 2 and own > rival and not other_title and not (d["stop"] or d["toc"] or d["note"]):
                filled.append(gap)
        out[stmt] = sorted(filled)
    return out


if __name__ == "__main__":  # python -m src.extraction.locate file.pdf
    import sys

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        found, page_scores, secs = locate_statements(sys.argv[1], return_scores=True)
    for sc in page_scores:
        if sc.balance_sheet or sc.income_statement or sc.other:
            print(
                f"p{sc.page:>3} {sc.source:10} BS {sc.balance_sheet:4.1f} IS {sc.income_statement:4.1f} "
                f"other {sc.other:3.1f} nums {sc.numbers:4d}  {', '.join(sc.hits)}"
            )
    print(found, f"{secs}s")
