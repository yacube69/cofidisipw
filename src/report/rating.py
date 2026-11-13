"""Overall risk rating by rules (CIPW-16, step 4): a PROPOSAL, the team has not decided yet.

The ticket recommends a rating the rules calculate from the numbers (same numbers, same rating, every step
explainable) with the LLM only explaining it. This module is that proposal, written so the team can read it, run
it on the ground truth companies and change it. Nothing here calls an LLM.

The rules look at the current year of the assessment input (`build_assessment_input`):

| rule | condition | rating |
| --- | --- | --- |
| R1 | equity is negative | high |
| R2 | IN05 and Altman Z′ both in the distress zone | high |
| R3 | one model in the distress zone and a loss for the period | high |
| R4 | no bankruptcy score could be computed | no rating |
| R5 | both models safe, profit for the period not negative, reliable data | low |
| R6 | everything else | medium |
| R7 | partial rating only (off by default): no score and current ratio below 1 | high |
| R8 | partial rating only (off by default): no score and current ratio 1 or more | medium |

The team can change this without editing the code (`report.rating` in config/fides.yaml, see `RatingConfig`):
switch single rules off and allow a partial rating from the balance sheet alone for companies without a score.

"Reliable data" = no failed accounting check and no score computed from values flagged for review. Industry
benchmarks (CIPW-14) are not used yet; traffic lights can become part of the rules when they exist.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.report.models import RatingValue

RULES_VERSION = "proposal-1"
SWITCHABLE = ("R1", "R2", "R3", "R5")
MODELS = (("in05", "IN05"), ("altman_z_prime", "Altman Z′"))


class RatingConfig(BaseModel):
    """What the team can still change about the rules (CIPW-16, open questions for Cofidis)."""

    model_config = ConfigDict(extra="forbid")

    disabled_rules: list[str] = Field(
        default_factory=list,
        description=f"Rules left out, from {', '.join(SWITCHABLE)}; R4 and R6 always apply.",
    )
    partial: bool = Field(
        False,
        description="Companies without a bankruptcy score get a partial rating from the balance sheet alone "
        "(R7 high, R8 medium, never low) instead of no rating.",
    )

    @field_validator("disabled_rules")
    @classmethod
    def _known_rules(cls, rules: list[str]) -> list[str]:
        unknown = sorted(set(rules) - set(SWITCHABLE))
        if unknown:
            raise ValueError(f"cannot switch off {unknown}; choose from {list(SWITCHABLE)}")
        return sorted(set(rules))

    @property
    def is_default(self) -> bool:
        return not self.disabled_rules and not self.partial


class RatingDecision(BaseModel):
    value: RatingValue | None
    rules: list[str] = Field(description="Ids of the rules that decided, e.g. ['R2'].")
    reasoning: str
    rules_version: str = RULES_VERSION
    partial: bool = False  # the partial rating (R7, R8) was switched on when this was decided


def rules_table() -> str:
    """The rules above as a Markdown block, for the pages reviewers read."""
    return "\n".join(
        [
            f"**How the risk rating is decided** (rules `{RULES_VERSION}`, a proposal: "
            "Cofidis has not named its criteria yet). "
            "The risk rating (high risk is the worst, low risk the best) is calculated from the numbers by "
            "these rules, the first one that applies decides. The AI only writes the text and does not choose it.",
            "",
            "| rule | condition | risk rating |",
            "| --- | --- | --- |",
            "| R1 | equity is negative | high risk |",
            "| R2 | IN05 and Altman Z′ are both in the distress zone | high risk |",
            "| R3 | one of the two models is in the distress zone and the period ends with a loss | high risk |",
            "| R4 | no bankruptcy score could be computed (usually no income statement in the filing) | no rating |",
            "| R5 | both models are in the safe zone, the period is not a loss, all accounting checks passed and no "
            "score is computed from values flagged for review | low risk |",
            "| R6 | everything else | medium risk |",
            "",
            "Not part of the rules yet: debt levels, industry benchmarks.",
            "",
        ]
    )


def _score_text(label: str, score: dict[str, Any]) -> str:
    if score["score"] is None:
        return f"{label} not computable"
    return f"{label} {score['score']} ({score['zone']})"


def _current_ratio(data: dict[str, Any]) -> float | None:
    for ratio in data.get("ratios", []):
        if ratio["key"] == "current_ratio":
            return ratio["current"]["value"]
    return None


def rate(data: dict[str, Any], config: RatingConfig | None = None) -> RatingDecision:
    """Rating for the current year from an assessment input."""
    config = config or RatingConfig()
    on = lambda rule: rule not in config.disabled_rules  # noqa: E731
    current = {key: data["bankruptcy"][key]["current"] for key, _ in MODELS}
    texts = [_score_text(label, current[key]) for key, label in MODELS]
    scored = [s for s in current.values() if s["score"] is not None]
    zones = [s["zone"] for s in scored]
    doubtful_score = any(s["flag"] == "unreliable" for s in scored)
    equity = (data["key_figures"].get("equity") or {}).get("current")
    profit = (data["key_figures"].get("profit_for_period") or {}).get("current")
    checks_failed = not data["validation"]["passed"]
    scores_text = ", ".join(texts)
    caveat = " Computed from values flagged for review." if doubtful_score else ""

    version = RULES_VERSION + ("+custom" if config.disabled_rules else "") + ("+partial" if config.partial else "")

    def decision(value: RatingValue | None, rule: str, why: str) -> RatingDecision:
        return RatingDecision(
            value=value,
            rules=[rule],
            reasoning=f"{why} ({scores_text}).{caveat}",
            rules_version=version,
            partial=config.partial,
        )

    if on("R1") and equity is not None and equity < 0:
        return decision("high", "R1", f"Equity is negative ({equity:,.0f} CZK)")
    if on("R2") and len(zones) == 2 and all(z == "distress" for z in zones):
        return decision("high", "R2", "Both bankruptcy models are in the distress zone")
    if on("R3") and "distress" in zones and profit is not None and profit < 0:
        return decision(
            "high",
            "R3",
            f"A bankruptcy model is in the distress zone and the period ends with a loss ({profit:,.0f} CZK)",
        )
    if not scored:
        ratio = _current_ratio(data) if config.partial else None
        if ratio is not None:
            partial = "Partial rating from the balance sheet alone: no bankruptcy score could be computed"
            if ratio < 1:
                return decision("high", "R7", f"{partial}, and the current ratio is below 1 ({ratio:.2f})")
            return decision("medium", "R8", f"{partial}; the current ratio is {ratio:.2f}")
        return decision(None, "R4", "No bankruptcy score could be computed, so no rating is given")
    if on("R5") and len(zones) == 2 and all(z == "safe" for z in zones) and profit is not None and profit >= 0:
        if not checks_failed and not doubtful_score:
            return decision(
                "low",
                "R5",
                "Both bankruptcy models are in the safe zone, the period is not a loss "
                "and all accounting checks passed",
            )
    reasons = []
    if len(zones) < 2:
        reasons.append("only one bankruptcy model could be computed")
    elif not all(z == "safe" for z in zones):
        reasons.append("the bankruptcy models are not both in the safe zone")
    if profit is not None and profit < 0:
        reasons.append("the period ends with a loss")
    if checks_failed:
        reasons.append("an accounting check failed")
    if doubtful_score:
        reasons.append("a score is computed from values flagged for review")
    why = "; ".join(reasons) or "the signals are mixed"
    return decision("medium", "R6", f"No rule for high risk applies, but {why}, so the rating is not low")
