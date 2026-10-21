# Data schema and conventions (CIPW-5)

One model for all financial data: `src/schema.py`. The list of fields and how each one is printed on the Czech forms lives in `config/line_items.yaml` (the line-item dictionary). Extraction, validation, ratios and the report all use these keys.

## Files

| File | What |
| --- | --- |
| `config/line_items.yaml` | every field: key, statement, side, označení, číslo řádku, official label and variants, English name |
| `src/line_items.py` | loads and validates the dictionary |
| `src/schema.py` | `FinancialStatements` (extraction output), `GroundTruth` (hand-filled reference) |
| `docs/schema/*.schema.json` | exported JSON schemas (vision baseline CIPW-8 uses the first one) |
| `data/ground_truth/_template.json` | blank ground truth with every field |

Regenerate the exports after changing the dictionary or the model:

```bash
python scripts/export_schema.py
```

A test fails when `_template.json` is out of date.

## Conventions (agreed for the team)

- **Missing line = `null`**, never 0. A line printed as `0` or `-` is 0.
- **Losses and negative amounts are negative numbers.** Korekce (adjustment) is stored as printed: most software prints it negative (`-17 406 042`), some positive; validation accepts both.
- **Amounts are normalized to CZK** in `FinancialStatements` (`Value.value`). The printed text is kept in `Value.raw` and the unit in `Metadata.unit_multiplier` (1, 1000, 1 000 000).
- **Ground truth stores values as printed** (usually tis. Kč) with `unit_multiplier`, because that is what people transcribe. `GroundTruth.to_statements()` converts to CZK.
- **Assets have four amounts** (`gross`, `adjustment`, `current` = net, `prior` = net prior year). Liabilities and the income statement have `current` and `prior`.
- **Periods:** `current` is the balance sheet date of the statement (`Metadata.period_end`), `prior` the column before it.
- **Every extracted amount keeps its source:** 1-based page, bounding box in PDF points (top-left origin), OCR confidence 0–100 (None for a text layer) and parser match score 0–100.
- **Označení is normalized** without spaces and with a dot after every part: `B. II. 1.` → `B.II.1.`, `B. + C.` → `B.+C.`. Income-statement subtotals keep their stars (`*`, `**`, `***`).
- **Partial ground truth:** set `"partial": true` when only some lines are filled on purpose (e.g. an IFRS statement whose lines do not map to the Czech form) and explain the gaps in `notes`. Accuracy is measured on the filled lines only; the accounting checks that can run must still pass.
- **Row numbers (`row`) are a hint, not a key.** They are verified on a real 2025 full-form statement where marked `# verified` and counted from the form in the decree where marked `# from the decree`, but micro forms omit them and some software numbers differently.

- **Časové rozlišení is printed twice on each side** of the form valid since 2016: inside receivables (`C.II.3.`, `accruals_in_receivables`) and on its own (`D.`, `accruals_assets`); inside liabilities (`C.III.`, `accruals_in_liabilities`) and on its own (`D.`, `accruals_liabilities`). Most companies use only `D.`; the sums of C.II. and C. include the inner line when it is printed.

## Scope of fields

The dictionary covers what the ratios (CIPW-12), bankruptcy models (CIPW-13) and checks (CIPW-10) need, not every line of the decree. Add a field by adding it to `config/line_items.yaml`, re-running the export and filling it in the ground truth files.
