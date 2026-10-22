"""What the accounting checks say about each value (CIPW-10, CIPW-32).

The parser flags a value for review when OCR was unsure of a digit or the line label matched loosely. The checks
know more: a sum that adds up confirms every number in it, and a sum that does not points at the values to look
at. `validate_and_review` runs the checks and then

1. restores a minus OCR lost or misread ("-189" read as "189", "-7396" as "+7396"): when turning the sign of one
   value of a failed check makes that check pass, the value takes the sign (the note says so);
2. flags every value of a failed check for review;
3. clears the review flag of a value that only passing checks involve and one of them adds up exactly
   (`Value.confirmed_by` names it). Sums may differ by ±1 for rounding, but then a last digit read one off would
   pass too, so a rounded pass confirms nothing.
"""

from __future__ import annotations

from collections.abc import Iterator

from src.schema import FinancialStatements, Period, Value
from src.validation.checks import validate
from src.validation.models import CheckResult, ValidationReport

# lines that can be negative, so a lost minus is possible: results ("(+/-)" in the form) and equity
SIGNED = {
    "equity",
    "share_premium_capital_funds",
    "retained_earnings",
    "profit_current_period",
    "change_in_inventory",
    "capitalization",
    "value_adjustments_operating",  # E., E.2., E.3., I.: a reversal of an impairment is negative
    "value_adjustments_inventories",
    "value_adjustments_receivables",
    "value_adjustments_financial",
    "operating_result",
    "financial_result",
    "profit_before_tax",
    "profit_after_tax",
    "profit_for_period",
}
MAX_SIGN_FIXES = 4

# the amounts of a line a check involves, by the period of the check
CHECK_PERIODS: dict[str | None, tuple[Period, ...]] = {
    "current": ("current",),
    "prior": ("prior",),
    "gross": ("gross",),
    None: ("current", "prior"),
}
NET_CHECK = "Net = gross"  # net = gross + korekce: brutto, korekce and netto of the current year


def periods_of(check: CheckResult) -> tuple[Period, ...]:
    return ("gross", "adjustment", "current") if check.name.startswith(NET_CHECK) else CHECK_PERIODS[check.period]


def _values(fs: FinancialStatements, key: str, periods: tuple[Period, ...]) -> Iterator[tuple[Period, Value]]:
    for period in periods:
        value = fs.value(key, period)
        if value is not None and value.value is not None:
            yield period, value


def restore_signs(fs: FinancialStatements, report: ValidationReport) -> tuple[ValidationReport, list[str]]:
    """Turn the sign of single values while that makes more checks pass. Returns the new report and notes."""
    notes: list[str] = []
    for _ in range(MAX_SIGN_FIXES):
        errors = len(report.errors)
        if not errors:
            break
        best: tuple[int, str, Value, ValidationReport] | None = None
        candidates = {
            (key, period, id(value)): (key, value)
            for check in report.errors
            for key in check.fields
            if key in SIGNED
            for period, value in _values(fs, key, periods_of(check))
            if value.value
        }
        for key, value in candidates.values():
            value.value = -value.value  # type: ignore[operator]
            trial = validate(fs)
            value.value = -value.value  # type: ignore[operator]
            if len(trial.errors) < errors and (best is None or len(trial.errors) < best[0]):
                best = (len(trial.errors), key, value, trial)
        if best is None:
            break
        _, key, value, report = best
        printed = value.raw or ""
        value.value = -value.value  # type: ignore[operator]
        value.note = f"sign restored: printed {printed!r}, the accounting checks pass with {value.value:,.0f}"
        notes.append(f"{key}: {value.note}")
    return report, notes


def review_by_checks(fs: FinancialStatements, report: ValidationReport) -> None:
    """Flag the values of failed checks; clear the flag of values that passing checks confirm."""
    failed: set[tuple[str, Period]] = set()
    confirmed: dict[tuple[str, Period], str] = {}
    for check in report.checks:
        if check.severity != "error":
            continue
        for key in check.fields:
            for period, _ in _values(fs, key, periods_of(check)):
                if not check.passed:
                    failed.add((key, period))
                elif abs(check.difference or 0) < 0.5:
                    confirmed.setdefault((key, period), check.name)
    for (key, period), name in confirmed.items():
        if (key, period) not in failed:
            value = fs.value(key, period)
            if value is not None:
                value.needs_review = False
                value.confirmed_by = name
    for key, period in failed:
        value = fs.value(key, period)
        if value is not None:
            value.needs_review = True
            value.confirmed_by = None


def validate_and_review(fs: FinancialStatements) -> tuple[ValidationReport, list[str]]:
    """Checks, restored signs and review flags; the report is computed again so its review notes match."""
    report, notes = restore_signs(fs, validate(fs))
    review_by_checks(fs, report)
    return validate(fs), notes
