# Financial Ratios (CIPW-12)

`src/ratios/engine.py` computes the ratios from the validated statements (`compute_ratios(fs, validation)`), for the current and the prior year, without an LLM. The pipeline stage `ratios` runs it after the accounting checks; the result is `Result.ratios` (`src/ratios/models.py`).

Every ratio has its formula, the input amounts it used (in CZK, `inputs`), a value and a flag:

| flag | value | meaning |
| --- | --- | --- |
| `ok` | number | computed from values that read cleanly |
| `unreliable` | number | an input is flagged for review or sits in a failed accounting check of that year; `reason` names which |
| `not_meaningful` | None | the denominator is zero or negative: negative equity, no interest expense, revenue 0, a negative receivable/payable balance |
| `missing` | None | an input is not in the statement; `reason` names it |

The year-over-year change (`Ratio.change`) is in the ratio's own unit: times, percentage points or days.

## Formulas

Balances are end-of-period (no averages: the prior year's opening balance is not in the statement). Revenue = I. + II. (revenue from products and services + revenue from goods). EBIT = profit before tax + interest expense (J.).

| key | ratio | formula | unit |
| --- | --- | --- | --- |
| `current_ratio` | Current ratio | current_assets / liabilities_short_term | x |
| `quick_ratio` | Quick ratio | (current_assets − inventories) / liabilities_short_term | x |
| `cash_ratio` | Cash ratio | (cash + short_term_financial_assets) / liabilities_short_term | x |
| `roa` | Return on assets | EBIT / total_assets | % |
| `roe` | Return on equity | profit_for_period / equity | % |
| `ros` | Return on sales | profit_for_period / revenue | % |
| `ebitda_margin` | EBITDA margin | (operating_result + depreciation) / revenue | % |
| `debt_ratio` | Debt ratio | external_sources / total_assets | % |
| `debt_to_equity` | Debt to equity | external_sources / equity | x |
| `interest_coverage` | Interest coverage | EBIT / interest_expense | x |
| `receivables_days` | Receivables days | receivables_short_term / revenue × 365 | days |
| `payables_days` | Payables days | trade_payables_short_term / revenue × 365 | days |

## Rules for real statements

- **A line of a sum that is not printed counts as 0** when the rest is there: revenue from goods (a manufacturer has none), short-term financial assets, inventories (a service firm), interest expense (no loans). The reason says "… not printed, counted as 0". If the parser missed such a line, the sum check it belongs to fails and the ratio is flagged `unreliable`.
- **Stand-ins:** profit for the period not printed → profit after tax, then A.V. of the balance sheet; depreciation (E.1.) not printed (abbreviated statement) → E. value adjustments in operating activities. The reason names the line used.
- **Payables days** use trade payables when they are printed for every year that has payables, otherwise all short-term liabilities for both years (so the change compares like with like); the formula then reads `liabilities_short_term / revenue * 365` and the reason says so.
- **Costs printed with a minus** (some statements do, the operating result check accepts both): interest expense and depreciation count by their size.
- **Negative equity:** ROE and debt to equity are `not_meaningful`. The debt ratio stays: above 100 % is what negative equity looks like.
- **A loss** is a negative return, not `not_meaningful`.
- **Abbreviated statements** have no short-term split of liabilities or receivables in some layouts: those ratios are `missing`.

## Tests and results

`tests/test_ratios.py` checks every ratio against values calculated by hand from the printed statements of Mondi Štětí 2025 and Skyport 2025 (ground truth), plus the edge cases above.

On the 99 collected statements (`outputs/ocr`, read and parsed as in `scripts/audit_helpers.py`), 2 376 ratio values (12 ratios × 2 years):

| flag | values |
| --- | --- |
| ok | 841 |
| unreliable | 574 |
| not_meaningful | 65 |
| missing | 896 |

`missing` comes mostly from files whose statements are not in the Czech form (IFRS annual reports, banks) or were not located; 14 files have no ratio at all. `unreliable` follows the failed accounting checks of the parser (CIPW-32) and the values to review on scans. Of the values outside a plausible range, those flagged `ok` are real: negative equity (debt ratio above 100 %), a holding with group receivables far above its revenue, a firm with almost no interest.

The formulas are reviewed in CIPW-15.

---


# Bankruptcy models (CIPW-13)

`src/ratios/bankruptcy.py` computes IN05 and Altman Z′ for the current and the prior year (`compute_bankruptcy(fs, validation)`, pipeline stage `bankruptcy`, `Result.bankruptcy`). Both use the definitions of the ratio engine above: **EBIT = profit before tax + interest expense**, **revenue = I. + II.**, end-of-period balances, and the same rules for real statements (a missing part of a sum counts as 0 with a note, costs printed with a minus count by size).

Every score keeps its variables, the input amounts and a flag, like a ratio: `ok`, `unreliable` (an input to review or in a failed check), `not_meaningful` (zero or negative total assets or external sources, score None), `missing` (an input not in the statement, score None). The zone (`safe`, `grey`, `distress`) is given only with a score.

## IN05

Neumaierová, I., Neumaier, I. (2005). *Index IN05.* In: Sborník příspěvků z mezinárodní vědecké konference Evropské finanční systémy. Brno: Masarykova univerzita, pp. 143–148.

IN05 = 0.13 · V1 + 0.04 · V2 + 3.97 · V3 + 0.21 · V4 + 0.09 · V5

| | variable | statement lines |
| --- | --- | --- |
| V1 | assets / external sources | `total_assets` / `external_sources` (B. + C. of liabilities) |
| V2 | EBIT / interest expense, at most 9 | (`profit_before_tax` + `interest_expense`) / `interest_expense` (J.) |
| V3 | EBIT / assets | EBIT / `total_assets` |
| V4 | revenue / assets | (I. + II.) / `total_assets` |
| V5 | current assets / (short-term liabilities + short-term bank loans) | `current_assets` / `liabilities_short_term` (C.II.) |

- **Zones:** above 1.6 the firm creates value (safe), 0.9–1.6 grey zone, below 0.9 it destroys value (distress).
- **V2 cap:** the authors cap EBIT / interest at 9, so a firm with almost no interest does not get an unlimited score. Without any interest expense a profitable firm gets 9 and a loss-making one 0: the cap rewards the ability to pay interest, not the absence of loans in a loss.
- **V5, no double counting:** since 2016 (Vyhláška 500/2002 Sb.) short-term bank loans are C.II.2., a part of C.II. short-term liabilities. Adding them again would count them twice, so V5 uses C.II. alone.
- **V1 has no cap:** a holding with almost no external sources gets a very high V1 and IN05 (07506546: V1 338, IN05 46). The score is right by the formula; the model was not built for such firms.
- **V4:** the paper relates the firm's revenues (výnosy) to assets. Fides uses revenue from sales (I. + II.), the same revenue as the ratio engine, which every statement prints; net turnover (all income lines) is not printed by every firm. Other operating and financial income would raise V4 slightly (weight 0.21).

## Altman Z′ (private companies)

Altman, E. I. (1983). *Corporate Financial Distress: A Complete Guide to Predicting, Avoiding, and Dealing with Bankruptcy.* New York: Wiley. Altman, E. I. (2000). *Predicting Financial Distress of Companies: Revisiting the Z-Score and ZETA Models.* New York University, Stern School of Business.

Z′ is the variant of the Z-score for companies without listed shares: the book value of equity replaces the market value.

Z′ = 0.717 · X1 + 0.847 · X2 + 3.107 · X3 + 0.420 · X4 + 0.998 · X5

| | variable | statement lines |
| --- | --- | --- |
| X1 | working capital / assets | (`current_assets` − `liabilities_short_term`) / `total_assets` |
| X2 | retained earnings / assets | (`equity` − `share_capital` − `share_premium_capital_funds`) / `total_assets` |
| X3 | EBIT / assets | EBIT / `total_assets` |
| X4 | book value of equity / total liabilities | `equity` / `external_sources` |
| X5 | sales / assets | (I. + II.) / `total_assets` |

- **Zones:** above 2.9 safe, 1.23–2.9 grey zone, below 1.23 distress.
- **Retained earnings (X2):** the equity the firm earned itself, not the capital its owners paid in: A. equity − A.I. share capital − A.II. share premium and capital funds. This equals A.III. funds from profit + A.IV. retained earnings of prior years + A.V. profit of the year − A.VI. advance profit distribution. It includes the current year's profit, as retained earnings on a US balance sheet do. Taking A.IV. alone would miss the funds from profit and the year's result.
- **Negative equity** is not "not meaningful" here: X2 and X4 turn negative and the score falls, which is what the model is for.

On the 99 collected statements: IN05 for 44, Z′ for 44 (the others have no income statement, mostly balance-sheet-only filings, or are not in the Czech form); the two models give the same zone for 32 of 43.

## Hand calculation

Computed by hand from the printed statements (tis. Kč) and checked against the code in `tests/test_bankruptcy.py`:

| | Mondi Štětí 2025 | Mondi 2024 | Skyport 2025 | Skyport 2024 |
| --- | --- | --- | --- | --- |
| EBIT | 507 009 + 797 693 = 1 304 702 | 1 334 961 + 635 640 = 1 970 601 | 69 387 + 443 = 69 830 | 5 515 + 495 = 6 010 |
| V1 · V2 · V3 · V4 · V5 | 1.371 · 1.636 · 0.044 · 0.527 · 0.223 | 1.363 · 3.100 · 0.069 · 0.509 · 0.216 | 2.297 · 9 (157.6) · 0.293 · 2.223 · 2.396 | 2.329 · 9 (12.1) · 0.046 · 2.137 · 2.986 |
| **IN05** | **0.550 distress** | **0.703 distress** | **2.505 safe** | **1.561 grey** |
| X1 · X2 · X3 · X4 · X5 | −0.526 · 0.199 · 0.044 · 0.371 · 0.527 | −0.535 · 0.192 · 0.069 · 0.363 · 0.509 | 0.498 · 0.230 · 0.293 · 1.296 · 2.223 | 0.583 · −0.851 · 0.046 · 1.327 · 2.137 |
| **Z′** | **0.610 distress** | **0.655 distress** | **4.225 safe** | **2.529 grey** |

Mondi is in distress in both models although it is a profitable manufacturer: 16.5 bn CZK of its 20 bn CZK short-term liabilities are owed within the Mondi group, which turns working capital and V5 negative. The analyst has to read the scores with the notes; the models do not know about group financing.

## For the presentation: why IN05

IN05 was built by Inka and Ivan Neumaier on the financial statements of Czech industrial companies and published in 2005, so its weights reflect Czech accounting (Vyhláška 500/2002), Czech capital structures and Czech interest levels rather than those of US listed firms in the 1960s. It joins the creditor's view (bankruptcy risk) with the owner's view (whether the firm creates value), and it is widely taught and used in Czech financial analysis, so a Cofidis analyst can compare a Fides result with what they already know. Altman Z′ is computed next to it as the internationally known reference.
