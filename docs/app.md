# Fides web app (CIPW-41)

The app runs in the browser. Python does the work it is good at (reading the PDF, OCR, parsing, the checks, ratios and the report) and serves the results as JSON through a small API; the user interface is a React app that renders them. In the Docker image both run in one process on one port.

```
browser  ──>  http://localhost:8000/        React app (web/, built to web/dist)
         ──>  http://localhost:8000/api/…   FastAPI (src/api.py) ──> src/pipeline.py ──> Result
```

## Run

Step-by-step guide with installation and troubleshooting: [run.md](run.md).

| How | Command | Open |
| --- | --- | --- |
| Docker (Cofidis, demo) | `docker compose up`, or `docker run --rm -p 8000:8000 ghcr.io/yacube69/cofidisipw:main` | http://localhost:8000 |
| Development | `uvicorn src.api:app --reload`, and in `web/`: `npm install` once, then `npm run dev` | http://localhost:5173 (Vite forwards `/api` to port 8000) |
| API only | `uvicorn src.api:app` | http://localhost:8000/api/docs (OpenAPI) |

Settings (offline mode, model, OCR, upload limit) come from `config/fides.yaml` and the environment ([settings.md](settings.md)); **About this result** in the header shows the audit block of the result.

Nothing has to be installed on the user's computer apart from Docker. Fonts are bundled, so the app also works without internet access. The only outgoing call is the LLM for the AI report, and only when an `ANTHROPIC_API_KEY` is set.

## Screens

Layout chosen from three variants on 2026-10-21 (Mobbin references in `docs/brand/inspiration.md`): **overview first** (Xero business health, 1Password Watchtower) with **the document next to the data** for the extraction (Mistral Document AI).

**Navigation (2026-11-01):** the sidebar only navigates. *New analysis* opens the upload form; under it are the files analysed in this session and the samples. A result has its steps as tabs, and *About this result* and *Export JSON* in its header. The IČO used for benchmarks is edited in the Ratios step. Notes on what was read (pages, a format Fides does not read, unit, date) stay in view under the header; notes from the checks and the re-read (signs restored, cells read again) are folded when there are more than two.

**Colours and type:** one typeface, IBM Plex Sans, with tabular figures for numbers; IBM Plex Mono only for formulas and printed text. One accent, navy, for everything that can be clicked. **Red only for what failed and needs a person:** a failed check, a value to review, an error. A weak ratio, a distress zone or a high risk rating is a finding about the company, not a failure, so it is marked with an icon, not red. The brand red and yellow stay in the logo.

| Step | What the user sees |
| --- | --- |
| Overview | company, balance sheet date, scope, source; one card per step with its status; "Needs attention" (failed identities, values to review, weak ratios); the verdict |
| Extraction | the statement page with a box around every value (grey confident, amber check, red review) and the values of the page next to it. A click on a row highlights its box and the other way round; arrow keys move through the table; zoom 100–200 % |
| Validation | the accounting identities with status, period and difference; a value of a failed check opens on its page |
| Ratios | ratio tiles (current, prior, change, benchmark light), IN05 and Altman Z′ with their zone |
| AI report | rating, summary, strengths, risks, red flags, the number check of the report |

A value is **Review** when the parser flagged it, OCR confidence is below 75 or it breaks an accounting identity; **Check** when OCR confidence is 75–90; otherwise **Confident**. Steps that are still stubs show a "Mock data" badge and banner.

## API

| Endpoint | |
| --- | --- |
| `GET /api/health` | liveness |
| `GET /api/samples` | saved samples (open without OCR) |
| `GET /api/dictionary` | line items: key, code, Czech label, English name |
| `POST /api/analyse` | PDF (form field `file`, optional `scope` = individual / consolidated, `balance_sheet_pages` and `income_statement_pages` such as `75-76`, which replace the page locator) → result view |
| `GET /api/results/{id}` | result view: `Result` without `page_words`, plus `id`, `page_sizes` (PDF points) and `stubbed_stages` |
| `GET /api/results/{id}/pages/{page}` | page image (JPEG, 144 dpi) |
| `GET /api/results/{id}/export` | full `Result` JSON as a download |

Results of uploaded files live in memory (the last 20) and are gone after a restart. That is enough for one analyst; a shared server deployment would add sign-in and storage on the Cofidis side.

## Samples

`data/fixtures/result_*.json` with page images in `data/fixtures/pages/`, made with `python scripts/make_sample_result.py <pdf>` and registered in `SAMPLES` in `src/api.py`. The images let a sample open in the Docker image, which ships without `data/raw/*.pdf`.

## Code

| Path | |
| --- | --- |
| `src/api.py` | FastAPI app; also serves `web/dist` when it exists |
| `web/src/types.ts` | TypeScript types of the result view (keep in sync with `src/result.py` and `src/schema.py`) |
| `web/src/lib/` | API calls, Czech number formatting, value confidence, step summaries (unit tests in `lib.test.ts`) |
| `web/src/components/` | the navigation sidebar, the new analysis form and one component per step |
| `web/src/styles.css` | brand tokens from `docs/brand/fides.html`, light and dark mode |
| `web/src/App.test.tsx` | renders every step from the saved sample |

Checks: `pytest tests/test_api.py`; in `web/`: `npm run typecheck`, `npm test`, `npm run build`. CI runs all of them, and `docker.yml` starts the image and opens the app, a sample and a page image.
