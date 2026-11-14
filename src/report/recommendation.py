"""Recommendation for the analyst, derived from the risk rating (CIPW-16): a PROPOSAL until Cofidis names its own.

The recommendation is not written by the AI. It follows the rating by a table the team can change
(`report.recommendation` in config/fides.yaml), and it comes with the criteria behind it: the rule that decided and
the figures it looked at, taken from the assessment input. The AI only writes the narrative.

| risk rating | recommendation (default) |
| --- | --- |
| low | recommended |
| medium | recommended with caution (further review) |
| high | not recommended |
| no rating | no recommendation (insufficient data) |
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from src.report.models import Recommendation
from src.report.rating import RatingDecision, rule_table_rows, short_name
from src.report.tables import _round, amount
from src.report.texts import texts

Level = Literal["recommended", "caution", "not_recommended", "no_recommendation"]

RULE_WHY = {
    "en": {
        "R1": "equity is negative",
        "R2": "all bankruptcy models with a score are in the distress zone",
        "R3": "a bankruptcy model is in the distress zone and the period ends with a loss",
        "R4": "no bankruptcy score could be computed",
        "R5": "all bankruptcy models are in the safe zone, the period is not a loss and all accounting checks passed",
        "R6": "no rule for high risk applies, but the signals do not allow low risk",
        "R7": "no bankruptcy score and the current ratio is below 1 (partial rating)",
        "R8": "no bankruptcy score, the current ratio is 1 or more (partial rating)",
        "R9": "ratios with a red or amber traffic light make the rating at least medium or high",
    },
    "cs": {
        "R1": "vlastní kapitál je záporný",
        "R2": "všechny modely bankrotu se skóre jsou v zóně ohrožení",
        "R3": "jeden model bankrotu je v zóně ohrožení a období končí ztrátou",
        "R4": "nelze vypočítat žádné skóre bankrotu",
        "R5": "všechny modely bankrotu jsou v bezpečné zóně, období není ztrátové a prošly všechny účetní kontroly",
        "R6": "neplatí žádné pravidlo pro vysoké riziko, ale signály nedovolují nízké riziko",
        "R7": "bez skóre bankrotu a běžná likvidita je pod 1 (částečné hodnocení)",
        "R8": "bez skóre bankrotu, běžná likvidita je 1 nebo více (částečné hodnocení)",
        "R9": "ukazatele s červeným nebo oranžovým semaforem určují hodnocení nejméně střední nebo vysoké",
    },
}


class RecommendationConfig(BaseModel):
    """Which recommendation each risk rating gets (open question for Cofidis)."""

    model_config = ConfigDict(extra="forbid")

    low: Level = Field("recommended", description="Recommendation for low risk.")
    medium: Level = Field("caution", description="Recommendation for medium risk.")
    high: Level = Field("not_recommended", description="Recommendation for high risk.")
    none: Level = Field("no_recommendation", description="Recommendation when no rating could be given.")


def criteria(data: dict[str, Any], decision: RatingDecision, language: str) -> list[str]:
    """Why: the deciding rule, the score of every bankruptcy model, equity, the result for the period and the checks."""
    t = texts(language)
    why = RULE_WHY.get(language.lower()[:2], RULE_WHY["en"])
    lines = [t["criteria_rule"].format(rule=decision.rules[0], why=why[decision.rules[0]])]
    flagged = False
    for model in data["bankruptcy"]:  # every model of src.ratios.bankruptcy.MODELS, as the input lists them
        label, score = short_name(model["name"]), model["current"]
        if score["score"] is None:
            lines.append(f"{label}: {t['not_computable']}")
            continue
        flagged = flagged or score["flag"] == "unreliable"
        number = _round(score["score"], 2).replace(".", t["decimal"])
        lines.append(t["criteria_model"].format(label=label, score=number, zone=t["zones"][score["zone"]]))
    equity = (data["key_figures"].get("equity") or {}).get("current")
    if equity is not None:
        lines.append(t["criteria_equity"].format(value=amount(equity, language)))
    profit = (data["key_figures"].get("profit_for_period") or {}).get("current")
    if profit is None:
        profit = (data["key_figures"].get("profit_current_period") or {}).get("current")
    if profit is not None:
        lines.append(t["criteria_profit"].format(value=amount(profit, language)))
    validation = data["validation"]
    if validation["passed"]:
        lines.append(t["criteria_checks_ok"].format(total=validation["checks_total"]))
    else:
        lines.append(
            t["criteria_checks_failed"].format(failed=validation["failed_count"], total=validation["checks_total"])
        )
    if flagged:
        lines.append(t["criteria_flagged"])
    return lines


def recommend(
    data: dict[str, Any], decision: RatingDecision, config: RecommendationConfig | None = None, language: str = "en"
) -> Recommendation:
    config = config or RecommendationConfig()
    level: Level = getattr(config, decision.value or "none")
    return Recommendation(
        level=level,
        label=texts(language)["levels"][level],
        criteria=criteria(data, decision, language),
    )


def rule_rows(
    language: str, partial: bool, disabled: list[str] | tuple[str, ...] = ()
) -> tuple[str, str, list[str], list[list[str]]]:
    """The rules of the rating as (title, intro, header, rows) in the language of the report."""
    t = texts(language)
    return t["rules_title"], t["rules_intro"], t["rules_header"], rule_table_rows(language, partial, disabled)
