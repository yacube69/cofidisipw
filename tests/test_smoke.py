"""Environment smoke tests. OCR tests are skipped if Tesseract is not installed."""

import importlib
import shutil
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_DIRS = [
    "data/raw",
    "data/ground_truth",
    "config",
    "src/extraction",
    "src/validation",
    "src/ratios",
    "src/report",
    "app",
    "prompts",
    "docs",
    "scripts",
    "tests",
]


@pytest.mark.parametrize("rel", REQUIRED_DIRS)
def test_folder_exists(rel):
    assert (ROOT / rel).is_dir()


@pytest.mark.parametrize(
    "module",
    ["pytesseract", "cv2", "pymupdf", "pdfplumber", "rapidfuzz", "pydantic", "pandas", "anthropic", "dotenv"],
)
def test_dependency_importable(module):
    importlib.import_module(module)


def test_line_items_config_loads():
    config = yaml.safe_load((ROOT / "config/line_items.yaml").read_text(encoding="utf-8"))
    assert "balance_sheet" in config and "income_statement" in config


def test_env_is_ignored():
    assert ".env" in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()


def _tesseract_available() -> bool:
    import pytesseract

    try:
        pytesseract.get_tesseract_version()
        return True
    except pytesseract.TesseractNotFoundError:
        return shutil.which("tesseract") is not None


@pytest.mark.skipif(not _tesseract_available(), reason="Tesseract not installed")
def test_tesseract_has_czech():
    import pytesseract

    assert "ces" in pytesseract.get_languages(config="")


@pytest.mark.skipif(not _tesseract_available(), reason="Tesseract not installed")
def test_hello_ocr_runs():
    import sys

    sys.path.insert(0, str(ROOT / "scripts"))
    import hello_ocr

    assert hello_ocr.main(["hello_ocr.py"]) == 0
