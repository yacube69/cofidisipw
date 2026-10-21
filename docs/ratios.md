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
| ok | 585 |
| unreliable | 752 |
| not_meaningful | 62 |
| missing | 977 |

`missing` comes mostly from files whose statements are not in the Czech form (IFRS annual reports, banks) or were not located; 14 files have no ratio at all. `unreliable` follows the failed accounting checks of the parser (CIPW-32) and the values to review on scans. Of the values outside a plausible range, those flagged `ok` are real: negative equity (debt ratio above 100 %), a holding with group receivables far above its revenue, a firm with almost no interest.

The formulas are reviewed in CIPW-15.

---


# Bankruptcy Prediction Models

## IN05 Model (Neumaierová & Neumaier, 2005)
Designed specifically for the Czech market, reflecting local accounting standards and economic conditions.

**Formula:**
IN05 = (0.13 * V1) + (0.04 * V2) + (3.97 * V3) + (0.21 * V4) + (0.09 * V5)

**Variables:**
* **V1** = Total Assets / Total Liabilities (External Sources)
* **V2** = EBIT / Interest Expense
  * *Note: According to the original paper, if the company has zero or near-zero interest expense, cap this ratio at a maximum value of 9.*
* **V3** = EBIT / Total Assets
* **V4** = Revenue (Sales) / Total Assets
* **V5** = Current Assets / (Short-term Liabilities + Short-term Bank Loans)
  * *Note: Ensure bank loans are not double-counted if they are already included in short-term liabilities.*

**Interpretation Zones:**
* **Safe:** IN05 > 1.6 (Company creates value)
* **Grey:** 0.9 <= IN05 <= 1.6 (Grey zone)
* **Distress:** IN05 < 0.9 (Company destroys value, high risk of bankruptcy)

---

## Altman Z'-score (For Private / Non-listed Companies)
A variant of the traditional Altman Z-score adapted for private, non-listed entities by replacing the market value of equity with the book value.

**Formula:**
Z' = (0.717 * X1) + (0.847 * X2) + (3.107 * X3) + (0.420 * X4) + (0.998 * X5)

**Variables:**
* **X1** = Working Capital / Total Assets
  * *(Working Capital = Current Assets - Current Liabilities)*
* **X2** = Retained Earnings / Total Assets
* **X3** = EBIT / Total Assets
* **X4** = Book Value of Equity / Total Liabilities
* **X5** = Revenue (Sales) / Total Assets

**Interpretation Zones:**
* **Safe:** Z' > 2.9 (Safe zone)
* **Grey:** 1.23 <= Z' <= 2.9 (Grey zone)
* **Distress:** Z' < 1.23 (Distress zone, high risk of bankruptcy)
