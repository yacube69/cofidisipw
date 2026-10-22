"""Accounting identity checks (CIPW-10).

For a lender the worst error is a plausible but wrong number nobody notices. Czech statements are full of
identities (totals = sum of sections, assets = liabilities, P&L result = balance-sheet A.V.), so most OCR
misreads break at least one of them. Every check returns name, period, expected, actual, difference, fields
involved and severity, so CIPW-11 knows which rows to re-read.

Severity:
    error    an accounting identity that must hold; a failure means a wrong or missing value
    warning  plausibility and OCR-quality signals; worth a look, not necessarily wrong
"""

from __future__ import annotations

from collections.abc import Iterable

from src.schema import FinancialStatements, Period
from src.validation.models import CheckResult, ValidationReport

# How a part enters a sum: added, subtracted, or by its meaning whatever sign the software printed it with.
#   "lower": always lowers the total (A.VI. advance profit distribution: printed "1 000" or "-1 000")
#   "raise": always raises it (C. Aktivace: own work capitalised reduces costs, printed with either sign)
Sign = int | str
Part = str | tuple[str, Sign]

# total = sum(parts); a check runs when the total and at least one part are present (missing parts count as 0)
SUM_CHECKS: list[tuple[str, str, list[Part], str]] = [
    # name, total, parts, severity
    ("Assets: A + B + C + D = total assets", "total_assets",
     ["receivables_subscribed_capital", "fixed_assets", "current_assets", "accruals_assets"], "error"),
    ("Fixed assets: B.I + B.II + B.III = B", "fixed_assets",
     ["intangible_assets", "tangible_assets", "financial_fixed_assets"], "error"),
    ("Current assets: C.I + C.II + C.III + C.IV = C", "current_assets",
     ["inventories", "receivables", "short_term_financial_assets", "cash"], "error"),
    ("Receivables: C.II.1 + C.II.2 + C.II.3 = C.II", "receivables",
     ["receivables_long_term", "receivables_short_term", "accruals_in_receivables"], "error"),
    ("Liabilities and equity: A + (B + C) + D = total", "total_liabilities_equity",
     ["equity", "external_sources", "accruals_liabilities"], "error"),
    ("External sources: B + C = B + C total", "external_sources", ["provisions", "liabilities"], "error"),
    ("Liabilities: C.I + C.II + C.III = C", "liabilities",
     ["liabilities_long_term", "liabilities_short_term", "accruals_in_liabilities"], "error"),
    ("Equity: A.I + … + A.V − A.VI = A", "equity",
     ["share_capital", "share_premium_capital_funds", "funds_from_profit", "retained_earnings",
      "profit_current_period", ("advance_profit_distribution", "lower")], "error"),
    # the form's own formula: * = I + II − A − B − C − D − E + III − F (ř. 01 + 02 − 03 − 07 − 08 − 09 − 14 + 20 − 24)
    ("Operating result: I + II − A − B − C − D − E + III − F", "operating_result",
     ["revenue_products_services", "revenue_goods", ("cost_of_sales", -1), ("change_in_inventory", -1),
      ("capitalization", "raise"), ("personnel_costs", -1), ("value_adjustments_operating", -1),
      "other_operating_income", ("other_operating_expenses", -1)], "error"),
    # the form's financial part (ř. 31 − 34 + 35 − 38 + 39 − 42 − 43 + 46 − 47 = 48)
    ("Financial result: IV − G + V − H + VI − I − J + VII − K", "financial_result",
     ["income_from_shares", ("cost_of_shares_sold", -1), "income_other_fixed_financial_assets",
      ("costs_other_fixed_financial_assets", -1), "interest_income", ("value_adjustments_financial", -1),
      ("interest_expense", -1), "other_financial_income", ("other_financial_expenses", -1)], "error"),
    ("Value adjustments: E.1 + E.2 + E.3 = E", "value_adjustments_operating",
     ["depreciation", "value_adjustments_inventories", "value_adjustments_receivables"], "error"),
    ("Cost of sales: A.1 + A.2 + A.3 = A", "cost_of_sales", ["cost_of_goods_sold", "materials_and_energy", "services"],
     "error"),
    ("Income from shares: IV.1 + IV.2 = IV", "income_from_shares",
     ["income_from_shares_group", "other_income_from_shares"], "error"),
    ("Income from other financial assets: V.1 + V.2 = V", "income_other_fixed_financial_assets",
     ["income_other_fixed_financial_assets_group", "other_income_other_fixed_financial_assets"], "error"),
    ("Interest income: VI.1 + VI.2 = VI", "interest_income",
     ["interest_income_group", "other_interest_income"], "error"),
    ("Interest expense: J.1 + J.2 = J", "interest_expense",
     ["interest_expense_group", "other_interest_expense"], "error"),
    ("Long-term liabilities: C.I.1 + … + C.I.9 = C.I", "liabilities_long_term",
     ["bonds_long_term", "bank_loans_long_term", "advances_received_long_term", "trade_payables_long_term",
      "bills_payable_long_term", "group_payables_long_term", "associate_payables_long_term",
      "deferred_tax_liability", "other_payables_long_term"], "error"),
    ("Short-term liabilities: C.II.1 + … + C.II.8 = C.II", "liabilities_short_term",
     ["bonds_short_term", "bank_loans_short_term", "advances_received_short_term", "trade_payables_short_term",
      "bills_payable_short_term", "group_payables_short_term", "associate_payables_short_term",
      "other_payables_short_term"], "error"),
]  # fmt: skip

