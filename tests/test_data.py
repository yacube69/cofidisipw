"""The statement files in the repository are real PDFs and every row of data/index.csv has its file.

Renaming a PDF in the GitHub web editor can replace it with an empty line (it happened on 2026-10-21: three
statements became 2-byte files and CI stayed green, because the locator test skips missing files).
"""

import csv
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
MIN_SIZE = 1024  # the smallest real statement in the repository has about 70 kB


def tracked_raw_files() -> list[Path]:
    try:
        out = subprocess.run(["git", "ls-files", "data/raw"], cwd=ROOT, capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    return [ROOT / line for line in out.stdout.splitlines() if not line.endswith(".gitkeep")]


@pytest.mark.parametrize("path", tracked_raw_files(), ids=lambda p: p.name)
def test_every_file_in_data_raw_is_a_pdf(path):
    assert path.suffix == ".pdf", f"{path.name}: statement files end in .pdf"
    head = path.read_bytes()[:5]
    assert head == b"%PDF-", f"{path.name}: not a PDF (starts with {head!r}); restore it from the git history"
    assert path.stat().st_size >= MIN_SIZE, f"{path.name}: only {path.stat().st_size} bytes"


def test_every_index_row_has_its_file():
    tracked = {p.name for p in tracked_raw_files()}
    with (ROOT / "data" / "index.csv").open(encoding="utf-8") as f:
        missing = [row["file"] for row in csv.DictReader(f) if row["file"] not in tracked]
    assert not missing, f"in data/index.csv but not in data/raw/: {missing}"
