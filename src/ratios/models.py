"""Result types of the ratio engine (CIPW-12)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Flag = Literal["ok", "unreliable", "not_meaningful", "missing"]


class RatioValue(BaseModel):
    value: float | None = None
    flag: Flag = "missing"
    reason: str | None = None
    inputs: dict[str, float | None] = Field(default_factory=dict)


class Ratio(BaseModel):
    key: str
    name: str
    group: Literal["liquidity", "profitability", "leverage", "activity"]
    formula: str
    unit: Literal["x", "%", "days"]
    current: RatioValue = Field(default_factory=RatioValue)
    prior: RatioValue = Field(default_factory=RatioValue)

    @property
    def change(self) -> float | None:
        """Year-over-year change in the ratio's own unit (percentage points for %)."""
        if self.current.value is None or self.prior.value is None:
            return None
        return self.current.value - self.prior.value


class RatioReport(BaseModel):
    ratios: list[Ratio] = Field(default_factory=list)

    def get(self, key: str) -> Ratio:
        for r in self.ratios:
            if r.key == key:
                return r
        raise KeyError(key)
