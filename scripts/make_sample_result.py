"""Save the app's sample: a full pipeline `Result` plus JPEG images of its statement pages (CIPW-41).

The app opens on this sample, so the demo works without running OCR and inside the Docker image, which ships
without data/raw/*.pdf.

Usage:
    python scripts/make_sample_result.py                                   # Mondi 2025
    python scripts/make_sample_result.py data/raw/07880383_2025_statement.pdf
"""

from __future__ import annotations

import sys
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.pipeline import run_pipeline  # noqa: E402
from src.result import PipelineOptions  # noqa: E402
from src.settings import get_settings  # noqa: E402

FIXTURES = get_settings().paths.fixtures
ZOOM = 2.0  # same as app/loading.py
JPEG_QUALITY = 80


def main(argv: list[str]) -> int:
    pdf = Path(argv[0]) if argv else get_settings().paths.raw / "26161516_2025_statement.pdf"
    result = run_pipeline(pdf, PipelineOptions())
    out = FIXTURES / f"result_{'_'.join(pdf.stem.split('_')[:2])}.json"
    out.write_text(result.model_dump_json(), encoding="utf-8")
    print(f"{out} ({out.stat().st_size // 1024} kB) {[(s.stage, s.status) for s in result.stages]}")

    pages = FIXTURES / "pages"
    pages.mkdir(exist_ok=True)
    with pymupdf.open(pdf) as doc:
        for page in sorted({p for ps in result.pages.values() for p in ps}):
            pix = doc[page - 1].get_pixmap(matrix=pymupdf.Matrix(ZOOM, ZOOM))
            path = pages / f"{pdf.stem}_p{page}.jpg"
            pix.save(path, jpg_quality=JPEG_QUALITY)
            print(f"{path} ({path.stat().st_size // 1024} kB)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
