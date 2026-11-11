"""Render the PDF report of a saved result (CIPW-43), e.g. to review the template.

    python scripts/render_report.py data/fixtures/result_26161516_2025.json            # -> outputs/reports/
    python scripts/render_report.py data/fixtures/result_26161516_2025.json --lang en
    python scripts/render_report.py outputs/full_run/28377141_2024_statement.result.json --refresh

`--refresh` recomputes what later code adds to a saved result (company from ARES, ratios, bankruptcy models,
traffic lights) from its statements, so a result saved by an older version shows the current report.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.integrations.ares import lookup  # noqa: E402
from src.ratios.bankruptcy import compute_bankruptcy  # noqa: E402
from src.ratios.benchmarks import SOURCE, industry_benchmarks, industry_group, light  # noqa: E402
from src.ratios.engine import compute_ratios  # noqa: E402
from src.report.pdf import render_report, report_filename  # noqa: E402
from src.result import Result  # noqa: E402
from src.settings import get_settings  # noqa: E402


def refresh(result: Result) -> None:
    if result.statements.metadata.ico:
        result.company, _ = lookup(result.statements.metadata.ico)
    result.ratios = compute_ratios(result.statements, result.validation)
    result.bankruptcy = compute_bankruptcy(result.statements, result.validation)
    result.benchmarks = {
        "mock": False,
        "industry_group": industry_group(result.company),
        "industry_benchmarks": industry_benchmarks(result.company),
        "source": SOURCE,
        "ratios": {
            r.key: {"value": r.current.value, "light": light(r.key, r.current.value, r.current.flag)}
            for r in result.ratios.ratios
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("result", type=Path, help="a saved Result JSON (pipeline output or data/fixtures)")
    parser.add_argument("--lang", choices=["cs", "en"], help="label language (default: settings.report.language)")
    parser.add_argument("--refresh", action="store_true", help="recompute company, ratios, models and lights")
    parser.add_argument("--out", type=Path, help="output folder (default: outputs/reports)")
    args = parser.parse_args(argv)

    settings = get_settings()
    if args.lang:
        settings.report.language = args.lang
    result = Result.model_validate_json(args.result.read_text(encoding="utf-8"))
    if args.refresh:
        refresh(result)
    out = args.out or settings.paths.outputs / "reports"
    out.mkdir(parents=True, exist_ok=True)
    path = out / report_filename(result).replace(".pdf", f"_{settings.report.language}.pdf")
    path.write_bytes(render_report(result, settings))
    print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
