"""CIPW-32: rule-based parser."""

from datetime import date
from pathlib import Path

import pymupdf
import pytest

from src.evaluation import compare
from src.extraction.parser import ParsedLine, ParseLog, code_key, continues, identify, norm_code, parse_statements
from src.extraction.reading import read_page
from src.extraction.types import PageWords
from src.line_items import load_dictionary
from src.schema import GroundTruth
from tests.synthetic import make_digital_pdf, make_scanned_pdf
from tests.test_reading import needs_ocr

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/pagewords"
PAGES = {"balance_sheet": [1, 2], "income_statement": []}


def test_norm_code():
    assert norm_code("B. I. 2.") == "B.I.2."
    assert norm_code("B. + C.") == "B.+C."
    assert norm_code("C,II") == "C.II."
    assert norm_code("**") == "**"


def test_code_key_tolerates_roman_numeral_ocr_confusion():
    assert code_key("B.I.") == code_key("B.1.") == code_key("B.l.") == code_key("B.|.")


def _line(label, code="", row=None):
    return ParsedLine(page=1, kind="assets", code=code, label=label, row=row)


def test_identify_prefers_exact_code_for_repeated_labels():
    d = load_dictionary()
    candidates = d.for_statement("balance_sheet", "liabilities")
    assert identify(_line("Základní kapitál", "A.I.", "084"), candidates)[0].key == "share_capital"
    best = identify(_line("Základní kapitál", "A.I.1.", "085"), candidates)[0]
    assert best.key == "share_capital" and best.score < 100  # weaker: code and row disagree


def test_identify_garbled_shaded_label_by_row_number():
    candidates = load_dictionary().for_statement("balance_sheet", "assets")
    best = identify(_line("Dlouhodctý nehmotný majetek", "a1", "004"), candidates)[0]
    assert best.key == "intangible_assets" and best.score >= 90


def test_identify_accruals_by_code_and_row():
    """Časové rozlišení is printed twice on each side (C.II.3./D. and C.III./D.): code and row number decide."""
    d = load_dictionary()
    assets, liabilities = d.for_statement("balance_sheet", "assets"), d.for_statement("balance_sheet", "liabilities")
    assert identify(_line("Časové rozlišení aktiv", "C.II.3.", "068"), assets)[0].key == "accruals_in_receivables"
    assert identify(_line("Časové rozlišení aktiv", "D.", "078"), assets)[0].key == "accruals_assets"
    assert identify(_line("Časové rozlišení aktiv", "D."), assets)[0].key == "accruals_assets"
    assert identify(_line("Časové rozlišení pasiv", "C.III.", "144"), liabilities)[0].key == "accruals_in_liabilities"
    assert identify(_line("Časové rozlišení pasiv", "D.", "147"), liabilities)[0].key == "accruals_liabilities"
    assert identify(_line("Časové rozlišení pasiv"), liabilities)[0].key == "accruals_liabilities"  # tie: D.


def test_identify_wrapped_label_does_not_match_its_second_half():
    candidates = load_dictionary().for_statement("balance_sheet", "assets")
    line = _line("dlouhodobý hmotný majetek", "B.II.5.", "024")
    best = identify(line, candidates, extra_label="Poskytnuté zálohy na dlouhodobý hmotný majetek a nedokončený")[0]
    assert best.key != "tangible_assets" or best.score < 78


