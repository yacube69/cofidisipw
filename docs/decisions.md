# Decisions

Log of project decisions. Newest first. Format: date, decision, reason.

## 2026-11-12 — Local accounts and storage in SQLite, the backend stays in Python (CIPW-62, CIPW-60)

- **Decision:** sign-in with local accounts (`auth.mode: local`) and one SQLite file (`outputs/fides.db`) for the results, their PDFs and corrections, the users and the sessions. One admin account is created at the first start; its password comes from `.env` or is generated into `outputs/admin-password.txt`, never from the code. Active Directory and SSO stay options for Cofidis (`docs/users.md`, CIPW-59).
- **Why:** a shared Cofidis server needs to know who analyses and corrects, and must not lose work at a restart. SQLite needs no database server and only Python's standard library; a request costs one read of its session.
- **Kotlin was considered for the backend and dropped:** the heavy work (OCR, reading the PDF) is Python and stays Python. A Kotlin API in front of it would add a JVM to the image (more memory), a call between two runtimes for every analysis, and a second language to maintain, which is heavier for the server, not lighter.
- **Affected tasks:** CIPW-59, CIPW-60, CIPW-62.

## 2026-11-11 — Mistral is the language model of the AI report (CIPW-58)

- **Decision:** in production the AI report (CIPW-16) uses Mistral, through Mistral's chat completions API or a Mistral model on a server of Cofidis' own with the same interface. The default model is `mistral-medium-3.5`, the one Cofidis uses internally. The key comes from `MISTRAL_API_KEY`. This replaces Anthropic for the AI report (entry of 2026-10-07); Anthropic stays only in the vision baseline experiment (CIPW-8).
- **Why:** Cofidis wants it. A model on its own server also keeps the statements inside its network, which fits a server of its own (CIPW-61).
- **How:** `src/llm.py` (`ask_json`) on the HTTP client of the vision baseline, no SDK; settings `llm.url`, `llm.model`, `llm.timeout`, token prices (`docs/llm.md`). Without a model the rule-based assessment stays (CIPW-55).
- **Open, for Cofidis (CIPW-61):** which model, Mistral's API or its own server, who provides the key.
- **Affected tasks:** CIPW-16, CIPW-17, CIPW-58, CIPW-61.

## 2026-11-12 — Partial rating on, rules table in every report, Czech review (CIPW-16)

- **Decision:** companies without a bankruptcy score (usually no income statement) get a partial rating from the balance sheet alone (R7 high risk when the current ratio is below 1, R8 medium risk, never low risk) and therefore also a recommendation. `report.rating.partial` is `true` in `config/fides.yaml`; a rating with it carries `rules_version: proposal-1+partial`. Every report ends with the table of the rules (R1 to R8) in its own language. The Czech reviewers' corrections are in `prompts/translation_v2.md` (the English report and its prompt are unchanged) and "aktuální rok" in `src/report/texts.py`.
- **Why:** Marek asked to give what can be calculated; the Czech reviewers found the wording "běžný rok", "za období končící" and "celkem aktiv" unnatural.
- **Still open:** Cofidis' criteria for the rules (see below); Marek said to keep the current reports and give instructions later, the rules are settings and can change after the merge.

## 2026-11-04 — Rating by rules, the LLM only explains it (CIPW-16)

- **Decision:** the overall risk rating (read as low risk / medium risk / high risk, or no rating; high risk is the worst) is calculated by fixed rules from the numbers (`src/report/rating.py`). The LLM writes the summary, strengths, risks, red flags and the reasoning; the rating in its answer is always replaced by the rules' value. A company whose bankruptcy scores cannot be computed gets no rating, with an explanation. The report is in English for now.
- **Rules** (version `proposal-1`, the first one that applies decides):

  | rule | condition | risk rating |
  | --- | --- | --- |
  | R1 | equity is negative | high risk |
  | R2 | IN05 and Altman Z′ are both in the distress zone | high risk |
  | R3 | one of the two models is in the distress zone and the period ends with a loss | high risk |
  | R4 | no bankruptcy score could be computed (usually no income statement in the filing) | no rating |
  | R5 | both models are in the safe zone, the period is not a loss, all accounting checks passed and no score is computed from values flagged for review | low risk |
  | R6 | everything else | medium risk |

