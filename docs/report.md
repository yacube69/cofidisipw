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
| Verdict box: overall risk, its reasoning, the bankruptcy scores with their zones | rating and reasoning from the assessment, scores from `Result.bankruptcy` |
| Summary, strengths, risks, red flags | the assessment: the AI report (CIPW-16), or the rule-based one without an AI model (below) |
| Key figures (CZK thousands), both years and the change | the statements |
| Financial ratios with their traffic light and the MPO industry value | `Result.ratios`, `Result.benchmarks` (CIPW-12, CIPW-14) |
| Bankruptcy models (and the probability of default for logit/probit models) | `Result.bankruptcy` (CIPW-13, CIPW-52) |
| Data quality: the AI's note, checks passed, values to review, statement pages, the number check | `Result.validation`, the statements, `Result.number_check` (CIPW-17) |
| Footer: the Fides logo, "Vytvořeno v programu Fides, který vznikl ve spolupráci s ČVUT v rámci projektu IPW.", page number, Fides version and run id | `docs/brand/logo/fides-logo-1200.png`, `Result.audit` |

**The numbers never come from the AI.** The tables are filled from the result; only the summary, strengths, risks, red flags, the rating's reasoning and the data quality note are the assessment's text, and CIPW-17 checks every number in an AI text against the data.

**Every state is visible:**
- a report with mock AI text is marked "UKÁZKOVÝ TEXT" at the top of every page;
- a rule-based assessment says so in the data quality section;
- without any assessment (`render_report` on a result analysed with `skip_report`) one sentence says so, and the numbers stand alone;
- a ratio with an input to review gets "ověřit" instead of a traffic light;
- mock traffic lights of an old sample are not shown.

## Export from the app

*Export PDF* in the header of a result downloads `fides_{IČO}_{year}.pdf` from `GET /api/results/{id}/report.pdf` (CIPW-54). The labels follow `report.language`. A result without an assessment (analysed with `skip_report`) gets the rule-based one, so the PDF always has its text.

## Without an AI model: the rule-based assessment

Until the LLM report of CIPW-16 is built, and whenever no AI model is connected (offline mode, no API key), `src/report/fallback.py` builds the assessment from the numbers by fixed rules (CIPW-55). It has the same fields as the AI report and `source: "rules"`.

| Field | Built from |
| --- | --- |
| Summary | revenue and its change, profit for the period, current ratio and debt ratio, the bankruptcy scores with their zones |
| Strengths | the ratios with a green light |
| Risks | the red ratios, then the amber ones |
| Red flags | a model in distress, negative equity, a loss, failed accounting checks |
| Rating | **high:** a model in distress, negative equity, or 2 or more red ratios; **medium:** a model in the grey zone, 1 red ratio, or 3 or more amber ratios; **low:** otherwise, when a model has a score; **none** when no model has a score |

- A value computed from an input to review is still used, but the text marks it "ověřit".
- The same numbers always give the same text and rating.
- The app shows a banner, and the PDF says that fixed rules built the text.
- The LLM report keeps the same fields with `source: "llm"`, so the app and the PDF show it without changes.

## Cofidis' brand

The colours are Cofidis' own, from its public brand toolkit (https://www.cofidis.hu/toolkit):

| Colour | HEX | RGB | CMYK | Used for |
| --- | --- | --- | --- | --- |
| Cofidis red | `#D0103A` | 208 16 58 | 0 100 80 0 | header band, headings, the verdict bar and the rating |
| Cofidis yellow | `#FECB00` | 254 203 0 | 0 20 100 0 | the thin band under the header, the verdict box (as a light tint) |

The logo is `config/brand/cofidis-logo.png`, the horizontal logo for white backgrounds from the toolkit (`cofidis_logo_cmyk_horizontal_feher_alapra-01.png` in `cofidis-toolkit-logo.zip`, 3702 × 1203 px, transparent). It goes in the top left corner, on white, 33.5 mm wide (the PNG includes the clear space), following the toolkit's rules:
- at least 32 mm wide (the drawn logo, without its clear space);
- in a corner, never part of a text;
- on white or a brand colour;
- clear space of half a capital "C".

The toolkit's other variants (for red, yellow and black backgrounds, black for print) are not needed on a white page. With `logo: ""` the name "Cofidis" in red stands in. The settings in `config/fides.yaml`:

```yaml
report:
  language: cs          # cs | en (labels; the AI text has its own language)
  primary: "#D0103A"    # Cofidis red
  accent: "#FECB00"     # Cofidis yellow
  logo: config/brand/cofidis-logo.png   # PNG or JPEG, the horizontal logo on white
```

The toolkit's typeface is Gotham, a commercial font; the report uses the built-in Nimbus Sans instead, so no font licence is needed for the Docker image.

Fides stays in the footer, apart from the Cofidis logo (rule in `docs/brand/README.md`: the two logos are never locked together).

## How it is built

The body is HTML laid out by PyMuPDF (`pymupdf.Story`), so no extra dependency is needed. The built-in Nimbus Sans covers Czech, and the body flows over as many A4 pages as it needs. The brand band, the footer, the page numbers and the mock mark are drawn on every page afterwards.

The verdict box is drawn under its text after the layout. A background inside the Story leaks a strip onto the top of the next page.
