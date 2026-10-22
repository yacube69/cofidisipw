# Settings, run log and audit trail (CIPW-45)

Every result can be reproduced and explained: it records which code, settings, line dictionary, OCR, prompt and model produced it.

## Settings: one place

`src/settings.py` loads one `Settings` object. Use it instead of reading files or environment variables yourself:

```python
from src.settings import get_settings

settings = get_settings()
settings.paths.raw              # data/raw as an absolute path
settings.ocr.config()           # OcrConfig of the reading stage
settings.offline, settings.llm_enabled
```

Where a value comes from, highest first:

| Source | Example | For |
| --- | --- | --- |
| environment variable, `FIDES_` prefix, `__` for nesting | `FIDES_OFFLINE=true`, `FIDES_OCR__DPI=400` | a single run, Docker, CI |
| the same names in `.env` | `FIDES_LLM__MAX_COST_USD=0.2` | your computer |
| the familiar names, environment or `.env` | `ANTHROPIC_API_KEY`, `LLM_MODEL`, `TESSERACT_CMD`, `TESSDATA_PREFIX` | secrets and per-computer paths |
| `config/fides.yaml` (committed) | `ocr.dpi: 300` | the team's shared configuration |
| defaults in `src/settings.py` | | |

| Setting | Default | |
| --- | --- | --- |
| `offline` | false | no LLM call at all: the AI report step is skipped, everything else runs; needs no API key |
| `log_runs` | true | JSON-lines log of every run in `paths.logs` |
| `paths.*` | `data/raw`, `data/ground_truth`, `data/fixtures`, `outputs`, `outputs/logs`, `config/line_items.yaml`, `prompts` | relative to the repository root (`/workspace` in the image) |
| `ocr.*` | dpi 300, psm 6, adaptive 31/15, shading removal, deskew, line removal, grey second pass | tuned in CIPW-25, see `docs/ocr.md` |
| `reread.enabled`, `reread.dpi` | true, 400 | re-read of the cells the checks doubt (CIPW-11, `docs/validation.md`); needs the `eng` Tesseract data |
| `performance.ocr_workers`, `performance.tesseract_threads`, `performance.light_locator` | 0 (one per core), 1, true | pages read side by side and the light page locator (CIPW-50, `docs/ocr.md`) |
| `tesseract.cmd`, `tesseract.tessdata_prefix` | – | set `TESSERACT_CMD`, `TESSDATA_PREFIX` in `.env` |
| `llm.provider`, `llm.model` | anthropic, claude-sonnet-5-5 | |
| `llm.max_cost_usd` | 0.50 | upper limit for one AI report (CIPW-16) |
| `llm.api_key` | – | `ANTHROPIC_API_KEY` only; a key in `config/fides.yaml` is refused |
| `api.max_upload_mb`, `api.max_results` | 60, 20 | web app API |

The API key never appears in `Settings.public()`, the settings hash, a result or a log.

**Offline mode:** `FIDES_OFFLINE=true` (or `offline: true` in the YAML). The report and its number check are skipped with the note "offline mode: no LLM call", `Result.audit.offline` is true, and the result is complete apart from the report. `python scripts/hello_llm.py` reports offline mode instead of failing.

## Run log

Every pipeline run writes `outputs/logs/<time>_<file>_<run id>.jsonl` (switch off with `log_runs: false`). One JSON object per line:

```json
{"ts": "2026-10-21T20:57:44.786+02:00", "run_id": "6e0904db82e9", "event": "run_start", "file": "…", "options": {…}, "audit": {…}}
{"ts": "…", "run_id": "6e0904db82e9", "event": "stage", "stage": "read", "status": "ok", "seconds": 0.013, "note": null}
{"ts": "…", "run_id": "6e0904db82e9", "event": "run_end", "seconds": 0.37, "failed": false, "pages": {…}, "values": 47, "checks_failed": 0, "errors": [], "warnings": [], "model": null}
```

Read it with `src.runlog.read_log(path)` or `pandas.read_json(path, lines=True)`. In Docker, `docker compose` mounts `outputs/`, so the logs stay on the host. Tests write their logs to the temp folder.

## Audit block

`Result.audit` (`src/result.py`), filled by every run:

| Field | |
| --- | --- |
| `run_id` | the same id as in the run log |
| `tool_version`, `git_commit`, `git_describe`, `git_dirty` | the code: version from `pyproject.toml`, commit from git (in the image from the build argument `GIT_COMMIT`, set by CI) |
| `settings_hash`, `settings` | fingerprint and values of the settings that change a result: OCR, LLM, offline. Paths and the Tesseract location are left out, so the same configuration gives the same hash on every computer |
| `dictionary_version` | `v<version> <content hash>` of `config/line_items.yaml` |
| `prompt_version`, `model` | content hash of `prompts/report.md` and the model, only when an LLM wrote the report; `null` for mock data, skipped reports and offline mode |
| `offline`, `ocr_config`, `tesseract_version`, `python_version` | |
| `started_at`, `finished_at` | |
| `log_file` | the run log, relative to the repository root |
| `corrections` | values changed by hand in the app (key, period, old, new, by, at, reason); filled by CIPW-42 |

The web app shows it under **About this result** in the header, together with the status and time of every step.
