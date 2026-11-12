"""Input of the AI assessment (CIPW-16): everything the report may say, as one JSON-ready dict.

The LLM never sees the PDF, only this summary of verified data: company, key figures of both years, ratios with
their reliability flags, bankruptcy scores, validation status and a data quality section. Everything here is
computed without an LLM, so it can be tested, and the report can be checked against it (CIPW-17).

All amounts are in CZK. Mock data never gets in: benchmarks from a stub stage are left out and said to be missing.
"""

from __future__ import annotations

from typing import Any

from src.ratios.bankruptcy import BankruptcyReport
from src.ratios.models import Ratio, RatioReport, RatioValue
from src.result import Result
from src.schema import FinancialStatements
from src.validation.models import ValidationReport

INPUT_VERSION = 1
MAX_LISTED = 15  # values flagged for review named one by one; the count says the rest

KEY_FIGURES: list[tuple[str, str]] = [
    ("total_assets", "Total assets"),
    ("fixed_assets", "Fixed assets"),
    ("current_assets", "Current assets"),
    ("inventories", "Inventories"),
    ("receivables_short_term", "Short-term receivables"),
    ("cash", "Cash"),
    ("equity", "Equity"),
    ("external_sources", "External sources (liabilities and provisions)"),
    ("liabilities_long_term", "Long-term liabilities"),
    ("liabilities_short_term", "Short-term liabilities"),
    ("bank_loans_long_term", "Long-term bank loans"),
    ("bank_loans_short_term", "Short-term bank loans"),
    ("revenue", "Revenue (products, services and goods)"),
    ("net_turnover", "Net turnover (printed at the end of the income statement)"),
    ("operating_result", "Operating result"),
    ("financial_result", "Financial result"),
    ("profit_before_tax", "Profit before tax"),
    ("profit_for_period", "Profit for the period"),
    # only when the income statement is not in the filing: the result printed in the balance sheet (line A.V.)
    ("profit_current_period", "Result for the period (balance sheet line A.V.; no income statement in the filing)"),
]


def _amount(fs: FinancialStatements, key: str, period: str) -> float | None:
    if key == "revenue":  # I. + II.; one of them is enough
        parts = [fs.get(k, period) for k in ("revenue_products_services", "revenue_goods")]  # type: ignore[arg-type]
        return sum(p for p in parts if p is not None) if any(p is not None for p in parts) else None
    return fs.get(key, period)  # type: ignore[arg-type]


def _doubted(fs: FinancialStatements, key: str, period: str) -> bool:
    keys = ("revenue_products_services", "revenue_goods") if key == "revenue" else (key,)
    return any((v := fs.value(k, period)) is not None and v.needs_review for k in keys)  # type: ignore[arg-type]


def _change_pct(current: float | None, prior: float | None) -> float | None:
    if current is None or prior is None or prior == 0:
        return None
    return round((current - prior) / abs(prior) * 100, 1)


def _key_figures(fs: FinancialStatements) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, label in KEY_FIGURES:
        current, prior = _amount(fs, key, "current"), _amount(fs, key, "prior")
        if current is None and prior is None:
            continue
        if key == "profit_current_period" and "profit_for_period" in out:
            continue
        out[key] = {
            "label": label,
            "current": current,
            "prior": prior,
            "change_pct": _change_pct(current, prior),
            "needs_review": _doubted(fs, key, "current") or _doubted(fs, key, "prior"),
        }
    return out


def _ratio_value(v: RatioValue) -> dict[str, Any]:
    return {"value": v.value, "flag": v.flag, "reason": v.reason}


def _ratios(report: RatioReport) -> list[dict[str, Any]]:
    out = []
    for r in report.ratios:
        change = r.change
        out.append(
            {
                "key": r.key,
                "name": r.name,
                "group": r.group,
                "unit": r.unit,
                "formula": r.formula,
                "current": _ratio_value(r.current),
                "prior": _ratio_value(r.prior),
                "change": None if change is None else round(change, 4),
            }
        )
    return out


def _bankruptcy(report: BankruptcyReport) -> dict[str, Any]:
    out = {}
    for model in (report.get("in05"), report.get("altman_z_prime")):
        out[model.key] = {
            "name": model.name,
            "zones": model.zones,
            **{
                period: {
                    "score": s.score,
                    "zone": s.zone,
                    "flag": s.flag,
                    "reason": s.reason,
                }
                for period, s in (("current", model.current), ("prior", model.prior))
            },
        }
    return out


