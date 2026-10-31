# cofidisipw

[![CI](https://github.com/yacube69/cofidisipw/actions/workflows/ci.yml/badge.svg)](https://github.com/yacube69/cofidisipw/actions/workflows/ci.yml) [![Docker image](https://github.com/yacube69/cofidisipw/actions/workflows/docker.yml/badge.svg)](https://github.com/yacube69/cofidisipw/actions/workflows/docker.yml)

Extraction of Czech financial statements (PDF, including scanned) → validated data → financial ratios, bankruptcy models and an AI assessment, shown in a web app.

**Run it:** `docker run --rm -p 8000:8000 ghcr.io/yacube69/cofidisipw:main`, then open http://localhost:8000. Step-by-step guide for the team (Docker, from the repository, sharing on a network): [docs/run.md](docs/run.md).

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
src/api.py             # API for the web app (CIPW-41)
web/                   # web app: React + TypeScript (CIPW-41)
prompts/               # versioned prompts (vision baseline, AI report)
docs/                  # ratio definitions, OCR settings, decisions
scripts/               # evaluation and helper scripts
tests/
```

## Setup

### 1. Tesseract OCR

| OS | Install |
|---|---|
| Linux (Debian/Ubuntu) | `sudo apt install tesseract-ocr` |
| macOS | `brew install tesseract` |
| Windows | [UB Mannheim installer](https://github.com/UB-Mannheim/tesseract/wiki) (or `winget install UB-Mannheim.TesseractOCR`) |

The Czech model ships with Fides in `models/tessdata` (`ocr.tessdata` in `config/fides.yaml`), so a PDF is read the same on every machine and no language data has to be installed.

Windows: if `tesseract` is not on `PATH`, set `TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe` in `.env`.

Alternatively use the dev container (`Dockerfile`, `.devcontainer/`) — VS Code → *Reopen in Container*. It has Tesseract with Czech data preinstalled.

### 2. Python 3.11+

```bash
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 3. API key and settings

```bash
cp .env.example .env           # then fill in ANTHROPIC_API_KEY
```

All other settings (OCR, paths, model, offline mode) are in `config/fides.yaml` and can be overridden in `.env` or the environment, e.g. `FIDES_OFFLINE=true` to run without any LLM call. See [docs/settings.md](docs/settings.md).

**Never commit `.env` or API keys.** The key is shared privately within the team; the account has a spending limit.

## Smoke tests

```bash
python scripts/hello_ocr.py            # OCR of a built-in Czech sample; or pass a PDF/image path
python scripts/hello_llm.py            # one test LLM call
pytest
ruff check .
```

All of them should end with `OK` / pass.

## CI/CD (GitHub Actions)

| Workflow | Runs on | What it does |
| --- | --- | --- |
| `ci.yml` | every push to any branch, PRs from forks | installs Tesseract with Czech data, `ruff check`, `pytest` on Python 3.11 and 3.12, uploads the JUnit report; web app typecheck, `vitest` and build |
| `docker.yml` | every push, `v*` tags | builds the image, runs the OCR smoke test, a pipeline run and the web app inside it; on `main` and tags publishes `ghcr.io/yacube69/cofidisipw` (`main`, `sha-…`, version tags) |
| `release.yml` | `v*` tags | creates a GitHub release with notes generated from merged PRs |

Tests never call the paid LLM API: CI runs with an empty `ANTHROPIC_API_KEY`, LLM calls must be mocked.

Release: `git tag v1.0.0 && git push origin v1.0.0` → GitHub release + image `ghcr.io/yacube69/cofidisipw:1.0.0`.

Run the published image locally (documents stay on your machine):

```bash
docker run --rm -v "$PWD/data:/workspace/data" ghcr.io/yacube69/cofidisipw:main python scripts/hello_ocr.py
```

Recommended repository setting (Settings → Branches → `main`): require a pull request with 1 approval and the `CI` checks to pass before merging.

## Team conventions

- One branch per task, named after the issue, e.g. `CIPW-32-parser`.
- Issue ID in every commit message, e.g. `CIPW-32: add line code matcher`.
- Every PR is reviewed by one other team member before merge.
- Decisions go to `docs/decisions.md`.
