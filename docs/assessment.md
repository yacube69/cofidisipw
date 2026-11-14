# AI assessment report (CIPW-16)

Status: **first part, no LLM call yet.** This page describes what the LLM will get (input), what it must return
(output) and the proposed rule-based rating. The prompt, the LLM call and the reports for the ground truth
companies come after the team decisions listed at the end.

## Principle

The LLM writes the narrative only. It never sees the PDF. Everything it may say comes from one JSON summary of
verified data (`build_assessment_input`), computed without an LLM and therefore testable. Its output is checked
against that same input (CIPW-17).

## Input (`src/report/inputs.py`)

`build_assessment_input(statements, validation, ratios, bankruptcy, benchmarks=None)` returns a JSON-ready dict.
All amounts are in CZK.

| section | content |
| --- | --- |
| `company` | name, IČO, period ends, full/abbreviated statements, accounting standard |
| `key_figures` | 18 key lines, current and prior year, change in %, `needs_review` flag (revenue = I. + II.; net turnover = the line "Čistý obrat za účetní období" printed at the end of the income statement, a separate figure, not the same as revenue) |
| `ratios` | all ratios of the engine: formula, both years, change, flag (`ok` / `unreliable` / `not_meaningful` / `missing`), reason and the traffic light of the current year (`light`, CIPW-14) |
| `bankruptcy` | a list with every model of `src.ratios.bankruptcy.MODELS`: `key`, `name`, `zones`, `horizon` and, for both years, score, probability (models with a 12-month horizon), zone, flag and reason |
| `benchmarks` | industry comparison (CIPW-14). Today `{"available": false}`: the stage is a stub and mock data never gets in |
| `validation` | accounting checks passed or not, failed checks (up to 15 named), counts |
| `data_quality` | `good` / `limited` / `poor` and the notes an analyst must know (flagged values, failed checks, ratios not computable, missing benchmarks) |

`assessment_input_from_result(result)` builds the same from a pipeline `Result`.

Try it on the ground truth, no PDF, no LLM:

```
python scripts/build_assessment_inputs.py                 # table of all companies + JSON in outputs/report_inputs/
python scripts/build_assessment_inputs.py 26161516_2025   # one company
```

## Output (`src/report/models.py`)

`AssessmentReport`, the format the mock report and the app already use: `language`, `summary` (3 to 5 sentences),
`strengths`, `risks`, `red_flags`, `rating` (`value`: low / medium / high or none, plus `reasoning`) and
`data_quality_note`.

## Risk rating: proposal, not decided (step 4 of the ticket)

Wording: readers see "high risk", "medium risk", "low risk" or "no rating" (a plain "high" reads like a good mark); the data keep `low` / `medium` / `high`.

Option A (recommended by the ticket): the rating is calculated by rules from the numbers; the LLM only explains it.
Same numbers, same rating, every step auditable. Option B: the LLM chooses. `src/report/rating.py` is a written
version of option A, so the team can read it, run it and change it. `rules_version` is `proposal-2`. It is the **only** module that decides the rating: the pipeline, the app and the PDF (`fallback.py`) take it from `rate()` too.

| rule | condition (current year) | risk rating |
| --- | --- | --- |
| R1 | equity is negative | high |
| R2 | every model with a score is in the distress zone, and at least two have a score | high |
| R3 | a model in the distress zone and a loss for the period | high |
| R4 | no model has a score | no rating |
| R5 | every model has a score and is safe, profit not negative, no failed accounting check, no score from flagged values | low |
| R6 | everything else | medium |
| R7 / R8 | partial rating only (`report.rating.partial`): no score and current ratio below 1 / 1 or more | high / medium |
| R9 | 2 or more ratios with a red traffic light: high; 1 red ratio or 3 or more amber ratios: at least medium. Checked after R1 to R8, it only makes the rating worse; `disabled_rules: [R9]` switches it off | high / medium |