def test_digital_synthetic_statement_is_extracted_exactly(tmp_path):
    pdf = make_digital_pdf(tmp_path / "12345678_2025_statement.pdf")
    with pymupdf.open(pdf) as doc:
        pws = [read_page(doc, p) for p in (1, 2)]
    fs = parse_statements(pws, PAGES)
    k = 1000
    assert fs.get("total_assets", "gross") == 1_250_000 * k
    assert fs.get("total_assets", "adjustment") == -250_000 * k
    assert fs.get("total_assets") == 1_000_000 * k
    assert fs.get("total_assets", "prior") == 900_000 * k
    assert fs.get("cash") == 70_000 * k and fs.get("cash", "adjustment") == 0
    assert fs.get("total_liabilities_equity") == 1_000_000 * k
    assert fs.get("profit_current_period", "prior") == -20_000 * k
    assert fs.get("external_sources") == 600_000 * k
    assert fs.get("liabilities_short_term") == 300_000 * k
    assert fs.flagged() == []
    m = fs.metadata
    assert (m.ico, m.unit_multiplier, m.scope, m.period_end) == ("12345678", 1000, "abbreviated", date(2025, 12, 31))
    assert m.company_name == "Testovací firma s.r.o"
    v = fs.value("total_assets")
    assert v.page == 1 and v.source == "text_layer" and v.bbox is not None and v.raw == "1 000 000"


@needs_ocr
def test_scanned_synthetic_statement(tmp_path):
    digital = make_digital_pdf(tmp_path / "a.pdf")
    reference = parse_statements([read_page(pymupdf.open(digital), p) for p in (1, 2)], PAGES)
    scan = make_scanned_pdf(digital, tmp_path / "scan.pdf")
    with pymupdf.open(scan) as doc:
        fs = parse_statements([read_page(doc, p) for p in (1, 2)], PAGES)
    pairs = [
        (key, period)
        for st in ("balance_sheet", "income_statement")
        for key, item in reference.statement(st).items()
        for period in ("gross", "adjustment", "current", "prior")
        if getattr(item, period) is not None
    ]
    correct = sum(fs.get(k, p) == reference.get(k, p) for k, p in pairs)
    assert correct >= 0.8 * len(pairs), f"only {correct}/{len(pairs)} correct from the scan"


def _from_fixtures(stem: str, pages: dict[str, list[int]]):
    pws = [
        PageWords.model_validate_json((FIXTURES / f"{stem}_p{p}.json").read_text(encoding="utf-8"))
        for ps in pages.values()
        for p in ps
    ]
    log = ParseLog()
    return parse_statements(pws, pages, log=log), log


@pytest.mark.parametrize(
    "stem, pages, min_accuracy, max_silent",
    [
        ("26161516_2025", {"balance_sheet": [20, 21], "income_statement": [22]}, 0.84, 8),
    ],
)
def test_real_statements_regression(stem, pages, min_accuracy, max_silent):
    """Frozen OCR output of real statements vs ground truth. Raise the bar when the parser improves."""
    gt = GroundTruth.model_validate_json((ROOT / f"data/ground_truth/{stem}.json").read_text(encoding="utf-8"))
    fs, _ = _from_fixtures(stem, pages)
    fields = compare(gt, fs)
    accuracy = sum(f.correct for f in fields) / len(fields)
    silent = sum(f.outcome == "wrong" for f in fields)
    missing = [f"{f.key}.{f.period}" for f in fields if f.outcome == "missing"]
    assert missing == []
    assert accuracy >= min_accuracy, f"accuracy {accuracy:.1%}"
    assert silent <= max_silent, f"{silent} wrong values not flagged"


def test_every_value_has_provenance():
    fs, _ = _from_fixtures("26161516_2025", {"balance_sheet": [20, 21], "income_statement": [22]})
    for st in ("balance_sheet", "income_statement"):
        for item in fs.statement(st).values():
            for v in item.values():
                assert v.page in (20, 21, 22) and v.bbox is not None and v.source == "ocr"
                assert v.match_score is not None and v.raw


# ---------------------------------------------------------------- CIPW-32: lines the parser used to miss


