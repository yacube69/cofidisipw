"""Vision baseline vs rule-based parser on the ground truth (CIPW-8).

Usage:
    python scripts/run_vision_baseline.py --dry-run          # no API call: pages, image sizes, prompt size
    python scripts/run_vision_baseline.py                    # all files in data/ground_truth/
    python scripts/run_vision_baseline.py 26161516_2025      # one file
    python scripts/run_vision_baseline.py --use-gt-pages     # send the hand-annotated pages instead of the locator's
    python scripts/run_vision_baseline.py --details          # list every wrong and missing value of the vision run

Needs ANTHROPIC_API_KEY in .env (see .env.example) for real runs. For a cost column set the prices of your model in
.env: LLM_PRICE_IN_PER_MTOK and LLM_PRICE_OUT_PER_MTOK (USD per million tokens, from the provider's price list).

Both methods are scored with the same function (src.evaluation.compare): a value is correct only if it equals the
ground truth. The parser can also flag values for review; the vision baseline cannot, so every wrong vision value is a
silent error. The parser time is measured with an empty page cache (locate + read + parse). Vision answers are cached
in outputs/vision/cache (same pages, prompt, model and DPI: no second charge); --force calls the API again.

Writes outputs/vision/summary.json and outputs/vision/comparison.md (the table for the ticket comment).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import src.evaluation as ev  # noqa: E402
from src.extraction import vision  # noqa: E402
from src.extraction.parser import ParseLog  # noqa: E402
from src.schema import FinancialStatements, GroundTruth  # noqa: E402

RAW = ROOT / "data" / "raw"
GT_DIR = ROOT / "data" / "ground_truth"
OUT = ROOT / "outputs" / "vision"


def score(fields: list[ev.FieldResult]) -> dict[str, int]:
    return {
        "values": len(fields),
        "correct": sum(f.correct for f in fields),
        "silent_errors": sum(1 for f in fields if f.outcome == "wrong"),
        "caught": sum(1 for f in fields if f.outcome == "flagged_wrong"),
        "missing": sum(1 for f in fields if f.outcome == "missing"),
    }


def run_parser(gt: GroundTruth) -> tuple[list[ev.FieldResult], dict[str, list[int]], float, bool]:
    """The parser on one file with an empty page cache. Returns fields, located pages, seconds, is_scan."""
    original = ev.CACHE
    with tempfile.TemporaryDirectory() as tmp:
        ev.CACHE = Path(tmp)
        try:
            started = time.perf_counter()
            fs, pages = ev.extract(RAW / gt.file, log=ParseLog())
            seconds = time.perf_counter() - started
        finally:
            ev.CACHE = original
    return ev.compare(gt, fs), pages, seconds, "ocr" in set(fs.metadata.extraction_source.values())


def cache_path(gt: GroundTruth, pages: list[int], provider: str, model: str, dpi: int) -> Path:
    schema = vision.output_schema(simple=provider == "mistral")
    key = json.dumps([vision.build_prompt(), schema, provider, model, dpi, pages], sort_keys=True)
    return OUT / "cache" / f"{Path(gt.file).stem}_{hashlib.sha1(key.encode()).hexdigest()[:12]}.json"


def run_vision(
    gt: GroundTruth, pages: list[int], client: Any, provider: str, model: str, dpi: int, force: bool
) -> vision.VisionResult:
    path = cache_path(gt, pages, provider, model, dpi)
    if path.exists() and not force:
        data = json.loads(path.read_text(encoding="utf-8"))
        result = vision.VisionResult(**{k: v for k, v in data.items() if k != "statements"})
        if result.raw is not None and result.error is None:
            result.statements = vision.to_statements(result.raw, gt.file, pages)
        result.from_cache = True
        return result
    result = vision.extract_with_vision(RAW / gt.file, pages, client, model, dpi, provider=provider)
    if result.error is None:  # failures are not cached: a rerun must try again
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(asdict(result) | {"statements": None}, ensure_ascii=False, indent=1), encoding="utf-8"
        )
    return result


def dry_run(gt: GroundTruth, pages: list[int], dpi: int) -> None:
    images = vision.render_pages(RAW / gt.file, pages, dpi)
    sizes = ", ".join(f"p{p} {len(png) / 1024:.0f} KB" for p, png in images)
    print(f"{gt.file:32} {len(pages)} page(s) to send: {sizes}")


def fmt_cost(c: float | None) -> str:
    return "n/a" if c is None else f"${c:.3f}"


def main(argv: list[str] | None = None, client: Any = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*", help="ground truth names (without .json); default: all")
    ap.add_argument("--provider", choices=vision.PROVIDERS, default=os.getenv("LLM_PROVIDER") or "anthropic")
    ap.add_argument(
        "--model", default=None, help="default: MISTRAL_MODEL / LLM_MODEL from .env, else the provider default"
    )
    ap.add_argument("--dpi", type=int, default=vision.DPI)
    ap.add_argument("--use-gt-pages", action="store_true", help="send the annotated pages, not the locator's")
    ap.add_argument("--dry-run", action="store_true", help="no API call")
    ap.add_argument("--force", action="store_true", help="ignore the vision cache")
    ap.add_argument("--details", action="store_true")
    ap.add_argument("--pause", type=float, default=0.0, help="seconds to wait after each API call (free-tier limits)")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    from dotenv import load_dotenv

    load_dotenv()
    args.model = args.model or os.getenv("MISTRAL_MODEL" if args.provider == "mistral" else "LLM_MODEL")
    args.model = args.model or vision.DEFAULT_MODELS[args.provider]
    if client is None and not args.dry_run:
        key = "MISTRAL_API_KEY" if args.provider == "mistral" else "ANTHROPIC_API_KEY"
        if not os.getenv(key):
            print(f"ERROR: {key} is not set. Copy .env.example to .env and fill it in.")
            return 1
        if args.provider == "mistral":
            client = vision.MistralClient()
        else:
            import anthropic

            client = anthropic.Anthropic()

    paths = [GT_DIR / f"{n}.json" for n in args.files] or sorted(
        p for p in GT_DIR.glob("*.json") if not p.name.startswith("_")
    )
    rows: list[dict[str, Any]] = []
    for path in paths:
        gt = GroundTruth.model_validate_json(path.read_text(encoding="utf-8"))
        p_fields, located, p_seconds, is_scan = run_parser(gt)
        source = gt.pages if args.use_gt_pages else located
        pages = sorted({p for ps in source.values() for p in ps})
        if args.dry_run:
            dry_run(gt, pages, args.dpi)
            continue
        vr = run_vision(gt, pages, client, args.provider, args.model, args.dpi, args.force)
        if args.pause and not vr.from_cache:
            time.sleep(args.pause)
        v_fields = ev.compare(gt, vr.statements or FinancialStatements())
        rows.append(
            {
                "file": gt.file,
                "kind": "scan" if is_scan else "digital",
                "pages": pages,
                "parser": score(p_fields) | {"seconds": round(p_seconds, 1)},
                "vision": score(v_fields)
                | {
                    "seconds": round(vr.seconds, 1),
                    "input_tokens": vr.input_tokens,
                    "output_tokens": vr.output_tokens,
                    "cost_usd": vision.cost_usd(vr.input_tokens, vr.output_tokens),
                    "error": vr.error,
                    "notes": vr.notes,
                    "from_cache": vr.from_cache,
                    "temperature_used": vr.temperature_used,
                },
            }
        )
        if args.details:
            for f in v_fields:
                if f.outcome != "correct":
                    got = "-" if f.actual is None else f"{f.actual:,.0f}"
                    print(f"    {gt.file} {f.outcome:8} {f.key}.{f.period}: expected {f.expected:,.0f} got {got}")
    if args.dry_run:
        print(
            f"Prompt: {len(vision.build_prompt())} characters, schema keys: "
            f"{len(vision.output_schema()['properties']['balance_sheet']['properties'])} balance sheet + "
            f"{len(vision.output_schema()['properties']['income_statement']['properties'])} income statement."
        )
        return 0
    return report(rows, args)


def report(rows: list[dict[str, Any]], args: argparse.Namespace) -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    lines = [
        f"Vision baseline vs parser, {len(rows)} file(s). {args.provider} / {args.model}, {args.dpi} DPI, "
        f"prompt {vision.PROMPT_PATH.stem}, pages from the {'annotation' if args.use_gt_pages else 'page locator'}.",
        "",
        "| file | type | values | parser correct | parser silent errors | parser missing | parser s | "
        "vision correct | vision wrong | vision missing | vision s | tokens in/out | cost |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|",
    ]
    groups: dict[str, dict[str, float]] = {}
    for r in rows:
        p, v = r["parser"], r["vision"]
        n = p["values"]
        lines.append(
            f"| {r['file']} | {r['kind']} | {n} | {p['correct'] / n:.1%} | {p['silent_errors']} | {p['missing']} | "
            f"{p['seconds']} | {v['correct'] / n:.1%} | {v['silent_errors']} | {v['missing']} | {v['seconds']} | "
            f"{v['input_tokens']}/{v['output_tokens']} | {fmt_cost(v['cost_usd'])} |"
        )
        if v["error"]:
            lines.append(f"| ↳ {r['file']} vision error: {v['error']} | | | | | | | | | | | | |")
        g = groups.setdefault(
            r["kind"], dict.fromkeys(("files", "values", "pc", "ps", "vc", "vw", "pt", "vt", "cost"), 0.0)
        )
        g["files"] += 1
        g["values"] += n
        g["pc"] += p["correct"]
        g["ps"] += p["silent_errors"]
        g["vc"] += v["correct"]
        g["vw"] += v["silent_errors"]
        g["pt"] += p["seconds"]
        g["vt"] += v["seconds"]
        g["cost"] += v["cost_usd"] or 0.0
    lines += [
        "",
        "| group | files | values | parser correct | parser silent errors | vision correct | vision wrong "
        "| parser s/file | vision s/file | vision cost/file |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for kind, g in sorted(groups.items()):
        k = g["files"]
        lines.append(
            f"| {kind} | {k:.0f} | {g['values']:.0f} | {g['pc'] / g['values']:.1%} | {g['ps']:.0f} | "
            f"{g['vc'] / g['values']:.1%} | {g['vw']:.0f} | {g['pt'] / k:.1f} | {g['vt'] / k:.1f} | "
            f"{fmt_cost(g['cost'] / k if g['cost'] else None)} |"
        )
    text = "\n".join(lines) + "\n"
    (OUT / "comparison.md").write_text(text, encoding="utf-8")
    (OUT / "summary.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print(text)
    print(f"Written to {OUT.relative_to(ROOT)}/comparison.md and summary.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
