"""`Result`: everything one pipeline run produces for one PDF (CIPW-40, used by the app, evaluation and demo)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, computed_field

from src.extraction.reread import RereadReport
from src.extraction.types import PageWords
from src.integrations.ares import Company, name_differs
from src.ratios.bankruptcy import BankruptcyReport
from src.ratios.models import RatioReport
from src.schema import FinancialStatements
from src.validation.models import ValidationReport
from src.version import package_version

StageStatus = Literal["ok", "stub", "skipped", "error"]


class PipelineOptions(BaseModel):
    read_mode: Literal["auto", "text", "ocr"] = Field(
        "auto", description="auto = text layer when it is clean, OCR otherwise."
    )
    ocr_dpi: int | None = Field(None, description="OCR resolution; None = settings.ocr.dpi (config/fides.yaml).")
    skip_report: bool = Field(False, description="Run only the deterministic part, no LLM call.")
    pages: dict[str, list[int]] | None = Field(None, description="Override the page locator (1-based pages).")
    scope: Literal["individual", "consolidated"] = Field(
        "individual", description="Which statements to read when the PDF has both (annual reports of groups)."
    )


class StageRun(BaseModel):
    stage: str
    status: StageStatus
    seconds: float = 0.0
    note: str | None = None


class Correction(BaseModel):
    """A value changed by hand in the app (CIPW-42); kept so every manual change is visible in the result."""

    key: str
    period: Literal["gross", "adjustment", "current", "prior"]
    old: float | None
    new: float | None
    by: str | None = None
    at: datetime = Field(default_factory=datetime.now)
    reason: str | None = None


class Audit(BaseModel):
    """What produced this result (CIPW-45): enough to reproduce it and to explain it."""

    run_id: str
    tool_version: str = Field(description="Fides package version (pyproject.toml).")
    git_commit: str | None = Field(None, description="Full commit hash of the code; None outside a git checkout.")
    git_describe: str | None = Field(None, description="Nearest tag and distance, e.g. v1.0.0-3-gabc1234.")
    git_dirty: bool | None = Field(None, description="The checkout had uncommitted changes.")
    settings_hash: str = Field(description="Fingerprint of the settings that change a result (src/settings.py).")
    settings: dict[str, Any] = Field(default_factory=dict, description="Those settings: OCR, LLM, offline.")
    dictionary_version: str = Field(description="config/line_items.yaml: its `version` and content hash.")
    prompt_version: str | None = Field(None, description="Content hash of the report prompt; None without a report.")
    model: str | None = Field(None, description="LLM used for this result; None when no LLM was called.")
    offline: bool = False
    ocr_config: str | None = Field(None, description="OCR configuration of the reading stage.")
    tesseract_version: str | None = None
    python_version: str
    started_at: datetime
    finished_at: datetime | None = None
    log_file: str | None = Field(None, description="JSON-lines log of this run, relative to the repository root.")
    corrections: list[Correction] = Field(default_factory=list)


class LLMUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


class Result(BaseModel):
    tool_version: str = Field(default_factory=package_version)
    file: str
    created_at: datetime = Field(default_factory=datetime.now)
    options: PipelineOptions = Field(default_factory=PipelineOptions)
    pages: dict[str, list[int]] = Field(default_factory=dict, description="Located statement pages, 1-based.")
    consolidation: Literal["individual", "consolidated"] | None = Field(
        None, description="Scope of the statements read; None when the pages were given in the options."
    )
    page_words: list[PageWords] = Field(default_factory=list)
    statements: FinancialStatements = Field(default_factory=FinancialStatements)
    company: Company | None = Field(None, description="The company in ARES by the IČO of the statement (CIPW-44).")
    validation: ValidationReport | None = None
    reread: RereadReport | None = Field(None, description="Cells read again because the checks doubted them (CIPW-11).")
    ratios: RatioReport | None = None
    bankruptcy: BankruptcyReport | None = None
    benchmarks: dict[str, Any] | None = None
    report: dict[str, Any] | None = None
    number_check: dict[str, Any] | None = None
    stages: list[StageRun] = Field(default_factory=list)
    llm_usage: LLMUsage = Field(default_factory=LLMUsage)
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list, description="Problems that did not stop a stage.")
    audit: Audit | None = Field(None, description="Filled by every pipeline run; None only in hand-made results.")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def name_source(self) -> Literal["ares", "statement"] | None:
        """Where the company name to show comes from (CIPW-56): ARES when it names the statement's company (its
        spelling is the registered one, a scan's OCR text may be misread), the statement when ARES names another
        company (a misread IČO, with a warning of the company stage) or does not know it."""
        printed, registered = self.statements.metadata.company_name, self.company.name if self.company else None
        if registered and not name_differs(printed, registered):
            return "ares"
        return "statement" if printed else None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def display_name(self) -> str | None:
        """The company name the app and the PDF report show; None when neither the statement nor ARES has one."""
        if self.name_source == "ares":
            return self.company.name if self.company else None
        return self.statements.metadata.company_name

    @property
    def stubbed_stages(self) -> list[str]:
        """Stages that returned mock data (data/fixtures/mock_26161516_2025.json) instead of real results."""
        return [s.stage for s in self.stages if s.status == "stub"]

    @property
    def failed(self) -> bool:
        return any(s.status == "error" for s in self.stages)
