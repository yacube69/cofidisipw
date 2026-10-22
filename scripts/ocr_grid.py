"""Compare OCR settings on the scanned ground truth files through the whole parser (CIPW-25).

Every setting is one change from the default (`config/fides.yaml`, `ocr`), so its effect is clear; `--combine`
adds settings made of the best single changes. Each setting reads the statement pages with that OCR (cached in
outputs/cache/pagewords per setting), parses them, runs the checks and the re-read of doubted cells like the
pipeline, and is scored against the ground truth: values correct, wrong and not flagged, not found, seconds per
page (of the first, uncached read).

    python scripts/ocr_grid.py 26161516_2025 26915758_2024    # these files only
    python scripts/ocr_grid.py --scans                        # every scanned ground truth file
    python scripts/ocr_grid.py --scans --only default best    # chosen settings
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.evaluation import evaluate_file  # noqa: E402
from src.extraction.ocr import OcrConfig  # noqa: E402
from src.schema import GroundTruth  # noqa: E402
from src.settings import get_settings  # noqa: E402

FAST, BEST = "outputs/tessdata/fast", "outputs/tessdata/best"  # official tessdata_fast / tessdata_best ces


def settings(base: OcrConfig) -> dict[str, OcrConfig]:
    return {
        "default": base,
        "dpi 200": replace(base, dpi=200),
        "dpi 400": replace(base, dpi=400),
        "psm 4": replace(base, psm=4),
        "psm 11": replace(base, psm=11),
        "otsu": replace(base, binarize="otsu"),
        "no binarize": replace(base, binarize="none"),
        "lines kept": replace(base, remove_lines=False),
        "no deskew": replace(base, deskew=False),
        "model fast": replace(base, tessdata=FAST),
        "model best": replace(base, tessdata=BEST),
        "fast, dpi 200": replace(base, tessdata=FAST, dpi=200),
        "fast, psm 4": replace(base, tessdata=FAST, psm=4),
    }


def scanned(paths: list[Path]) -> list[Path]:
    """Ground truth files whose PDF is read by OCR (index format 'scan')."""
    import csv

    rows = csv.DictReader(open(ROOT / "data/index.csv", encoding="utf-8"))
    scans = {r["file"] for r in rows if r["format"] == "scan"}
    out = []
    for path in paths:
        gt = GroundTruth.model_validate_json(path.read_text(encoding="utf-8"))
        if gt.file in scans and not gt.partial:
            out.append(path)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("files", nargs="*", help="ground truth names (without .json)")
    parser.add_argument("--scans", action="store_true", help="every scanned ground truth file")
    parser.add_argument("--only", nargs="*", help="setting names to run (default: all)")
    args = parser.parse_args(argv)

    gt_dir = get_settings().paths.ground_truth
    paths = [gt_dir / f"{n}.json" for n in args.files]
    if args.scans:
        paths = scanned(sorted(p for p in gt_dir.glob("*.json") if not p.name.startswith("_")))
    grid = settings(get_settings().ocr.config())
    if args.only:
        grid = {k: v for k, v in grid.items() if k in args.only}
    print(f"{len(paths)} files: {', '.join(p.stem for p in paths)}")
    head = f"{'setting':14} {'values':>6} {'correct':>8} {'wrong':>5} {'flag-':>5} {'miss':>5}"
    print(f"{head} {'checks failed':>14} {'s/page':>7}")
    for name, config in grid.items():
        start = time.perf_counter()
        reports = [evaluate_file(p, config=config) for p in paths]
        fields = sum(len(r.fields) for r in reports)
        correct = sum(r.count("correct", "flagged_correct") for r in reports)
        failed = sum(r.reread.failed_after for r in reports if r.reread)
        seconds = sum(r.ocr_seconds for r in reports)
        ocr_pages = sum(r.ocr_pages for r in reports)
        print(
            f"{name:14} {fields:6d} {correct / fields:8.1%} {sum(r.count('wrong') for r in reports):5d} "
            f"{sum(r.count('flagged_wrong') for r in reports):5d} {sum(r.count('missing') for r in reports):5d} "
            f"{failed:14d} {seconds / max(ocr_pages, 1):7.1f}   ({time.perf_counter() - start:.0f} s)",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
