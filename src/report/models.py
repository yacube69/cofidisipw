"""Output of the AI assessment (CIPW-16): the structured report the LLM fills in.

The format is the one the mock report (`data/fixtures/mock_26161516_2025.json`) and the app already use; the
pipeline stores `AssessmentReport.model_dump()` in `Result.report`.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

RatingValue = Literal["low", "medium", "high"]


class ReportRating(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: RatingValue | None = Field(
        None, description="Overall risk. None when the data do not allow a rating (the reasoning says why)."
    )
    reasoning: str = Field(description="Why, in terms of the numbers and rules the rating follows.")


RecommendationLevel = Literal["recommended", "caution", "not_recommended", "no_recommendation"]


class Recommendation(BaseModel):
    """Indicative recommendation calculated from the risk rating by fixed rules, with the criteria behind it."""

    model_config = ConfigDict(extra="forbid")

    level: RecommendationLevel
    label: str = Field(description="The level in the language of the report.")
    criteria: list[str] = Field(default_factory=list, description="Why: the deciding rule and the figures it used.")


class AssessmentReport(BaseModel):
    """What an analyst reads: a short summary, what is good, what is risky, what is alarming, and the rating."""

    model_config = ConfigDict(extra="forbid")

    language: str = Field("en", description="Language of the text (ISO 639-1).")
    summary: str = Field(description="3 to 5 sentences.")
    strengths: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    red_flags: list[str] = Field(default_factory=list)
    rating: ReportRating
    recommendation: Recommendation | None = Field(
        None, description="Calculated by the code from the rating, not written by the LLM."
    )
    data_quality_note: str = Field(description="What an analyst must know about the reliability of the numbers.")