# Lines added to the dictionary after the first ground truth files were transcribed (Oct 2026). A ground truth file
# without them did not transcribe them (rather than "not printed"), so a check that needs them does not run on it.
LATER_LINES = {
    "income_from_shares", "cost_of_shares_sold", "income_other_fixed_financial_assets",
    "costs_other_fixed_financial_assets", "interest_income", "value_adjustments_financial", "other_financial_income",
    "other_financial_expenses", "value_adjustments_inventories", "value_adjustments_receivables",
    "bonds_long_term", "advances_received_long_term", "bills_payable_long_term", "group_payables_long_term",
    "associate_payables_long_term", "deferred_tax_liability", "other_payables_long_term",
    "bonds_short_term", "advances_received_short_term", "bills_payable_short_term", "group_payables_short_term",
    "associate_payables_short_term", "other_payables_short_term",
    "cost_of_goods_sold", "materials_and_energy", "services", "income_from_shares_group", "other_income_from_shares",
    "income_other_fixed_financial_assets_group", "other_income_other_fixed_financial_assets",
    "interest_income_group", "other_interest_income", "interest_expense_group", "other_interest_expense",
}  # fmt: skip

# a part that is one of several lines of a section cannot be larger than the section (both printed positive)
PART_OF_CHECKS: list[tuple[str, str, str]] = [
    ("Trade receivables C.II.2.1 ≤ short-term receivables C.II.2", "trade_receivables_short_term",
     "receivables_short_term"),
    ("Trade receivables C.II.1.1 ≤ long-term receivables C.II.1", "trade_receivables_long_term",
     "receivables_long_term"),
]  # fmt: skip

# Some software prints the costs negative (Osobní náklady -1 230 126): then the printed lines simply add up to the
# total. For these checks a plain sum of the printed parts is accepted as well.
PLAIN_SUM_ACCEPTED = {
    "Operating result: I + II − A − B − C − D − E + III − F",
    "Financial result: IV − G + V − H + VI − I − J + VII − K",
}

# a = b (+ c …): both sides must be present. The 5th item says whether the check compares the same number
# printed twice (exact, no tolerance) or a sum of separately rounded lines (rounding tolerance).
EQUALITY_CHECKS: list[tuple[str, list[tuple[str, int]], list[tuple[str, int]], str, bool]] = [
    ("Balance: total assets = total liabilities and equity",
     [("total_assets", 1)], [("total_liabilities_equity", 1)], "error", True),
    ("Profit for the period (P&L) = A.V. in the balance sheet",
     [("profit_for_period", 1)], [("profit_current_period", 1)], "error", True),
    ("Profit before tax − income tax = profit after tax",
     [("profit_before_tax", 1), ("income_tax", -1)], [("profit_after_tax", 1)], "error", False),
    ("Operating result + financial result = profit before tax",
     [("operating_result", 1), ("financial_result", 1)], [("profit_before_tax", 1)], "error", False),
    ("Profit after tax = profit for the period (no transfer to partners, line M.)",
     [("profit_after_tax", 1)], [("profit_for_period", 1)], "warning", True),
]  # fmt: skip