def _benchmarks(benchmarks: dict[str, Any] | None) -> dict[str, Any]:
    if not benchmarks or benchmarks.get("mock"):
        return {"available": False}
    return {"available": True, **{k: v for k, v in benchmarks.items() if k != "mock"}}


def _validation(report: ValidationReport) -> dict[str, Any]:
    return {
        "passed": report.passed,
        "checks_total": len(report.checks),
        "failed": [{"name": c.name, "period": c.period, "message": c.message} for c in report.errors[:MAX_LISTED]],
        "failed_count": len(report.errors),
        "warnings_count": len(report.warnings),
    }


def _unreliable(ratios: list[Ratio]) -> list[str]:
    return sorted({r.key for r in ratios if "unreliable" in (r.current.flag, r.prior.flag)})


def _not_computable(ratios: list[Ratio]) -> list[str]:
    bad = ("missing", "not_meaningful")
    return sorted({r.key for r in ratios if r.current.flag in bad})


def _data_quality(
    fs: FinancialStatements,
    ratios: RatioReport,
    bankruptcy: BankruptcyReport,
    validation: ValidationReport,
    benchmarks: dict[str, Any],
) -> dict[str, Any]:
    notes: list[str] = []
    flagged = fs.flagged()
    if flagged:
        named = ", ".join(f"{k} ({p})" for k, p, _ in flagged[:MAX_LISTED])
        more = f" and {len(flagged) - MAX_LISTED} more" if len(flagged) > MAX_LISTED else ""
        notes.append(f"{len(flagged)} values are flagged for review: {named}{more}")
    if validation.errors:
        notes.append(f"{len(validation.errors)} accounting checks failed")
    unreliable = _unreliable(ratios.ratios)
    if unreliable:
        notes.append("ratios computed from doubtful values: " + ", ".join(unreliable))
    missing = _not_computable(ratios.ratios)
    if missing:
        notes.append("ratios that could not be computed for the current year: " + ", ".join(missing))
    scores = [m.current for m in (bankruptcy.get("in05"), bankruptcy.get("altman_z_prime"))]
    if any(s.score is None for s in scores):
        notes.append("a bankruptcy model could not be computed for the current year")
    elif any(s.flag == "unreliable" for s in scores):
        notes.append("a bankruptcy score is computed from doubtful values")
    if fs.metadata.scope == "abbreviated":
        notes.append("abbreviated statements: fewer lines than a full statement")
    if not benchmarks["available"]:
        notes.append("no industry benchmarks: ratios are not compared with the industry")
    serious = bool(validation.errors) or all(s.score is None for s in scores)
    doubtful = bool(flagged or unreliable or missing) or any(s.flag == "unreliable" for s in scores)
    return {"overall": "poor" if serious else "limited" if doubtful else "good", "notes": notes}


def build_assessment_input(
    fs: FinancialStatements,
    validation: ValidationReport,
    ratios: RatioReport,
    bankruptcy: BankruptcyReport,
    benchmarks: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The summary the LLM writes the assessment from. JSON-ready (numbers, strings, lists, None)."""
    meta = fs.metadata
    bench = _benchmarks(benchmarks)
    return {
        "version": INPUT_VERSION,
        "company": {
            "name": meta.company_name,
            "ico": meta.ico,
            "period_end": meta.period_end.isoformat() if meta.period_end else None,
            "prior_period_end": meta.prior_period_end.isoformat() if meta.prior_period_end else None,
            "statements": meta.scope,
            "accounting_standard": meta.accounting_standard,
            "currency": meta.currency,
            "amounts_in": "CZK",
        },
        "key_figures": _key_figures(fs),
        "ratios": _ratios(ratios),
        "bankruptcy": _bankruptcy(bankruptcy),
        "benchmarks": bench,
        "validation": _validation(validation),
        "data_quality": _data_quality(fs, ratios, bankruptcy, validation, bench),
    }


def assessment_input_from_result(result: Result) -> dict[str, Any]:
    """The same from a pipeline `Result`. Stub benchmarks (mock data) are not passed on."""
    missing = [
        name
        for name, part in (
            ("validation", result.validation),
            ("ratios", result.ratios),
            ("bankruptcy", result.bankruptcy),
        )
        if part is None
    ]
    if missing:
        raise ValueError("cannot build the assessment input, missing: " + ", ".join(missing))
    benchmarks = None if "benchmarks" in result.stubbed_stages else result.benchmarks
    return build_assessment_input(
        result.statements,
        result.validation,  # type: ignore[arg-type]
        result.ratios,  # type: ignore[arg-type]
        result.bankruptcy,  # type: ignore[arg-type]
        benchmarks,
    )
