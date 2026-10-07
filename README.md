# cofidisipw

Extraction of Czech financial statements (PDF, including scanned) → validated data → financial ratios, bankruptcy models and an AI assessment, shown in a Streamlit app.

## Structure

```
data/raw/              # downloaded PDFs (not committed, see shared drive)
data/ground_truth/     # hand-transcribed reference values
config/line_items.yaml # line codes and label variants for the parser (CIPW-5)
src/schema.py          # data model (CIPW-5)
src/extraction/        # page locator, text/OCR layer, parser, vision baseline
src/validation/        # accounting checks, re-read logic
src/ratios/            # ratio engine, bankruptcy models
src/report/            # AI assessment
app/                   # Streamlit app
prompts/               # versioned prompts (vision baseline, AI report)
docs/                  # ratio definitions, OCR settings, decisions
scripts/               # evaluation and helper scripts
tests/
```

## Setup

### 1. Tesseract OCR with Czech language data

| OS | Install |
|---|---|
| Linux (Debian/Ubuntu) | `sudo apt install tesseract-ocr tesseract-ocr-ces` |
| macOS | `brew install tesseract tesseract-lang` |
| Windows | [UB Mannheim installer](https://github.com/UB-Mannheim/tesseract/wiki) (or `winget install UB-Mannheim.TesseractOCR`); in the installer select **Additional language data → Czech** |

Windows: if `tesseract` is not on `PATH`, set `TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe` in `.env`.

Windows without admin rights (silent/winget install includes only `eng`): download
[`ces.traineddata`](https://github.com/tesseract-ocr/tessdata/raw/main/ces.traineddata) into a user folder,
e.g. `%LOCALAPPDATA%\tessdata`, copy `eng`/`osd` from `C:\Program Files\Tesseract-OCR\tessdata` next to it,
and set `TESSDATA_PREFIX` to that folder in `.env`.

Check that `ces` is listed:

```bash
tesseract --list-langs
```

Alternatively use the dev container (`Dockerfile`, `.devcontainer/`) — VS Code → *Reopen in Container*. It has Tesseract with Czech data preinstalled.

### 2. Python 3.11+

```bash
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 3. API key

```bash
cp .env.example .env           # then fill in ANTHROPIC_API_KEY
```

**Never commit `.env` or API keys.** The key is shared privately within the team; the account has a spending limit.

## Smoke tests

```bash
python scripts/hello_ocr.py            # OCR of a built-in Czech sample; or pass a PDF/image path
python scripts/hello_llm.py            # one test LLM call
pytest
ruff check .
```

All of them should end with `OK` / pass.

## Team conventions

- One branch per task, named after the issue, e.g. `CIPW-32-parser`.
- Issue ID in every commit message, e.g. `CIPW-32: add line code matcher`.
- Every PR is reviewed by one other team member before merge.
- Decisions go to `docs/decisions.md`.
