"""CIPW-6: page locator finds the statement pages."""

import csv
from pathlib import Path

import pymupdf
import pytest

from src.extraction.locate import locate_statements, normalize
from tests.synthetic import asset_page_html, make_digital_pdf, make_scanned_pdf
from tests.test_reading import needs_ocr

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "data/index.csv"


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


@pytest.mark.parametrize("row", index_rows(), ids=lambda r: r["file"])
def test_real_files_match_index(row):
    with pymupdf.open(ROOT / "data/raw" / row["file"]) as d:
        assert locate_statements(d) == parse_expected(row["expected_pages"])
