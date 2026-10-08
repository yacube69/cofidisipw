"""CIPW-32: rule-based parser."""

from datetime import date
from pathlib import Path

import pymupdf
import pytest

from src.evaluation import compare
from src.extraction.parser import ParsedLine, ParseLog, code_key, identify, norm_code, parse_statements
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
        ("25080776_2025", {"balance_sheet": [1, 2], "income_statement": []}, 1.0, 0),
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
