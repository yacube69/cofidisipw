"""CIPW-11: cells the checks doubt are read again from the page; readings are taken only when the checks agree."""

import pymupdf
import pytest

from src.extraction.parser import parse_statements
from src.extraction.reading import read_page
from src.extraction.reread import reread
from src.validation.review import validate_and_review
from tests.synthetic import make_digital_pdf
from tests.test_checks import load
from tests.test_reading import needs_ocr

PAGES = {"balance_sheet": [1, 2], "income_statement": []}
K = 1000


@pytest.fixture
def statement(tmp_path):
    """The synthetic statement read from its text layer, with its pages marked as OCR so the re-read runs."""
    pdf = make_digital_pdf(tmp_path / "12345678_2025_statement.pdf")
    doc = pymupdf.open(pdf)
    fs = parse_statements([read_page(doc, p) for p in (1, 2)], PAGES)
    fs.metadata.extraction_source = {1: "ocr", 2: "ocr"}
    yield doc, fs
    doc.close()


@needs_ocr
def test_a_misread_digit_is_read_again_and_corrected(statement):
    doc, fs = statement
    fs.balance_sheet["inventories"].current.value = 170_000 * K  # OCR read 1[7]0 000
    fs.balance_sheet["inventories"].current.raw = "170 000"
    report, _ = validate_and_review(fs)
    assert not report.passed
    out = reread(doc, fs, report)
    assert fs.get("inventories") == 120_000 * K
    value = fs.value("inventories", "current")
    assert "re-read" in value.note and 170_000 * K in value.candidates  # never silent: the old reading is kept
    assert [(c.key, c.period, c.old, c.new) for c in out.changes] == [
        ("inventories", "current", 170_000 * K, 120_000 * K)
    ]
    assert out.failed_after < out.failed_before


@needs_ocr
def test_an_empty_cell_is_filled_when_the_checks_pass_with_it(statement):
    doc, fs = statement
    fs.balance_sheet["cash"].prior = None  # the parser missed the prior-year cell
    report, _ = validate_and_review(fs)
    reread(doc, fs, report)
    assert fs.get("cash", "prior") == 50_000 * K
    assert fs.value("cash", "prior").source == "ocr"


def _amounts(fs):
    return {(k, p): fs.get(k, p) for k in fs.balance_sheet for p in ("gross", "adjustment", "current", "prior")}


@needs_ocr
def test_values_that_read_cleanly_are_not_changed(statement):
    doc, fs = statement
    before = _amounts(fs)
    out = reread(doc, fs, validate_and_review(fs)[0])
    assert out.changes == [] and _amounts(fs) == before


def test_a_value_derived_from_a_failed_sum_is_only_proposed():
    """No page to read: the re-read finds nothing, the value that would close the sum becomes a candidate."""
    fs = load()
    fs.balance_sheet["inventories"].current.value = 2_939_741_000  # 2 439 741 misread
    report, _ = validate_and_review(fs)
    doc = pymupdf.open()
    doc.new_page()
    fs.metadata.extraction_source = {}
    out = reread(doc, fs, report)
    assert out.cells == 0 and out.changes == []
    assert fs.get("inventories") == 2_939_741_000  # nothing applied
    value = fs.value("inventories", "current")
    assert 2_439_741_000 in value.candidates and value.needs_review
    assert out.proposals >= 1
