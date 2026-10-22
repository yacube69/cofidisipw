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
7. **Grey profile, second pass:** a page without coloured shading whose first reading is weak (mean confidence < 75) or which is more than 15 % mid-grey is read again with a median blur (5) and a wider, stricter adaptive window (51/25). This removes the halftone dots of grey-shaded total rows on scanned justice.cz forms. The reading with more numbers read at confidence ≥ 80 is kept, so the second pass never makes a page worse; it only costs time on weak pages.

| Grey-shaded scanned form (`27114147_2024`, p. 9), 18 numbers on shaded rows | Found |
| --- | --- |
| default (adaptive 31/15) | 5 / 18 |
| median 5 + adaptive 51/25 (grey profile) | 16 / 18 |
| median 5 + fixed threshold 140 | 18 / 18, but only 38 / 108 on the pink-shaded Mondi scan |

A fixed share-of-grey threshold to pick the profile did not work: on a sample of 22 real pages the grey profile was clearly better on 10 and clearly worse on 4 (e.g. 128 → 43 confident numbers), hence the best-of-two rule.

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

## Speed (CIPW-50)

Almost all the time of a scan goes to Tesseract. Two changes, both with exactly the same output as before (the same pages, words and values on every file checked):

1. **Pages side by side** (`src/extraction/parallel.py`): the page locator, the `read` stage and the re-read of doubted cells (CIPW-11) run Tesseract on several pages or cells at once. Threads are enough, because Tesseract runs as its own process; PyMuPDF is called under a lock, as it must not be used by two threads at once. Results keep the page order. On this PC (i5-8600K, 6 cores) 6 workers with 1 Tesseract thread each read the 6 statement pages of `27114147_2024` in 49.9 s; 3 workers × 2 threads 53.1 s, 2 workers 57.5 s, one page at a time 84.6 s.
2. **Light page locator with the full one as the fallback** (`src/extraction/locate.py`): on a PDF with at least 10 scanned pages, the locator first OCRs only the top third of every page (titles, the column header, AKTIVA CELKEM). As soon as a top shows a statement, the whole page and its neighbours are read (while the other tops are still being read), then the pages next to what was found until the statements are complete. If the result is incomplete (no balance sheet, a balance sheet without its assets or liabilities, an income statement title not followed), the remaining pages are read whole and located again: that is the full locator as before, it decides whenever the light one is not sure. The stage note says which one decided ("light (9 of 37 scanned pages read whole)", "full (balance sheet without its assets after the light pass)").

**Pipeline times** (`python -m src.pipeline … --skip-report`, offline):

| PDF | before | now |
| --- | --- | --- |
| `27114147_2024`, scan without text layer, 37 pages | 193.7 s (locate 67.7, read 97.8, re-read 27.5) | **70.7 s** (locate 15.5, read 48.6, re-read 5.8) |
| `26161516_2025`, scan with the scanner's text layer, 38 pages | 53.6 s | **24.6 s** |

**Light vs full locator** on the 42 index PDFs with scanned pages: the same pages on all 42; the light one decided on 20, fell back 3 times, the other 19 have fewer than 10 scanned pages and are read whole at once. Index accuracy unchanged (67 of 68, the known Shoptet failure). On 6 cores both take about the same time in total (665 s vs 678 s): the light one wins on long PDFs (`60193336_2025`, 194 pages: 65 s vs 110 s) and loses a few seconds on mid-size scans and on a fallback. It reads 35 % less (the top third instead of whole pages), so it gains more on a machine with fewer cores, such as a small server running the Docker image.

Settings (`config/fides.yaml`, `performance`): `ocr_workers` (0 = one per CPU core), `tesseract_threads` (1), `light_locator` (true). `scripts/run_reading.py` runs files in parallel processes and reads the pages of each one at a time.

## Batch run on all statements (`scripts/run_reading.py`)

```bash
python scripts/run_reading.py            # every PDF in data/raw/, 5 worker processes
python scripts/run_reading.py --index    # only data/index.csv
```

Locates the statement pages, reads them, writes `outputs/ocr/{file}_p{page}.json`, a debug PNG per page and `outputs/ocr/summary.csv` (source, page kind, fallback, words, mean confidence, share of words below 60, skew, seconds per page). Results of the run on 19 Oct 2026 are in the CIPW-7 comment.

## Debugging

```bash
python -m src.extraction.reading data/raw/26161516_2025_statement.pdf 20
```

writes `outputs/ocr/<file>_p20.json` and a PNG with every word box (green = confidence ≥ 70, red below).