The rules are written over the list of bankruptcy models of the input, so a model added to `MODELS` (for example Cofidis'
12-month default model) takes part in R2, R3 and R5 by itself, and a model that cannot be computed never makes the
rating better. The reasoning of the rating is written in Czech or English (`rate(data, config, language)`).

Industry benchmarks are not used; the project's own traffic lights of CIPW-14 are (R9).

## Limits seen on the ground truth

Several ground truth files have no income statement lines (profit before tax or revenue not recorded), so IN05 and
Altman Z′ cannot be computed and R4 gives no rating. This is a limit of the ground truth, not of the rules; it
affects step 5 (reports for all ground truth companies). Run the script above to see which companies.

## Recommendation and languages (9 Nov, answer of Cofidis: English and Czech, tables with ratios, text and a recommendation, PDF or MS Office)

The report ends its narrative with a **recommendation for the analyst**: `Recommended`, `Recommended with caution (further
review)`, `Not recommended` or `No recommendation (insufficient data)`. It is **not written by the AI**: the code derives
it from the risk rating by a table (`src/report/recommendation.py`, `report.recommendation` in `config/fides.yaml`:
low = recommended, medium = caution, high = not recommended, no rating = no recommendation) and lists the **criteria**
behind it: the rule that decided (R1 to R9), every bankruptcy model with its zone, equity, the result for the period and
the accounting checks. The recommendation is indicative; the decision stays with the analyst. The table is a proposal
until Cofidis names its own criteria; changing it needs no new model call (the recommendation is added to saved answers).
It is `AssessmentReport.recommendation` (optional, filled by the code, not asked of the model).

**Languages (10 Nov): written once, then translated.** The report is written in English (prompt `assessment_v8.md`);
another language (`report.language` or `--language cs`) is the **translation of that English report**
(`prompts/translation_v4.md`, `generate.translate_report`), so every language says the same. Writing each language
from scratch was tried on Skyport and gave different risks in English and Czech. The code checks the translation: the
same number of items in each list (otherwise an error, nothing is saved) and every number of the English text in the
translation (a difference is a warning in the run and in the JSON). Rating and recommendation are set by the code, not
by the model. A translation needs the English report: `--language cs` uses the saved English answer, or writes it
first (and keeps the English page too). The fixed texts (headings, table labels, rating and recommendation wording,
units) come from `src/report/texts.py`, a dictionary per language; another language falls back to English for them.
Czech amounts read `29,46 mld.` and `176,52 mil.` with a decimal comma. Czech reports go to `outputs/reports_cs`,
English to `outputs/reports`. The review pack explaining the rules (`rules_table`) is English only. A native Czech
reader should check the Czech text. In the app the user would choose the language and get the translation of the
one analysis.

## Word file (`--docx`)

`python scripts/generate_reports.py <files> --language cs --docx` also writes one Word file with the reports of the
given companies, one after the other (`outputs/reports/Fides_AI_assessment_EN.docx`, `outputs/reports_cs/..._CS.docx`);
answers already saved cost no call. It is the report page laid out for reading (`src/report/export_docx.py`: Calibri, a
colour for each risk rating, tables with a dark header, header and page number); text, numbers, tables, rating and
recommendation are those of the page, and the review checklist is left out. Needs `python-docx` (in `requirements.txt`).

## Prompt (`prompts/assessment_v8.md`, v1 to v7 kept)

