"""Rule-based assessment (CIPW-16 fallback): the report's fields built from the numbers by fixed rules.

Used whenever no AI model is connected (offline mode, no API key) and until the LLM report of CIPW-16 is built. It
has the same fields as the AI report (summary, strengths, risks, red flags, rating with its reasoning, data quality
note) and `source: "rules"`, so the app and the PDF show it the same way and say where it came from. Every number in
it is the data's own, so it needs no number check (CIPW-17).

Rating: not decided here. `rate()` of `src/report/rating.py` is the one module that decides it (rules R1 to R9, over
every model of `src.ratios.bankruptcy.MODELS`); this module takes its value and its reasoning, in the language of the
report. R9 is the old rule of this module: 2 or more red ratios give high risk, 1 red or 3 or more amber ones at
least medium. The summary, strengths, risks and red flags are built here, from the same numbers.

A value computed from an input to review or in a failed check (flag `unreliable`) is still used, as a lender would
rather see a warning twice, but it is marked "ověřit" / "check" wherever the text names it.
"""

from __future__ import annotations

from typing import Any

from src.report.inputs import assessment_input_from_result
from src.report.pdf import LABELS, number, ratio_value
from src.report.rating import RatingConfig, rate
from src.result import Result
from src.settings import get_settings

TEXT = {
    "cs": {
        "revenue": "Tržby {cur} tis. Kč ({change} proti minulému roku)",
        "revenue_only": "Tržby {cur} tis. Kč",
        "profit": "výsledek hospodaření za účetní období {cur} tis. Kč",
        "liquidity": "Běžná likvidita {cr}, celková zadluženost {dr}.",
        "models": "Bankrotní modely: {models}.",
        "distress": "{name} v pásmu tísně ({score})",
        "negative_equity": "Záporný vlastní kapitál ({value} tis. Kč)",
        "loss": "Ztráta za účetní období ({value} tis. Kč)",
        "failed": "{n} účetních kontrol neprošlo: dotčené hodnoty ověřte proti výkazu",
        "no_input": "Chybí kontroly, ukazatele nebo modely bankrotu, hodnocení proto není určeno.",
        "note": "Hodnocení sestavila pevná pravidla z čísel výkazu; AI model není připojen.",
        "and": " a ",
    },
    "en": {
        "revenue": "Revenue CZK {cur} thousand ({change} on the prior year)",
        "revenue_only": "Revenue CZK {cur} thousand",
        "profit": "profit for the period CZK {cur} thousand",
        "liquidity": "Current ratio {cr}, debt ratio {dr}.",
        "models": "Bankruptcy models: {models}.",
        "distress": "{name} in the distress zone ({score})",
        "negative_equity": "Negative equity (CZK {value} thousand)",
        "loss": "Loss for the period (CZK {value} thousand)",
        "failed": "{n} accounting checks failed: verify those values against the statement",
        "no_input": "The checks, ratios or bankruptcy models are missing, so the risk is not rated.",
        "note": "Built by fixed rules from the statement's numbers; no AI model is connected.",
        "and": " and ",
    },
}


def _short(name: str) -> str:
    return name.split(" (")[0]  # "IN05 (Neumaierová & Neumaier)" -> "IN05"


