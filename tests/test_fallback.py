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
    reasoning = report["rating"]["reasoning"]  # the rating module's, in the language of the report (R2)
    assert reasoning.startswith("Všechny modely bankrotu se skóre jsou v zóně ohrožení. ")
    assert "Skóre: IN05 0,55 (zóna ohrožení), Altman Z′ 0,61 (zóna ohrožení)." in reasoning
    assert reasoning.endswith("Vypočteno z hodnot označených ke kontrole.")
    assert rule_based_report(mondi, "en")["rating"]["reasoning"].startswith("All bankruptcy models with a score")
    assert "Běžná likvidita 0,22× (ověřit)" in report["summary"]


def test_no_warning_sign_is_low_risk(mondi):
    report = rule_based_report(_calm(mondi))
    assert report["rating"]["value"] == "low" and report["risks"] == [] and report["red_flags"] == []
    assert report["rating"]["reasoning"].startswith("Všechny modely bankrotu jsou v bezpečné zóně, období nekončí")
    assert report["strengths"]  # the green lights


def test_the_grey_zone_is_medium_risk(mondi):
    report = rule_based_report(_calm(mondi, zone="grey"))
    assert report["rating"]["value"] == "medium" and "nejsou všechny v bezpečné zóně" in report["rating"]["reasoning"]


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
    assert report["rating"]["value"] == "high"
    assert report["rating"]["reasoning"].startswith("Vlastní kapitál je záporný (-5,00 mil. Kč)")
    assert "Záporný vlastní kapitál (−5 000 tis. Kč)" in report["red_flags"]  # thin no-break space


def test_without_model_scores_the_risk_is_not_rated():
    result = Result.model_validate_json(ARZ.read_text(encoding="utf-8"))
    report = rule_based_report(result)
    assert report["rating"]["value"] is None
    assert report["rating"]["reasoning"].startswith("Nelze vypočítat žádné skóre bankrotu")
    assert report["summary"].startswith("Tržby")


def test_the_rating_is_the_rating_modules_for_the_same_data(mondi):
    """One module decides (CIPW-16): the rule-based report takes value and reasoning from rate()."""
    from src.report.inputs import assessment_input_from_result
    from src.report.rating import RatingConfig, rate

    for result in (mondi, Result.model_validate_json(ARZ.read_text(encoding="utf-8"))):
        for config in (None, RatingConfig(partial=True), RatingConfig(disabled_rules=["R9"])):
            for lang in ("cs", "en"):
                decision = rate(assessment_input_from_result(result), config, lang)
                rating = rule_based_report(result, lang, config)["rating"]
                assert rating == {"value": decision.value, "reasoning": decision.reasoning}


def test_a_model_in_distress_without_a_loss_is_medium_not_high(mondi):
    """A change from the old rule of this module (any model in distress = high): R3 needs a loss, as in rating.py."""
    result = _calm(mondi)
    result.bankruptcy.models[0].current.zone = "distress"
    result.statements.income_statement["profit_for_period"].current.value = 1_000_000
    assert rule_based_report(result)["rating"]["value"] == "medium"
    result.statements.income_statement["profit_for_period"].current.value = -1_000_000
    assert rule_based_report(result)["rating"]["value"] == "high"


def test_the_partial_rating_reaches_the_app_through_the_config():
    """Andel Czech has no income statement: no rating today, medium (R8, current ratio) with `partial` on."""
    from src.ratios.bankruptcy import compute_bankruptcy
    from src.ratios.engine import compute_ratios
    from src.report.rating import RatingConfig
    from src.schema import GroundTruth
    from src.validation.review import validate_and_review

    gt = GroundTruth.model_validate_json((ROOT / "data/ground_truth/09704086_2024.json").read_text(encoding="utf-8"))
    fs = gt.to_statements()
    validation, _ = validate_and_review(fs)
    ratios = compute_ratios(fs, validation)
    result = Result(file="andel.pdf", statements=fs, validation=validation, ratios=ratios,
                    bankruptcy=compute_bankruptcy(fs, validation))  # fmt: skip
    assert rule_based_report(result, "en", RatingConfig())["rating"]["value"] is None  # partial rating switched off
    assert rule_based_report(result)["rating"]["value"] == "medium"  # the default is the app's configuration
    partial = rule_based_report(result, "en", RatingConfig(partial=True))["rating"]
    assert partial["value"] == "medium" and partial["reasoning"].startswith("Partial rating from the balance sheet")
