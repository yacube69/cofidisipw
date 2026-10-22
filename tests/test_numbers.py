"""CIPW-9: number parsing, unit, period, scope, IČO and column detection."""

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
from src.extraction.parser import _split_sides
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
        ("-+253", -253),  # minus read twice
        ("B12", 812),
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
        # OCR variants from real scans
        ("ROZVAHA (vtíis. Kč)", 1000),
        ("dveelých tislcick KŘ", 1000),
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


@pytest.mark.parametrize(
    "text, expected",
    [
        # every grammatical case of the month
        ("ROZVAHA k 31. prosinci 2025 (v tisících Kč)", (date(2025, 12, 31), date(2024, 12, 31))),
        ("VÝKAZ ZISKU A ZTRÁTY za rok končící 31. prosincem 2024", (date(2024, 12, 31), date(2023, 12, 31))),
        ("Unipetrol a.s. k 31. března 2026, minulé období 31.3.2025", (date(2026, 3, 31), date(2025, 3, 31))),
        # slashes (IFRS statements)
        ("Bod 31/12/2025 31/12/2024 (upraveno)*", (date(2025, 12, 31), date(2024, 12, 31))),
        # a form with one box per digit
        (
            "k . . . 3 . . . 1 . . . . 1 . . . 2 . . . . 2 . . . 0 . . . 2 . . . 5 . . REKO",
            (date(2025, 12, 31), date(2024, 12, 31)),
        ),
        # fiscal year: the starts of the periods are not the prior period end
        ("Běžné Minulé od 1.3.2024 - 28.2.2025 od 1.3.2023 - 29.2.2024", (date(2025, 2, 28), date(2024, 2, 29))),
        (
            "Stav v běžném účetním období 1.7.2024 - 30.6.2025, 1.7.2023 - 30.6.2024",
            (date(2025, 6, 30), date(2024, 6, 30)),
        ),
        # only the calendar year is printed
        ("KONSOLIDOVANÁ ÚČETNÍ ZÁVĚRKA ZA ROK 2024 (v mil. Kč)", (date(2024, 12, 31), date(2023, 12, 31))),
    ],
)
def test_detect_period_formats(text, expected):
    assert detect_period(text) == expected


def test_detect_period_ignores_dates_of_preparation_and_signature():
    assert detect_period("Sestaveno dne: 17.4.2026") == (None, None)
    # printed once next to the signature vs in the header of every page
    text = "Brano a.s. k 31.12.2024\n" * 3 + "Podpisový záznam (podpis): 30.06.2025"
    assert detect_period(text)[0] == date(2024, 12, 31)


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
    # a header table "Daňový subjekt: … | IČ / DIČ: …" (OCR reads IČ as 1Č)
    header = "„Daňový subjekt: Lidová zahrada, s.r.o. — | 1Č/DIČ: 25400223 / CZ25400223 : Sídlo účetní jednotky: …"
    assert detect_company(header) == "Lidová zahrada, s.r.o"
    assert detect_ico(header) == "25400223"
    assert detect_company("Název položky běžné účetní období") is None


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


# PageWords saved from real statements (OCR output frozen, so no Tesseract needed): expected column names per part
SAVED = {
    "26161516_2025_p20": {"assets": ["row", "gross", "adjustment", "current", "prior"]},  # Mondi, shaded scan
    "26161516_2025_p21": {"liabilities": ["row", "current", "prior"]},
    "26161516_2025_p22": {"income_statement": ["row", "current", "prior"]},
    "25080776_2025_p1": {"assets": ["gross", "current", "prior"]},  # micro entity, no Korekce printed
    "25080776_2025_p2": {"liabilities": ["current", "prior"]},
    # formula references "(ř. 83 + 104 + 147)" left of the real row-number column
    "00268577_2025_p7": {"liabilities": ["row", "current", "prior"]},
    # three years printed: Netto 2024, Netto 2023, Netto 2022
    "46504931_2024_p2": {"assets": ["row", "gross", "adjustment", "current", "prior", "extra"]},
    "46504931_2024_p4": {"liabilities": ["row", "current", "prior", "extra"]},
    # Korekce printed on three lines only
    "45307971_2025_p1": {"assets": ["gross", "adjustment", "current", "prior"]},
    # first (extended) fiscal year: only the current period column, under "Stav v běžném"
    "26178541_2024_p11": {"income_statement": ["current"]},
    # header words doubled in the text layer
    "27145107_2025_p1": {
        "assets": ["row", "gross", "adjustment", "current", "prior"],
        "liabilities": ["row", "current", "prior"],
    },
    # OCR splits one column into two clusters of right edges
    "05391423_2025_p4": {"assets": ["row", "gross", "adjustment", "current", "prior"]},
}


@pytest.mark.parametrize("stem", SAVED)
def test_columns_on_saved_real_pages(stem):
    pw = PageWords.model_validate_json((ROOT / f"tests/fixtures/pagewords/{stem}.json").read_text(encoding="utf-8"))
    lines = group_lines(straighten(pw))
    kinds = SAVED[stem]
    parts = _split_sides(lines) if "income_statement" not in kinds else [("income_statement", lines)]
    found = {kind: [c.name for c in detect_columns(part, kind)] for kind, part in parts}
    assert found == kinds


def test_every_saved_page_is_tested():
    assert {p.stem for p in (ROOT / "tests/fixtures/pagewords").glob("*.json")} == set(SAVED)


def test_a_ruling_after_a_number_is_not_part_of_it():
    """OCR reads the vertical rule after a number as a bracket: "0)" is 0 (28297580), a bracketed number stays
    negative."""
    assert parse_number("0)") == 0
    assert parse_number("1 234]") == 1234
    assert parse_number("(1 234)") == -1234
