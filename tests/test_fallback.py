"""CIPW-55: the rule-based assessment when no AI model is connected."""

from pathlib import Path

import pytest

from src.report.fallback import rule_based_report
from src.result import Result

ROOT = Path(__file__).resolve().parents[1]
MONDI = ROOT / "data/fixtures/result_26161516_2025.json"  # two models in distress, values to review, failed checks
ARZ = ROOT / "data/fixtures/result_26701529_2024.json"  # abbreviated statement: the models have no score


@pytest.fixture
def mondi() -> Result:
    return Result.model_validate_json(MONDI.read_text(encoding="utf-8"))


def _calm(result: Result, zone: str = "safe") -> Result:
    """No warning sign: every model in one zone and every ratio green, so a test can add one sign."""
    for m in result.bankruptcy.models:
        m.current.zone, m.current.flag = zone, "ok"
    for value in result.benchmarks["ratios"].values():
        if value.get("light"):
            value["light"] = "green"
    result.validation.checks = [c for c in result.validation.checks if c.passed or c.severity != "error"]
    return result


def test_same_fields_as_the_ai_report_and_marked_as_rules(mondi):
    report = rule_based_report(mondi)
    assert set(report) >= {"summary", "strengths", "risks", "red_flags", "rating", "data_quality_note"}
    assert report["source"] == "rules" and report["mock"] is False and report["language"] == "cs"
    assert "AI model není připojen" in report["data_quality_note"]
    assert rule_based_report(mondi, "en")["data_quality_note"].endswith("no AI model is connected.")


def test_a_model_in_distress_is_high_risk_and_a_red_flag(mondi):
    report = rule_based_report(mondi)
    assert report["rating"]["value"] == "high"
    assert "IN05 v pásmu tísně (0,55; ověřit)" in report["red_flags"]  # a score from values to review says so
    assert any("účetních kontrol neprošlo" in f for f in report["red_flags"])
    assert "skóre z hodnot k ověření: IN05" in report["rating"]["reasoning"]
    assert "Běžná likvidita 0,22× (ověřit)" in report["summary"]


def test_no_warning_sign_is_low_risk(mondi):
    report = rule_based_report(_calm(mondi))
    assert report["rating"]["value"] == "low" and report["risks"] == [] and report["red_flags"] == []
    assert report["rating"]["reasoning"] == "Bez varovných signálů v modelech a ukazatelích."
    assert report["strengths"]  # the green lights


def test_the_grey_zone_is_medium_risk(mondi):
    report = rule_based_report(_calm(mondi, zone="grey"))
    assert report["rating"]["value"] == "medium" and "v šedé zóně" in report["rating"]["reasoning"]


@pytest.mark.parametrize(("reds", "ambers", "rating"), [(1, 0, "medium"), (2, 0, "high"), (0, 2, "low"),
                                                         (0, 3, "medium")])  # fmt: skip
def test_red_and_amber_lights_set_the_rating(mondi, reds, ambers, rating):
    result = _calm(mondi)
    lit = [v for v in result.benchmarks["ratios"].values() if v.get("light")]
    for value in lit[:reds]:
        value["light"] = "red"
    for value in lit[reds : reds + ambers]:
        value["light"] = "amber"
    report = rule_based_report(result)
    assert report["rating"]["value"] == rating
    assert len(report["risks"]) == reds + ambers
    assert [r for r in report["risks"] if r.endswith("(slabé)")] == report["risks"][:reds]  # red ones first


def test_negative_equity_is_high_risk_and_a_red_flag(mondi):
    result = _calm(mondi)
    result.statements.balance_sheet["equity"].current.value = -5_000_000
    report = rule_based_report(result)
    assert report["rating"]["value"] == "high" and report["rating"]["reasoning"] == "Záporný vlastní kapitál."
    assert "Záporný vlastní kapitál (−5 000 tis. Kč)" in report["red_flags"]  # thin no-break space


def test_without_model_scores_the_risk_is_not_rated():
    result = Result.model_validate_json(ARZ.read_text(encoding="utf-8"))
    report = rule_based_report(result)
    assert report["rating"]["value"] is None
    assert "nemají skóre" in report["rating"]["reasoning"]
    assert report["summary"].startswith("Tržby")
