"""Traffic lights for the ratios and industry benchmarks (CIPW-14).

`light(key, value, flag)` rates one ratio against the project limits in `LIMITS` (temporary, until Cofidis confirms
its own, docs/ratios.md "Default thresholds"):

    higher   a higher value is better: green from `green`, amber from `amber`, red below
    lower    a lower value is better: green up to `green`, amber up to `amber`, red above
    range    liquidity: green inside [green_min, green_max], amber below it down to `amber_min` and ABOVE it, red
             only below `amber_min`. A firm with more liquidity than the usual range may keep idle funds (worth a
             look), but for a lender that is no risk, so it is never red.

A ratio that is missing, not meaningful or unreliable (an input to review, a failed check) gets no light.

`industry_benchmarks(company)` gives the MPO sector values (data/benchmarks.csv) for the company's industry. MPO
groups firms by CZ-NACE 2008 sections, so the section comes from the company's CZ-NACE 2008 code, not the 2025 one
(the 2025 classification moved most section letters from J on).
"""

from __future__ import annotations

import csv
from typing import Any, Literal

from src.integrations.ares import Company
from src.settings import ROOT

Light = Literal["green", "amber", "red"]

LIMITS: dict[str, tuple] = {
    "current_ratio": ("range", 1.5, 2.5, 1.0),  # green 1.5–2.5, amber 1.0–1.5 and above 2.5, red below 1.0
    "quick_ratio": ("range", 1.0, 1.5, 0.7),
    "cash_ratio": ("range", 0.2, 0.5, 0.1),
    "roa": ("higher", 5.0, 0.0),  # %: green from 5, amber from 0, red below 0
    "roe": ("higher", 10.0, 0.0),
    "ros": ("higher", 5.0, 0.0),
    "ebitda_margin": ("higher", 10.0, 0.0),
    "debt_ratio": ("lower", 60.0, 80.0),  # %: green up to 60, amber up to 80, red above 80
    "debt_to_equity": ("lower", 1.5, 3.0),
    "interest_coverage": ("higher", 3.0, 1.5),
}
SOURCE = "Temporary project limits, not confirmed by Cofidis"

BENCHMARKS = ROOT / "data" / "benchmarks.csv"
BENCHMARK_PERIOD = "2025-Q4"
SERVICES = "HIJKLMNOPQRS"  # MPO reports the service sections H to S of CZ-NACE 2008 as one group "H-S"
# CZ-NACE 2008 (NACE Rev. 2): section of each division, by the first two digits of the code
SECTIONS_2008 = [
    ("A", 1, 3), ("B", 5, 9), ("C", 10, 33), ("D", 35, 35), ("E", 36, 39), ("F", 41, 43), ("G", 45, 47),
    ("H", 49, 53), ("I", 55, 56), ("J", 58, 63), ("K", 64, 66), ("L", 68, 68), ("M", 69, 75), ("N", 77, 82),
    ("O", 84, 84), ("P", 85, 85), ("Q", 86, 88), ("R", 90, 93), ("S", 94, 96), ("T", 97, 98), ("U", 99, 99),
]  # fmt: skip


def light(key: str, value: float | None, flag: str) -> Light | None:
    """The traffic light of one ratio value, or None (no limit for it, or no reliable value)."""
    if value is None or flag != "ok" or key not in LIMITS:
        return None
    rule, *limits = LIMITS[key]
    if rule == "higher":
        green, amber = limits
        return "green" if value >= green else "amber" if value >= amber else "red"
    if rule == "lower":
        green, amber = limits
        return "green" if value <= green else "amber" if value <= amber else "red"
    green_min, green_max, amber_min = limits
    if green_min <= value <= green_max:
        return "green"
    return "amber" if value > green_max or value >= amber_min else "red"


def section_2008(code: str | None) -> str | None:
    """'25620' -> 'C': the CZ-NACE 2008 section of a code."""
    if not code or len(code) < 2 or not code[:2].isdigit():
        return None
    division = int(code[:2])
    return next((s for s, lo, hi in SECTIONS_2008 if lo <= division <= hi), None)


def industry_group(company: Company | None) -> str | None:
    """The MPO group of the company: its CZ-NACE 2008 section, or 'H-S' for the service sections."""
    section = section_2008(company.nace_2008) if company else None
    return "H-S" if section and section in SERVICES else section


def industry_benchmarks(company: Company | None) -> dict[str, dict[str, Any]]:
    """MPO sector values for the company's group (reference only, not a traffic light limit)."""
    group = industry_group(company)
    if not group or not BENCHMARKS.exists():
        return {}
    with BENCHMARKS.open(encoding="utf-8-sig", newline="") as f:
        rows = [r for r in csv.DictReader(f) if r["cz_nace_group"] == group and r["period"] == BENCHMARK_PERIOD]
    return {
        r["ratio_key"]: {
            "value": float(r["benchmark_value"]),
            "unit": r["unit"],
            "sector": r["sector_en"],
            "period": r["period"],
            "source": r["source_url"],
            "note": "Reference only, not a traffic light limit",
        }
        for r in rows
    }
