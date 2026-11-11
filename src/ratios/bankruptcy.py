"""Bankruptcy models (CIPW-13): IN05 for Czech companies and Altman Z' for private companies, both years.

Same inputs and definitions as the ratio engine (CIPW-12): EBIT = profit before tax + interest expense,
revenue = I. + II., end-of-period balances, missing = None. Sources, coefficients, zones and the mapping of every
variable to the statement lines are in docs/ratios.md.

A score is None when an input is missing or a denominator is zero or negative; the flag and the reason say
which. A score from an input to review or in a failed accounting check is computed but flagged unreliable.

Every model is one `Model` in `MODELS` (CIPW-52): `compute_bankruptcy` computes them in that order, and the app
shows a card for each. Adding or swapping a model needs nothing else (docs/ratios.md, "Adding or swapping a model").
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from src.ratios.engine import ZERO_WHEN_MISSING, Inputs, Missing, NotMeaningful, _doubtful, positive
from src.ratios.models import Flag
from src.schema import FinancialStatements
from src.validation.models import ValidationReport

Zone = Literal["safe", "grey", "distress"]
IN05_V2_CAP = 9.0  # EBIT / interest is capped at 9 (Neumaierová & Neumaier 2005)


class Variable(BaseModel):
    key: str
    formula: str
    weight: float
    value: float | None = None


class Score(BaseModel):
    score: float | None = None
    probability: float | None = Field(None, description="Of bankruptcy within the model's horizon (logit/probit).")
    zone: Zone | None = None
    flag: Flag = "missing"
    reason: str | None = None
    variables: list[Variable] = Field(default_factory=list)
    inputs: dict[str, float | None] = Field(default_factory=dict)


class BankruptcyModel(BaseModel):
    key: str = Field(description="Key of the model in MODELS, e.g. 'in05'.")
    name: str
    source: str
    zones: str = Field(description="The interpretation zones in words.")
    horizon: str | None = Field(None, description="Period the probability is for, e.g. '12 months'.")
    current: Score = Field(default_factory=Score)
    prior: Score = Field(default_factory=Score)


class BankruptcyReport(BaseModel):
    models: list[BankruptcyModel] = Field(default_factory=list, description="In the order of MODELS.")

    @model_validator(mode="before")
    @classmethod
    def _one_field_per_model(cls, data: Any) -> Any:
        """Results saved before CIPW-52 hold one field per model ({"in05": ..., "altman_z_prime": ...})."""
        if isinstance(data, dict) and "models" not in data:
            return {"models": [m for m in data.values() if isinstance(m, dict) and "key" in m]}
        return data

    def get(self, key: str) -> BankruptcyModel | None:
        return next((m for m in self.models if m.key == key), None)


@dataclass(frozen=True)
class Term:
    key: str
    formula: str
    weight: float
    compute: Callable[[Inputs], float]


@dataclass(frozen=True)
class Model:
    """score = constant + Σ weight · term, read against two limits.

    `higher_is_safer`: a high score means a healthy firm (IN05, Altman); False for models where it means risk (e.g.
    Zmijewski, score above 0 = distress). The grey zone lies between `distress` and `safe` either way.
    `link`: a logit or probit model turns its score into the probability of bankruptcy within `horizon` (models
    that predict default in the next 12 months); the limits stay on the score (logit and probit: 0 = 50 %)."""

    key: str
    name: str
    source: str
    zones: str
    distress: float
    safe: float
    terms: tuple[Term, ...]
    constant: float = 0.0
    higher_is_safer: bool = True
    link: Literal["logit", "probit"] | None = None
    horizon: str | None = None

    def probability(self, score: float) -> float | None:
        if self.link == "logit":
            return 1 / (1 + math.exp(-score))
        if self.link == "probit":
            return 0.5 * (1 + math.erf(score / math.sqrt(2)))
        return None

    def zone(self, score: float) -> Zone:
        sign = 1 if self.higher_is_safer else -1
        if sign * score < sign * self.distress:
            return "distress"
        return "safe" if sign * score > sign * self.safe else "grey"


def _assets(i: Inputs) -> float:
    return positive(i.get("total_assets"), "total_assets")


def _external(i: Inputs) -> float:
    return positive(i.get("external_sources"), "external_sources", "no provisions and no liabilities")


def _interest_cover(i: Inputs) -> float:
    """EBIT / interest, capped at 9. Without interest a profitable firm gets the cap, a loss-making one 0: the
    cap rewards a firm that could pay interest, not one without loans that makes a loss."""
    ebit, interest = i.ebit(), i.get("interest_expense")
    if interest > 0:
        return min(ebit / interest, IN05_V2_CAP)
    i.notes.append(f"no interest expense: V2 = {IN05_V2_CAP:g} for a profit, 0 for a loss")
    return IN05_V2_CAP if ebit > 0 else 0.0


def _short_term(i: Inputs) -> float:
    # C.II. short-term liabilities already include C.II.2. bank loans (Vyhláška 500/2002 since 2016)
    return positive(i.get("liabilities_short_term"), "liabilities_short_term")


def _earned_capital(i: Inputs) -> float:
    """Retained earnings for Altman X2: the equity the firm earned, not the capital paid in.
    = A. equity − A.I. share capital − A.II. share premium and capital funds (= A.III. + A.IV. + A.V. − A.VI.)."""
    earned = i.get("equity") - i.get("share_capital") - i.get("share_premium_capital_funds")
    i.used["retained_earnings_altman"] = earned
    return earned


IN05 = Model(
    key="in05",
    name="IN05 (Neumaierová & Neumaier)",
    source="Neumaierová, I., Neumaier, I. (2005). Index IN05. In: Evropské finanční systémy, Brno: Masarykova "
    "univerzita, pp. 143–148.",
    zones="above 1.6 the firm creates value (safe), 0.9–1.6 grey zone, below 0.9 it destroys value (distress)",
    distress=0.9,
    safe=1.6,
    terms=(
        Term("V1", "total_assets / external_sources", 0.13, lambda i: _assets(i) / _external(i)),
        Term("V2", "EBIT / interest_expense (max 9)", 0.04, _interest_cover),
        Term("V3", "EBIT / total_assets", 3.97, lambda i: i.ebit() / _assets(i)),
        Term("V4", "revenue / total_assets", 0.21, lambda i: i.revenue() / _assets(i)),
        Term("V5", "current_assets / liabilities_short_term", 0.09, lambda i: i.get("current_assets") / _short_term(i)),
    ),
)

ALTMAN_Z_PRIME = Model(
    key="altman_z_prime",
    name="Altman Z′ (private companies)",
    source="Altman, E. I. (1983). Corporate Financial Distress. New York: Wiley; Altman, E. I. (2000). Predicting "
    "Financial Distress of Companies: Revisiting the Z-Score and ZETA Models. NYU Stern.",
    zones="above 2.9 safe, 1.23–2.9 grey zone, below 1.23 distress",
    distress=1.23,
    safe=2.9,
    terms=(
        Term("X1", "(current_assets − liabilities_short_term) / total_assets", 0.717,
             lambda i: (i.get("current_assets") - i.get("liabilities_short_term")) / _assets(i)),
        Term("X2", "(equity − share_capital − share_premium_capital_funds) / total_assets", 0.847,
             lambda i: _earned_capital(i) / _assets(i)),
        Term("X3", "EBIT / total_assets", 3.107, lambda i: i.ebit() / _assets(i)),
        Term("X4", "equity / external_sources", 0.420, lambda i: i.get("equity") / _external(i)),
        Term("X5", "revenue / total_assets", 0.998, lambda i: i.revenue() / _assets(i)),
    ),  # fmt: skip
)

MODELS: tuple[Model, ...] = (IN05, ALTMAN_Z_PRIME)  # computed and shown in this order


def _score(model: Model, fs: FinancialStatements, validation: ValidationReport | None, period: str) -> Score:
    inputs = Inputs(fs, period)
    variables = [Variable(key=t.key, formula=t.formula, weight=t.weight) for t in model.terms]
    exact = []
    try:
        for term, variable in zip(model.terms, variables, strict=True):
            exact.append(term.compute(inputs))
            variable.value = round(exact[-1], 4)
    except Missing as e:
        return Score(flag="missing", reason=str(e), variables=variables, inputs=inputs.used)
    except NotMeaningful as e:
        return Score(flag="not_meaningful", reason=str(e), variables=variables, inputs=inputs.used)
    score = round(model.constant + sum(t.weight * x for t, x in zip(model.terms, exact, strict=True)), 4)
    doubts = _doubtful(fs, validation, period, inputs.sources)
    reasons = doubts + list(dict.fromkeys(inputs.notes))
    zeros = sorted(k for k in ZERO_WHEN_MISSING if k in inputs.used and inputs.used[k] is None)
    if zeros:
        reasons.append(", ".join(zeros) + " not printed, counted as 0")
    probability = model.probability(score)
    return Score(
        score=score,
        probability=round(probability, 4) if probability is not None else None,
        zone=model.zone(score),
        flag="unreliable" if doubts else "ok",
        reason="; ".join(reasons) or None,
        variables=variables,
        inputs=inputs.used,
    )


def compute_bankruptcy(
    fs: FinancialStatements, validation: ValidationReport | None = None, models: Sequence[Model] = MODELS
) -> BankruptcyReport:
    """Every model (default: MODELS) for the current and the prior year."""
    return BankruptcyReport(
        models=[
            BankruptcyModel(
                key=model.key,
                name=model.name,
                source=model.source,
                zones=model.zones,
                horizon=model.horizon,
                current=_score(model, fs, validation, "current"),
                prior=_score(model, fs, validation, "prior"),
            )
            for model in models
        ]
    )
