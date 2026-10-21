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
