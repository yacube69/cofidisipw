"""Reading layer (CIPW-7): one function that returns words with positions for any page.

- **Digital PDFs** use their own text layer (exact, fast).
- **Scans** go through our Tesseract OCR. A scan that already carries an OCR text layer from the scanner
  software (e.g. Adobe Paper Capture) is treated as a scan too: on the real 2025 annual report that layer
  misread a third of the shaded totals (46 863 840 → 46163840), so we do not trust it.
- A text layer with broken encoding (garbage characters, no digits) falls back to OCR.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Literal

import cv2
import numpy as np
import pymupdf

from src.extraction.ocr import DEFAULT, OcrConfig, ocr_page, render
from src.extraction.types import PageWords, Word

Mode = Literal["auto", "text", "ocr"]
PageKind = Literal["digital", "scan", "empty"]

_ALLOWED = re.compile(r"[0-9A-Za-zÁČĎÉĚÍŇÓŘŠŤÚŮÝŽáčďéěíňóřšťúůýž .,;:()+\-/%*&'\"§–—]")


def page_kind(page: pymupdf.Page) -> PageKind:
    """'scan' when one image covers most of the page, 'digital' when there is real text, else 'empty'."""
    area = page.rect.width * page.rect.height
    for info in page.get_image_info():
        x0, y0, x1, y1 = info["bbox"]
        if (x1 - x0) * (y1 - y0) > 0.6 * area:
            return "scan"
    return "digital" if page.get_text().strip() else "empty"


def text_layer_quality(text: str) -> float:
    """Share of characters that look like normal Czech statement text (0–1)."""
    chars = [c for c in text if not c.isspace()]
    if not chars:
        return 0.0
    return sum(1 for c in chars if _ALLOWED.match(c)) / len(chars)


def text_layer_ok(page: pymupdf.Page) -> bool:
    text = page.get_text()
    return len(text.strip()) >= 20 and text_layer_quality(text) >= 0.9 and any(c.isdigit() for c in text)


def read_text_layer(page: pymupdf.Page, file: str = "") -> PageWords:
    start = time.perf_counter()
    words = [
        Word(text=w[4], x0=w[0], y0=w[1], x1=w[2], y1=w[3], conf=None)
        for w in page.get_text("words", sort=True)
        if w[4].strip()
    ]
    return PageWords(
        file=file,
        page=page.number + 1,
        source="text_layer",
        width=page.rect.width,
        height=page.rect.height,
        seconds=round(time.perf_counter() - start, 3),
        words=words,
    )


def read_page(
    doc: pymupdf.Document, page_no: int, mode: Mode = "auto", config: OcrConfig = DEFAULT, file: str = ""
) -> PageWords:
    """Words of one page (1-based `page_no`)."""
    page = doc[page_no - 1]
    file = file or Path(doc.name or "").name
    if mode == "text":
        return read_text_layer(page, file)
    if mode == "ocr":
        return ocr_page(page, file, config)
    if page_kind(page) == "digital" and text_layer_ok(page):
        return read_text_layer(page, file)
    return ocr_page(page, file, config)


def save_debug(doc: pymupdf.Document, page_words: PageWords, out_dir: str | Path, dpi: int = 150) -> Path:
    """Write `{file}_p{page}.json` and a PNG with every word box drawn (green = sure, red = conf < 70)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{Path(page_words.file).stem}_p{page_words.page}"
    (out_dir / f"{stem}.json").write_text(page_words.model_dump_json(indent=1), encoding="utf-8")

    img = cv2.cvtColor(render(doc[page_words.page - 1], dpi), cv2.COLOR_RGB2BGR)
    s = dpi / 72.0
    for w in page_words.words:
        color = (0, 160, 0) if w.conf is None or w.conf >= 70 else (0, 0, 220)
        cv2.rectangle(img, (int(w.x0 * s), int(w.y0 * s)), (int(w.x1 * s), int(w.y1 * s)), color, 1)
    path = out_dir / f"{stem}.png"
    ok, buf = cv2.imencode(".png", img)
    if ok:
        path.write_bytes(np.asarray(buf).tobytes())
    return path


def summary(pages: list[PageWords]) -> dict:
    """Mean confidence and time per page, for the CIPW-7 comment and the evaluation."""
    ocr = [p for p in pages if p.source == "ocr"]
    confs = [p.mean_conf for p in ocr if p.mean_conf is not None]
    return {
        "pages": len(pages),
        "ocr_pages": len(ocr),
        "mean_conf": round(sum(confs) / len(confs), 1) if confs else None,
        "seconds_per_ocr_page": round(sum(p.seconds or 0 for p in ocr) / len(ocr), 2) if ocr else None,
    }


if __name__ == "__main__":  # quick manual check: python -m src.extraction.reading file.pdf 20
    import sys

    path, page_no = sys.argv[1], int(sys.argv[2])
    with pymupdf.open(path) as d:
        pw = read_page(d, page_no)
        print(json.dumps({"source": pw.source, "words": len(pw.words), "mean_conf": pw.mean_conf}, indent=1))
        print(save_debug(d, pw, "outputs/ocr"))
