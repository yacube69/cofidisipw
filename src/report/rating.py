"""Overall risk rating by rules (CIPW-16, step 4): a PROPOSAL, Cofidis has not named its criteria yet.

The ticket recommends a rating the rules calculate from the numbers (same numbers, same rating, every step
explainable) with the LLM only explaining it. This is the ONE module that decides the rating: the AI report, the
rule-based assessment of the pipeline (`fallback.py`), the app and the PDF all take it from `rate()`. Nothing here
calls an LLM.

The rules look at the current year of the assessment input (`build_assessment_input`). They are written over the
list of bankruptcy models of the input, which is `src.ratios.bankruptcy.MODELS` (CIPW-52), so a model added or
replaced there (e.g. Cofidis' 12-month default model) takes part without a change here; no model is named.

| rule | condition | rating |
| --- | --- | --- |
| R1 | equity is negative | high |
| R2 | every model with a score is in the distress zone, and at least two have a score | high |
| R3 | a model is in the distress zone and the period ends with a loss | high |
| R4 | no model has a score | no rating |
| R5 | every model has a score and is safe, profit not negative, checks passed, reliable data | low |
| R6 | everything else | medium |
| R7 | partial rating only (`partial`): no score and current ratio below 1 | high |
| R8 | partial rating only (`partial`): no score and current ratio 1 or more | medium |
| R9 | red ratios (traffic lights of CIPW-14): 2 or more red, or 1 red or 3 or more amber, see below | high / medium |

R1 to R8: the first rule that applies decides. R9 is checked afterwards and can only make the rating worse: 2 or
more ratios with a red traffic light give high risk, 1 red ratio or 3 or more amber ratios give at least medium risk.
That is the rule `fallback.py` applied before this module decided, so the app keeps its signal. A model that cannot
be computed never makes the rating better: R5 needs a score from every model.

The team can change this without editing the code (`report.rating` in config/fides.yaml, see `RatingConfig`):
switch single rules off and allow a partial rating from the balance sheet alone for companies without a score.

"Reliable data" = no failed accounting check and no score computed from values flagged for review. Industry
benchmarks (CIPW-14) are not used, only the project's own traffic lights.

The reasoning is written in the language asked for (`cs` or `en`, the texts of `src/report/texts.py`).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.report.models import RatingValue
from src.report.tables import _local, _round, amount
from src.report.texts import texts

RULES_VERSION = "proposal-2"
SWITCHABLE = ("R1", "R2", "R3", "R5", "R9")
SEVERITY = {None: 0, "low": 1, "medium": 2, "high": 3}  # for R9: it only ever raises the rating


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
    disabled_rules: list[str] = Field(default_factory=list, description="Rules switched off when this was decided.")


def rule_order(partial: bool = False, disabled: list[str] | tuple[str, ...] = ()) -> list[str]:
    """The rules in the order they are read (R9 last), without the ones switched off."""
    order = ["R1", "R2", "R3"] + (["R7", "R8"] if partial else []) + ["R4", "R5", "R6", "R9"]
    return [rule for rule in order if rule not in disabled]


def rule_table_rows(
    language: str, partial: bool = False, disabled: list[str] | tuple[str, ...] = ()
) -> list[list[str]]:
    """[rule, condition, risk rating] per rule, in the language of the report."""
    t = texts(language)
    rows = []
    for rule in rule_order(partial, disabled):
        condition, outcome = t["rules"]["R4_partial" if rule == "R4" and partial else rule]
        rows.append([rule, condition, outcome])
    return rows


def rules_table(partial: bool = False, disabled: list[str] | tuple[str, ...] = ()) -> str:
    """The rules as a Markdown block, for the pages reviewers read (R7 and R8 with the partial rating)."""
    t = texts("en")
    lines = [
        f"**{t['rules_title']}** (rules `{RULES_VERSION}`, a proposal: Cofidis has not named its criteria yet). "
        + t["rules_intro"],
        "",
        "| " + " | ".join(t["rules_header"]) + " |",
        "| --- | --- | --- |",
        *("| " + " | ".join(row) + " |" for row in rule_table_rows("en", partial, disabled)),
        "",
        "Not part of the rules yet: debt levels, industry benchmarks.",
        "",
    ]
    return "\n".join(lines)


def short_name(name: str) -> str:
    """'IN05 (Neumaierová & Neumaier)' -> 'IN05': the name a text uses."""
    return name.split(" (")[0]


def _current_ratio(data: dict[str, Any]) -> float | None:
    for ratio in data.get("ratios", []):
        if ratio["key"] == "current_ratio":
            return ratio["current"]["value"]
    return None


def _lights(data: dict[str, Any]) -> tuple[int, int]:
    """(red, amber) ratios of the current year; inputs without lights (before version 2) have none."""
    lights = [ratio.get("light") for ratio in data.get("ratios", [])]
    return lights.count("red"), lights.count("amber")


def rate(data: dict[str, Any], config: RatingConfig | None = None, language: str = "en") -> RatingDecision:
    """Rating for the current year from an assessment input, with its reasoning in `language` ('en' or 'cs')."""
    config = config or RatingConfig()
    t = texts(language)
    why = t["reasoning"]

    def on(rule: str) -> bool:
        return rule not in config.disabled_rules

    def number(value: float) -> str:
        return _local(_round(value, 2), t)

    def money(value: float) -> str:
        return why["money"].format(value=amount(value, language))

    models = [(short_name(m["name"]), m["current"]) for m in data["bankruptcy"]]
    scored = [(name, s) for name, s in models if s["score"] is not None]
    zones = [s["zone"] for _, s in scored]
    doubtful_score = any(s["flag"] == "unreliable" for _, s in scored)
    equity = (data["key_figures"].get("equity") or {}).get("current")
    profit = (data["key_figures"].get("profit_for_period") or {}).get("current")
    checks_failed = not data["validation"]["passed"]
    scores_text = ", ".join(
        f"{name} {number(s['score'])} ({t['zones'][s['zone']]})" if s["score"] is not None
        else f"{name} {t['not_computable']}"
        for name, s in models
    )  # fmt: skip
    caveat = why["caveat"] if doubtful_score else ""
    version = RULES_VERSION + ("+custom" if config.disabled_rules else "") + ("+partial" if config.partial else "")

    def base() -> tuple[RatingValue | None, str, str]:
        """R1 to R8, the first that applies: (rating, rule, why)."""
        if on("R1") and equity is not None and equity < 0:
            return "high", "R1", why["R1"].format(value=money(equity))
        if on("R2") and len(zones) >= 2 and all(z == "distress" for z in zones):
            return "high", "R2", why["R2"].format(models=", ".join(name for name, _ in scored))
        if on("R3") and "distress" in zones and profit is not None and profit < 0:
            distressed = ", ".join(name for name, s in scored if s["zone"] == "distress")
            return "high", "R3", why["R3"].format(models=distressed, value=money(profit))
        if not scored:
            ratio = _current_ratio(data) if config.partial else None
            if ratio is None:
                return None, "R4", why["R4"]
            if ratio < 1:
                return "high", "R7", why["R7"].format(ratio=number(ratio))
            return "medium", "R8", why["R8"].format(ratio=number(ratio))
        all_safe = len(scored) == len(models) and all(z == "safe" for z in zones)
        if on("R5") and all_safe and profit is not None and profit >= 0 and not checks_failed and not doubtful_score:
            return "low", "R5", why["R5"]
        reasons = []
        if len(scored) < len(models):
            reasons.append(why["why_uncomputed"])
        if not all(z == "safe" for z in zones):
            reasons.append(why["why_not_safe"])
        if profit is not None and profit < 0:
            reasons.append(why["why_loss"])
        if checks_failed:
            reasons.append(why["why_checks"])
        if doubtful_score:
            reasons.append(why["why_doubtful"])
        return "medium", "R6", why["R6"].format(why="; ".join(reasons) or why["why_mixed"])

    value, rule, text = base()
    reds, ambers = _lights(data)
    if on("R9") and (reds or ambers >= 3):  # never makes the rating better
        floor, floor_text = (
            ("high", why["R9_red2"].format(n=reds)) if reds >= 2
            else ("medium", why["R9_red1"]) if reds == 1
            else ("medium", why["R9_amber"].format(n=ambers))
        )  # fmt: skip
        if SEVERITY[floor] > SEVERITY[value]:
            value, rule, text = floor, "R9", floor_text  # type: ignore[assignment]
    return RatingDecision(
        value=value,
        rules=[rule],
        reasoning=f"{text} ({scores_text}).{caveat}",
        rules_version=version,
        partial=config.partial,
        disabled_rules=list(config.disabled_rules),
    )
