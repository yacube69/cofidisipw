# Changelog

What changed for the people who use Fides, one section per version. The version is set in `pyproject.toml` (the web app's `web/package.json` must match, `tests/test_api.py` checks it). A tag `vX.Y.Z` publishes a GitHub release with the section of that version and the Docker image `ghcr.io/yacube69/cofidisipw:X.Y.Z` (`docs/run.md`).

## 0.2.0 — 2026-11-01

### The app

- **Calmer, corporate look.**
  - Red marks only what failed and needs a person: a failed check, a value to review, an error. Everything you can click is navy, and passed checks and confident values are neutral.
  - A weak ratio, a distress zone or a high risk rating is a finding about the company, so it is marked with an icon, not red.
  - One typeface, IBM Plex Sans, with tabular figures for numbers.
- **Clearer navigation.**
  - The sidebar only navigates: *New analysis*, the files analysed in this session, and the samples.
  - Uploading a PDF has its own screen.
  - *Export JSON* and *About this result* are in the result header, and the IČO for benchmarks is in the Ratios step.
- **Statement pages can be entered** when the page locator picks the wrong pages of a long annual report (e.g. `75-76` and `77`); the API takes them too.
- **A near-empty result says why.** IFRS statements and bank statements are not read yet (`docs/decisions.md`). A note also says when a scan is too poor to read, when only a few lines were found, or when no balance sheet or income statement was found.

### Extraction

- A number kept as one word of the text layer, with thin spaces between the thousands groups, is now read. Albatros Media 2024 gives 151 values instead of 3, and all 57 checks pass.
- The balance sheet is found even when it has no title and sits right before the income statement (Metrostav 2024).
- Every value carries its line match score and OCR confidence, including dashes and cells filled by the re-read.
- Vision LLM baseline for comparison (CIPW-8): `scripts/run_vision_baseline.py`, measured with the same evaluation as the parser (`docs/vision_baseline.md`).

### Measured

- Ground truth: 15 files, all verified by a second person; 96.1 % of 1 379 values correct, 3 wrong and not flagged.
- All 117 collected PDFs run through the whole pipeline without an error.

### Documentation

- `docs/run.md`: how to run Fides (Docker image, from the repository, on a network) and what to do when something goes wrong.

## 0.1.0

The first prototype, never tagged:
- page locator, text layer and Tesseract OCR, rule-based parser;
- accounting checks with a targeted re-read of doubted cells;
- 12 ratios, IN05 and Altman Z′;
- the web app and the Docker image.
