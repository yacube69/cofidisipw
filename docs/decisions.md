# Decisions

Log of project decisions. Newest first. Format: date, decision, reason.

## 2026-10-19 — Page locator scope and acceptance (CIPW-6)

- **Decision:** the page locator targets Czech companies with statements from 2022 on (older years from 2016 use the same layout and work too). Banks and investment companies are out of scope for now. One extra page per statement is acceptable; a missing page is a failure. The separate IFRS statement of other comprehensive income (OCI) is not part of the income statement (it may become a separate output, pending input from Marek).
- **Why:** Cofidis lends mainly to private partners; an extra page only costs some OCR time in CIPW-7, a missing page loses data.
- **How:** `tests/test_locate.py` compares the locator with `expected_pages` in `data/index.csv` using this tolerance. Clean test on unseen filings within this scope: 26/28 exact, 27/28 with one extra page. Image-only scans run only with `LOCATOR_FULL=1` to keep CI fast.
- **Affected tasks:** CIPW-6, CIPW-7, CIPW-8.

## 2026-10-09 — Delivery as a Docker container (CIPW-47, approved by Cofidis)

- **Decision:** Fides is delivered as a Docker image with Tesseract + Czech data and the app inside, started with `docker compose up` (app on `http://localhost:8501`, `data/` and `outputs/` as volumes). The same image is used for the demo laptop, the handover and a possible production deployment at Cofidis.
- **Why:** documents stay on the user's machine or inside Cofidis infrastructure; no Tesseract/Python installation for the user; identical environment everywhere (the OCR results depend on the Tesseract version); CI already builds, tests and publishes the image.
- **How:** CI publishes `ghcr.io/yacube69/cofidisipw:main` from `main` and `:<version>` from `v*` tags; the release also ships an offline `docker save` archive. Native install scripts are only a fallback for team laptops. Streamlit Community Cloud is not used.
- **Affected tasks:** CIPW-47, CIPW-18, CIPW-49, CIPW-31, CIPW-28, CIPW-29.

## 2026-10-07 — Environment setup (CIPW-4)

- **OCR:** Tesseract 5 with Czech data (`ces`), called via `pytesseract`. A Dockerfile / dev container is the reference environment so everyone can run the same Tesseract version.
- **PDF:** PyMuPDF for rendering pages and reading the text layer, pdfplumber for table/word geometry.
- **LLM:** Anthropic API (`anthropic` SDK), model set via `LLM_MODEL` in `.env`. Used for the AI report (CIPW-16) and the vision baseline (CIPW-8). Spending limit set in the provider console.
- **Python:** 3.11+.
