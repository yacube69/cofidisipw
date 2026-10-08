"""Export the JSON schemas and the blank ground-truth template (CIPW-5).

Usage:
    python scripts/export_schema.py

Writes:
    docs/schema/financial_statements.schema.json   main model (used by the vision baseline, CIPW-8)
    docs/schema/ground_truth.schema.json           hand-filled ground truth format (CIPW-3)
    data/ground_truth/_template.json               blank ground truth with every field
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.schema import FinancialStatements, GroundTruth, blank_ground_truth  # noqa: E402


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {path.relative_to(ROOT)}")


def main() -> int:
    write_json(ROOT / "docs/schema/financial_statements.schema.json", FinancialStatements.model_json_schema())
    write_json(ROOT / "docs/schema/ground_truth.schema.json", GroundTruth.model_json_schema())
    write_json(ROOT / "data/ground_truth/_template.json", blank_ground_truth())
    return 0


if __name__ == "__main__":
    sys.exit(main())