NON_NEGATIVE = [
    "total_assets", "fixed_assets", "intangible_assets", "tangible_assets", "financial_fixed_assets",
    "current_assets", "inventories", "receivables", "cash", "share_capital", "revenue_products_services",
    "revenue_goods", "personnel_costs", "provisions", "liabilities",
]  # fmt: skip
YOY_WATCH = ["total_assets", "equity", "liabilities", "revenue_products_services", "cost_of_sales", "cash"]
YOY_LIMIT = 5.0  # +500 %


def _get(fs: FinancialStatements, key: str, period: Period) -> float | None:
    return fs.get(key, period)


def _close(a: float, b: float, tolerance: float) -> bool:
    return abs(a - b) <= tolerance + 1e-6


def _contribution(value: float, sign: Sign) -> float:
    if sign == "lower":
        return -abs(value)
    if sign == "raise":
        return abs(value)
    return value * sign  # type: ignore[operator]


def _untranscribed(fs: FinancialStatements, keys: Iterable[str]) -> bool:
    """A ground truth file made before LATER_LINES existed: the lines it lacks were not transcribed."""
    statement_keys = set(fs.balance_sheet) | set(fs.income_statement)
    ground_truth = any(v.source == "ground_truth" for item in fs.balance_sheet.values() for v in item.values())
    return ground_truth and any(k in LATER_LINES and k not in statement_keys for k in keys)


def _sum_checks(fs: FinancialStatements, tol: float, periods: Iterable[Period]) -> list[CheckResult]:
    out = []
    for name, total_key, parts, severity in SUM_CHECKS:
        signed = [(p, 1) if isinstance(p, str) else p for p in parts]
        if _untranscribed(fs, [p for p, _ in signed]):
            continue
        for period in periods:
            total = _get(fs, total_key, period)
            values = {p: (_get(fs, p, period), sign) for p, sign in signed}
            present = {p: (v, sign) for p, (v, sign) in values.items() if v is not None}
            if total is None or not present:
                continue
            actual = sum(_contribution(v, sign) for v, sign in present.values())
            if name in PLAIN_SUM_ACCEPTED and not _close(total, actual, tol):
                plain = sum(v for v, _ in present.values())
                if _close(total, plain, tol):
                    actual = plain  # costs printed negative
            out.append(
                CheckResult(
                    name=name,
                    period=period,
                    passed=_close(total, actual, tol),
                    severity=severity,  # type: ignore[arg-type]
                    expected=total,
                    actual=actual,
                    difference=actual - total,
                    fields=[total_key, *present],
                    message="" if _close(total, actual, tol) else f"sum of parts differs by {actual - total:,.0f} CZK",
                )
            )
    return out


def _equality_checks(fs: FinancialStatements, tol: float, periods: Iterable[Period]) -> list[CheckResult]:
    out = []
    for name, left, right, severity, exact in EQUALITY_CHECKS:
        check_tol = 0.0 if exact else tol
        for period in periods:
            lv = [(_get(fs, k, period), s) for k, s in left]
            rv = [(_get(fs, k, period), s) for k, s in right]
            if any(v is None for v, _ in lv + rv):
                continue
            a = sum(v * s for v, s in lv)  # type: ignore[operator]
            b = sum(v * s for v, s in rv)  # type: ignore[operator]
            out.append(
                CheckResult(
                    name=name,
                    period=period,
                    passed=_close(a, b, check_tol),
                    severity=severity,  # type: ignore[arg-type]
                    expected=b,
                    actual=a,
                    difference=a - b,
                    fields=[k for k, _ in left + right],
                    message="" if _close(a, b, check_tol) else f"sides differ by {a - b:,.0f} CZK",
                )
            )
    return out


def _net_checks(fs: FinancialStatements, tol: float) -> list[CheckResult]:
    """Net = Gross + Korekce for every asset line with a gross value.

    Korekce is usually printed negative (depreciation, impairment) and lowers the value. Some software prints it
    positive with the same meaning, and a positive korekce can also really raise the value (revaluation of
    shareholdings, e.g. Skyport 2025 B.III.: 12 357 + 9 905 = 22 262). Both readings are accepted:
    net = gross + korekce as printed, or net = gross − |korekce|. A swapped Brutto/Netto pair fails both."""
    out = []
    for key, item in fs.balance_sheet.items():
        gross, net = item.amount("gross"), item.amount("current")
        if gross is None or net is None:
            continue
        adj = item.amount("adjustment") or 0.0
        as_printed, reducing = gross + adj, gross - abs(adj)
        ok = _close(net, as_printed, tol) or _close(net, reducing, tol)
        expected = as_printed if _close(net, as_printed, tol) or adj < 0 else reducing
        out.append(
            CheckResult(
                name=f"Net = gross − adjustment ({key})",
                period="current",
                passed=ok,
                severity="error",
                expected=expected,
                actual=net,
                difference=net - expected,
                fields=[key],
                message="" if ok else "netto does not equal brutto plus korekce",
            )
        )
    return out


