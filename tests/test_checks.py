"""CIPW-10: accounting identity checks."""

from pathlib import Path

import pytest

from src.evaluation import compare
from src.line_items import load_dictionary
from src.schema import FinancialStatements, GroundTruth, LineItem, Value
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
    gt = GroundTruth.model_validate_json(path.read_text(encoding="utf-8"))
    if not gt.partial:  # a complete statement allows most identities; a partial one only what was filled
        assert len(report.checks) >= 8, f"only {len(report.checks)} checks: fill more lines or set partial"


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


def test_positive_korekce_that_raises_the_value_is_accepted():
    """Skyport 2025, B.III. Dlouhodobý finanční majetek: brutto 12 357, korekce +9 905, netto 22 262."""
    fs = load()
    item = fs.balance_sheet["financial_fixed_assets"]
    item.gross.value, item.adjustment.value, item.current.value = 12_357_000, 9_905_000, 22_262_000
    assert "Net = gross − adjustment (financial_fixed_assets)" not in failed(fs)


def test_positive_korekce_printed_as_a_reduction_is_accepted():
    fs = load()
    item = fs.balance_sheet["inventories"]
    item.adjustment.value = abs(item.adjustment.value)  # same numbers, korekce printed without the minus
    assert "Net = gross − adjustment (inventories)" not in failed(fs)


def test_accruals_within_receivables_and_liabilities_count_in_the_sums():
    """C.II.3. and C.III. (Časové rozlišení inside receivables and liabilities, form since 2016) are part of C.II.
    and C.: moving an amount there must not break the sums."""
    checks = {
        "receivables_short_term": ("accruals_in_receivables", "Receivables: C.II.1 + C.II.2 + C.II.3 = C.II"),
        "liabilities_short_term": ("accruals_in_liabilities", "Liabilities: C.I + C.II + C.III = C"),
    }
    fs = load()
    for part, (_key, name) in checks.items():
        fs.balance_sheet[part].current.value -= 5_000_000
        assert name in failed(fs)
    for key, name in checks.values():
        fs.balance_sheet[key] = LineItem(key=key, current=Value(value=5_000_000))
        assert name not in failed(fs)


def test_misread_revenue_breaks_the_operating_result():
    """The operating part of the income statement is covered by the form's own formula (* = I + II − A − …)."""
    fs = load()
    fs.income_statement["revenue_products_services"].current.value += 9_000_000  # one digit misread
    assert "Operating result: I + II − A − B − C − D − E + III − F" in failed(fs)
    fs = load()
    fs.income_statement["personnel_costs"].prior.value *= -1  # lost minus
    assert "Operating result: I + II − A − B − C − D − E + III − F" in failed(fs)


def test_operating_result_accepts_costs_printed_negative_and_capitalization_with_either_sign():
    fs = load()
    for key in ("cost_of_sales", "personnel_costs", "value_adjustments_operating", "other_operating_expenses"):
        item = fs.income_statement[key]
        item.current.value = -item.current.value  # software that prints costs negative
    fs.income_statement["change_in_inventory"].current.value *= -1  # ... prints every line as it adds up
    fs.income_statement["capitalization"].current.value *= -1
    assert not {n for n in failed(fs) if n.startswith("Operating result:")}
    fs = load()
    fs.income_statement["capitalization"].current.value *= -1  # "Aktivace" printed positive, same meaning
    assert "Operating result: I + II − A − B − C − D − E + III − F" not in failed(fs)


def test_advance_profit_distribution_lowers_equity_whatever_its_printed_sign():
    """A.VI. "Rozhodnuto o zálohové výplatě podílu na zisku (-)": some software prints it positive and subtracts it
    (Siemens 2025: ř. 84 + 88 + 96 + 99 + 102 - 103), some prints it negative."""
    for printed in (5_000_000, -5_000_000):
        fs = load()
        fs.balance_sheet["equity"].current.value -= 5_000_000
        fs.balance_sheet["advance_profit_distribution"] = LineItem(
            key="advance_profit_distribution", current=Value(value=printed)
        )
        assert "Equity: A.I + … + A.V − A.VI = A" not in failed(fs)


def test_a_missed_equity_line_is_now_an_error():
    fs = load()
    del fs.balance_sheet["retained_earnings"]
    assert "Equity: A.I + … + A.V − A.VI = A" in failed(fs)


def failed_current(fs: FinancialStatements) -> set[str]:
    return {c.name for c in validate(fs).errors if c.period == "current"}


def _add(fs: FinancialStatements, key: str, current: float, source: str = "ground_truth") -> None:
    statement = fs.income_statement if key in load_dictionary().keys("income_statement") else fs.balance_sheet
    statement[key] = LineItem(key=key, current=Value(value=current * 1000, source=source))  # type: ignore[arg-type]


def _financial_part(fs: FinancialStatements) -> None:
    """Mondi 2025 financial part, made up to add up to its printed financial result −769 443 (= 507 009 − 1 276 452)."""
    for key, value in [("income_from_shares", 0), ("cost_of_shares_sold", 0),
                       ("income_other_fixed_financial_assets", 0), ("costs_other_fixed_financial_assets", 0),
                       ("interest_income", 12_000),
                       ("value_adjustments_financial", 0), ("other_financial_income", 50_000),
                       ("other_financial_expenses", 33_750)]:  # fmt: skip
        _add(fs, key, value)


def test_financial_result_covers_interest_expense():
    fs = load()
    _financial_part(fs)
    name = "Financial result: IV − G + V − H + VI − I − J + VII − K"
    assert name not in failed_current(fs)
    assert any(c.name == name and c.period == "current" for c in validate(fs).checks)
    fs.income_statement["interest_expense"].current.value = 707_693_000  # 7 read as 0
    assert name in failed_current(fs)


def test_new_lines_missing_from_an_older_ground_truth_file_are_not_judged():
    """The financial lines were added to the dictionary after the ground truth was transcribed: a file without
    them did not transcribe them, so the financial result check does not run (no false alarm)."""
    fs = load()
    assert not any(c.name.startswith("Financial result:") for c in validate(fs).checks)
    _financial_part(fs)  # once the file has the financial lines, the check runs
    assert any(c.name.startswith("Financial result:") for c in validate(fs).checks)


def test_extracted_statement_counts_unprinted_new_lines_as_zero():
    fs = load()
    for item in [*fs.balance_sheet.values(), *fs.income_statement.values()]:
        for v in item.values():
            v.source = "ocr"
    # an extracted statement: lines the parser did not find are not printed; the financial result runs with J alone
    checks = [c for c in validate(fs).checks if c.name.startswith("Financial result:") and c.period == "current"]
    assert checks and not checks[0].passed  # −797 693 alone is not −769 443: a line was not read


def test_short_term_liabilities_cover_bank_loans_and_trade_payables():
    fs = load("07880383_2025")  # Skyport: C.II. 85 003 = bank loans 194 + trade payables 29 323 + the rest
    rest = 85_003 - 194 - 29_323
    _add(fs, "other_payables_short_term", rest)
    for key in ("bonds_short_term", "advances_received_short_term", "bills_payable_short_term",
                "group_payables_short_term", "associate_payables_short_term"):  # fmt: skip
        _add(fs, key, 0)
    name = "Short-term liabilities: C.II.1 + … + C.II.8 = C.II"
    assert name not in failed_current(fs)
    fs.balance_sheet["trade_payables_short_term"].current.value = 39_323_000
    assert name in failed_current(fs)
