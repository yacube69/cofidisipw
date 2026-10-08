"""`Result`: everything one pipeline run produces for one PDF (CIPW-40, used by the app, evaluation and demo)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from src.extraction.types import PageWords
from src.ratios.models import RatioReport
from src.schema import FinancialStatements
from src.validation.models import ValidationReport

StageStatus = Literal["ok", "stub", "skipped", "error"]


class PipelineOptions(BaseModel):
    read_mode: Literal["auto", "text", "ocr"] = Field(
        "auto", description="auto = text layer when it is clean, OCR otherwise."
    )
    ocr_dpi: int = 300
    skip_report: bool = Field(False, description="Run only the deterministic part, no LLM call.")
    pages: dict[str, list[int]] | None = Field(None, description="Override the page locator (1-based pages).")


class StageRun(BaseModel):
    stage: str
    status: StageStatus
    seconds: float = 0.0
    note: str | None = None


class LLMUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


class Result(BaseModel):
    tool_version: str = "0.1.0"
    file: str
    created_at: datetime = Field(default_factory=datetime.now)
    options: PipelineOptions = Field(default_factory=PipelineOptions)
    pages: dict[str, list[int]] = Field(default_factory=dict, description="Located statement pages, 1-based.")
    page_words: list[PageWords] = Field(default_factory=list)
    statements: FinancialStatements = Field(default_factory=FinancialStatements)
    validation: ValidationReport | None = None
    ratios: RatioReport | None = None
    bankruptcy: dict[str, Any] | None = None
    benchmarks: dict[str, Any] | None = None
    report: dict[str, Any] | None = None
    number_check: dict[str, Any] | None = None
    stages: list[StageRun] = Field(default_factory=list)
    llm_usage: LLMUsage = Field(default_factory=LLMUsage)
    errors: list[str] = Field(default_factory=list)

    @property
    def stubbed_stages(self) -> list[str]:
        return [s.stage for s in self.stages if s.status == "stub"]
