"""CIPW-43: the PDF report in Cofidis' layout, with Fides credited in the footer."""

from pathlib import Path

import pymupdf
import pytest

from src.report.pdf import number, ratio_change, ratio_value, render_report, report_filename
from src.result import Result
from src.settings import get_settings

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "data/fixtures/result_26161516_2025.json"  # Mondi Štětí 2025, with the mock AI text


@pytest.fixture
def settings():
    s = get_settings().model_copy(deep=True)
    s.report.language, s.report.logo = "cs", ""
    return s


def _pages(pdf: bytes) -> list[str]:
    flags = pymupdf.TEXTFLAGS_TEXT & ~pymupdf.TEXT_PRESERVE_LIGATURES  # "ﬁ" as "fi"
    with pymupdf.open("pdf", pdf) as doc:
        return [page.get_text(flags=flags) for page in doc]


def test_czech_and_english_numbers():
    assert number(1234567.8) == "1 234 568" and number(1234567.8, 0, "en") == "1,234,568"
    assert number(-0.004, 2) == "0,00" and number(None) == "–" and number(-12.5, 1) == "−12,5"
    assert ratio_value(0.2227, "x", "cs") == "0,22×" and ratio_value(4.43, "%", "cs") == "4,4 %"
    assert ratio_value(47.2, "days", "en") == "47 days"
    assert ratio_change(2.5, "%", "cs") == "+2,5 p. b." and ratio_change(-0.004, "x", "cs") == "0,00"


def test_the_report_of_the_sample(settings):
    result = Result.model_validate_json(SAMPLE.read_text(encoding="utf-8"))
    pages = _pages(render_report(result, settings))
    first = pages[0]
    assert "Cofidis" in first and "Hodnocení finanční situace" in first
    assert "VYSOKÉ" in first and "IN05 0,55 (tíseň)" in first  # the rating and the bankruptcy zones
    assert "Aktiva celkem" in first and "29 457 798" in first  # key figures in tis. Kč
    assert "Běžná likvidita" in first and "ověřit" in first  # an input to review: no light
    for i, text in enumerate(pages, 1):  # on every page: the Fides credit, the page number, the mock mark
        assert "Vytvořeno v programu Fides, který vznikl ve spolupráci s ČVUT v rámci projektu IPW." in text
        assert f"Strana {i} z {len(pages)}" in text
        assert "UKÁZKOVÝ TEXT" in text
    assert "Účetní kontroly:" in pages[-1] and "rozvaha 20–21, výsledovka 22" in pages[-1]


def test_english_labels_and_a_logo(settings):
    settings.report.language = "en"
    settings.report.logo = "docs/brand/logo/fides-mark-192.png"  # stands in for Cofidis' logo file
    result = Result.model_validate_json(SAMPLE.read_text(encoding="utf-8"))
    pdf = render_report(result, settings)
    first = _pages(pdf)[0]
    assert "Financial assessment" in first and "Created with Fides" in first and "Current ratio" in first
    assert "Cofidis" not in first  # the logo replaces the name
    with pymupdf.open("pdf", pdf) as doc:
        assert len(doc[0].get_images()) == 2  # the logo in the header and the Fides logo in the footer


def test_without_an_ai_report_the_numbers_stand_alone(settings):
    result = Result.model_validate_json(SAMPLE.read_text(encoding="utf-8"))
    result.report = None
    first = _pages(render_report(result, settings))[0]
    assert "Text hodnocení AI není k dispozici" in first and "UKÁZKOVÝ TEXT" not in first
    assert "Klíčové údaje" in first
    assert report_filename(result) == "fides_26161516_2025.pdf"
