"""Compare extraction with the ground truth, field by field (CIPW-32, CIPW-19).

Usage:
    python scripts/evaluate_extraction.py                 # all files in data/ground_truth/
    python scripts/evaluate_extraction.py 26161516_2025   # one file
    python scripts/evaluate_extraction.py --details       # list every wrong, missing and flagged value

Outcomes per value:
    correct          extracted exactly, not flagged
    flagged_correct  extracted exactly, but flagged for review (costs the analyst a click)
    flagged_wrong    wrong, but flagged for review (caught)
    wrong            wrong and NOT flagged (silent error: the dangerous kind)
    missing          not extracted
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.evaluation import evaluate_file  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("files", nargs="*", help="ground truth names (without .json); default: all")
    parser.add_argument("--details", action="store_true")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    gt_dir = ROOT / "data" / "ground_truth"
    paths = [gt_dir / f"{n}.json" for n in args.files] or sorted(
        p for p in gt_dir.glob("*.json") if not p.name.startswith("_")
    )
    total = {"fields": 0, "correct": 0, "wrong": 0, "missing": 0, "flagged_correct": 0, "flagged_wrong": 0}
    print(
        f"{'file':32} {'src':6} {'pages':5} {'fields':>6} {'acc':>6} {'ok':>4} {'flag+':>5} {'flag-':>5} "
        f"{'WRONG':>5} {'miss':>4}"
    )
    for path in paths:
        r = evaluate_file(path)
        src = "ocr" if "ocr" in r.sources else "text"
        print(
            f"{r.file:32} {src:6} {'ok' if r.pages_ok else 'BAD':5} {len(r.fields):6d} {r.accuracy:6.1%} "
            f"{r.count('correct'):4d} {r.count('flagged_correct'):5d} {r.count('flagged_wrong'):5d} "
            f"{r.count('wrong'):5d} {r.count('missing'):4d}"
        )
        total["fields"] += len(r.fields)
        for k in ("correct", "wrong", "missing", "flagged_correct", "flagged_wrong"):
            total[k] += r.count(k)  # type: ignore[arg-type]
        if args.details:
            for f in r.fields:
                if f.outcome != "correct":
                    print(
                        f"    {f.outcome:16} {f.key}.{f.period}: expected {f.expected:,.0f} got "
                        f"{f.actual if f.actual is None else f'{f.actual:,.0f}'} (raw {f.raw!r}, p{f.page})"
                    )
            if r.log:
                for page, code, label, row in r.log.unmatched:
                    print(f"    unmatched p{page} code={code!r} row={row!r} label={label[:60]!r}")
    if total["fields"]:
        acc = (total["correct"] + total["flagged_correct"]) / total["fields"]
        print(
            f"\nALL: {total['fields']} values, accuracy {acc:.1%}, silent errors {total['wrong']}, "
            f"caught {total['flagged_wrong']}, missing {total['missing']}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
