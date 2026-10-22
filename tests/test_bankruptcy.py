"""CIPW-13: IN05 and Altman Z', checked against a hand calculation from two ground truth statements."""

import pytest

from src.ratios.bankruptcy import ALTMAN_Z_PRIME, IN05, compute_bankruptcy
from src.validation.checks import validate
from tests.test_checks import load

# By hand from the printed statements (tis. Kč), docs/ratios.md shows every variable.
# EBIT = profit before tax + interest; revenue = I. + II.; X2 uses equity − A.I. − A.II.
# Mondi 2025: A 29 457 798, CZ 21 486 058, EBIT 507 009 + 797 693 = 1 304 702, revenue 15 510 024,
#   OA 4 443 661, KZ 19 950 475, VK 7 971 740, A.I. 2 066 000, A.II. 59 183
#   V = 1.3710, 1.6356, 0.0443, 0.5265, 0.2227 -> IN05 0.5501
#   X = −0.5264, 0.1985, 0.0443, 0.3710, 0.5265 -> Z' 0.6096
# Skyport 2025: A 238 171, CZ 103 680, EBIT 69 387 + 443 = 69 830, revenue 529 345,
#   OA 203 632, KZ 85 003, VK 134 394, A.I. 2 000, A.II. 77 700
#   V = 2.2972, 9 (157.6 capped), 0.2932, 2.2225, 2.3956 -> IN05 2.5049
#   X = 0.4981, 0.2296, 0.2932, 1.2962, 2.2225 -> Z' 4.2251
HAND = {
    ("26161516_2025", "current"): (0.5501, "distress", 0.6096, "distress"),
    ("26161516_2025", "prior"): (0.7028, "distress", 0.6546, "distress"),
    ("07880383_2025", "current"): (2.5049, "safe", 4.2251, "safe"),
    ("07880383_2025", "prior"): (1.5610, "grey", 2.5290, "grey"),
}


@pytest.mark.parametrize(("stem", "period"), list(HAND), ids=lambda x: str(x))
def test_scores_match_the_hand_calculation(stem, period):
    fs = load(stem)
    report = compute_bankruptcy(fs, validate(fs))
    in05, in05_zone, z, z_zone = HAND[(stem, period)]
    assert getattr(report.in05, period).score == pytest.approx(in05, abs=1e-4)
    assert getattr(report.in05, period).zone == in05_zone
    assert getattr(report.altman_z_prime, period).score == pytest.approx(z, abs=1e-4)
    assert getattr(report.altman_z_prime, period).zone == z_zone
    assert getattr(report.in05, period).flag == "ok"


def test_every_variable_is_shown():
    report = compute_bankruptcy(load())
    assert [v.key for v in report.in05.current.variables] == ["V1", "V2", "V3", "V4", "V5"]
    assert [v.value for v in report.altman_z_prime.current.variables] == pytest.approx(
        [-0.5264, 0.1985, 0.0443, 0.3710, 0.5265], abs=1e-4
    )
    assert report.in05.current.inputs["ebit"] == pytest.approx(1_304_702_000)
    assert "Neumaierová" in report.in05.source and "Altman" in report.altman_z_prime.source


def test_zone_limits():
    assert [IN05.zone(s) for s in (0.89, 0.9, 1.6, 1.61)] == ["distress", "grey", "grey", "safe"]
    assert [ALTMAN_Z_PRIME.zone(s) for s in (1.22, 1.23, 2.9, 2.91)] == ["distress", "grey", "grey", "safe"]


def test_a_missing_input_gives_no_score():
    fs = load()
    del fs.balance_sheet["current_assets"]
    report = compute_bankruptcy(fs)
    for model in (report.in05, report.altman_z_prime):
        assert model.current.score is None and model.current.zone is None
        assert model.current.flag == "missing" and model.current.reason == "current_assets not found"


def test_zero_total_assets_is_not_meaningful():
    fs = load()
    fs.balance_sheet["total_assets"].current.value = 0
    score = compute_bankruptcy(fs).in05.current
    assert score.score is None and score.flag == "not_meaningful" and "total_assets is 0" in score.reason


def test_without_interest_v2_is_capped_only_for_a_profit():
    fs = load()
    fs.income_statement["interest_expense"].current.value = 0
    v2 = compute_bankruptcy(fs).in05.current.variables[1]
    assert v2.value == 9.0  # profit before tax 507 009 > 0
    fs.income_statement["profit_before_tax"].current.value = -100_000_000
    score = compute_bankruptcy(fs).in05.current
    assert score.variables[1].value == 0.0 and "no interest expense" in score.reason


def test_negative_equity_lowers_altman_and_still_scores():
    fs = load()
    fs.balance_sheet["equity"].current.value = -500_000_000
    z = compute_bankruptcy(fs).altman_z_prime.current
    assert z.score is not None and z.variables[3].value < 0 and z.zone == "distress"


def test_a_value_to_review_makes_the_score_unreliable():
    fs = load()
    fs.balance_sheet["total_assets"].current.needs_review = True
    report = compute_bankruptcy(fs, validate(fs))
    assert report.in05.current.flag == "unreliable" and "total_assets needs review" in report.in05.current.reason
    assert report.in05.current.score == pytest.approx(0.5501, abs=1e-4)
