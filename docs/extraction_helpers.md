# Columns, numbers, units and periods (CIPW-9)

Helpers the rule-based parser (CIPW-32) uses, all in `src/extraction/numbers.py`:

| Function | Returns |
| --- | --- |
| `parse_number(text)` | float or None: `1 234 567`, `-17 406 042`, `(1 234)`, `12,5`; OCR letters inside a number (`2O66` → 2066, `7S8` → 758), OCR minus signs (`«`, `~`, `-+`) |
| `detect_unit(text)` | 1, 1000 or 1 000 000 from `v Kč`, `v (celých) tisících Kč`, `v tis. Kč`, `v mil. Kč` and OCR variants (`vtíis.`, `tislcick`) |
| `detect_period(text)` | (current period end, prior period end) |
| `detect_scope(text)` | `full` / `abbreviated` from the title |
| `detect_columns(lines, kind)` | `[Column(name, x0, x1)]`, name in `gross`, `adjustment`, `current`, `prior`, `row`, `extra` |

The parser normalizes every value to CZK with the detected unit (`Value.value`), keeping the printed text in `Value.raw`.

## Columns

1. **Clusters of right edges.** Numbers are right-aligned, so the right edges of the numeric tokens form one cluster per column. Clusters whose x-ranges overlap are one column (OCR jitters the edges of a scanned column into two clusters).
2. **Row-number column** (`řád.`): the *rightmost* column of rising 2–3 digit codes. Everything left of it is the label area, including formula references such as `(ř. 83 + 104 + 147)` that also look like a column of small numbers.
3. **Names.**
   - Column count as expected (assets 4, liabilities and income statement 2): names by order. The order is more reliable than OCR-garbled headers.
   - Other counts: names from the header words above the numbers (`Brutto`, `Korekce`, `Netto`, `Běžné`, `Minulé`). The first `Netto` is the current period, the second the prior one. A third year (`Netto 2022`) or a second column claiming the same header becomes `extra`. The parser ignores `extra` columns.
   - No usable header: a column of small reference numbers (`Příloha`, `Bod`) is `extra`; with too many columns the rightmost ones are the statement; with too few the rightmost ones are `current`, `prior`; three asset columns are `gross`, `current`, `prior` (no Korekce printed).
4. **Sparse Korekce.** Korekce is often printed on a few lines only (just the depreciated assets). When no column belongs to the `Korekce` header, the few numbers under it are kept as the `adjustment` column.

## Periods

In this order:
1. the date after `Rozvahový den`;
2. otherwise the last day of a month that is printed most often (quarter ends first): the balance sheet date is in the header of every page, a date printed once is more likely a maturity in a note;
3. otherwise the end of an `od … – …` range;
4. otherwise `za rok 2024` → 31. 12. 2024.

Dates are read as `31.12.2025`, `31. 12. 2025`, `31/12/2025`, `31. prosince 2025` in every case (`k 31. prosinci`, `končící 31. prosincem`) and in forms with one box per digit. Ignored: period starts (`od 1.3.2024`, `1.3.2024 – 28.2.2025`, the day after another printed date) and dates of preparation or signature (`Sestaveno dne`, `podpis`). The prior period end is the latest remaining date at least 300 days earlier, or the same day a year before.

When the statement pages print no date, the pipeline takes the year from the file name (`ICO_YEAR_…pdf`, 31. 12.) and adds a warning; when they print no unit it assumes tis. Kč and adds a warning.

## Check on all collected files

```bash
python scripts/audit_helpers.py
```

Runs the helpers and the parser on every page saved by `scripts/run_reading.py` (no OCR) and writes `outputs/audit_helpers.csv`. Where there is no ground truth, the accounting checks are the measure: a wrongly named column breaks the net and sum checks.

Result on 99 files (Oct 2026), before → after this task:

| | before | after |
| --- | --- | --- |
| files with a column, unit or period problem | 54 | 18 |
| unit found | 94 | 96 |
| period found | 88 | 93 (+ file-name fallback for the rest) |
| accounting checks passed | 1 370 / 1 786 | 1 536 / 1 939 |

What is left:
- **Wrong digits from OCR** on scans: most of the failed checks. CIPW-11 (re-reading) is meant to fix them; the checks catch them.
- **Several statements in one PDF** (consolidated and individual, annual reports): the locator (CIPW-6) passes all of them and the parser mixes their values.
- **No date or no unit on the statement pages** (5 files): the file-name fallback and warnings above.
- **Badly garbled scans** (`29008522_2024`, `48110248_2025`): headers and numbers are unreadable.
