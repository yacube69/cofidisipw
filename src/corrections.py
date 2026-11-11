"""Values a person confirms or corrects in the app (CIPW-42).

A value to review is looked at on the page and then either confirmed (the reading is right) or corrected (the right
amount typed, or one of the other readings picked). Either way it becomes `source: "manual"`: it is no longer flagged
for review, the checks never turn its sign, and a failed check no longer makes a ratio unreliable through it
(src/validation/review.py, src/ratios/engine.py). Every change is kept in `Result.audit.corrections`, and the
checks, ratios, bankruptcy models, traffic lights and the assessment are computed again (`recompute`).
"""

from __future__ import annotations

from src.pipeline import recompute
from src.result import Correction, Result
from src.schema import Period
from src.settings import Settings

CONFIRMED_BY = "a person in the app"


class NoSuchValue(KeyError):
    """The statement has no amount for this line and period."""


def correct(
    result: Result,
    key: str,
    period: Period,
    value: float | None,
    reason: str | None = None,
    by: str | None = None,
    settings: Settings | None = None,
) -> Result:
    """A copy of the result with this amount (in CZK) confirmed or corrected by hand, everything after the reading
    computed again. The same amount as before confirms it."""
    if result.audit is None:
        raise ValueError("a result without its audit block cannot record a correction")
    fixed = result.model_copy(deep=True)
    target = fixed.statements.value(key, period)
    if target is None:
        raise NoSuchValue(f"The statement has no {period} amount for {key!r}")
    old = target.value
    confirmed = old == value
    fixed.audit.corrections.append(  # type: ignore[union-attr]
        Correction(key=key, period=period, old=old, new=value, by=by,
                   reason=reason or ("confirmed" if confirmed else "corrected"))  # fmt: skip
    )
    target.value = value
    target.source = "manual"
    target.needs_review = False
    target.confirmed_by = CONFIRMED_BY
    target.note = "confirmed by hand" if confirmed else f"corrected by hand, read as {target.raw or old!r}"
    return recompute(fixed, settings)
