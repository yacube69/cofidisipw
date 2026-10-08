"""CIPW-10: accounting identity checks."""

from pathlib import Path

import pytest

from src.evaluation import compare
from src.schema import FinancialStatements, GroundTruth
from src.validation.checks import fields_in_failed_checks, validate
from tests.test_parser import _from_fixtures

ROOT = Path(__file__).resolve().parents[1]
GT_FILES = sorted(p for p in (ROOT / "data/ground_truth").glob("*.json") if not p.name.startswith("_"))


def load(stem: str = "26161516_2025") -> FinancialStatements:
    path = ROOT / f"data/ground_truth/{stem}.json"
    return GroundTruth.model_validate_json(path.read_text(encoding="utf-8")).to_statements()


def failed(fs: FinancialStatements) -> set[str]:
    return {c.name for c in validate(fs).errors}


@pytest.mark.parametrize("path", GT_FILES, ids=lambda p: p.stem)
def test_ground_truth_passes_every_check(path):
    fs = GroundTruth.model_validate_json(path.read_text(encoding="utf-8")).to_statements()
    report = validate(fs)
    assert report.passed, [c.name for c in report.errors]
    assert len(report.checks) >= 8


def test_misread_digit_in_total_is_caught():
    fs = load()
    fs.balance_sheet["total_assets"].current.value = 29_452_798_000  # 7 read as 2
    assert "Balance: total assets = total liabilities and equity" in failed(fs)
    assert "Assets: A + B + C + D = total assets" in failed(fs)


def test_lost_minus_sign_is_caught():
    fs = load()
    fs.income_statement["financial_result"].current.value = 769_443_000  # printed -769 443
    assert "Operating result + financial result = profit before tax" in failed(fs)


def test_swapped_columns_are_caught():
    fs = load()
    item = fs.balance_sheet["inventories"]
    item.gross.value, item.current.value = item.current.value, item.gross.value
    names = failed(fs)
    assert "Net = gross − adjustment (inventories)" in names
    assert "Current assets: C.I + C.II + C.III + C.IV = C" in names


def test_missing_part_breaks_the_sum():
    fs = load()
    del fs.balance_sheet["cash"]
    assert "Current assets: C.I + C.II + C.III + C.IV = C" in failed(fs)


def test_rounding_tolerance_for_sums_but_not_for_the_same_number():
    fs = load()
    fs.balance_sheet["cash"].current.value += 1000  # +1 tis. Kč: rounding in a sum is fine ...
    fs.balance_sheet["cash"].gross.value += 1000
    assert "Current assets: C.I + C.II + C.III + C.IV = C" not in failed(fs)
    fs.balance_sheet["cash"].current.value += 1000  # ... +2 is not
    assert "Current assets: C.I + C.II + C.III + C.IV = C" in failed(fs)
    fs = load()
    fs.income_statement["profit_for_period"].current.value += 1000  # same number printed twice: exact
    assert "Profit for the period (P&L) = A.V. in the balance sheet" in failed(fs)


def test_plausibility_warnings():
    fs = load()
    fs.balance_sheet["cash"].current.value = -20_186_000
    fs.balance_sheet["equity"].current.value *= 10
    names = {c.name for c in validate(fs).warnings}
    assert "Negative value where none is expected (cash)" in names
    assert "Year-over-year change above 500% (equity)" in names


def test_every_silent_parser_error_on_the_real_scan_is_caught():
    """The trust promise: on the real 2025 scan, every value the parser got wrong without flagging it is
    involved in at least one failed check, so it cannot slip into the ratios unnoticed."""
    gt = GroundTruth.model_validate_json((ROOT / "data/ground_truth/26161516_2025.json").read_text(encoding="utf-8"))
    fs, _ = _from_fixtures("26161516_2025", {"balance_sheet": [20, 21], "income_statement": [22]})
    report = validate(fs)
    suspect = {(f, c.period) for c in report.checks if not c.passed for f in c.fields}
    for f in compare(gt, fs):
        if f.outcome == "wrong":
            period = "current" if f.period in ("gross", "adjustment") else f.period
            assert (f.key, period) in suspect, f"{f.key}.{f.period} ({f.raw}) wrong and not caught"
    assert fields_in_failed_checks(report)
