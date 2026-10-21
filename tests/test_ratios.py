"""CIPW-12: ratio engine, checked against values calculated by hand from two ground truth statements."""

import pytest

from src.ratios.engine import compute_ratios
from src.schema import FinancialStatements, LineItem, Value
from src.validation.checks import validate
from tests.test_checks import load

# by hand from the printed statements, in tis. Kč (the engine works in CZK, the ratios are the same)
MONDI_2025 = {
    "current_ratio": 0.2227,  # 4 443 661 / 19 950 475
    "quick_ratio": 0.1004,  # (4 443 661 − 2 439 741) / 19 950 475
    "cash_ratio": 0.0010,  # 20 186 / 19 950 475, no short-term financial assets printed
    "roa": 4.429,  # EBIT (507 009 + 797 693 = 1 304 702) / 29 457 798
    "roe": 4.968,  # 396 011 / 7 971 740
    "ros": 2.553,  # 396 011 / 15 510 024 (no revenue from goods)
    "ebitda_margin": 16.946,  # (1 276 452 + 1 351 836) / 15 510 024
    "debt_ratio": 72.938,  # 21 486 058 / 29 457 798
    "debt_to_equity": 2.6953,  # 21 486 058 / 7 971 740
    "interest_coverage": 1.6356,  # 1 304 702 / 797 693
    "receivables_days": 46.68,  # 1 983 734 / 15 510 024 × 365
    "payables_days": 59.59,  # trade payables 2 532 204 / 15 510 024 × 365
}
MONDI_2024 = {
    "current_ratio": 0.2164,  # 4 201 648 / 19 413 530
    "roe": 16.505,  # 1 250 388 / 7 575 872
    "interest_coverage": 3.1002,  # (1 334 961 + 635 640) / 635 640
}
SKYPORT_2025 = {
    "current_ratio": 2.3956,  # 203 632 / 85 003
    "quick_ratio": 2.3956,  # no inventories printed
    "cash_ratio": 0.5493,  # 46 693 / 85 003
    "roa": 29.319,  # EBIT (69 387 + 443 = 69 830) / 238 171
    "roe": 39.145,  # 52 608 / 134 394
    "ros": 9.938,  # 52 608 / 529 345
    "ebitda_margin": 13.540,  # (70 938 + 734) / 529 345
    "debt_ratio": 43.532,  # 103 680 / 238 171
    "debt_to_equity": 0.7715,  # 103 680 / 134 394
    "interest_coverage": 157.63,  # 69 830 / 443
    "receivables_days": 74.82,  # 108 505 / 529 345 × 365
    "payables_days": 20.22,  # 29 323 / 529 345 × 365
}
SKYPORT_2024 = {
    "current_ratio": 2.9858,  # 115 707 / 38 752
    "roe": 4.6908,  # 3 528 / 75 211
}


@pytest.mark.parametrize(
    ("stem", "current", "prior"),
    [("26161516_2025", MONDI_2025, MONDI_2024), ("07880383_2025", SKYPORT_2025, SKYPORT_2024)],
    ids=["mondi", "skyport"],
)
def test_ratios_match_hand_calculation(stem, current, prior):
    fs = load(stem)
    report = compute_ratios(fs, validate(fs))
    for key, expected in current.items():
        ratio = report.get(key)
        assert ratio.current.value == pytest.approx(expected, rel=2e-3, abs=1e-4), key
        assert ratio.current.flag == "ok", (key, ratio.current.reason)
    for key, expected in prior.items():
        assert report.get(key).prior.value == pytest.approx(expected, rel=2e-3), key
    # the change between the years is in the ratio's own unit
    roe = report.get("roe")
    assert roe.change == pytest.approx(roe.current.value - roe.prior.value)


def test_every_ratio_for_both_years_with_formula_and_inputs():
    report = compute_ratios(load("26161516_2025"))
    assert len(report.ratios) == 12
    for ratio in report.ratios:
        for value in (ratio.current, ratio.prior):
            assert value.value is not None and value.flag == "ok"
            assert value.inputs and (value.reason is None or "counted as 0" in value.reason)
    assert report.get("roa").current.inputs["ebit"] == pytest.approx(1_304_702_000)


