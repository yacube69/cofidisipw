Markdown
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
