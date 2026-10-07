# Decisions

Log of project decisions. Newest first. Format: date, decision, reason.

## 2026-10-07 — Environment setup (CIPW-4)

- **OCR:** Tesseract 5 with Czech data (`ces`), called via `pytesseract`. A Dockerfile / dev container is the reference environment so everyone can run the same Tesseract version.
- **PDF:** PyMuPDF for rendering pages and reading the text layer, pdfplumber for table/word geometry.
- **LLM:** Anthropic API (`anthropic` SDK), model set via `LLM_MODEL` in `.env`. Used for the AI report (CIPW-16) and the vision baseline (CIPW-8). Spending limit set in the provider console.
- **Python:** 3.11+.
