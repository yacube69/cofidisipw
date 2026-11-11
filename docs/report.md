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