def _plausibility(fs: FinancialStatements) -> list[CheckResult]:
    out = []
    for key in NON_NEGATIVE:
        for period in ("current", "prior"):
            v = _get(fs, key, period)  # type: ignore[arg-type]
            if v is not None and v < 0:
                out.append(CheckResult(name=f"Negative value where none is expected ({key})", period=period,
                                       passed=False, severity="warning", actual=v, fields=[key],
                                       message="possibly a misread minus sign"))  # fmt: skip
    for key in YOY_WATCH:
        cur, prior = _get(fs, key, "current"), _get(fs, key, "prior")
        if cur is not None and prior not in (None, 0) and abs(cur - prior) / abs(prior) > YOY_LIMIT:  # type: ignore[arg-type]
            out.append(CheckResult(name=f"Year-over-year change above {YOY_LIMIT:.0%} ({key})", period="current",
                                   passed=False, severity="warning", expected=prior, actual=cur, fields=[key],
                                   message="unusual change, check the digits"))  # fmt: skip
    return out


def _ocr_quality(fs: FinancialStatements) -> list[CheckResult]:
    out = []
    for key, period, v in fs.flagged():
        reason = []
        if v.ocr_conf is not None and v.ocr_conf < 60:
            reason.append(f"OCR confidence {v.ocr_conf:.0f}")
        if v.match_score is not None and v.match_score < 86:
            reason.append(f"line match {v.match_score:.0f}")
        out.append(CheckResult(name=f"Value needs review ({key})", period=period if period != "adjustment" else None,
                               passed=False, severity="warning", actual=v.value, fields=[key],
                               message=", ".join(reason) or f"unclear digits {v.raw!r}"))  # fmt: skip
    return out


def _part_of_checks(fs: FinancialStatements, tol: float, periods: Iterable[Period]) -> list[CheckResult]:
    out = []
    for name, part_key, whole_key in PART_OF_CHECKS:
        for period in periods:
            part, whole = _get(fs, part_key, period), _get(fs, whole_key, period)
            if part is None or whole is None or part < 0 or whole < 0:
                continue
            passed = part <= whole + tol
            out.append(
                CheckResult(
                    name=name,
                    period=period,
                    passed=passed,
                    severity="error",
                    expected=whole,
                    actual=part,
                    difference=part - whole if not passed else 0.0,
                    fields=[part_key, whole_key],
                    message="" if passed else f"the part is larger than the whole by {part - whole:,.0f} CZK",
                )
            )
    return out


def validate(fs: FinancialStatements, tolerance_units: float = 1.0) -> ValidationReport:
    """Run every check. Sums of rounded lines may differ by `tolerance_units` of the printed unit (±1 tis. Kč by
    default); the same number printed twice (assets = liabilities, P&L result = A.V.) must match exactly.
    ±2 would let a misread last digit (57 553 for 57 555) pass."""
    unit = fs.metadata.unit_multiplier or 1
    tol = tolerance_units * unit
    periods: tuple[Period, ...] = ("current", "prior")
    checks = (
        _equality_checks(fs, tol, periods)
        + _sum_checks(fs, tol, periods)
        + _part_of_checks(fs, tol, periods)
        + _net_checks(fs, tol)
        + _plausibility(fs)
        + _ocr_quality(fs)
    )
    return ValidationReport(checks=checks, tolerance=tol)


def fields_in_failed_checks(report: ValidationReport) -> dict[tuple[str, str], list[str]]:
    """(field, period) -> names of failed error-level checks that involve it (input for CIPW-11)."""
    out: dict[tuple[str, str], list[str]] = {}
    for c in report.errors:
        for f in c.fields:
            out.setdefault((f, c.period or "current"), []).append(c.name)
    return out
