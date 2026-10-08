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

# total = sum(parts); a check runs when the total and at least one part are present (missing parts count as 0)
SUM_CHECKS: list[tuple[str, str, list[str], str]] = [
    # name, total, parts, severity
    ("Assets: A + B + C + D = total assets", "total_assets",
     ["receivables_subscribed_capital", "fixed_assets", "current_assets", "accruals_assets"], "error"),
    ("Fixed assets: B.I + B.II + B.III = B", "fixed_assets",
     ["intangible_assets", "tangible_assets", "financial_fixed_assets"], "error"),
    ("Current assets: C.I + C.II + C.III + C.IV = C", "current_assets",
     ["inventories", "receivables", "short_term_financial_assets", "cash"], "error"),
    ("Receivables: C.II.1 + C.II.2 = C.II", "receivables", ["receivables_long_term", "receivables_short_term"],
     "error"),
    ("Liabilities and equity: A + (B + C) + D = total", "total_liabilities_equity",
     ["equity", "external_sources", "accruals_liabilities"], "error"),
    ("External sources: B + C = B + C total", "external_sources", ["provisions", "liabilities"], "error"),
    ("Liabilities: C.I + C.II = C", "liabilities", ["liabilities_long_term", "liabilities_short_term"], "error"),
    # A.VI (advance profit distribution) and A.II–A.III details are not in the dictionary: warning only
    ("Equity: A.I + … + A.V = A", "equity",
     ["share_capital", "share_premium_capital_funds", "funds_from_profit", "retained_earnings",
      "profit_current_period"], "warning"),
]  # fmt: skip

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


def _sum_checks(fs: FinancialStatements, tol: float, periods: Iterable[Period]) -> list[CheckResult]:
    out = []
    for name, total_key, parts, severity in SUM_CHECKS:
        for period in periods:
            total = _get(fs, total_key, period)
            values = {p: _get(fs, p, period) for p in parts}
            present = {p: v for p, v in values.items() if v is not None}
            if total is None or not present:
                continue
            actual = sum(present.values())
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
    """Net = Gross − |Adjustment| for every asset line with a gross value (Korekce may be printed with either
    sign, but it always lowers the value; a swapped Brutto/Netto pair fails)."""
    out = []
    for key, item in fs.balance_sheet.items():
        gross, net = item.amount("gross"), item.amount("current")
        if gross is None or net is None:
            continue
        adj = item.amount("adjustment") or 0.0
        ok = _close(net, gross - abs(adj), tol)  # korekce always lowers the value, whatever sign is printed
        out.append(
            CheckResult(
                name=f"Net = gross − adjustment ({key})",
                period="current",
                passed=ok,
                severity="error",
                expected=gross - abs(adj),
                actual=net,
                difference=net - (gross - abs(adj)),
                fields=[key],
                message="" if ok else "netto does not equal brutto minus korekce",
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
