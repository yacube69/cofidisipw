"""The figures of an assessment input as tables for the report (CIPW-16).

The tables come straight from the assessment input, not from the LLM: the analyst reads the numbers next to the
narrative and can check every sentence against them. Amounts are written as in the prompt (billions and millions with
two decimals, below one million in full), ratios and days as in the input.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from src.report.texts import texts

_DECIMALS = {"x": 2, "%": 1, "days": 1}


def _round(value: float, places: int) -> str:
    """Round half up (118.945 -> 118.95), as people and the LLM do; `format` rounds the binary float (118.94)."""
    step = Decimal(1).scaleb(-places)
    return str(Decimal(repr(value)).quantize(step, rounding=ROUND_HALF_UP))


def _local(text: str, t: dict[str, Any]) -> str:
    """The decimal sign of the language (a comma in Czech)."""
    return text.replace(".", t["decimal"]) if t["decimal"] != "." else text


def amount(value: float | None, language: str = "en") -> str:
    """CZK amount: `29.46 billion`, `176.52 million` (Czech `29,46 mld.`), or in full with spaces below a million."""
    if value is None:
        return "–"
    t = texts(language)
    magnitude = abs(value)
    sign = "-" if value < 0 else ""
    if magnitude >= 1e9:
        return f"{sign}{_local(_round(magnitude / 1e9, 2), t)} {t['billion']}"
    if magnitude >= 1e6:
        return f"{sign}{_local(_round(magnitude / 1e6, 2), t)} {t['million']}"
    return f"{sign}{magnitude:,.0f}".replace(",", " ")


def _signed(value: float, places: int, t: dict[str, Any]) -> str:
    text = _round(value, places)
    if Decimal(text) == 0:
        text = text.lstrip("-")
    elif not text.startswith("-"):
        text = f"+{text}"
    return _local(text, t)


def _percent(value: float | None, t: dict[str, Any]) -> str:
    return "–" if value is None else f"{_signed(value, 1, t)} %"


def _ratio(unit: str, entry: dict[str, Any] | None, t: dict[str, Any]) -> str:
    """A ratio value of one year, or why there is none."""
    if not entry:
        return "–"
    if entry["value"] is None:
        return t["reasons"].get(entry["flag"], entry["flag"])
    unit_text = {"x": "", "%": " %", "days": f" {t['days']}"}[unit]
    text = f"{_local(_round(entry['value'], _DECIMALS[unit]), t)}{unit_text}"
    return text + (t["unreliable_suffix"] if entry["flag"] == "unreliable" else "")


def _change(unit: str, value: float | None, t: dict[str, Any]) -> str:
    if value is None:
        return "–"
    suffix = {"x": "", "%": " pp", "days": f" {t['days']}"}[unit]
    return f"{_signed(value, _DECIMALS[unit], t)}{suffix}"


def _table(header: list[str], rows: list[list[str]]) -> list[str]:
    return ["| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in header) + " |"] + [
        "| " + " | ".join(row) + " |" for row in rows
    ]


Table = tuple[str, list[str], list[list[str]]]  # title, header, rows


def figure_tables(data: dict[str, Any], language: str = "en") -> tuple[list[Table], str]:
    """Key figures, ratios and bankruptcy models as tables, and the text of the accounting checks."""
    t = texts(language)
    tables: list[Table] = []

    rows = [
        [
            t["key_labels"].get(key, figure["label"]) + (" *" if figure.get("needs_review") else ""),
            amount(figure["current"], language),
            amount(figure["prior"], language),
            _percent(figure.get("change_pct"), t),
        ]
        for key, figure in data["key_figures"].items()
    ]
    tables.append((t["key_figures"], [t["col_line"], t["col_current"], t["col_prior"], t["col_change"]], rows))

    rows = [
        [
            t["ratio_names"].get(ratio["key"], ratio["name"]),
            ratio["formula"],
            _ratio(ratio["unit"], ratio["current"], t),
            _ratio(ratio["unit"], ratio["prior"], t),
            _change(ratio["unit"], ratio.get("change"), t),
        ]
        for ratio in data["ratios"]
    ]
    header = [t["col_ratio"], t["col_formula"], t["col_current"], t["col_prior"], t["col_change"]]
    tables.append((t["ratios"], header, rows))

    rows = []
    for key, model in data["bankruptcy"].items():
        cells = []
        for year in ("current", "prior"):
            entry = model[year]
            cells.append(
                f"{_local(_round(entry['score'], 2), t)} ({t['zones'][entry['zone']]})"
                if entry["score"] is not None
                else t["not_computed"]
            )
        name, zones = t["model_names"].get(key, (model["name"], model["zones"]))
        rows.append([name, zones, *cells])
    header = [t["col_model"], t["col_zones"], t["col_current"], t["col_prior"]]
    tables.append((t["bankruptcy"], header, rows))

    validation = data["validation"]
    result = (
        t["checks_passed"]
        if validation["passed"]
        else t["checks_failed"].format(count=validation["failed_count"], names=", ".join(validation["failed"]))
    )
    return tables, t["checks_text"].format(total=validation["checks_total"], result=result)


def figures_markdown(data: dict[str, Any], language: str = "en") -> list[str]:
    """Key figures, ratios, bankruptcy models and accounting checks as Markdown lines."""
    t = texts(language)
    tables, checks = figure_tables(data, language)
    lines = [f"## {t['figures']}", "", t["figures_intro"], ""]
    for title, header, rows in tables:
        lines += [f"### {title}", ""] + _table(header, rows) + [""]
    lines += [f"### {t['checks']}", "", checks, ""]
    return lines
