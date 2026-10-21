# Rule-based parser (CIPW-32)

`src/extraction/parser.py` turns the words of the statement pages (text layer or OCR, CIPW-7) into `FinancialStatements` without an LLM. Every value keeps its page, bounding box, OCR confidence and match score.

## How a line is read

1. Words are grouped into lines; the columns come from `detect_columns` (CIPW-9, `docs/extraction_helpers.md`).
2. Each line is split into označení (code), label, row number and values per column.
3. The line is identified against the line dictionary (`config/line_items.yaml`): fuzzy match of the label, confirmed or contradicted by the code and the row number. A line below `ACCEPT` (78) is not used; a value below `REVIEW` (86) is flagged.

## Rules for real-world layouts

Each rule describes how Czech statements are printed, not one file.

| Layout | Rule |
| --- | --- |
| A formula after the name: "PASIVA CELKEM (A.+B.+C.+D.)", "Rezervy (součet B.1. až B.4.)", "(ř. 80 + 84 …" | the label is also scored without the formula |
| OCR garbles the označení so it stays in the label: "C.m.2.1. — Pohledávky…", "c. Závazky", "AIM. Fondy ze zisku" | the label is also scored without it; the garbled code is still compared with the field's code. A code is short (at most 4 letters) and ends at a space, so "A.V.1 Výsledek…" or "Ostat." are not cut off |
| A garbled code deeper than the field's (B.4.3. for B.II.) | −8: it is a sub-line of that field, not the field |
| A label wrapped over two lines | the line above is joined only when this line carries it on: it starts with a small letter and has no code of its own. The code may sit on the second line, centred with the numbers ("A.V. podílů (+/-)"), when the first line has none. A line starting with a capital or a code is a new line ("Osobní náklady" after an empty "A.3. Služby") |
| A sub-line printed with its own number only ("4. Závazky z obchodních vztahů") | compared with the end of the field's code (C.I.**4.** / C.II.**4.**) |
| The same label twice (trade receivables under long-term C.II.1. and short-term C.II.2.; trade payables C.I.4. / C.II.4.) | the section decides: the field whose parent line was read last; within one section a field not yet filled; with no section line read, the dictionary order (short-term first, as long-term trade lines are rare) |
| A statement over several pages, current year mostly blank on a continuation page | the page keeps the columns of the previous page of the same side when the columns it found sit at the same places |

## Results

Field accuracy against the ground truth (`python scripts/evaluate_extraction.py`), and on the 99 collected statements without ground truth (`python scripts/audit_helpers.py`, the accounting checks as the measure). The rules were developed on the ground truth files and then checked on the 99 statements, where they were not tuned.

| | before (2026-10-21) | after (2026-10-22) |
| --- | --- | --- |
| ground truth: values correct | 79.0 % | **92.4 %** |
| ground truth: wrong and not flagged | 18 (2.9 %) | **12 (1.9 %)** |
| ground truth: not found | 93 | **21** |
| 99 statements: values read | 6 691 | **7 426** |
| 99 statements: accounting checks passed | 1 661 | **1 997** |
| 99 statements: checks failed | 485 | **475** |
| 99 statements: every check passes | 20 | **30** |

Per ground truth file: digital 100 %, Skyport 96 %, 09704086 98 %, 27587908 97 %, 28437098 89 %, Mondi (shaded scan) 84 %, ČEPS (IFRS, out of the Czech form) 73 %.

**Known limits**

- Forms that print the numbers above a wrapped label (25083341) assign values one line off; the equity and liabilities checks flag it.
- Notes columns read as gross values in annual reports ("Zásoby 15 …"): the net check flags it.
- Shaded scans (Mondi): misread digits, flagged by the checks; CIPW-11 (re-reading) is the fix.
