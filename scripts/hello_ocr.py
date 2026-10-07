"""Smoke test: OCR one page and print the text.

Usage:
    python scripts/hello_ocr.py                      # renders a built-in Czech sample
    python scripts/hello_ocr.py path/to/file.pdf [page_number]
    python scripts/hello_ocr.py path/to/image.png
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytesseract
from dotenv import load_dotenv
from PIL import Image

SAMPLE_TEXT = [
    "ROZVAHA v plném rozsahu",
    "AKTIVA CELKEM 001 12 345",
    "Dlouhodobý majetek 003 6 789",
    "Oběžná aktiva 037 5 556",
    "Tržby z prodeje výrobků a služeb",
]


def configure_tesseract() -> None:
    load_dotenv()
    cmd = os.getenv("TESSERACT_CMD")
    if cmd:
        pytesseract.pytesseract.tesseract_cmd = cmd


def sample_image() -> Image.Image:
    """Render the sample text into an image via PyMuPDF.

    insert_htmlbox falls back to bundled Noto fonts, so Czech diacritics render correctly
    (the base-14 PDF fonts lack glyphs like ě, ž, ů).
    """
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=595, height=300)
    html = "".join(f'<p style="font-size:16px;margin:0 0 20px 0">{line}</p>' for line in SAMPLE_TEXT)
    page.insert_htmlbox(pymupdf.Rect(40, 30, 560, 290), html)
    return page_to_image(page)


def page_to_image(page, dpi: int = 300) -> Image.Image:
    pix = page.get_pixmap(dpi=dpi)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def load_image(path: Path, page_number: int = 1) -> Image.Image:
    if path.suffix.lower() == ".pdf":
        import pymupdf

        with pymupdf.open(path) as doc:
            return page_to_image(doc[page_number - 1])
    return Image.open(path)


def main(argv: list[str]) -> int:
    configure_tesseract()

    try:
        langs = pytesseract.get_languages(config="")
    except pytesseract.TesseractNotFoundError:
        print("ERROR: tesseract binary not found. Install it (see README) or set TESSERACT_CMD in .env.")
        return 1

    print(f"Tesseract {pytesseract.get_tesseract_version()}, languages: {', '.join(sorted(langs))}")
    if "ces" not in langs:
        print("ERROR: Czech language data ('ces') is missing. See README.")
        return 1

    if len(argv) > 1:
        image = load_image(Path(argv[1]), int(argv[2]) if len(argv) > 2 else 1)
    else:
        image = sample_image()

    data = pytesseract.image_to_data(image, lang="ces", output_type=pytesseract.Output.DICT)
    confidences = [float(c) for c in data["conf"] if float(c) >= 0]
    text = pytesseract.image_to_string(image, lang="ces")

    print("-" * 60)
    print(text.strip())
    print("-" * 60)
    if confidences:
        print(f"Mean word confidence: {sum(confidences) / len(confidences):.1f}")
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
