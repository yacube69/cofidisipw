# Reading layer and OCR settings (CIPW-7, tuned in CIPW-25)

`src/extraction/reading.py` returns `PageWords` (words + boxes in PDF points, top-left origin) for any page:

| Page | Detected by | Read with |
| --- | --- | --- |
| Digital PDF | real text, no full-page image, clean characters | PDF text layer (`page.get_text("words")`) |
| Scan | one image covers > 60 % of the page | **our Tesseract OCR** |
| Scan with an OCR layer from the scanner software | same as scan | **our Tesseract OCR**; the existing layer is ignored |
| Digital PDF with broken encoding | < 90 % normal characters or no digits | Tesseract OCR |

Why the scanner's OCR layer is ignored: on `26161516_2025` (Adobe Paper Capture) it misread a third of the shaded totals, e.g. `46 863 840` → `46163840`, `24 967 588` → `24H7518`.

## Default OCR configuration

`OcrConfig()` in `src/extraction/ocr.py`:

1. Render at **300 DPI**.
2. **Shading removal:** if the page has coloured row shading, use the colour channel in which the shading is lightest (red for the usual pink totals). Text stays dark, shading becomes near-white.
3. **Deskew** from long horizontal lines (Hough); the angle is stored in `PageWords.skew`. Boxes are mapped back to the original page; the parser straightens them with `layout.straighten()`.
4. **Adaptive threshold** (Gaussian, block 31, C 15).
5. **Table-line removal** (morphological opening, horizontal and vertical).
6. Tesseract `lang=ces`, `--psm 6`, `preserve_interword_spaces=1`.

## Measurements on the real scan (26161516_2025, pages 20–22, 108 ground-truth numbers)

Numbers found exactly anywhere on the page (simple recall, before the parser):

| Configuration | Recall | Mean conf | Time (3 pages) |
| --- | --- | --- | --- |
| grayscale + Otsu, psm 6 (first version) | 37 / 108 | 81.9 | 26 s |
| max(R,G,B) + Otsu, psm 6 | 42 / 108 | 73.4 | 27 s |
| red channel + Otsu, psm 6 | 38 / 108 | 73.4 | 27 s |
| red channel + fixed threshold 150, psm 4 | 70 / 108 | 71.7 | 28 s |
| **red channel + adaptive, psm 6 (default)** | **69 / 108** | **81.0** | **29 s** |
| red channel + adaptive, psm 4 | 70 / 108 | 81.1 | 29 s |
| red channel + adaptive, 400 DPI | 43 / 108 | 51.9 | 55 s |
| red channel + adaptive, table lines kept | 67 / 108 | 36.8 | 31 s |

Plain rows are read almost perfectly. The remaining misses are bold digits on shaded total rows (`24 967 588` → `24067 508`). They are caught by the accounting checks (CIPW-10) and re-read with other settings (CIPW-11); a grid search over more settings is CIPW-25.

## Debugging

```bash
python -m src.extraction.reading data/raw/26161516_2025_statement.pdf 20
```

writes `outputs/ocr/<file>_p20.json` and a PNG with every word box (green = confidence ≥ 70, red below).
