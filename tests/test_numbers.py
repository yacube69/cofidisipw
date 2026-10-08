"""CIPW-9: number parsing, unit, period, scope, IČO and column detection."""

import glob
from datetime import date
from pathlib import Path

import pytest

from src.extraction.layout import group_lines, straighten
from src.extraction.numbers import (
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

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "text, expected",
    [
        ("1 234 567", 1234567),
        ("-17 406 042", -17406042),
        ("−52 392", -52392),  # unicode minus
        ("–2 478", -2478),  # en dash as minus
        ("(1 234)", -1234),
        ("0", 0),
        ("10000", 10000),
        ("1.234.567", 1234567),
        ("1,234,567", 1234567),
        ("12,5", 12.5),
        ("+48 871", 48871),
        # OCR confusions inside numbers
        ("2O66 OOO", 2066000),
        ("l 315", 1315),
        ("7S8 2O5", 758205),
        ("«78 225", -78225),
    ],
)
def test_parse_number(text, expected):
    assert parse_number(text) == expected


@pytest.mark.parametrize("text", ["Zásoby", "3a2 255", "31.12.2025", "", "B.", "1.2.3"])
def test_parse_number_rejects_non_numbers(text):
    assert parse_number(text) is None


def test_is_dash():
    assert is_dash("-") and is_dash("—") and not is_dash("-1")


@pytest.mark.parametrize(
    "text, unit",
    [
        ("(v celých tisících Kč)", 1000),
        ("v tis. Kč", 1000),
        ("(v celých tisících CZK)", 1000),
        ("v Kč", 1),
        ("v celých Kč", 1),
        ("v mil. Kč", 1_000_000),
        ("ROZVAHA", None),
    ],
)
def test_detect_unit(text, unit):
    assert detect_unit(text) == unit


def test_detect_period_from_rozvahovy_den():
    text = "Rozvahový den: 31. prosince 2025\nDatum sestavení účetní závěrky: 30. dubna 2026"
    assert detect_period(text) == (date(2025, 12, 31), date(2024, 12, 31))


def test_detect_period_from_column_dates_with_ocr_noise():
    assert detect_period("31.12.2025 31.12,.2024") == (date(2025, 12, 31), date(2024, 12, 31))


def test_detect_period_non_calendar_year():
    assert detect_period("Rozvahový den: 30.06.2025 30.06.2024") == (date(2025, 6, 30), date(2024, 6, 30))


def test_detect_scope():
    assert detect_scope("Rozvaha ve zkráceném rozsahu pro mikro účetní jednotku") == "abbreviated"
    assert detect_scope("ROZVAHA v plném rozsahu") == "full"
    assert detect_scope("ROZVAHA") is None


def test_detect_ico_and_company():
    assert detect_ico("Identifikační číslo: 26161516") == "26161516"
    assert detect_ico("IČ:\n25080776") == "25080776"
    assert detect_ico("Idertřikační číslo: 26161516") == "26161516"
    assert detect_ico("PASIVA CELKEM 21486058") is None
    assert detect_company("Firma: Mondi Štětí a.s.\nIdentifikační číslo") == "Mondi Štětí a.s"
    assert detect_company("Obchodní firma: Testovací firma s.r.o.") == "Testovací firma s.r.o"


def _word(text, x1, y):
    return Word(text=text, x0=x1 - 6 * len(text), y0=y, x1=x1, y1=y + 8)


def test_detect_columns_assets_with_row_codes_and_header():
    words = [_word("Brutto", 300, 40), _word("Korekce", 360, 40), _word("Netto", 420, 40), _word("Netto", 480, 40)]
    for i, y in enumerate(range(60, 200, 12)):
        words += [
            _word(f"{i + 1:03d}", 240, y),
            _word(str(1000 + i), 300, y),
            _word(f"-{10 + i}", 360, y),
            _word(str(990 + i), 420, y),
            _word(str(900 + i), 480, y),
        ]
    cols = detect_columns(group_lines(words), "assets")
    assert [c.name for c in cols] == ["row", "gross", "adjustment", "current", "prior"]


def test_detect_columns_without_header_uses_count():
    words = []
    for i, y in enumerate(range(60, 160, 12)):
        words += [_word(str(5000 + i), 420, y), _word(str(4000 + i), 480, y)]
    cols = detect_columns(group_lines(words), "liabilities")
    assert [c.name for c in cols] == ["current", "prior"]


SAVED = sorted(glob.glob(str(ROOT / "tests/fixtures/pagewords/*.json")))
EXPECTED = {
    "26161516_2025_p20": ["row", "gross", "adjustment", "current", "prior"],
    "26161516_2025_p21": ["row", "current", "prior"],
    "26161516_2025_p22": ["row", "current", "prior"],
    "25080776_2025_p1": ["gross", "current", "prior"],
    "25080776_2025_p2": ["current", "prior"],
}
KIND = {"p20": "assets", "p21": "liabilities", "p22": "income_statement", "p1": "assets", "p2": "liabilities"}


@pytest.mark.parametrize("path", SAVED, ids=lambda p: Path(p).stem)
def test_columns_on_saved_real_pages(path):
    """Regression on PageWords saved from the real statements (OCR output frozen, so no Tesseract needed)."""
    stem = Path(path).stem
    pw = PageWords.model_validate_json(Path(path).read_text(encoding="utf-8"))
    cols = detect_columns(group_lines(straighten(pw)), KIND[stem.rsplit("_", 1)[1]])
    assert [c.name for c in cols] == EXPECTED[stem]
