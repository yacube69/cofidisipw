"""CIPW-7: text layer and Tesseract OCR layer produce the same PageWords format."""

import re
from pathlib import Path

import numpy as np
import pymupdf
import pytest

from src.extraction import ocr
from src.extraction.layout import NumberToken, group_lines, merge_number_tokens, straighten
from src.extraction.reading import page_kind, read_page, save_debug, summary, text_layer_ok, text_layer_quality
from tests.synthetic import make_digital_pdf, make_scanned_pdf

ROOT = Path(__file__).resolve().parents[1]
MONDI = ROOT / "data/raw/26161516_2025_statement.pdf"
MICRO = ROOT / "data/raw/25080776_2025_statement.pdf"


def _tesseract_ok() -> bool:
    try:
        return "ces" in ocr.pytesseract.get_languages(config="")
    except Exception:
        return False


needs_ocr = pytest.mark.skipif(not _tesseract_ok(), reason="Tesseract with Czech data not installed")


@pytest.fixture(scope="module")
def pdfs(tmp_path_factory):
    d = tmp_path_factory.mktemp("synthetic")
    digital = make_digital_pdf(d / "12345678_2025_statement.pdf")
    scan = make_scanned_pdf(digital, d / "12345678_2025_scan.pdf")
    return digital, scan


def numbers_on(pw) -> set[int]:
    """Every number on the page, thousands groups joined by position (not by text)."""
    found = set()
    for line in group_lines(straighten(pw)):
        for tok in merge_number_tokens(line.words):
            if isinstance(tok, NumberToken):
                digits = re.sub(r"[^\d-]", "", tok.text)
                if re.fullmatch(r"-?\d+", digits):
                    found.add(int(digits))
    return found


def test_page_kind_and_text_layer(pdfs):
    digital, scan = pdfs
    with pymupdf.open(digital) as d:
        assert page_kind(d[0]) == "digital"
        assert text_layer_ok(d[0])
        pw = read_page(d, 1)
    assert pw.source == "text_layer"
    assert pw.page == 1 and pw.width == pytest.approx(595)
    assert {1_250_000, 1_000_000, 900_000} <= numbers_on(pw)
    with pymupdf.open(scan) as d:
        assert page_kind(d[0]) == "scan"


def test_text_layer_quality_detects_garbage():
    assert text_layer_quality("AKTIVA CELKEM 001 46 863 840") == 1.0
    assert text_layer_quality("ÿþ\x01\x02\x03 ¤¤¤ ÞÞÞ") < 0.5


def test_shading_channel_found_for_pink_rows():
    img = np.full((200, 200, 3), 255, np.uint8)
    img[50:100] = (232, 154, 154)
    assert ocr.shading_channel(img) == 0
    assert ocr.shading_channel(np.full((200, 200, 3), 255, np.uint8)) is None


def test_estimate_skew_recovers_rotation():
    img = np.full((800, 1000), 255, np.uint8)
    for y in range(100, 700, 60):
        img[y : y + 2, 50:950] = 0
    rotated, _ = ocr.rotate(img, 1.5)
    assert ocr.estimate_skew(rotated) == pytest.approx(-1.5, abs=0.3)


@needs_ocr
def test_ocr_reads_synthetic_scan_with_shading_and_skew(pdfs):
    _, scan = pdfs
    with pymupdf.open(scan) as d:
        pw = read_page(d, 1)
    assert pw.source == "ocr" and pw.dpi == 300
    assert pw.mean_conf is not None and pw.mean_conf > 60
    # boxes are in PDF points on the page
    assert all(0 <= w.x0 < w.x1 <= pw.width + 1 and 0 <= w.y0 < w.y1 <= pw.height + 1 for w in pw.words)
    found = numbers_on(pw)
    expected = {1_250_000, 1_000_000, 900_000, 850_000, 600_000, 550_000, 120_000, 70_000}
    assert len(expected & found) >= 6, f"OCR found only {sorted(expected & found)}"


@needs_ocr
def test_ocr_boxes_match_text_layer_positions(pdfs):
    """The same word must land on (nearly) the same spot whether read by OCR or from the text layer."""
    digital, _ = pdfs
    with pymupdf.open(digital) as d:
        text = read_page(d, 1, mode="text")
        scanned = read_page(d, 1, mode="ocr")
    a = next(w for w in text.words if w.text == "ROZVAHA")
    b = next(w for w in scanned.words if w.text == "ROZVAHA")
    assert abs(a.cx - b.cx) < 4 and abs(a.cy - b.cy) < 4


def test_save_debug_writes_json_and_png(pdfs, tmp_path):
    digital, _ = pdfs
    with pymupdf.open(digital) as d:
        pw = read_page(d, 1)
        png = save_debug(d, pw, tmp_path)
    assert png.exists() and png.stat().st_size > 1000
    assert (tmp_path / f"{Path(pw.file).stem}_p1.json").exists()
    assert summary([pw])["pages"] == 1


@pytest.mark.skipif(not MICRO.exists(), reason="sample statement not available")
def test_real_micro_statement_uses_text_layer():
    with pymupdf.open(MICRO) as d:
        pw = read_page(d, 1)
    assert pw.source == "text_layer"
    assert "AKTIVA" in pw.text and 10000 in numbers_on(pw)


@needs_ocr
@pytest.mark.skipif(not MONDI.exists(), reason="sample statement not available")
def test_real_scan_ignores_scanner_text_layer_and_reads_totals():
    with pymupdf.open(MONDI) as d:
        assert page_kind(d[19]) == "scan"
        pw = read_page(d, 21)  # pasiva
    assert pw.source == "ocr"
    found = numbers_on(pw)
    # plain (unshaded) rows must be read exactly
    assert {2066000, 5450546, 2532204, 16547077} <= found
