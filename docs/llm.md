# Language model: Mistral (CIPW-58)

In production the AI report (CIPW-16) uses **Mistral**, as Cofidis wants. Fides talks to it through Mistral's chat completions API. A Mistral model on a server of Cofidis' own that offers the same interface works the same way, for example vLLM or another OpenAI-compatible server. Then the statements never leave Cofidis' network.

Without a model (offline mode, no key) nothing is sent anywhere, and the assessment is the rule-based one (`docs/report.md`, CIPW-55).

## Two ways to connect

| | Mistral's API | A Mistral model on Cofidis' server |
| --- | --- | --- |
| `llm.url` | `https://api.mistral.ai/v1` (default) | the server's `/v1` address, e.g. `http://llm.internal:8000/v1` |
| Key | `MISTRAL_API_KEY` in `.env` or the environment | only if the server asks for one |
| Internet from the Fides server | needed (`api.mistral.ai`, directly or through a proxy) | not needed |
| Where the data goes | Mistral's cloud (EU) | stays in Cofidis' network |
| Cost | per token, by Cofidis' contract (`llm.price_*` for the cost in the result) | the server's hardware, no cost per call |

Which one, and which model, is Cofidis' choice (question 3 in CIPW-61).

## Settings

`config/fides.yaml`, overridable in `.env` or the environment (`docs/settings.md`):

```yaml
llm:
  provider: mistral
  url: https://api.mistral.ai/v1   # or the server of one's own
  model: mistral-medium-3.5        # the model Cofidis uses internally
  timeout: 120                     # seconds for one answer
  price_in_per_mtok: 0             # USD per million tokens; 0 = cost not counted
  price_out_per_mtok: 0
  max_cost_usd: 0.50               # upper limit for one AI report
```

| `.env` / environment | Sets |
| --- | --- |
| `MISTRAL_API_KEY` | `llm.api_key`, never written to `config/fides.yaml` (refused there), a result or a log |
| `MISTRAL_MODEL` | `llm.model` |
| `LLM_PRICE_IN_PER_MTOK`, `LLM_PRICE_OUT_PER_MTOK` | the token prices |
| `FIDES_LLM__URL` | `llm.url` |

**Check the connection:** `python scripts/hello_llm.py` asks the model one question and prints the answer, the tokens used and their cost.

## In code

`src/llm.py`:

```python
from src.llm import LlmError, ask_json

reply = ask_json(system_prompt, user_message)   # one JSON object back
reply.data            # dict
reply.usage           # LLMUsage: input and output tokens, cost in USD -> Result.llm_usage
reply.model           # the model that answered -> Result.audit.model
```

How the call works:
- **The request:** `temperature: 0` and `response_format: json_object`, so the answer is one JSON object.
- **The HTTP client** is the vision baseline's `MistralClient` (`src/extraction/vision.py`). It waits and retries when the service is busy (HTTP 429, 503).
- **Every failure is one `LlmError`:** no model, a rejected key, the server not reachable, an answer that is not a JSON object, an answer cut off at `max_tokens`.
- **On an `LlmError`** the pipeline is meant to fall back to the rule-based assessment and say why in the note of the `report` step. CIPW-16 builds that.

**Tests** never call the model. `tests/conftest.py` empties the key, whatever `.env` holds, and `tests/test_llm.py` replaces the HTTP client.

## For the AI report (CIPW-16)

- **Prompt:** in `prompts/`. Tune and review it on the model Cofidis will use: the quality of Czech text varies between Mistral models.
- **Answer:** the same fields as the rule-based assessment, with `source: "llm"`. The app and the PDF then show it without changes.
- **After the call:** the number check (CIPW-17) checks every number in the text against the data, whatever the model.

## What leaves the server

Only with a model connected, and only the input of the AI report. That input is:
- the numbers of the statements;
- the ratios and the bankruptcy scores;
- the company's name and industry.

The PDF itself is never sent. With a model on Cofidis' own server, nothing leaves its network.

The other outgoing call is ARES, which receives only the IČO (`docs/ares.md`).

## History

Until 2026-11-11 the settings named Anthropic (`docs/decisions.md`). Anthropic remains only in the vision baseline, an experiment (CIPW-8, `docs/vision_baseline.md`).
