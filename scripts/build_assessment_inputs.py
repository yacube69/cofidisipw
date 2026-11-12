"""The AI assessment inputs of the ground truth companies, and the rating the proposed rules give (CIPW-16).

    python scripts/build_assessment_inputs.py                  # all files in data/ground_truth/
    python scripts/build_assessment_inputs.py 26161516_2025    # one file
    python scripts/build_assessment_inputs.py --no-write       # table only

Starts from the verified ground truth (no PDF, no OCR, no LLM call), runs the accounting checks, the ratios and the
bankruptcy models, and writes `outputs/report_inputs/<file>.json`: exactly what the LLM will get. The table shows
the proposed rating per company, so the team can judge the rules (docs/assessment.md).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.ratios.bankruptcy import compute_bankruptcy  # noqa: E402
from src.ratios.engine import compute_ratios  # noqa: E402
from src.report.inputs import build_assessment_input  # noqa: E402
from src.report.rating import rate  # noqa: E402
from src.schema import GroundTruth  # noqa: E402
from src.settings import get_settings  # noqa: E402
from src.validation.review import validate_and_review  # noqa: E402


def assessment_input(path: Path) -> dict:
    gt = GroundTruth.model_validate_json(path.read_text(encoding="utf-8"))
    fs = gt.to_statements()
    validation, _ = validate_and_review(fs)
    return build_assessment_input(fs, validation, compute_ratios(fs, validation), compute_bankruptcy(fs, validation))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("files", nargs="*", help="ground truth names (without .json); default: all")
    parser.add_argument("--no-write", action="store_true", help="print the table, write no JSON")
    args = parser.parse_args(argv)

    settings = get_settings()
    gt_dir = settings.paths.ground_truth
    out_dir = settings.paths.outputs / "report_inputs"
    paths = [gt_dir / f"{n}.json" for n in args.files] or sorted(
        p for p in gt_dir.glob("*.json") if not p.name.startswith("_")
    )
    print(f"{'file':16} {'company':26} {'IN05':16} {'Altman Z′':16} {'quality':8} rating (rule)")
    for path in paths:
        data = assessment_input(path)
        decision = rate(data)
        b = data["bankruptcy"]

        def zone(key: str, b=b) -> str:
            s = b[key]["current"]
            return "-" if s["score"] is None else f"{s['score']:.2f} {s['zone']}"

        name = (data["company"]["name"] or "")[:25]
        rating = f"{decision.value or 'none'} ({','.join(decision.rules)})"
        quality = data["data_quality"]["overall"]
        print(f"{path.stem:16} {name:26} {zone('in05'):16} {zone('altman_z_prime'):16} {quality:8} {rating}")
        if not args.no_write:
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / f"{path.stem}.json").write_text(
                json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
    if not args.no_write:
        print(f"\nWritten to {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