def _set(fs: FinancialStatements, key: str, current: float | None, prior: float | None = None) -> None:
    statement = fs.balance_sheet if key in fs.balance_sheet else fs.income_statement
    statement[key] = LineItem(key=key, current=Value(value=current), prior=Value(value=prior))


def test_negative_equity_makes_roe_and_debt_to_equity_not_meaningful():
    fs = load()
    _set(fs, "equity", -1_000_000, 7_575_872_000)
    report = compute_ratios(fs)
    for key in ("roe", "debt_to_equity"):
        assert report.get(key).current.flag == "not_meaningful"
        assert report.get(key).current.value is None
        assert "negative" in report.get(key).current.reason
        assert report.get(key).prior.flag == "ok"


def test_zero_denominator_gives_none_and_a_reason():
    fs = load()
    _set(fs, "interest_expense", 0, 0)
    coverage = compute_ratios(fs).get("interest_coverage").current
    assert coverage.value is None and coverage.flag == "not_meaningful"
    assert coverage.reason == "interest_expense is 0: no interest to cover"


def test_missing_input_gives_none():
    fs = load()
    del fs.balance_sheet["liabilities_short_term"]
    report = compute_ratios(fs)
    for key in ("current_ratio", "quick_ratio", "cash_ratio"):
        assert report.get(key).current.flag == "missing" and report.get(key).current.value is None
    assert report.get("current_ratio").current.reason == "liabilities_short_term not found"
    # trade payables are still there, so payables days do not need the short-term liabilities
    assert report.get("payables_days").current.flag == "ok"


def test_payables_days_fall_back_to_short_term_liabilities():
    fs = load()
    del fs.balance_sheet["trade_payables_short_term"]
    days = compute_ratios(fs).get("payables_days")
    assert days.formula == "liabilities_short_term / revenue * 365"
    assert days.current.value == pytest.approx(19_950_475 / 15_510_024 * 365, rel=1e-4)
    assert "all short-term liabilities used" in days.current.reason


def test_profit_for_period_falls_back_to_profit_after_tax():
    fs = load()
    del fs.income_statement["profit_for_period"]
    roe = compute_ratios(fs).get("roe").current
    assert roe.value == pytest.approx(4.968, rel=1e-3)
    assert "profit_after_tax used" in roe.reason


def test_costs_printed_negative_count_by_size():
    fs = load()
    fs.income_statement["interest_expense"].current.value = -797_693_000
    assert compute_ratios(fs).get("interest_coverage").current.value == pytest.approx(1.6356, rel=1e-3)


def test_value_to_review_makes_the_ratio_unreliable():
    fs = load()
    fs.balance_sheet["current_assets"].current.needs_review = True
    report = compute_ratios(fs, validate(fs))
    current = report.get("current_ratio").current
    assert current.flag == "unreliable" and current.value == pytest.approx(0.2227, rel=1e-3)
    assert "current_assets needs review" in current.reason
    assert report.get("current_ratio").prior.flag == "ok"
    assert report.get("roe").current.flag == "ok"


def test_failed_check_makes_its_ratios_unreliable():
    fs = load()
    fs.balance_sheet["total_assets"].current.value = 29_452_798_000  # 7 read as 2
    report = compute_ratios(fs, validate(fs))
    roa = report.get("roa").current
    assert roa.flag == "unreliable"
    assert "check failed: Assets: A + B + C + D = total assets" in roa.reason
    assert report.get("roe").current.flag == "ok"


def test_abbreviated_statement_without_short_term_liabilities():
    report = compute_ratios(load("26701529_2024"))
    assert report.get("current_ratio").current.flag == "missing"
    assert report.get("interest_coverage").current.flag == "not_meaningful"  # no interest expense printed
    assert report.get("roe").current.value == pytest.approx(271 / 1698 * 100, rel=1e-3)
    assert report.get("roe").prior.value < 0  # a loss year is a negative return, not "not meaningful"