System prompt for the LLM: experienced credit analyst, only numbers from the input, exact values with their year,
no speculation, nothing built on flagged values, no industry comparison without benchmarks, rating copied from the
rules (the LLM explains it, never changes it). `{{LANGUAGE}}` is filled in at run time. A new prompt version is a new
file; the content hash goes into `audit.prompt_version`. v2 followed the first four Gemini reports (Mondi, Power Reality,
Skyport, Program H plus). v3 follows seven reports (all numbers matched the input): a strength is a figure that is stable
or better than the prior year and favourable by itself; a worsening of more than 20 % is a risk even when the level is
acceptable (the 20 % is a proposal, the team decides); a small worsening is in neither list; amounts in millions or
billions with two decimals, never in full; no "above the threshold of 1"; no cause between two lines.
v4 follows the team review of the 15 reports (Julian, 5 Nov; numbers all matched, the remarks were about wording and
structure): "stands at" for a level, "reached" only for a rise; "loss" and "no rating" instead of a negative profit and
"null"; "below 0.01" for a ratio of 0.00; the two cash points as one liquidity risk; days of payables and receivables
neutral; the basis of a return (profit and equity) shown; a bankruptcy model in the grey zone is a risk; a rating
reasoning of 3 to 5 sentences that uses only what the rule used (rule R2: the two models only); net turnover named as
such and never as revenue; the 20 % comes from `report.worsening_pct`. No new threshold: which figures are risks or red
flags, and any thresholds on debt, revenue or equity, are decisions of Cofidis and stay out of the prompt.
v5 follows the second review (Julian, 7 Nov; the reports were much better, the remarks were again about wording and
structure): "unchanged at" for equal values; a bankruptcy model in the distress zone is a red flag and not repeated under
the risks; no judging words; no "more than 20 %" in the text; net turnover only when revenue is missing; the number of
accounting checks that ran is always named; a profit taken from the balance sheet is said so (and is a key figure "Result
for the period (balance sheet line A.V.)" when the filing has no income statement). Still open for Cofidis, because the
reviewers ask for them repeatedly: a lower threshold than 20 % (revenue -15.2 %, equity -13.3 %), levels of debt ratio and
debt to equity as risk or red flag, a very low current ratio as red flag, a profit that comes only from the financial
result.
v6 follows the answer of Cofidis (9 Nov): reports in English and Czech (Czech number conventions in the prompt); the AI
must not write a recommendation, the code adds one from the rating. Nothing else changes.
v7 follows the reading of the 15 reports (13 Nov; wording only, no new threshold): "not reported" instead of "not
printed"; dates with the month as a word; a space between thousands also in percentages; ratio names as in the input
(no "peak liquidity"); no percentage across zero (loss to profit); every key figure, the financial result included, is
checked against the worsening threshold; a rating that is `null` is explained once.
v8 (14 Nov, after the review of PR #109) changes the way of asking, not the text rules: the answer is one JSON object
(the model is reached through `src.llm.ask_json`, which asks for JSON, instead of a tool call), `bankruptcy` is a list
with every model of `bankruptcy.MODELS`, and every ratio carries its traffic light. The translation prompt is v4.
To compare versions:
`python scripts/generate_reports.py <file> --prompt assessment_v2.md`.

## The call (`src/report/generate.py`)

`generate_report(data, language=...)` sends the prompt and the input (plus the rule-based rating) to the model through
`src.llm.ask_json` (CIPW-58), validates the JSON answer with `AssessmentReport` and returns a `ReportRun` (report or
error, model, tokens, cost, warnings). The model is the one of `settings.llm`: Mistral's API or a Mistral model on a
server of Cofidis' own (`llm.url`), its timeout and retries, the key from `MISTRAL_API_KEY`, temperature 0. **Offline
mode calls no model** (the run has the error "no AI model connected"), and a call that costs more than
`llm.max_cost_usd` is an error, not a report. There is no provider or model setting in `report:`. If the model gives a
rating other than the rules' value, the rules' value is kept, a warning is recorded and the rules' reasoning replaces
the model's paragraph. Errors are recorded, not raised. The pipeline's report stage is still the rule-based assessment
of CIPW-55 (`src/report/fallback.py`, which takes its rating from the same `rate()`); wiring this report into it is
CIPW-17/18.

**Gemini is a flag of the script only:** `python scripts/generate_reports.py --gemini` tunes the prompt on the public
ground truth with a free key (`GEMINI_API_KEY`); it passes its own `ask` function to `generate`, is not a setting and is
never used by the pipeline or the app. The free tier allows about 20 requests a day per model, and a "busy" answer (503)
may count against it: a 503 is not retried (a 429 per minute is), the script stops after 3 busy answers in a row, and an
error never replaces a report that is already saved. Real reports need a Mistral key (question 3 of CIPW-61).

Reports for the ground truth companies (step 5 of the ticket):

```
python scripts/generate_reports.py --dry-run                 # no call: prompt, input and rating per company
python scripts/generate_reports.py                           # all 15 by `llm` (Mistral); saved answers are not charged twice
python scripts/generate_reports.py --gemini                  # the same with a free Gemini key, to tune the prompt
python scripts/generate_reports.py 26161516_2025 --language cs
```

Writes `outputs/reports/<file>.md` (the report with a review checklist for the team), `<file>.json` and
`summary.md`. It stops when a daily quota is used up and continues from the saved answers the next day.
Failed calls are retried twice (a free quota counts the retries too). For the team review:
`python scripts/generate_reports.py --pack` writes `outputs/reports/review_pack.md`, all saved reports in one page
with a table (rating, input data quality, reviewer to fill in); no model call.

## The report page

`report_markdown` (`src/report/generate.py`) writes the page a reviewer reads: rating, summary, strengths, risks,
red flags, rating reasoning and data quality note (all written by the LLM), then the **figures** (`src/report/tables.py`):
key figures, all ratios with formula and the reason when one is not computed, the bankruptcy models with their zones, and
the accounting checks. The tables come straight from the assessment input, never from the LLM, so every sentence can be
checked against them. They are added when the page is written, so changing them needs no new LLM call: run
`python scripts/generate_reports.py` again (saved answers are reused) and then `--pack`.

## Settings: waiting for the answers (`report:` in `config/fides.yaml`)

Everything that still depends on an answer of the team or of Cofidis is a setting, so the answer is a one-line change
(or `FIDES_REPORT__...` in `.env`) and the pipeline code stays as it is:

| Setting | Default | Waiting for |
| --- | --- | --- |
| `report.language` | `en` | CIPW-1 decided nothing; ask Marek whether other branches (e.g. France) will use the tool |
| `llm.model`, `llm.url`, `llm.max_cost_usd`, `MISTRAL_API_KEY` | `mistral-medium-3.5`, Mistral's API, 0.50 | the model of the report (CIPW-58; `docs/llm.md`); the key is question 3 of CIPW-61 |
| `report.worsening_pct` | 20 | threshold for a risk; fills `{{WORSENING_PCT}}` in prompts from v4 on (v1 to v3 have the 20 written in) |
| `report.rating.disabled_rules` | `[]` | Cofidis criteria; any of R1, R2, R3, R5, R9 can be left out (R4, R6 always apply) |
| `report.rating.partial` | `true` | companies without a score get a partial rating from the balance sheet alone (R7 high when the current ratio is below 1, R8 medium, never low); `false` gives no rating instead. On since 12 November 2026: Marek asked to give what can be calculated |

A rating from changed rules carries `rules_version: proposal-2+custom`, one with the partial rating on carries `+partial` (both: `proposal-2+custom+partial`). Every report ends with the table of the rules (R7 and R8 only when the partial rating is on, a rule switched off is left out), in the language of the report, so the reader sees how the rating was decided. New rules beyond these (for example a debt rule)
are added in `src/report/rating.py` once Cofidis names its criteria. The command line options `--language`
and `--gemini` (with `--model`) of `scripts/generate_reports.py` override the settings.

## Open decisions

1. Rating by rules (A) or by the LLM (B): the team lead agrees with rules (A) (4 Nov); the criteria stay open (Cofidis).
2. Report language (CIPW-1) and the key to use: the model is Mistral (decision of 11 Nov), the key is open (CIPW-61).
3. Rule thresholds, once the traffic lights of CIPW-14 and the formula review of CIPW-15 exist.
