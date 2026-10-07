# Fides brand manual

Live version: https://claude.ai/artifact/8eveWaBuKpXpt9Zp1q2Q2j (CIPW-21). Logos in `logos/`, tokens in `tokens.json`, component CSS in `components/fides.css`, Mobbin references in `inspiration.md`.

**Fides — Financial Insight & Data Extraction System.** Fides reads Czech financial statements (including scanned PDFs), checks that the numbers add up, and turns them into ratios, bankruptcy-model scores and a short AI assessment for credit analysts.

*Fides* is Latin for trust and faith. It also echoes the end of "Cofidis". The whole brand rests on one promise:

> **Every number, traced to its page.**

Every design decision serves that promise. A figure in Fides is never a bare number: it always shows where it came from (page, line code) and whether it passed the checks.

## Brand idea

- **Trust you can check.** We do not ask analysts to believe the machine. We show the source, the check and the confidence next to every value.
- **The ledger row.** The visual motif is the row of a Czech statement: a line label, a gap, and a value in its box. The logo is built from it.
- **Quiet until it matters.** The UI stays calm (pine, paper, ink). Colour appears only for the brand, for the source trace (brass) and for check results.

## Name and wordmark

- Write **Fides** with a capital F and nothing else capitalised. Never FIDES in running text, never "the Fides".
- Spell out the acronym only once, on a title slide or the About screen: *Financial Insight & Data Extraction System*.
- Pronunciation: FEE-des (/ˈfiː.dɛs/). Say it on the first slide of a presentation.
- The wordmark is the word *Fides* set in `display` (Newsreader 500) in `ink`, placed right of the mark with a gap of one quarter of the mark height, cap height aligned to the top ledger row. It is set live in type; there is no outlined wordmark file yet.

## Logo

The mark is a pine square (`fides`, corner `radius-lg`) holding an **F built from two ledger rows**. The lower row ends in a **brass square** (`seal`): the value that Fides found and verified.

- Use `fides-mark.svg` on `surface` and `surface-raised` in the light theme.
- Use `fides-mark-on-dark.svg` on dark surfaces.
- Use `fides-mark-mono.svg` where only one ink is possible (fax-like print, one-colour stamps).
- Minimum size 20px on screen; clear space on every side = the width of the F stem (1/8 of the mark).
- Do not recolour the seal square, rotate the mark, add a shadow or place it on a photo.
- Fides is a prototype built for Cofidis. Do not combine the Fides mark with the Cofidis logo in one lockup; show Cofidis separately ("Built for Cofidis") and only with their approval.

## Voice and copy

Fides speaks like a careful senior analyst: precise, calm, never selling.

- **Plain and specific.** "Total assets on page 4 do not match the sum of lines 002–078 (difference 12 tis. Kč)." Not "Something went wrong with your data."
- **Always cite.** Every claim in the AI assessment ends with its source: *(Rozvaha, ř. 037)*.
- **Hedge honestly.** Use "likely", "suggests" for model output; never "guaranteed" or "100 % accurate".
- **Bilingual by design.** UI language is English; statement terms keep their Czech names with English in brackets the first time: *Rozvaha (balance sheet)*, *Výkaz zisku a ztráty (income statement)*.
- **Numbers the Czech way in data:** thin space thousands separator, decimal comma, unit stated once per table: `12 345 tis. Kč`, `1,84`.
- Sentence case for buttons and headings: "Upload statement", "Re-read page".
- No emoji, no exclamation marks.

Examples:

| Situation | Write | Avoid |
| --- | --- | --- |
| Upload button | Upload statement | Let's go! |
| Processing | Reading page 3 of 12 | Magic in progress |
| Check passed | Verified · assets = liabilities | All good 🎉 |
| Low confidence | Review · OCR confidence 71 % on line 037 | Might be wrong |
| AI summary | Low bankruptcy risk (IN05 = 1,92, above the 1,6 threshold). | This company is safe. |

## Colour

- Build every screen on `surface` with `surface-raised` cards and `line` hairlines. Separate with borders, not shadows. `shadow-page` belongs only to the rendered PDF page, so it reads as paper.
- Set text in `ink`; secondary text, units and source references in `ink-muted`.
- `fides` is the brand colour: primary button, active navigation, links, the logo. One primary button per view.
- `seal` (brass) means **source**. Use it only for the trace marker and the PDF highlight (`seal-tint`). Never for buttons or decoration.
- Check results use `status-verified`, `status-review`, `status-mismatch`. Each status always carries its icon **and** its word; colour is never the only signal. Verified is blue, not green, so it never depends on telling red from green.
- Both themes are first-class. Analysts read long tables, so dark mode is not an afterthought.

## Typography

Fonts are free on Google Fonts and cover Czech diacritics (ě š č ř ž ý á í é ů ú ň ť ď).

- `display` (Newsreader): company names, presentation titles and the AI verdict line in italic (`verdict`). Never for UI controls or numbers.
- `sans` (IBM Plex Sans): all UI text — `heading`, `title`, `body`, `small`.
- `mono` (IBM Plex Mono): every amount (`figure`, `figure-lg`) and every line code or column header (`label`, uppercase, letter-spaced). Turn on `font-variant-numeric: tabular-nums`; right-align amounts.
- Keep running text at about 65 characters per line.

## Layout and spacing

- 4px base grid: `space-1` … `space-12`.
- The core screen is the **split view**: rendered PDF page on the left (`surface-sunken` well), extracted values on the right. Selecting a value highlights its region on the page in `seal-tint` and scrolls the PDF there; selecting a region highlights the row.
- Summary before detail: a ratio dashboard opens with 4–6 ratio tiles (liquidity, leverage, profitability, IN05 / Altman Z), then the full statements.
- Radii: `radius-sm` for chips and inputs, `radius-md` for buttons and cards, `radius-lg` for the logo and dialogs. Nothing is fully rounded except status dots.
- Focus: 2px solid `focus` ring, 2px offset, on every interactive element.

## Iconography

- Use **Lucide** icons (MIT, available for Streamlit via SVG and on every CDN) at 16px / 1.5px stroke in tables and 20px in navigation, coloured `ink-muted` or the status colour.
- Fixed meanings: `check` = Verified, `eye` = Review, `x` = Mismatch, `file-search` = Source trace, `scan-text` = OCR, `scale` = Accounting check, `sparkles` is **not** used (the AI assessment is labelled "AI assessment" in words).
- No illustrations or stock photos in the product. Presentations may use real (anonymised) statement scans as imagery.

## Motion

- Only functional motion: the PDF scrolling to a highlighted region (200 ms ease-out) and the reading progress bar. Respect `prefers-reduced-motion`.

## Applying it to the Streamlit app (CIPW-18)

- App title: `Fides`, page icon: the mark. Theme: `primaryColor = fides`, `backgroundColor = surface`, `secondaryBackgroundColor = surface-sunken`, `textColor = ink`, font `sans serif` (Plex loaded via custom CSS).
- Put the Fides mark and wordmark top-left; below it the tagline in `small`, `ink-muted`.
