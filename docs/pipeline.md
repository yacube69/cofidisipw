# Pipeline and `Result` (CIPW-40, completed in CIPW-24)

`src/pipeline.py` turns one PDF into one `Result` (`src/result.py`): everything a run produces, in one object that the app, the evaluation and the demo read. Every track plugs its module into this one code path.

## Run

```bash
python -m src.pipeline data/raw/26161516_2025_statement.pdf --out outputs/ --skip-report
python -m src.pipeline data/raw/ --out outputs/          # every PDF in a folder
```

| Option | Default | What |
| --- | --- | --- |
| `--out` | `outputs/` | folder for `{file}.result.json` |
| `--mode` | `auto` | `auto` = text layer when it is clean, OCR otherwise; `text`; `ocr` |
| `--dpi` | 300 | OCR resolution of the reading stage (the page locator uses its own quick pass) |
| `--skip-report` | off | deterministic part only: `report` and `check_numbers` are skipped, no LLM call |
| `--scope` | `individual` | group annual reports hold an individual and a consolidated set of statements: only this one is read (the other one if the PDF has only that), so their numbers are never mixed. `Result.consolidation` says which set was read; a warning lists the pages of the other set |

The CLI prints one line per file with the status of every stage, then the first error and all warnings. **Exit code 1** when a stage of any file failed (or a PDF could not be opened), 0 otherwise.

From Python:

```python
from src.pipeline import run_pipeline, write_result
from src.result import PipelineOptions

result = run_pipeline("data/raw/25080776_2025_statement.pdf", PipelineOptions(skip_report=True))
write_result(result, "outputs/")
result.statements.get("total_assets")       # 10 000 000 CZK
```

`PipelineOptions(pages={"balance_sheet": [20, 21], "income_statement": [22]})` skips the page locator (1-based pages; pages outside the PDF fail the `locate` stage).

## Stages

They run in this order. A stage is a function `stage(result, ctx) -> StageStatus` listed in `STAGES`.

| Stage | Task | Today | Writes | Needs |
| --- | --- | --- | --- | --- |
| `locate` | CIPW-6 | real | `pages` | – |
| `read` | CIPW-7 | real | `page_words` | `locate` |
| `parse` | CIPW-32 | real | `statements` (amounts already normalized to CZK, see below) | `read` |
| `validate` | CIPW-10 | real | `validation` | `parse` |
| `reread` | CIPW-11 | stub | nothing yet | `validate` |
| `ratios` | CIPW-12 | stub | `ratios` | `validate` |
| `bankruptcy` | CIPW-13 | stub | `bankruptcy` | `ratios` |
| `benchmarks` | CIPW-14 | stub | `benchmarks` | `ratios` |
| `report` | CIPW-16 | stub | `report`, `llm_usage` | `ratios`, `bankruptcy`, `benchmarks` |
| `check_numbers` | CIPW-17 | stub | `number_check` | `report` |

There is no separate `normalize` stage: the parser multiplies every amount by `Metadata.unit_multiplier` (tis. Kč → Kč), see `docs/schema.md`.

**Status of a stage** (`StageRun.status`):

| Status | Meaning |
| --- | --- |
| `ok` | real module, finished |
| `stub` | not implemented yet; the stage wrote mock data (below) |
| `skipped` | `--skip-report`, or a stage it needs failed (`note`: `needs locate`) |
| `error` | the stage could not do its job; message in `note` and in `Result.errors` |

A stage that fails does not stop the run: stages that do not depend on it still run, the ones that need its output (`REQUIRES` in `src/pipeline.py`) are `skipped` instead of working on empty data. Expected failures raise `StageFailed` (no statement pages found, all pages without text, page override out of range) and are reported as one line; any other exception is a bug and is reported with a short traceback.

## `Result` fields

| Field | Type | Filled by |
| --- | --- | --- |
| `tool_version` | str | version of the tool (`0.1.0`) |
| `file` | str | PDF file name |
| `created_at` | datetime | start of the run (local time) |
| `options` | `PipelineOptions` | options of the run |
| `pages` | `{"balance_sheet": [..], "income_statement": [..]}` | `locate`, 1-based |
| `page_words` | list of `PageWords` | `read`: words with boxes per page, see `docs/ocr.md` |
| `statements` | `FinancialStatements` | `parse`: metadata + every line item with value, source page, box, confidence, `needs_review`; see `docs/schema.md` |
| `validation` | `ValidationReport` | `validate`: one `CheckResult` per accounting check (`passed`, `severity`, expected / actual) |
| `ratios` | `RatioReport` | `ratios`: per ratio formula, inputs, value and flag for both periods |
| `bankruptcy` | dict | `bankruptcy`: score and zone per model (format set by CIPW-13) |
| `benchmarks` | dict | `benchmarks`: traffic light per ratio (format set by CIPW-14) |
| `report` | dict | `report`: summary, strengths, risks, red flags, rating, data quality note (format set by CIPW-16) |
| `number_check` | dict | `check_numbers`: numbers found in the report and the ones not found in the data |
| `stages` | list of `StageRun` | every stage: `stage`, `status`, `seconds`, `note` |
| `llm_usage` | `LLMUsage` | input / output tokens and cost in USD (0 until CIPW-16) |
| `errors` | list of str | `"<stage>: <message>"` per failed stage; an unexpected exception adds its traceback as the next entry |
| `warnings` | list of str | problems that did not stop a stage, e.g. `read: no text on pages [3]` |

Properties (not in the JSON): `stubbed_stages` lists the stages that returned mock data, `failed` is true when a stage has status `error`.

`write_result()` saves the model as JSON; `Result.model_validate_json()` reads it back.

## Mock data of stub stages

Until a track replaces its stub, the stage fills its field with the data of one real company from `data/fixtures/mock_26161516_2025.json` (Mondi Štětí a.s., 2025, derived from its ground truth with the formulas proposed in CIPW-12/13/14). The app and the report can be built against realistic content from day one.

- The same mock data comes back **whatever PDF is read**. Check `StageRun.status == "stub"` (or `result.stubbed_stages`) before showing a value as real; every dict payload also carries `"mock": true`, every mock ratio has the reason `mock data ...`.
- Each run gets a fresh copy, so changing it in one result does not change the next one.
- The dict formats of `bankruptcy`, `benchmarks`, `report` and `number_check` are a proposal; the owner of the task fixes the real format and updates the fixture with it.

## Replacing a stub

1. Write the real function in the module of the task (e.g. `src/ratios/engine.py`) and call it from the stage in `src/pipeline.py`; return `"ok"`.
2. Raise `StageFailed("...")` for an expected failure the user can act on; let real bugs raise.
3. If the stage needs another stage's output, add it to `REQUIRES`.
4. Put problems that do not stop the stage into `result.warnings`.
5. Update `tests/test_pipeline.py` (`test_stubs_return_mock_data` lists the stub stages).
