# Vision baseline (CIPW-8)

The alternative to our OCR + rules pipeline that many people try first: render the located pages, send the images to a
multimodal LLM, ask for the numbers as JSON. It exists to be **compared** with the parser on the same ground truth.
It is not part of the pipeline.

| Piece | File |
| --- | --- |
| Prompt (versioned, line tables filled from `config/line_items.yaml`) | `prompts/extraction_vision_v1.md` |
| Rendering, call, validation | `src/extraction/vision.py` |
| Run on the ground truth and compare with the parser | `scripts/run_vision_baseline.py` |
| Tests (no API call, the client is a test double) | `tests/test_vision.py` |

## Run

```bash
python scripts/run_vision_baseline.py --dry-run          # no API call: pages, image sizes, prompt size
python scripts/run_vision_baseline.py                    # every file in data/ground_truth/
python scripts/run_vision_baseline.py 26161516_2025 --details
python scripts/run_vision_baseline.py --provider mistral --pause 5
```

Needs an API key in `.env`: `MISTRAL_API_KEY` for `--provider mistral` (the model Cofidis uses internally, default
`mistral-medium-3.5`, change with `MISTRAL_MODEL` or `--model`) or `ANTHROPIC_API_KEY` for `--provider anthropic`
(default). `LLM_PROVIDER` in `.env` sets the default provider. On a free plan with low rate limits use `--pause 5`
(seconds to wait after each call); the Mistral client also waits and retries by itself on HTTP 429. For a cost column also set `LLM_PRICE_IN_PER_MTOK` and `LLM_PRICE_OUT_PER_MTOK`
(USD per million tokens, from the provider's price list): prices are not hard-coded because they change. Without them
the table shows tokens and `n/a` for cost.

Output: `outputs/vision/comparison.md` (the table for the ticket comment) and `summary.json`. Answers are cached in
`outputs/vision/cache` per (pages, prompt, schema, model, DPI), so a repeated run costs nothing; `--force` calls the API
again.

## How it works

1. **Pages.** The ones found by the real page locator (`--use-gt-pages` sends the annotated pages instead, to separate
   locator errors from reading errors). Rendered at 200 DPI as PNG, one image per page.
2. **Call.** One request per file: the prompt as system message, the page images, a **forced tool call**
   (`record_statements`) so the answer is JSON that fits the schema. For Mistral the same schema is sent as a function
   tool (`tool_choice: any`) with plain `number` fields instead of `["number", "null"]`: a value that is not printed is
   left out. Temperature 0. If the model rejects
   `temperature`, the call is repeated without it and the result is marked (`temperature_used: false`).
3. **Schema.** The tool input is the ground-truth format (values exactly as printed plus `unit_multiplier`), one optional
   object per key of `config/line_items.yaml` (assets: gross, adjustment, current, prior; liabilities and income
   statement: current, prior). The full `FinancialStatements` schema is not sent: it holds bounding boxes and OCR
   confidences that a vision model cannot give. The answer is validated with `GroundTruth` (unknown keys fail) and
   converted to `FinancialStatements` in CZK.
4. **Scoring.** `src.evaluation.compare`, the same function as for the parser: a value is correct only if it equals the
   ground truth. The parser can flag values for review, the vision baseline cannot, so every wrong vision value is a
   *silent error*. A failed call (cut-off answer, unknown key) is reported for that file and the run goes on.
5. **Time.** Parser: locate + read + parse with an empty page cache. Vision: the whole call, including rendering.

## Assumptions and limits

* If a page set holds both consolidated and individual statements the prompt asks for the individual ones; the page
  locator returns both (CIPW-6), so this decides which numbers the model reports. Confirm with the team.
* The Mistral request format (images as base64 data URLs, function tool) follows Mistral's public documentation and is
  tested with test doubles only. The first real call may show differences (image size or count limits, schema rules):
  fix them in `_call_mistral` before comparing numbers.
* Whether `mistral-medium-3.5` accepts images was not confirmed by Cofidis. If it does not, the run reports an error
  for every file; use another Mistral model with vision and say so in the comparison.
* The free Mistral plan may use requests for training; the pages are public annual statements.
* The API may downscale large images, so small print on an A4 page can lose detail; this is a property of the baseline,
  not something to tune away before comparing.
* One run per file at temperature 0 is not a guarantee of identical answers; the determinism argument for the
  comparison needs a repeated run (`--force`) on a few files.
* The numbers say how a single prompt version performs on this ground truth, not how vision models perform in general.
