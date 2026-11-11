# PDF report (CIPW-43)

`src/report/pdf.py` turns one result into a PDF for the analyst: Cofidis' layout and colours, Fides credited in the footer of every page.

```
python scripts/render_report.py data/fixtures/result_26161516_2025.json             # -> outputs/reports/
python scripts/render_report.py <result.json> --lang en
python scripts/render_report.py <result.json> --refresh   # a result saved by an older version: recompute company, ratios, models, lights
```

## What it contains

| Part | Comes from |
| --- | --- |
| Cofidis' logo (or its name), report title, date | `settings.report` |
| Company: name, IČO, legal form, address, CZ-NACE | ARES (`Result.company`, `docs/ares.md`); the printed name when ARES is unknown |
| Statement: balance sheet date, scope, individual or consolidated, digital or scan | the parser (`Result.statements.metadata`) |
| Verdict box: overall risk, its reasoning, the bankruptcy scores with their zones | rating and reasoning from the AI report (CIPW-16), scores from `Result.bankruptcy` |
| Summary, strengths, risks, red flags | the AI report (CIPW-16) |
| Key figures (CZK thousands), both years and the change | the statements |
| Financial ratios with their traffic light and the MPO industry value | `Result.ratios`, `Result.benchmarks` (CIPW-12, CIPW-14) |
| Bankruptcy models (and the probability of default for logit/probit models) | `Result.bankruptcy` (CIPW-13, CIPW-52) |
| Data quality: the AI's note, checks passed, values to review, statement pages, the number check | `Result.validation`, the statements, `Result.number_check` (CIPW-17) |
| Footer: the Fides logo, "Vytvořeno v programu Fides, který vznikl ve spolupráci s ČVUT v rámci projektu IPW.", page number, Fides version and run id | `docs/brand/logo/fides-logo-1200.png`, `Result.audit` |

**The numbers never come from the AI.** The tables are filled from the result; only the summary, strengths, risks, red flags, the rating's reasoning and the data quality note are the AI's text, and CIPW-17 checks every number in that text against the data.

**Every state is visible:**
- a report with mock AI text is marked "UKÁZKOVÝ TEXT" at the top of every page;
- without an AI report (offline mode, no API key) one sentence says so, and the numbers stand alone;
- a ratio with an input to review gets "ověřit" instead of a traffic light;
- mock traffic lights of an old sample are not shown.

## Cofidis' brand

Cofidis introduced a new brand identity in March 2026; its brand files are not in this repository. Until Cofidis provides them, the template uses the red and yellow of the CIPW-18 requirement and the name "Cofidis" in place of the logo. To brand it, set in `config/fides.yaml`:

```yaml
report:
  language: cs          # cs | en (labels; the AI text has its own language)
  primary: "#D52B1E"    # Cofidis' main colour: header band, headings, verdict bar
  accent: "#FFC72C"     # second colour: the thin band under the header and the verdict box (as a light tint)
  logo: config/brand/cofidis-logo.png   # PNG or JPEG, about 30 pt high in the header
```

Fides stays in the footer, apart from the Cofidis logo (rule in `docs/brand/README.md`: the two logos are never locked together).

## How it is built

The body is HTML laid out by PyMuPDF (`pymupdf.Story`), so no extra dependency is needed. The built-in Nimbus Sans covers Czech, and the body flows over as many A4 pages as it needs. The brand band, the footer, the page numbers and the mock mark are drawn on every page afterwards.

The verdict box is drawn under its text after the layout. A background inside the Story leaks a strip onto the top of the next page.
