"""Run the reading layer (CIPW-7) on every located statement page and collect statistics.

For each PDF: locate the statement pages (CIPW-6), read them (text layer or Tesseract OCR), save
`outputs/ocr/{file}_p{page}.json` and a debug PNG with every word box, and write one row per page to
`outputs/ocr/summary.csv`. Prints the mean OCR confidence and time per page at the end.

Usage:
    python scripts/run_reading.py                  # every PDF in data/raw/
    python scripts/run_reading.py --index          # only the files listed in data/index.csv
    python scripts/run_reading.py 26161516_2025_statement.pdf --workers 2
"""

from __future__ import annotations

import argparse
import csv
import os
import statistics
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.settings import get_settings  # noqa: E402

RAW = get_settings().paths.raw
OUT = get_settings().paths.outputs / "ocr"
FIELDS = [
    "file", "page", "statement", "page_kind", "text_layer_ok", "source", "fallback_to_ocr", "words",
    "numeric_words", "mean_conf", "low_conf_share", "skew", "seconds", "error",
]  # fmt: skip


def process_file(path: str) -> list[dict]:
    """Locate + read one PDF; returns one row per located page (or one error row)."""
    import pymupdf

    from src.extraction.layout import is_numeric
    from src.extraction.locate import locate_statements
    from src.extraction.reading import page_kind, read_page, save_debug, text_layer_ok

    name = Path(path).name
    rows: list[dict] = []
    try:
        with pymupdf.open(path) as doc:
            located = locate_statements(doc)
            pages = [(p, st) for st, ps in located.items() for p in ps]
            if not pages:
                return [{"file": name, "error": f"no statement pages found ({doc.page_count} pages)"}]
            for page_no, statement in sorted(pages):
                page = doc[page_no - 1]
                kind = page_kind(page)
                layer_ok = text_layer_ok(page)
                start = time.perf_counter()
                pw = read_page(doc, page_no, "auto", file=name)
                seconds = time.perf_counter() - start
                save_debug(doc, pw, OUT)
                confs = [w.conf for w in pw.words if w.conf is not None]
                rows.append(
                    {
                        "file": name,
                        "page": page_no,
                        "statement": statement,
                        "page_kind": kind,
                        "text_layer_ok": layer_ok,
                        "source": pw.source,
                        "fallback_to_ocr": kind == "digital" and pw.source == "ocr",
                        "words": len(pw.words),
                        "numeric_words": sum(1 for w in pw.words if is_numeric(w.text)),
                        "mean_conf": round(statistics.fmean(confs), 1) if confs else "",
                        "low_conf_share": round(sum(c < 60 for c in confs) / len(confs), 3) if confs else "",
                        "skew": pw.skew,
                        "seconds": round(seconds, 2),
                        "error": "",
                    }
                )
    except Exception as exc:  # one broken PDF must not stop the batch
        rows.append({"file": name, "error": f"{type(exc).__name__}: {exc}"})
    return rows


def summarize(rows: list[dict]) -> str:
    ok = [r for r in rows if not r.get("error")]
    ocr = [r for r in ok if r["source"] == "ocr"]
    text = [r for r in ok if r["source"] == "text_layer"]
    files = {r["file"] for r in rows}
    failed = sorted({r["file"]: r["error"] for r in rows if r.get("error")}.items())
    lines = [
        f"files: {len(files)}, statement pages read: {len(ok)} (text layer {len(text)}, OCR {len(ocr)})",
        f"files without located statements or with errors: {len(failed)}",
    ]
    if ocr:
        confs = [float(r["mean_conf"]) for r in ocr if r["mean_conf"] != ""]
        secs = [float(r["seconds"]) for r in ocr]
        lines.append(
            f"OCR pages: mean confidence {statistics.fmean(confs):.1f} (median {statistics.median(confs):.1f}, "
            f"min {min(confs):.1f}), time per page {statistics.fmean(secs):.1f} s "
            f"(median {statistics.median(secs):.1f} s, max {max(secs):.1f} s)"
        )
        lines.append(f"OCR fallback for digital pages with a broken text layer: "
                     f"{sum(1 for r in ok if r['fallback_to_ocr'])}")  # fmt: skip
    if text:
        lines.append(f"text-layer pages: time per page {statistics.fmean(float(r['seconds']) for r in text):.3f} s")
    for name, err in failed:
        lines.append(f"  - {name}: {err}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("files", nargs="*", help="PDF names in data/raw (default: all)")
    parser.add_argument("--index", action="store_true", help="only files listed in data/index.csv")
    parser.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    if args.files:
        paths = [RAW / f for f in args.files]
    elif args.index:
        with (get_settings().paths.raw.parent / "index.csv").open(encoding="utf-8") as f:
            paths = [RAW / r["file"] for r in csv.DictReader(f)]
    else:
        paths = sorted(RAW.glob("*.pdf"))
    paths = [p for p in paths if p.exists()]
    OUT.mkdir(parents=True, exist_ok=True)

    rows: list[dict] = []
    start = time.perf_counter()
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(process_file, str(p)): p.name for p in paths}
        for i, fut in enumerate(as_completed(futures), 1):
            file_rows = fut.result()
            rows.extend(file_rows)
            status = file_rows[0].get("error") or f"{len(file_rows)} page(s), {file_rows[0].get('source')}"
            print(f"[{i}/{len(paths)}] {futures[fut]}: {status}", flush=True)

    rows.sort(key=lambda r: (r["file"], r.get("page") or 0))
    with (OUT / "summary.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"\n{summarize(rows)}\nwall time {time.perf_counter() - start:.0f} s, details: {OUT / 'summary.csv'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