- **Why:** same numbers give the same rating every time (the LLM text changes slightly from one generation to the next), every step can be explained and checked, and the rating cannot be argued away by a well-written paragraph.
- **How:** the 15 ground truth reports (prompt v3, Gemini) were checked automatically against the input data: 380 numbers, none wrong. The rules are a proposal (`rules_version: proposal-1`). Everything still waiting for an answer is a setting in `report:` of `config/fides.yaml` (language, provider and model, risk threshold, rules left out, partial rating); see `docs/assessment.md`.
- **Still open (questions to Marek and Marvin):** Cofidis' own criteria for assessing a company (the draft is too generous for DSC Art and perhaps too harsh for ZEVO, and debt is not part of the rules); a partial rating from the balance sheet for the 7 companies without an income statement (the team leaning towards yes if it is little work); the 20 % worsening threshold; the report language if other branches use the tool; LLM provider, model and project API key (no answer yet; Gemini free tier, 20 requests a day per model, is used for the tests).
- **Affected tasks:** CIPW-16, CIPW-17, CIPW-18.

## 2026-10-21 — Web app in React + TypeScript over a Python API, no desktop installer (CIPW-41)

- **Decision:** the user interface is a web app (React + TypeScript, `web/`) served by a small FastAPI app (`src/api.py`) over the existing pipeline. Python stays for reading, OCR, parsing, checks, ratios and the report. Both run in the one Docker image on port 8000 (`docker compose up`, then the browser). There is no Streamlit app and no `.exe` installer.
- **Why:** reviewing extracted values needs boxes drawn on the PDF page that react to hover and click, which Streamlit can only approximate; a typed result contract keeps the Python and UI sides apart. A desktop installer was considered and dropped: Cofidis approved the Docker delivery, an unsigned `.exe` needs IT approval in a bank, and Cofidis can put the same container on an internal server (browser access for a whole team, their own sign-in) or build on the documented API (`/api/docs`).
- **How:** layout "overview first" with "the document next to the data" inside, chosen from three variants; `docs/app.md` describes the screens, API and samples. CI builds and tests the web app and starts it inside the image.
- **Affected tasks:** CIPW-41, CIPW-18, CIPW-42, CIPW-46, CIPW-47.

## 2026-10-19 — Page locator scope and acceptance (CIPW-6)

- **Decision:** the page locator targets Czech companies with statements from 2022 on (older years from 2016 use the same layout and work too). Banks and investment companies are out of scope for now. One extra page per statement is acceptable; a missing page is a failure. The separate IFRS statement of other comprehensive income (OCI) is not part of the income statement (it may become a separate output, pending input from Marek).
- **Why:** Cofidis lends mainly to private partners; an extra page only costs some OCR time in CIPW-7, a missing page loses data.
- **How:** `tests/test_locate.py` compares the locator with `expected_pages` in `data/index.csv` using this tolerance. Clean test on unseen filings within this scope: 26/28 exact, 27/28 with one extra page. Image-only scans run only with `LOCATOR_FULL=1` to keep CI fast.
- **Affected tasks:** CIPW-6, CIPW-7, CIPW-8.

## 2026-10-09 — Delivery as a Docker container (CIPW-47, approved by Cofidis)

- **Decision:** Fides is delivered as a Docker image with Tesseract + Czech data and the app inside, started with `docker compose up` (app on `http://localhost:8000` since 2026-10-21, `data/` and `outputs/` as volumes). The same image is used for the demo laptop, the handover and a possible production deployment at Cofidis.
- **Why:** documents stay on the user's machine or inside Cofidis infrastructure; no Tesseract/Python installation for the user; identical environment everywhere (the OCR results depend on the Tesseract version); CI already builds, tests and publishes the image.
- **How:** CI publishes `ghcr.io/yacube69/cofidisipw:main` from `main` and `:<version>` from `v*` tags; the release also ships an offline `docker save` archive. Native install scripts are only a fallback for team laptops. Streamlit Community Cloud is not used.
- **Affected tasks:** CIPW-47, CIPW-18, CIPW-49, CIPW-31, CIPW-28, CIPW-29.

## 2026-10-07 — Environment setup (CIPW-4)

- **OCR:** Tesseract 5 with Czech data (`ces`), called via `pytesseract`. A Dockerfile / dev container is the reference environment so everyone can run the same Tesseract version.
- **PDF:** PyMuPDF for rendering pages and reading the text layer, pdfplumber for table/word geometry.
- **LLM:** Anthropic API (`anthropic` SDK), model set via `LLM_MODEL` in `.env`. Used for the AI report (CIPW-16) and the vision baseline (CIPW-8). Spending limit set in the provider console. *Superseded for the AI report on 2026-11-11: Mistral (CIPW-58).*
- **Python:** 3.11+.
