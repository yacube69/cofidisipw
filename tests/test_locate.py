"""CIPW-6: page locator finds the statement pages.

The real-file test compares the locator with the `expected_pages` column of data/index.csv (pages annotated by hand
before running the locator). As agreed with the team (2026-10-18), one extra page per statement is accepted; a
missing page is always a failure. Files that need Tesseract on more than a few pages (image-only scans) are slow,
so they run only with LOCATOR_FULL=1 (e.g. `LOCATOR_FULL=1 pytest tests/test_locate.py`). Rows whose notes start
with "known failure" are expected to fail (reason in the notes).
"""

import csv
import os
from pathlib import Path

import pymupdf
import pytest

from src.extraction.locate import _fill_gaps, has_text_layer, locate_one_set, locate_statements, normalize
from tests.synthetic import ASSET_ROWS, asset_page_html, liability_page_html, make_digital_pdf, make_scanned_pdf
from tests.test_reading import needs_ocr

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "data/index.csv"
MAX_OCR_PAGES_IN_CI = 5  # files with more pages without a text layer run only with LOCATOR_FULL=1
MAX_EXTRA_PAGES = 1  # per statement, agreed with the team


def parse_expected(text: str) -> dict[str, list[int]]:
    """'BS:20-21;IS:22' -> {'balance_sheet': [20, 21], 'income_statement': [22]}"""
    names = {"BS": "balance_sheet", "IS": "income_statement"}
    out = {}
    for part in text.split(";"):
        key, _, pages = part.partition(":")
        result: list[int] = []
        for chunk in filter(None, pages.split(",")):
            a, _, b = chunk.partition("-")
            result.extend(range(int(a), int(b or a) + 1))
        out[names[key]] = result
    return out


def index_rows():
    if not INDEX.exists():
        return []
    with INDEX.open(encoding="utf-8") as f:
        return [r for r in csv.DictReader(f) if (ROOT / "data/raw" / r["file"]).exists()]


def test_normalize_handles_diacritics_and_ocr_noise():
    assert normalize("Výkaz zisku a ztráty") == "VYKAZ ZISKU A ZTRATY"
    assert normalize("PASIVA C!LKEM") == "PASIVA CELKEM"
    assert normalize("R0ZVAHA") == "ROZVAHA"


def test_parse_expected():
    assert parse_expected("BS:20-21;IS:22") == {"balance_sheet": [20, 21], "income_statement": [22]}
    assert parse_expected("BS:1-2;IS:") == {"balance_sheet": [1, 2], "income_statement": []}


def _report_with_noise(path: Path) -> Path:
    """Cover, notes page mentioning the statements, balance sheet, cash flow page."""
    doc = pymupdf.open()
    pages = [
        "<h1>Výroční zpráva 2025</h1><p>Testovací firma s.r.o.</p>",
        "<h1>Příloha účetní závěrky</h1><p>Rozvaha a výkaz zisku a ztráty jsou sestaveny podle vyhlášky. "
        "Aktiva celkem vzrostla, vlastní kapitál 400 000, cizí zdroje 600 000.</p>",
        asset_page_html(),
        "<h1>Přehled o peněžních tocích</h1><p>Peněžní toky z provozní činnosti " + " 1 000" * 30 + "</p>",
    ]
    for html in pages:
        page = doc.new_page(width=595, height=842)
        page.insert_htmlbox(pymupdf.Rect(40, 40, 555, 800), html)
    doc.save(path)
    return path


def test_digital_statement(tmp_path):
    with pymupdf.open(make_digital_pdf(tmp_path / "a.pdf")) as d:
        assert locate_statements(d) == {"balance_sheet": [1, 2], "income_statement": []}


def test_notes_and_cash_flow_are_not_picked(tmp_path):
    with pymupdf.open(_report_with_noise(tmp_path / "report.pdf")) as d:
        assert locate_statements(d)["balance_sheet"] == [3]


@needs_ocr
def test_scan_without_text_layer_uses_quick_ocr(tmp_path):
    scan = make_scanned_pdf(make_digital_pdf(tmp_path / "a.pdf"), tmp_path / "scan.pdf")
    with pymupdf.open(scan) as d:
        found, scores, _ = locate_statements(d, return_scores=True)
    assert all(s.source == "ocr" for s in scores)
    assert found["balance_sheet"] == [1, 2]


def _pdf(path: Path, pages: list[str]) -> Path:
    doc = pymupdf.open()
    for html in pages:
        page = doc.new_page(width=595, height=842)
        page.insert_htmlbox(pymupdf.Rect(40, 40, 555, 800), html)
    doc.save(path)
    return path


