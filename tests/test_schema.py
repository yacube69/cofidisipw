"""CIPW-5: schema, line-item dictionary and ground truth files."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.line_items import load_dictionary
from src.schema import FinancialStatements, GroundTruth, LineItem, Value, blank_ground_truth

ROOT = Path(__file__).resolve().parents[1]
GT_FILES = sorted(p for p in (ROOT / "data/ground_truth").glob("*.json") if not p.name.startswith("_"))


def test_dictionary_loads_and_keys_unique():
    d = load_dictionary()
    keys = [line.key for line in d.lines]
    assert len(keys) == len(set(keys))
    assert {"total_assets", "total_liabilities_equity", "equity"} <= set(d.keys("balance_sheet"))
    assert {"revenue_products_services", "profit_for_period"} <= set(d.keys("income_statement"))


def test_balance_sheet_lines_have_side_and_codes_are_normalized():
    for line in load_dictionary().lines:
        if line.statement == "balance_sheet":
            assert line.side in ("assets", "liabilities"), line.key
        assert " " not in line.code, line.key
        assert line.code == "" or line.code.endswith((".", "*")), line.key


def test_unknown_key_is_rejected():
    with pytest.raises(ValidationError):
        FinancialStatements(balance_sheet={"not_a_field": LineItem(key="not_a_field")})


def test_get_returns_amount_from_either_statement():
    fs = FinancialStatements(
        balance_sheet={"equity": LineItem(key="equity", current=Value(value=5_000.0), prior=Value(value=4_000.0))},
        income_statement={"profit_for_period": LineItem(key="profit_for_period", current=Value(value=-200.0))},
    )
    assert fs.get("equity") == 5_000.0
    assert fs.get("equity", "prior") == 4_000.0
    assert fs.get("profit_for_period") == -200.0
    assert fs.get("cash") is None


def test_flagged_lists_values_needing_review():
    cash = LineItem(key="cash", current=Value(value=1.0, needs_review=True), prior=Value(value=2.0))
    fs = FinancialStatements(balance_sheet={"cash": cash})
    assert [(k, p) for k, p, _ in fs.flagged()] == [("cash", "current")]


def test_blank_template_covers_every_field():
    template = blank_ground_truth()
    d = load_dictionary()
    assert set(template["balance_sheet"]) == set(d.keys("balance_sheet"))
    assert set(template["income_statement"]) == set(d.keys("income_statement"))
    GroundTruth.model_validate(template)


def test_template_file_is_up_to_date():
    on_disk = json.loads((ROOT / "data/ground_truth/_template.json").read_text(encoding="utf-8"))
    assert on_disk == blank_ground_truth(), "Run: python scripts/export_schema.py"


@pytest.mark.parametrize("path", GT_FILES, ids=lambda p: p.name)
def test_ground_truth_files_are_valid_and_balance(path):
    gt = GroundTruth.model_validate_json(path.read_text(encoding="utf-8"))
    fs = gt.to_statements()
    for period in ("current", "prior"):
        assets, liabilities = fs.get("total_assets", period), fs.get("total_liabilities_equity", period)
        if assets is not None and liabilities is not None:
            assert assets == liabilities, f"{path.name} {period}: assets {assets} != liabilities {liabilities}"
