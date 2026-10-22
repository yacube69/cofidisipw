"""The accounting checks decide which values need review and restore lost minus signs (src/validation/review.py)."""

from src.validation.review import validate_and_review
from tests.test_checks import load


def test_a_passing_check_confirms_values_ocr_was_unsure_of():
    fs = load()
    value = fs.balance_sheet["inventories"].current
    value.needs_review, value.ocr_conf = True, 40.0
    report, notes = validate_and_review(fs)
    assert report.passed and notes == []
    assert value.needs_review is False
    assert value.confirmed_by == "Current assets: C.I + C.II + C.III + C.IV = C"


def test_values_of_a_failed_check_need_review():
    fs = load()
    fs.balance_sheet["inventories"].current.value += 9_000_000  # a misread digit
    report, _ = validate_and_review(fs)
    for key in ("current_assets", "inventories", "receivables", "cash"):
        value = fs.balance_sheet[key].current
        assert value.needs_review and value.confirmed_by is None, key
    assert fs.balance_sheet["inventories"].prior.needs_review is False  # the prior year adds up


def test_a_lost_minus_is_restored_when_the_checks_pass_with_it():
    fs = load("26701529_2024")  # a loss in the prior year: −166 before tax
    value = fs.income_statement["profit_before_tax"].prior
    value.value, value.raw = 166_000, "+166"
    report, notes = validate_and_review(fs)
    assert value.value == -166_000 and report.passed
    assert notes == ["profit_before_tax: sign restored: printed '+166', the accounting checks pass with -166,000"]


def test_a_sign_is_not_turned_when_it_does_not_close_the_gap():
    fs = load()
    fs.balance_sheet["equity"].current.value = 7_000_000_000  # a misread digit, not a sign
    report, notes = validate_and_review(fs)
    assert notes == [] and not report.passed
    assert fs.balance_sheet["equity"].current.value == 7_000_000_000


def test_a_rounded_pass_does_not_confirm():
    """Sums may differ by 1 unit for rounding; a last digit read one off would pass the same way."""
    fs = load()
    value = fs.income_statement["profit_after_tax"].current
    value.value += 1000  # 396 012 read for 396 011: still within ±1 tis. Kč of PBT − tax
    value.needs_review = True
    validate_and_review(fs)
    assert value.needs_review
