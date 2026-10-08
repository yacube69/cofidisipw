"""Result types of the accounting checks (CIPW-10)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Severity = Literal["error", "warning"]


class CheckResult(BaseModel):
    name: str
    period: Literal["current", "prior", "gross"] | None = None
    passed: bool
    severity: Severity = "error"
    expected: float | None = None
    actual: float | None = None
    difference: float | None = None
    fields: list[str] = Field(default_factory=list)
    message: str = ""


class ValidationReport(BaseModel):
    checks: list[CheckResult] = Field(default_factory=list)
    tolerance: float = 0.0

    @property
    def errors(self) -> list[CheckResult]:
        return [c for c in self.checks if not c.passed and c.severity == "error"]

    @property
    def warnings(self) -> list[CheckResult]:
        return [c for c in self.checks if not c.passed and c.severity == "warning"]

    @property
    def passed(self) -> bool:
        return not self.errors
