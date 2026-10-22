# Accounting checks (CIPW-10)

`src/validation/checks.py` tests the extracted statements against the identities every Czech statement must satisfy. A plausible but wrong number is the worst error for a lender; most misreads break at least one identity. Every result carries the report (`Result.validation`), and the web app shows it under Validation and marks the values of failed identities as *Review*.

## Checks

**Errors**: identities that must hold. A failure means a wrong or missing value (or a statement that does not add up itself).

| Check | Formula |
| --- | --- |
| Balance | total assets = total liabilities and equity (same number printed twice: exact) |
| Assets | A + B + C + D = total assets |
| Fixed assets | B.I + B.II + B.III = B |
| Current assets | C.I + C.II + C.III + C.IV = C |
| Receivables | C.II.1 + C.II.2 + C.II.3 = C.II |
| Liabilities and equity | A + (B + C) + D = total |
| External sources | B + C = B + C total |
| Liabilities | C.I + C.II + C.III = C |
| Equity | A.I + A.II + A.III + A.IV + A.V − A.VI = A |
| Operating result | I + II − A − B − C − D − E + III − F = * (the form's own formula, ř. 01 + 02 − 03 − 07 − 08 − 09 − 14 + 20 − 24) |
| Profit, P&L vs balance sheet | profit for the period = A.V. (exact) |
| Tax | profit before tax − income tax = profit after tax |
| Result | operating result + financial result = profit before tax |
| Net per asset line | net = gross + korekce |

**Warnings**: worth a look, not necessarily wrong: profit after tax = profit for the period (unless there is a line M.), negative values where none are expected, year-over-year change above 500 %, and every value the parser flagged ("Value needs review").

**Tolerance:** sums of separately rounded lines allow ±1 printed unit (1 000 CZK for statements in tis. Kč); the same number printed twice must match exactly. A sum runs when its total and at least one part are present; missing parts count as 0, so a missed line fails the sum.

**Signs as printed:** software differs, so each check accepts the readings that mean the same:

- Korekce: net = gross + korekce as printed, or gross − |korekce| (a positive korekce can really raise a value, e.g. revaluation of shareholdings).
- A.VI. (advance profit distribution, "(-)") always lowers equity, printed positive or negative.
- C. Aktivace always raises the operating result, printed positive or negative.
- Costs printed negative (some software): the printed lines then simply add up to the operating result, which is accepted as well.

## What the checks decide

`src/validation/review.py` (`validate_and_review`, run by the pipeline stage `validate`):

1. **Lost minus.** OCR drops a minus printed apart from the number ("- 17 602") or reads it as "+" or nothing. When turning the sign of one value of a failed check makes that check pass, and fewer checks fail overall, the value takes the sign; `Value.note` and the result warnings say so. Only lines that can be negative are turned: equity, share premium and capital funds, results ("(+/-)" in the form), change in inventory, capitalisation. A misread digit is never "fixed" this way: a sign change closes the gap only when the gap is exactly twice the value.
2. **Failed check → review.** Every value of a failed check is flagged for review.
3. **Passing check → confirmed.** A value that only passing checks involve, at least one of them adding up exactly, needs no review even when OCR was unsure of a digit or the label matched loosely; `Value.confirmed_by` names the check. A pass within the ±1 rounding tolerance confirms nothing: a last digit read one off passes the same way. The net check (net = gross + korekce) confirms all three amounts of its line.

On the 99 collected statements this lowers the values to review from 41 % to 27 % and restored 11 minus signs, each one checked on the page. On the ground truth the wrong values that are not flagged fall from 12 to 1 (`docs/parser.md`).

## Re-read of doubted cells (CIPW-11)

`src/extraction/reread.py`, pipeline stage `reread` (after `validate`, before `ratios`; `reread.enabled` and `reread.dpi` in `config/fides.yaml`). Only on OCR pages: a text layer is read exactly.

1. **The value columns as numbers only.** The right side of a statement holds no letters, so each OCR page's value columns are read once more at 400 dpi with Tesseract allowed only `0-9 - ( )` ("U" cannot be a reading of 11, "o" not of 0). This gives a second reading of every cell.
2. **Doubted cells, one by one.** The current and prior values of every failed check, and the empty cells of lines a check uses (the parser found the line, OCR gave no number), are also read from a crop of the cell (its row, the width of its column), one line, digits only. Two attempts: the grey crop as it is, and a 1.5× finer crop with adaptive thresholding. They were chosen by reading the misread cells of the ground truth scans with 36 settings: together they find the right number for 20 of 25. A dash a ruling adds before or after the number is also tried without it.
3. **What is taken.** A reading replaces a value only when the checks agree better with it: fewer checks fail and none that held fails, or an empty cell makes a check run and pass. One cell at a time, then two cells of the same failed check together (two misread values in one sum are fixed only together).
4. **Never silent.** A taken reading keeps a note ("re-read at higher resolution: '2575872' read as '7575872', the checks agree"), the old reading stays among `Value.candidates`, the result lists every change (`Result.reread`) and its warnings name them.
5. **What stays in doubt.** The values of checks that still fail stay flagged for review with every reading as a candidate, plus the value that would make the check pass (derived from the sum: offered, never applied). The app shows them as "Other readings"; picking one is CIPW-42.

**Results.** Ground truth (`python scripts/evaluate_extraction.py`, `--no-reread` for the comparison):

| | without re-read | with re-read |
| --- | --- | --- |
| values correct | 92.4 % | **95.5 %** |
| wrong and not flagged | 1 | **1** |
| correct but flagged for review | 92 | **33** |
| not found | 21 | **17** |
| failed checks | 34 | **14** |

Mondi 2025 (shaded scan) goes from 84 % to 93.5 %, its failed checks from 17 to 5.

On the 96 collected statements with a PDF, where nothing was tuned: failed checks 460 → 343, 110 cells corrected, values to review 18 % of 7 476, about 4.5 s per statement (most of it on scans with many failed checks; nothing is read again when every check passes and no cell is empty). The column strips add 8 of the 110 corrections for 0.9 s per statement; on shaded scans they read little, the cell crops do the work there. Corrections checked on the page include share capital 400 119 read as "-", fixed assets 68 990 read as 3 500, receivables 10 113 read as 210 113.

## How much they catch

`python scripts/mutation_test_checks.py` corrupts every value of the complete, verified ground truth files in turn, the way OCR and the parser go wrong, and counts what the checks catch (`--warnings` counts warnings too). Result on 6 files, 2 228 corrupted statements (2026-10-21):

| Error | Caught before | Caught now |
| --- | --- | --- |
| a digit misread | 66 % | 84 % |
| a digit dropped (÷10) | 70 % | 89 % |
| lost or extra minus | 63 % | 81 % |
| current and prior year swapped | 65 % | 87 % |
| value missing | 32 % | 49 % |
| **all** | **57 %** | **75.5 %** |

No false alarm on the correct ground truth. On the 99 collected statements the new checks found real extraction errors only (parser missed A.III. / A.IV. / A.V. or the cost lines), no false alarm on a correctly read statement.

**Not covered yet:** an error in these lines passes unnoticed, because no identity contains them:

- E.1. depreciation (part of E.: needs E.2 and E.3 in the dictionary)
- J. interest expense and the financial result (needs IV., G., V., H., VI., I., VII., K. in the dictionary and the ground truth)
- net turnover (needs the same income lines)
- bank loans (C.I.2., C.II.2.) and trade payables (C.II.4.): sub-lines of liabilities
- a missing total: a sum with no total does not run

Missing values are the weakest spot (49 %): when a total and all its parts are missing together, nothing fails. CIPW-11 (re-reading flagged rows) and the completeness of the parser matter more here than new checks.
