"""CIPW-14: traffic lights of the ratios and the industry benchmarks."""

import pytest

from src.integrations.ares import Company
from src.ratios.benchmarks import LIMITS, industry_benchmarks, industry_group, light, section_2008


@pytest.mark.parametrize(
    "key, value, expected",
    [
        # higher is better (%): green from 5, amber from 0
        ("roa", 5.0, "green"), ("roa", 4.99, "amber"), ("roa", 0.0, "amber"), ("roa", -0.1, "red"),
        # lower is better (%): green up to 60, amber up to 80
        ("debt_ratio", 60.0, "green"), ("debt_ratio", 80.0, "amber"), ("debt_ratio", 80.1, "red"),
        # liquidity: green 1.5–2.5, amber below down to 1.0 and above, red only below 1.0
        ("current_ratio", 2.0, "green"), ("current_ratio", 1.2, "amber"), ("current_ratio", 0.99, "red"),
        ("current_ratio", 3.87, "amber"),  # Metrostav 2025: very liquid, not a risk for a lender
        ("current_ratio", 100.0, "amber"), ("cash_ratio", 1.4, "amber"), ("quick_ratio", 0.69, "red"),
    ],
)  # fmt: skip
def test_light(key, value, expected):
    assert light(key, value, "ok") == expected


def test_no_light_without_a_reliable_value_or_a_limit():
    assert light("roa", None, "missing") is None
    assert light("roa", 7.0, "unreliable") is None  # an input to review or a failed check
    assert light("roe", 12.0, "not_meaningful") is None
    assert light("receivables_days", 40.0, "ok") is None and "receivables_days" not in LIMITS


def test_liquidity_above_the_range_is_never_red():
    for key in ("current_ratio", "quick_ratio", "cash_ratio"):
        green_max = LIMITS[key][2]
        assert [light(key, green_max * f, "ok") for f in (1.01, 2, 10)] == ["amber"] * 3


@pytest.mark.parametrize(
    "code, section",
    [("01110", "A"), ("25620", "C"), ("35301", "D"), ("47110", "G"), ("62010", "J"), ("64190", "K"),
     ("68200", "L"), ("96090", "S"), (None, None), ("4", None)],
)  # fmt: skip
def test_section_of_a_cz_nace_2008_code(code, section):
    assert section_2008(code) == section


def test_the_industry_group_follows_cz_nace_2008():
    """MPO groups by CZ-NACE 2008; the 2025 code (and its section letter) is not used."""
    def company(nace, nace_2008):
        return Company(ico="26161516", nace=nace, nace_2008=nace_2008, source="snapshot")

    assert industry_group(company("17120", "17120")) == "C"
    assert industry_group(company("61100", "61100")) == "H-S"  # T-Mobile, telecom: section K in 2025, J in 2008
    assert industry_group(company("96990", "96090")) == "H-S"  # other services: T in 2025, S in 2008
    assert industry_group(company("17120", None)) is None and industry_group(None) is None


def test_industry_benchmarks_of_the_group():
    found = industry_benchmarks(Company(ico="26161516", nace_2008="17120", source="snapshot"))  # manufacturing
    assert set(found) == {"roa", "roe", "debt_ratio"}
    assert all(b["period"] == "2025-Q4" and b["sector"] == "Manufacturing" for b in found.values())
    assert industry_benchmarks(None) == {}