def test_one_set_of_statements_is_read_from_a_group_report(tmp_path):
    """A group annual report holds the consolidated and the individual statements: the parser must get one set."""
    pdf = _pdf(
        tmp_path / "group.pdf",
        [
            "<p>Konsolidovaná účetní závěrka</p>" + asset_page_html(),
            liability_page_html(),
            "<h1>Příloha</h1><p>Text.</p>",
            "<p>Individuální účetní závěrka</p>" + asset_page_html(),
            liability_page_html(),
        ],
    )
    with pymupdf.open(pdf) as d:
        assert locate_statements(d)["balance_sheet"] == [1, 2, 4, 5]
        assert locate_one_set(d) == (
            {"balance_sheet": [4, 5], "income_statement": []},
            "individual",
            {"balance_sheet": [1, 2], "income_statement": []},
        )
        pages, scope, _ = locate_one_set(d, "consolidated")
        assert (pages["balance_sheet"], scope) == ([1, 2], "consolidated")


def test_one_set_falls_back_to_the_other_scope(tmp_path):
    pdf = _pdf(tmp_path / "group.pdf", ["<p>Konsolidovaná účetní závěrka</p>" + asset_page_html()])
    with pymupdf.open(pdf) as d:
        pages, scope, other = locate_one_set(d)
    assert (pages["balance_sheet"], scope, other["balance_sheet"]) == ([1], "consolidated", [])


def test_untitled_balance_sheet_starting_with_total_assets(tmp_path):
    """28437098_2022: the quick OCR lost the ROZVAHA title, the page starts with the AKTIVA CELKEM row."""
    rows = "".join(f"<tr><td>{c}</td><td>{label}</td><td>{r}</td><td>{g}</td><td>{n}</td></tr>"
                   for c, label, r, g, _, n, _, _ in ASSET_ROWS)  # fmt: skip
    pdf = _pdf(tmp_path / "untitled.pdf", [f"<table>{rows}</table>", "<h1>Příloha</h1><p>Text.</p>"])
    with pymupdf.open(pdf) as d:
        assert locate_statements(d)["balance_sheet"] == [1]


def pages_needing_ocr(doc: pymupdf.Document) -> int:
    return sum(1 for page in doc if not has_text_layer(page.get_text(sort=True)))


def matches(found: dict[str, list[int]], expected: dict[str, list[int]]) -> bool:
    """All expected pages found and at most MAX_EXTRA_PAGES more per statement."""
    for statement, pages in expected.items():
        got = set(found.get(statement, []))
        if not set(pages) <= got or len(got - set(pages)) > MAX_EXTRA_PAGES:
            return False
    return True


def test_matches_accepts_one_extra_page_only():
    expected = {"balance_sheet": [3, 4], "income_statement": [5]}
    assert matches({"balance_sheet": [3, 4], "income_statement": [5]}, expected)
    assert matches({"balance_sheet": [3, 4], "income_statement": [5, 6]}, expected)
    assert not matches({"balance_sheet": [3, 4], "income_statement": [5, 6, 7]}, expected)
    assert not matches({"balance_sheet": [3], "income_statement": [5]}, expected)


def test_path_and_open_document_give_the_same_result(tmp_path):
    pdf = make_digital_pdf(tmp_path / "a.pdf")
    with pymupdf.open(pdf) as d:
        assert locate_statements(pdf) == locate_statements(d)


@pytest.mark.parametrize("row", index_rows(), ids=lambda r: r["file"])
def test_real_files_match_index(row):
    with pymupdf.open(ROOT / "data/raw" / row["file"]) as d:
        if pages_needing_ocr(d) > MAX_OCR_PAGES_IN_CI and not os.getenv("LOCATOR_FULL"):
            pytest.skip("image-only scan (slow quick-OCR pass): run with LOCATOR_FULL=1")
        found = locate_statements(d)
    expected = parse_expected(row["expected_pages"])
    if row.get("notes", "").lower().startswith("known failure"):
        if not matches(found, expected):
            pytest.xfail(row["notes"])
    assert matches(found, expected), f"expected {expected}, found {found}"


def _info(bs: int, is_: int, bs_title: bool = False, is_title: bool = False, stop: bool = False) -> dict:
    return {"scores": {"balance_sheet": bs, "income_statement": is_},
            "titles": {"balance_sheet": bs_title, "income_statement": is_title},
            "stop": stop, "toc": False, "note": False}  # fmt: skip


def test_a_page_between_two_pages_of_a_statement_is_filled_when_it_reads_like_it():
    """Andel Czech 2024 on Linux Tesseract: the liabilities page (p3) was read with 0.5 % digits and skipped."""
    info = {1: _info(6, 1, bs_title=True), 2: _info(2, 0), 3: _info(2, 0), 4: _info(2, 0)}
    found = {"balance_sheet": [1, 2, 4], "income_statement": []}
    assert _fill_gaps(found, info) == {"balance_sheet": [1, 2, 3, 4], "income_statement": []}


def test_a_cover_page_or_another_statement_between_is_not_filled():
    info = {1: _info(6, 0, bs_title=True), 2: _info(0, 0), 3: _info(3, 0)}
    info |= {4: _info(1, 5, is_title=True), 5: _info(2, 0)}
    found = {"balance_sheet": [1, 3, 5], "income_statement": [4]}
    assert _fill_gaps(found, info)["balance_sheet"] == [1, 3, 5]  # p2 a cover page, p4 the income statement
