"""Ratio engine (CIPW-12): financial ratios for both years from the validated statements, no LLM.

Every ratio keeps its formula, the input amounts (CZK) and a flag:
- ok: computed from values that read cleanly;
- unreliable: computed, but an input is flagged for review or sits in a failed accounting check;
- not_meaningful: the denominator is zero or negative (negative equity, no interest expense), value None;
- missing: an input is not in the statement, value None.

A value is not None exactly when the flag is ok or unreliable. Balances are end-of-period. See docs/ratios.md.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from src.ratios.models import Flag, Ratio, RatioReport, RatioValue
from src.schema import FinancialStatements
from src.validation.models import ValidationReport

PERIODS = ("current", "prior")

# lines that are one part of a sum: not printed means 0 when the rest of the sum is there
# (a manufacturer prints no revenue from goods, a service firm no inventories)
ZERO_WHEN_MISSING = {
    "revenue_products_services", "revenue_goods", "short_term_financial_assets", "inventories", "interest_expense"
}

# costs some statements print negative (the operating result check accepts both); their size is what counts
COSTS = {"interest_expense", "depreciation", "value_adjustments_operating"}

# when a line is not printed, the next one stands in for it (with a note)
FALLBACKS = {
    "profit_for_period": ["profit_after_tax", "profit_current_period"],
    "depreciation": ["value_adjustments_operating"],
}


class Missing(Exception):
    """An input the ratio needs is not in the statement."""


class NotMeaningful(Exception):
    """The ratio cannot be read for this company (zero or negative denominator)."""


@dataclass
class Inputs:
    """The amounts one ratio used in one period, with notes on how they were found."""

    fs: FinancialStatements
    period: str
    used: dict[str, float | None] = field(default_factory=dict)
    sources: set[str] = field(default_factory=set)  # statement fields read, for the reliability flag
    notes: list[str] = field(default_factory=list)

    def get(self, key: str) -> float:
        for candidate in [key, *FALLBACKS.get(key, [])]:
            amount = self.fs.get(candidate, self.period)  # type: ignore[arg-type]
            if amount is not None:
                if candidate != key:
                    self.notes.append(f"{key} not printed, {candidate} used")
                self.sources.add(candidate)
                amount = abs(amount) if candidate in COSTS else amount
                self.used[key] = amount
                return amount
        self.used[key] = None
        if key in ZERO_WHEN_MISSING:
            return 0.0
        raise Missing(f"{key} not found")

    def revenue(self) -> float:
        """Revenue = I. + II. (products and services, goods); missing only when neither line is printed."""
        products = self.fs.get("revenue_products_services", self.period)  # type: ignore[arg-type]
        goods = self.fs.get("revenue_goods", self.period)  # type: ignore[arg-type]
        if products is None and goods is None:
            self.used["revenue"] = None
            raise Missing("revenue not found (neither I. nor II.)")
        total = self.get("revenue_products_services") + self.get("revenue_goods")
        self.used["revenue"] = total
        return total

    def ebit(self) -> float:
        """EBIT = profit before tax + interest expense."""
        total = self.get("profit_before_tax") + self.get("interest_expense")
        self.used["ebit"] = total
        return total


def balance(amount: float, name: str) -> float:
    """A receivable or payable balance below zero (an overpayment shown as a minus) gives no days."""
    if amount < 0:
        raise NotMeaningful(f"{name} is negative")
    return amount


def positive(amount: float, name: str, why: str = "") -> float:
    if amount == 0:
        raise NotMeaningful(f"{name} is 0" + (f": {why}" if why else ""))
    if amount < 0:
        raise NotMeaningful(f"{name} is negative" + (f": {why}" if why else ""))
    return amount


@dataclass(frozen=True)
class Definition:
    key: str
    name: str
    group: str
    formula: str
    unit: str
    compute: Callable[[Inputs], float]


def _payables_basis(fs: FinancialStatements) -> str:
    """Trade payables when printed for every year that has payables, else all short-term liabilities
    (the same basis for both years, so the change is comparable)."""
    keys = ("trade_payables_short_term", "liabilities_short_term")
    years = [p for p in PERIODS if any(fs.get(k, p) is not None for k in keys)]  # type: ignore[arg-type]
    if years and all(fs.get(keys[0], p) is not None for p in years):  # type: ignore[arg-type]
        return "trade_payables_short_term"
    return "liabilities_short_term"


def definitions(fs: FinancialStatements) -> list[Definition]:
    payables = _payables_basis(fs)

    def liquidity(numerator: Callable[[Inputs], float]) -> Callable[[Inputs], float]:
        return lambda i: numerator(i) / positive(i.get("liabilities_short_term"), "liabilities_short_term")

    def payables_days(i: Inputs) -> float:
        if payables == "liabilities_short_term":
            i.notes.append("trade payables not printed: all short-term liabilities used")
        return balance(i.get(payables), payables) / positive(i.revenue(), "revenue") * 365

    return [
        Definition(
            "current_ratio", "Current ratio", "liquidity", "current_assets / liabilities_short_term", "x",
            liquidity(lambda i: i.get("current_assets")),
        ),
        Definition(
            "quick_ratio", "Quick ratio", "liquidity", "(current_assets - inventories) / liabilities_short_term", "x",
            liquidity(lambda i: i.get("current_assets") - i.get("inventories")),
        ),
        Definition(
            "cash_ratio", "Cash ratio", "liquidity", "(cash + short_term_financial_assets) / liabilities_short_term",
            "x", liquidity(lambda i: i.get("cash") + i.get("short_term_financial_assets")),
        ),
        Definition(
            "roa", "Return on assets", "profitability", "EBIT / total_assets", "%",
            lambda i: i.ebit() / positive(i.get("total_assets"), "total_assets") * 100,
        ),
        Definition(
            "roe", "Return on equity", "profitability", "profit_for_period / equity", "%",
            lambda i: i.get("profit_for_period") / positive(i.get("equity"), "equity", "the firm has lost its equity")
            * 100,
        ),
        Definition(
            "ros", "Return on sales", "profitability", "profit_for_period / revenue", "%",
            lambda i: i.get("profit_for_period") / positive(i.revenue(), "revenue") * 100,
        ),
        Definition(
            "ebitda_margin", "EBITDA margin", "profitability", "(operating_result + depreciation) / revenue", "%",
            lambda i: (i.get("operating_result") + i.get("depreciation")) / positive(i.revenue(), "revenue") * 100,
        ),
        Definition(
            "debt_ratio", "Debt ratio", "leverage", "external_sources / total_assets", "%",
            lambda i: i.get("external_sources") / positive(i.get("total_assets"), "total_assets") * 100,
        ),
        Definition(
            "debt_to_equity", "Debt to equity", "leverage", "external_sources / equity", "x",
            lambda i: i.get("external_sources") / positive(i.get("equity"), "equity", "the firm has lost its equity"),
        ),
        Definition(
            "interest_coverage", "Interest coverage", "leverage", "EBIT / interest_expense", "x",
            lambda i: i.ebit() / positive(i.get("interest_expense"), "interest_expense", "no interest to cover"),
        ),
        Definition(
            "receivables_days", "Receivables days", "activity", "receivables_short_term / revenue * 365", "days",
            lambda i: balance(i.get("receivables_short_term"), "receivables_short_term")
            / positive(i.revenue(), "revenue")
            * 365,
        ),
        Definition(
            "payables_days", "Payables days", "activity", f"{payables} / revenue * 365", "days", payables_days,
        ),
    ]


def _doubtful(
    fs: FinancialStatements, validation: ValidationReport | None, period: str, sources: set[str]
) -> list[str]:
    """Why a computed value may be wrong: inputs to review and failed accounting checks that involve them."""
    reasons = []
    for key in sorted(sources):
        value = fs.value(key, period)  # type: ignore[arg-type]
        if value is not None and value.needs_review:
            reasons.append(f"{key} needs review")
    if validation is not None:
        for check in validation.errors:
            if check.period in (period, None) and sources & set(check.fields):
                reasons.append(f"check failed: {check.name}")
    return list(dict.fromkeys(reasons))


def _value(
    definition: Definition, fs: FinancialStatements, validation: ValidationReport | None, period: str
) -> RatioValue:
    inputs = Inputs(fs, period)
    flag: Flag
    try:
        value: float | None = round(definition.compute(inputs), 4)
        doubts = _doubtful(fs, validation, period, inputs.sources)
        flag = "unreliable" if doubts else "ok"
        reasons = doubts + inputs.notes
    except Missing as e:
        value, flag, reasons = None, "missing", [str(e)]
    except NotMeaningful as e:
        value, flag, reasons = None, "not_meaningful", [str(e)]
    zeros = [k for k in ZERO_WHEN_MISSING if k in inputs.used and inputs.used[k] is None]
    if value is not None and zeros:
        reasons.append(", ".join(sorted(zeros)) + " not printed, counted as 0")
    return RatioValue(value=value, flag=flag, reason="; ".join(reasons) or None, inputs=inputs.used)


def compute_ratios(fs: FinancialStatements, validation: ValidationReport | None = None) -> RatioReport:
    """All ratios for the current and the prior year."""
    return RatioReport(
        ratios=[
            Ratio(
                key=d.key,
                name=d.name,
                group=d.group,  # type: ignore[arg-type]
                formula=d.formula,
                unit=d.unit,  # type: ignore[arg-type]
                current=_value(d, fs, validation, "current"),
                prior=_value(d, fs, validation, "prior"),
            )
            for d in definitions(fs)
        ]
    )