def rule_based_report(result: Result, lang: str = "cs", rating_config: RatingConfig | None = None) -> dict[str, Any]:
    """The assessment by fixed rules; the rating and its reasoning come from `rate()` with `rating_config`, by
    default the app's (`report.rating` of the settings), so every caller rates the same."""
    rating_config = rating_config or get_settings().report.rating
    t, labels = TEXT[lang], LABELS[lang]
    fs = result.statements
    lights = {k: (v or {}).get("light") for k, v in ((result.benchmarks or {}).get("ratios") or {}).items()}
    ratios = {r.key: r for r in (result.ratios.ratios if result.ratios else [])}

    def checked(text: str, flag: str | None) -> str:
        return f"{text} ({labels['check']})" if flag == "unreliable" else text

    def ratio_text(key: str) -> str:
        r = ratios[key]
        return checked(f"{labels['ratio_names'].get(key, r.name)} {ratio_value(r.current.value, r.unit, lang)}",
                       r.current.flag)  # fmt: skip

    # summary: revenue and profit, liquidity and debt, the models
    sentences = []
    revenue = [fs.get(k) for k in ("revenue_products_services", "revenue_goods")]
    prior = [fs.get(k, "prior") for k in ("revenue_products_services", "revenue_goods")]
    profit = next((v for k in ("profit_for_period", "profit_after_tax", "profit_current_period")
                   if (v := fs.get(k)) is not None), None)  # fmt: skip
    first = []
    if any(v is not None for v in revenue):
        cur, pri = sum(v or 0 for v in revenue), sum(v or 0 for v in prior)
        if any(v is not None for v in prior) and pri:
            change = (cur - pri) / abs(pri) * 100
            first.append(t["revenue"].format(cur=number(cur / 1000, 0, lang),
                                             change=("+" if change > 0 else "") + number(change, 1, lang) + " %"))
        else:
            first.append(t["revenue_only"].format(cur=number(cur / 1000, 0, lang)))
    if profit is not None:
        first.append(t["profit"].format(cur=number(profit / 1000, 0, lang)))
    if first:
        sentence = t["and"].join(first)
        sentences.append(sentence[0].upper() + sentence[1:] + ".")
    if "current_ratio" in ratios and "debt_ratio" in ratios:
        cr, dr = ratios["current_ratio"], ratios["debt_ratio"]
        if cr.current.value is not None and dr.current.value is not None:
            sentences.append(t["liquidity"].format(
                cr=checked(ratio_value(cr.current.value, cr.unit, lang), cr.current.flag),
                dr=checked(ratio_value(dr.current.value, dr.unit, lang), dr.current.flag)))  # fmt: skip
    models = [m for m in (result.bankruptcy.models if result.bankruptcy else []) if m.current.score is not None]
    if models:
        sentences.append(t["models"].format(models=", ".join(
            f"{_short(m.name)} {number(m.current.score, 2, lang)} ({labels['zones'].get(m.current.zone, '–')}"
            + (f", {labels['check']})" if m.current.flag == "unreliable" else ")") for m in models)))  # fmt: skip

    # strengths and risks: the traffic lights; red flags: distress, negative equity, a loss, failed checks
    strengths = [ratio_text(k) for k, light in lights.items() if light == "green" and k in ratios]
    reds = [k for k, light in lights.items() if light == "red" and k in ratios]
    ambers = [k for k, light in lights.items() if light == "amber" and k in ratios]
    risks = [f"{ratio_text(k)} ({labels['lights']['red']})" for k in reds]
    risks += [f"{ratio_text(k)} ({labels['lights']['amber']})" for k in ambers]
    distress = [m for m in models if m.current.zone == "distress"]
    flags = [
        t["distress"].format(name=_short(m.name), score=number(m.current.score, 2, lang)
                             + (f"; {labels['check']}" if m.current.flag == "unreliable" else ""))  # fmt: skip
        for m in distress
    ]
    equity = fs.get("equity")
    if equity is not None and equity < 0:
        flags.append(t["negative_equity"].format(value=number(equity / 1000, 0, lang)))
    if profit is not None and profit < 0:
        flags.append(t["loss"].format(value=number(profit / 1000, 0, lang)))
    checks = result.validation.checks if result.validation else []
    failed = [c for c in checks if c.severity == "error" and not c.passed]
    if failed:
        flags.append(t["failed"].format(n=len(failed)))

    # the rating is the rating module's: the same rules for this report, the AI report, the app and the PDF
    try:
        decision = rate(assessment_input_from_result(result), rating_config, lang)
    except ValueError:  # a hand-made result without validation, ratios or bankruptcy: nothing to rate from
        decision = None
    rating = decision.value if decision else None
    reasoning = decision.reasoning if decision else TEXT[lang]["no_input"]

    return {
        "mock": False,
        "source": "rules",
        "language": lang,
        "summary": " ".join(sentences),
        "strengths": strengths,
        "risks": risks,
        "red_flags": flags,
        "rating": {"value": rating, "reasoning": reasoning},
        "data_quality_note": t["note"],
    }
