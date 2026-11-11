# Changelog

What changed for the people who use Fides, one section per version. The version is set in `pyproject.toml` (the web app's `web/package.json` must match, `tests/test_api.py` checks it). A tag `vX.Y.Z` publishes a GitHub release with the section of that version and the Docker image `ghcr.io/yacube69/cofidisipw:X.Y.Z` (`docs/run.md`).

## Unreleased

### The app

- **Check values by hand.** In the Extraction step, a value to review is confirmed with one click, or the right amount is typed in or picked from the other readings.
  - The checks, ratios, bankruptcy models, traffic lights and the assessment are computed again at once.
  - A verified value is no longer flagged, and OCR never overwrites it.
  - Every change is kept with the result: in *About this result*, the JSON export and the PDF report.
- **The company from ARES.** Fides looks the IČO up in the public register ARES: name, legal form, address, industry (CZ-NACE) and size. The industry sits in the header, everything else in *About this result*.
- **One company name in the app and the PDF.** When ARES names the same company as the statement, its name is shown, so a name misread by OCR on a scan is not. When ARES names another company (a misread IČO), the printed name stays and the PDF shows none of the other company's data (`docs/ares.md`).
- **Wrong IČO warnings.** A warning appears when the IČO has a wrong check digit, is not in ARES, or belongs to a company of another name.
- **Works offline.** Without network the companies of our dataset come from a saved snapshot (`docs/ares.md`).

### Analysis

- **Traffic lights for the ratios (CIPW-14).** Every ratio is rated green, amber or red against temporary project limits, and the company's industry is compared with MPO sector values.
  - Liquidity above the usual range is amber, never red: it is no risk for a lender.
  - The industry follows CZ-NACE 2008, as the MPO statistics do.
- **Bankruptcy models are a list.** A model is defined in one place (`MODELS` in `src/ratios/bankruptcy.py`) and the app shows a card for each. Models that give the probability of bankruptcy within a period, such as a 12-month default model, are supported, and so are models where a higher score means more risk. Results saved before still open (`docs/ratios.md`).

### Report

- **PDF report in Cofidis' layout.**
  - Contents: company, the overall risk with the bankruptcy scores, the AI's summary, strengths, risks and red flags, key figures, the ratios with their traffic lights and the industry values, the bankruptcy models, and data quality.
  - The numbers come straight from the statements, never from the AI.
  - The Fides logo and the line "Vytvořeno v programu Fides, který vznikl ve spolupráci s ČVUT v rámci projektu IPW" sit in the footer of every page.
  - Cofidis' own logo and colours from its brand toolkit, set in `config/fides.yaml` (`docs/report.md`).
- **Export PDF** in the header of a result downloads the report (`fides_IČO_year.pdf`).
- **An assessment without an AI model.** Offline, without an API key and until the AI report is built, fixed rules build the summary, strengths, risks, red flags and the overall risk from the numbers.
  - The app and the PDF say that the text comes from rules.
  - Values to review are marked "ověřit".
  - Without bankruptcy scores the overall risk is not rated.
- **The samples are up to date:** company from ARES, real traffic lights and the rule-based assessment.

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