def test_formula_in_the_label_does_not_hide_the_line():
    """Some software prints the formula after the name: "PASIVA CELKEM (A.+B.+C.+D.)",
    "Rezervy (součet B.1. až B.4.)"."""
    liabilities = load_dictionary().for_statement("balance_sheet", "liabilities")
    for label, key in [("PASIVA CELKEM (A.+B.+C.+D.)", "total_liabilities_equity"),
                       ("Rezervy (součet B.1. až B.4.)", "provisions"),
                       ("Vlastní kapitál (ř. 80 + 84 + 92 + 95 + 98 +", "equity")]:  # fmt: skip
        best = identify(_line(label), liabilities)[0]
        assert best.key == key and best.score >= 90, (label, best)


def test_garbled_code_in_front_of_the_label_is_ignored():
    """OCR leaves an unreadable označení in the label: "C.m.2.1. — Pohledávky z obchodních vztahů", "c. Závazky"."""
    assets = load_dictionary().for_statement("balance_sheet", "assets")
    liabilities = load_dictionary().for_statement("balance_sheet", "liabilities")
    assert identify(_line("C.m.2.1. — Pohledávky - ostatní z obchodních vztahů"), assets)[0].key.startswith("trade_")
    assert identify(_line("c. Závazky"), liabilities)[0].key == "liabilities"
    assert identify(_line("AIM. Fondy ze zisku"), liabilities)[0].key == "funds_from_profit"
    # a garbled sub-line code (B.4.3.) does not win the line it belongs to (B.II.)
    sub = identify(_line("B.4.3. | Jiný dlouhodobý hmotný majetek"), assets)[0]
    assert sub.key != "tangible_assets" or sub.score < 86


def test_only_a_real_continuation_joins_the_label_above():
    above = _line("Poskytnuté zálohy na dlouhodobý hmotný majetek a")
    coded_above = _line("A.3. Služby")
    assert continues(_line("nedokončený dlouhodobý hmotný majetek"), above)
    assert not continues(_line("Osobní náklady", "D."), coded_above)  # a new line, not the end of "Služby"
    assert not continues(_line("c. Závazky"), above)
    # the code of a two-line label may sit on its second line, next to the numbers
    first_half = _line("Výsledek hospodaření běžného účetního období")
    assert continues(_line("podílů (+/-)", "A.V."), first_half)


def test_a_sub_line_printed_with_its_own_number_only():
    """'4. Závazky z obchodních vztahů' fits C.I.4. (long-term) and C.II.4. (short-term) equally: the section
    decides."""
    liabilities = load_dictionary().for_statement("balance_sheet", "liabilities")
    matches = identify(_line("Závazky z obchodních vztahů", "4."), liabilities)
    assert {matches[0].key, matches[1].key} == {"trade_payables_short_term", "trade_payables_long_term"}
    assert matches[0].score == matches[1].score


def test_receivables_sections_on_a_real_page():
    """27145107 2025: C.II.1. Dlouhodobé and C.II.2. Krátkodobé pohledávky, both read."""
    fs, _ = _from_fixtures("27145107_2025", {"balance_sheet": [1], "income_statement": []})
    assert fs.get("receivables_long_term", "current") == 7_492_000
    assert fs.get("receivables_short_term", "current") == 5_278_000


def test_continuation_page_keeps_the_columns_of_the_page_before():
    """A balance sheet over several pages: a page where the current year is mostly blank finds too few numbers
    to form its columns, and takes those of the page before when they sit at the same place."""
    from src.extraction.numbers import Column
    from src.extraction.parser import _with_template

    full = [Column("row", 300, 310), Column("gross", 337, 367), Column("adjustment", 396, 422),
            Column("current", 451, 477), Column("prior", 508, 533)]  # fmt: skip
    sparse = [Column("row", 302, 311), Column("prior", 520, 534)]
    names = [c.name for c in _with_template(sparse, (595.0, full), 595.0)]
    assert names == ["row", "gross", "adjustment", "current", "prior"]
    other_layout = [Column("current", 200, 230), Column("prior", 260, 290)]
    assert _with_template(other_layout, (595.0, full), 595.0) == other_layout  # columns elsewhere: keep them
    assert _with_template(sparse, (595.0, full), 842.0) == sparse  # another page size
