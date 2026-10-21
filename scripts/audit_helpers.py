"""Check the CIPW-9 helpers on every collected file: columns, unit, period and scope.

Reads the pages saved by scripts/run_reading.py (outputs/ocr/summary.csv + one PageWords JSON per page), so no OCR
runs here. Per file it reports what the helpers detected, then parses and validates the statements: a column that
was named wrong (e.g. Korekce taken as Netto) breaks the net and sum checks, so the share of passed checks is the
proxy for correct columns where we have no ground truth.

Writes outputs/audit_helpers.csv (one row per file) and prints the problems.

Usage:
    python scripts/audit_helpers.py
    python scripts/audit_helpers.py 26161516_2025_statement.pdf
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.extraction.layout import group_lines, straighten  # noqa: E402
from src.extraction.numbers import detect_columns, detect_period, detect_scope, detect_unit  # noqa: E402
from src.extraction.parser import _split_sides, parse_statements  # noqa: E402
from src.extraction.types import PageWords  # noqa: E402
from src.settings import get_settings  # noqa: E402
from src.validation.checks import validate  # noqa: E402

OCR = get_settings().paths.outputs / "ocr"
OUT = get_settings().paths.outputs / "audit_helpers.csv"
EXPECTED = {"current", "prior"}  # every statement part needs at least these two columns


def load_pages(only: set[str]) -> dict[str, dict[str, list[PageWords]]]:
    files: dict[str, dict[str, list[PageWords]]] = defaultdict(lambda: defaultdict(list))
    with (OCR / "summary.csv").open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["error"] or not row["statement"] or (only and row["file"] not in only):
                continue
            path = OCR / f"{Path(row['file']).stem}_p{row['page']}.json"
            if path.exists():
                pw = PageWords.model_validate_json(path.read_text(encoding="utf-8"))
                files[row["file"]][row["statement"]].append(pw)
    return files


def file_year(name: str) -> int | None:
    m = re.search(r"_((?:19|20)\d{2})(?:_|\b)", name)
    return int(m.group(1)) if m else None


def audit(name: str, statements: dict[str, list[PageWords]]) -> dict:
    page_words = sorted((pw for pws in statements.values() for pw in pws), key=lambda pw: pw.page)
    text = "\n".join(pw.text for pw in page_words)
    unit = detect_unit(text)
    current, prior = detect_period(text)
    layouts, problems = [], []
    for statement, pws in statements.items():
        for pw in pws:
            lines = group_lines(straighten(pw))
            parts = _split_sides(lines) if statement == "balance_sheet" else [("income_statement", lines)]
            for kind, part in parts:
                names = [c.name for c in detect_columns(part, kind) if c.name not in ("row", "extra")]
                layouts.append(f"p{pw.page}:{kind[:3]}={'/'.join(names) or '-'}")
                if not EXPECTED <= set(names):
                    problems.append(f"p{pw.page} {kind}: columns {names or 'none'}")
                if len(names) != len(set(names)):
                    problems.append(f"p{pw.page} {kind}: duplicate column names {names}")
    if unit is None:
        problems.append("unit not found (parser assumes tis. Kč)")
    year = file_year(name)
    if current is None:
        problems.append("period not found")
    elif year and current.year not in (year, year + 1):
        problems.append(f"period {current} does not match the year {year} in the file name")
    pages = {st: [pw.page for pw in pws] for st, pws in statements.items()}
    fs = parse_statements(page_words, pages)
    report = validate(fs)
    hard = [c for c in report.checks if c.severity == "error"]
    failed = [c for c in hard if not c.passed]
    return {
        "file": name,
        "source": "/".join(sorted({pw.source for pw in page_words})),
        "unit": unit or "",
        "period_end": current or "",
        "prior_period_end": prior or "",
        "scope": detect_scope(text) or "",
        "columns": " ".join(layouts),
        "values": sum(len(item.values()) for item in [*fs.balance_sheet.values(), *fs.income_statement.values()]),
        "checks": len(hard),
        "failed": len(failed),
        "failed_names": "; ".join(sorted({c.name for c in failed}))[:300],
        "problems": "; ".join(problems),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("files", nargs="*", help="PDF file names (default: all in outputs/ocr/summary.csv)")
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    files = load_pages(set(args.files))
    rows = []
    for name in sorted(files):
        try:
            rows.append(audit(name, files[name]))
        except Exception as exc:  # report and continue with the next file
            rows.append({"file": name, "problems": f"{type(exc).__name__}: {exc}"})
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["file"])
        writer.writeheader()
        writer.writerows(rows)

    with_problems = [r for r in rows if r.get("problems")]
    checked = [r for r in rows if r.get("checks")]
    passed = sum(r["checks"] - r["failed"] for r in checked)
    total = sum(r["checks"] for r in checked)
    for r in with_problems:
        print(f"{r['file']}: {r['problems']}")
    print(
        f"\n{len(rows)} files, {len(with_problems)} with problems; "
        f"unit found {sum(bool(r.get('unit')) for r in rows)}, "
        f"period found {sum(bool(r.get('period_end')) for r in rows)}; "
        f"checks passed {passed}/{total} ({100 * passed / max(total, 1):.1f} %), "
        f"files with every check passing {sum(r['failed'] == 0 for r in checked)}/{len(checked)}\n-> {OUT}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
