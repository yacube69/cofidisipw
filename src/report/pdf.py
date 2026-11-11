"""PDF report of one result (CIPW-43): Cofidis' layout and colours, Fides credited in the footer.

The numbers come straight from the result: the statements, the ratios with their traffic lights (CIPW-14) and the
bankruptcy models (CIPW-13). Only the summary, the strengths, risks and red flags, the reasoning of the rating and the
data quality note are the assessment's text: the AI report (CIPW-16), whose numbers CIPW-17 checks against the data, or,
without an AI model, the rule-based assessment (src/report/fallback.py). A report with mock text says so on every page.

The page is HTML laid out by PyMuPDF (`pymupdf.Story`), so no new dependency. Colours, logo and language come from
`settings.report` (config/fides.yaml): Cofidis' colours and logo from its brand toolkit (docs/report.md).

    render_report(result) -> bytes          # the PDF
    python scripts/render_report.py data/fixtures/result_26161516_2025.json
"""

from __future__ import annotations

import html
import io
from datetime import date
from pathlib import Path

import pymupdf

from src.result import Result
from src.settings import ROOT, Settings, get_settings

A4 = pymupdf.paper_rect("a4")
MARGIN = 42
BODY = pymupdf.Rect(MARGIN, 58, A4.width - MARGIN, A4.height - 70)  # leaves room for the header band and footer
FIDES_LOGO = "docs/brand/logo/fides-logo-1200.png"
# Cofidis' toolkit: the logo at least 32 mm wide, in a corner, on white or a brand colour, clear space half a "C".
# The toolkit's PNG carries its clear space: the drawn logo is 86 % of its width, so 110 pt gives a 33.5 mm logo.
LOGO_WIDTH = 110  # pt
INK, MUTED, LINE = "#1E1A18", "#6B6560", "#D9D4CF"
LIGHT_COLOUR = {"green": "#2E7D32", "amber": "#E0A100", "red": "#C62828"}
# the verdict box is drawn under its text after the layout: a background inside the Story leaks onto the next page
VERDICT_PAD, VERDICT_BAR = 9, 6

LABELS = {
    "cs": {
        "title": "Hodnocení finanční situace",
        "made": "Vytvořeno",
        "ico": "IČO",
        "nace": "Obor (CZ-NACE)",
        "other_company": "ARES uvádí pro toto IČO jinou firmu ({name}), ověřte IČO",
        "statement": "Účetní závěrka k",
        "full": "plný rozsah",
        "abbreviated": "zkrácený rozsah",
        "individual": "individuální",
        "consolidated": "konsolidovaná",
        "scan": "sken, čteno OCR",
        "digital": "digitální PDF",
        "rating": "Celkové riziko",
        "rating_value": {"low": "NÍZKÉ", "medium": "STŘEDNÍ", "high": "VYSOKÉ"},
        "summary": "Shrnutí",
        "strengths": "Silné stránky",
        "risks": "Rizika",
        "red_flags": "Varovné signály",
        "none": "Žádné.",
        "no_report": "Text hodnocení AI není k dispozici (offline režim nebo bez API klíče). Čísla níže pocházejí "
        "přímo z výkazu.",
        "key_figures": "Klíčové údaje (tis. Kč)",
        "item": "Položka",
        "change": "Změna",
        "ratios": "Finanční ukazatele",
        "ratio": "Ukazatel",
        "assessment": "Hodnocení",
        "industry": "Obor",
        "bankruptcy": "Bankrotní modely",
        "model": "Model",
        "zone": "Pásmo",
        "probability": "Pravděpodobnost úpadku",
        "zones": {"safe": "bezpečí", "grey": "šedá zóna", "distress": "tíseň"},
        "lights": {"green": "zdravé", "amber": "sledovat", "red": "slabé"},
        "check": "ověřit",
        "industry_note": "Obor: průměr odvětví podle MPO ({period}, čtvrtletní údaje), jen pro orientaci.",
        "data_quality": "Kvalita dat",
        "checks": "Účetní kontroly: {passed} z {total} prošlo.",
        "review": "Hodnot k ověření: {n}.",
        "pages": "Strany výkazů v PDF: rozvaha {bs}, výsledovka {is_}.",
        "numbers_ok": "Všechna čísla v textu hodnocení odpovídají datům výkazu.",
        "numbers_bad": "Čísla v textu, která neodpovídají datům: {numbers}.",
        "how": "Čísla v tabulkách pocházejí přímo z výkazu. Text hodnocení napsala AI pouze z těchto čísel.",
        "how_rules": "Čísla v tabulkách pocházejí přímo z výkazu. Text hodnocení sestavila pevná pravidla z těchto "
        "čísel (AI model nebyl připojen).",
        "mock": "UKÁZKOVÝ TEXT – hodnocení zatím nenapsala AI",
        "footer": "Vytvořeno v programu Fides, který vznikl ve spolupráci s ČVUT v rámci projektu IPW.",
        "page": "Strana {i} z {n}",
        "days": "dní",
        "items": {
            "total_assets": "Aktiva celkem",
            "equity": "Vlastní kapitál",
            "external_sources": "Cizí zdroje",
            "revenue": "Tržby (I. + II.)",
            "operating_result": "Provozní výsledek hospodaření",
            "profit_for_period": "Výsledek hospodaření za účetní období",
        },
        "ratio_names": {
            "current_ratio": "Běžná likvidita",
            "quick_ratio": "Pohotová likvidita",
            "cash_ratio": "Okamžitá likvidita",
            "roa": "Rentabilita aktiv (ROA)",
            "roe": "Rentabilita vlastního kapitálu (ROE)",
            "ros": "Rentabilita tržeb (ROS)",
            "ebitda_margin": "EBITDA marže",
            "debt_ratio": "Celková zadluženost",
            "debt_to_equity": "Míra zadluženosti (D/E)",
            "interest_coverage": "Úrokové krytí",
            "receivables_days": "Doba obratu pohledávek",
            "payables_days": "Doba obratu závazků",
        },
    },
    "en": {
        "title": "Financial assessment",
        "made": "Created",
        "ico": "IČO",
        "nace": "Industry (CZ-NACE)",
        "other_company": "ARES names another company for this IČO ({name}): check the IČO",
        "statement": "Financial statements as of",
        "full": "full",
        "abbreviated": "abbreviated",
        "individual": "individual",
        "consolidated": "consolidated",
        "scan": "scan, read by OCR",
        "digital": "digital PDF",
        "rating": "Overall risk",
        "rating_value": {"low": "LOW", "medium": "MEDIUM", "high": "HIGH"},
        "summary": "Summary",
        "strengths": "Strengths",
        "risks": "Risks",
        "red_flags": "Red flags",
        "none": "None.",
        "no_report": "The AI assessment text is not available (offline mode or no API key). The numbers below come "
        "straight from the statements.",
        "key_figures": "Key figures (CZK thousands)",
        "item": "Item",
        "change": "Change",
        "ratios": "Financial ratios",
        "ratio": "Ratio",
        "assessment": "Assessment",
        "industry": "Industry",
        "bankruptcy": "Bankruptcy models",
        "model": "Model",
        "zone": "Zone",
        "probability": "Probability of default",
        "zones": {"safe": "safe", "grey": "grey zone", "distress": "distress"},
        "lights": {"green": "healthy", "amber": "watch", "red": "weak"},
        "check": "check",
        "industry_note": "Industry: sector average by MPO ({period}, quarterly data), for orientation only.",
        "data_quality": "Data quality",
        "checks": "Accounting checks: {passed} of {total} passed.",
        "review": "Values to review: {n}.",
        "pages": "Statement pages in the PDF: balance sheet {bs}, income statement {is_}.",
        "numbers_ok": "Every number in the assessment text matches the statement data.",
        "numbers_bad": "Numbers in the text that do not match the data: {numbers}.",
        "how": "The numbers in the tables come straight from the statements. The AI wrote the assessment text from "
        "these numbers only.",
        "how_rules": "The numbers in the tables come straight from the statements. Fixed rules built the assessment "
        "text from these numbers (no AI model was connected).",
        "mock": "SAMPLE TEXT – not written by the AI yet",
        "footer": "Created with Fides, developed in cooperation with the Czech Technical University (ČVUT) in the "
        "IPW project.",
        "page": "Page {i} of {n}",
        "days": "days",
        "items": {
            "total_assets": "Total assets",
            "equity": "Equity",
            "external_sources": "External sources",
            "revenue": "Revenue (I. + II.)",
            "operating_result": "Operating result",
            "profit_for_period": "Profit for the period",
        },
        "ratio_names": {},  # the engine's English names
    },
}
KEY_FIGURES = ["total_assets", "equity", "external_sources", "revenue", "operating_result", "profit_for_period"]


# --------------------------------------------------------------------------------------------- formatting


def number(value: float | None, decimals: int = 0, lang: str = "cs") -> str:
    """1234567.8 -> '1 234 568' (cs, thin no-break spaces and a decimal comma) or '1,234,568' (en)."""
    if value is None:
        return "–"
    if round(value, decimals) == 0:
        value = 0.0  # no "−0,00"
    text = f"{value:,.{decimals}f}"
    if lang == "cs":
        text = text.replace(",", " ").replace(".", ",")
    return text.replace("-", "−")


def ratio_value(value: float | None, unit: str, lang: str) -> str:
    if value is None:
        return "–"
    if unit == "x":
        return number(value, 2, lang) + "×"
    if unit == "%":
        return number(value, 1, lang) + " %"
    return number(value, 0, lang) + " " + LABELS[lang]["days"]


def ratio_change(change: float | None, unit: str, lang: str) -> str:
    if change is None:
        return "–"
    sign = "+" if round(change, 2 if unit == "x" else 1 if unit == "%" else 0) > 0 else ""
    suffix = {"x": "", "%": " p. b." if lang == "cs" else " pp", "days": ""}[unit]
    return sign + number(change, 2 if unit == "x" else 1 if unit == "%" else 0, lang) + suffix


def esc(text: object) -> str:
    return html.escape(str(text), quote=True)


# --------------------------------------------------------------------------------------------- content


def _amount(result: Result, key: str, period: str) -> float | None:
    fs = result.statements
    if key == "revenue":
        parts = [fs.get(k, period) for k in ("revenue_products_services", "revenue_goods")]  # type: ignore[arg-type]
        return None if all(p is None for p in parts) else sum(p or 0.0 for p in parts)
    if key == "profit_for_period":
        for k in ("profit_for_period", "profit_after_tax", "profit_current_period"):
            if (v := fs.get(k, period)) is not None:  # type: ignore[arg-type]
                return v
        return None
    return fs.get(key, period)  # type: ignore[arg-type]


def _header(result: Result, t: dict, s: Settings) -> str:
    meta = result.statements.metadata
    # ARES' data only for the statement's own company: for a misread IČO ARES names another one (CIPW-56)
    company = result.company if result.name_source == "ares" else None
    name = result.display_name or f"{t['ico']} {meta.ico or '–'}"
    logo = ROOT / s.report.logo if s.report.logo else None
    brand = (
        f'<img src="{esc(s.report.logo)}" width="{LOGO_WIDTH}"/>'
        if logo and logo.exists()
        else f'<span style="color:{s.report.primary};font-size:22pt;font-weight:bold">Cofidis</span>'
    )
    facts = [f"{t['ico']} {esc(meta.ico or '–')}"]
    if company and company.legal_form:
        facts.append(esc(company.legal_form))
    if company and company.address:
        facts.append(esc(company.address))
    if result.company and result.company.name and company is None:
        facts.append(t["other_company"].format(name=esc(result.company.name)))
    statement = []
    if meta.period_end:
        statement.append(f"{t['statement']} {meta.period_end.day}. {meta.period_end.month}. {meta.period_end.year}")
    if meta.scope:
        statement.append(t[meta.scope])
    if result.consolidation:
        statement.append(t[result.consolidation])
    statement.append(t["scan"] if "ocr" in meta.extraction_source.values() else t["digital"])
    made = date.today()
    made_text = f"{t['made']} {made.day}. {made.month}. {made.year}"
    nace = ""
    if company and company.nace:
        industry = f"{esc(company.nace)} {esc(company.nace_name or '')}"
        nace = f'<p style="margin:2px 0 0 0;color:{MUTED}">{t["nace"]}: {industry}</p>'
    return f"""
<table style="width:100%;border-collapse:collapse"><tr>
  <td style="vertical-align:middle">{brand}</td>
  <td style="text-align:right;vertical-align:middle;color:{MUTED};font-size:8.5pt">{t['title']}<br/>{made_text}</td>
</tr></table>
<p style="margin:14px 0 0 0;font-size:17pt;font-weight:bold;color:{INK}">{esc(name)}</p>
<p style="margin:3px 0 0 0;color:{MUTED}">{' · '.join(facts)}</p>
{nace}
<p style="margin:2px 0 0 0;color:{MUTED}">{' · '.join(statement)}</p>
"""


def _verdict(result: Result, t: dict, s: Settings) -> str:
    report = result.report or {}
    rating = (report.get("rating") or {}).get("value")
    reasoning = (report.get("rating") or {}).get("reasoning") or ""
    scores = []
    for model in result.bankruptcy.models if result.bankruptcy else []:
        cur = model.current
        if cur.score is None:
            continue
        zone = f" ({t['zones'][cur.zone]})" if cur.zone else ""
        scores.append(f"{esc(model.name.split(' (')[0])} {number(cur.score, 2, s.report.language)}{zone}")
    word = t["rating_value"].get(rating, "–") if rating else "–"
    return f"""
<div id="verdict" style="margin-top:23px;margin-bottom:20px;padding:0 12px 0 18px">
  <span style="font-size:8.5pt;color:{MUTED}">{t['rating']}</span><br/>
  <span style="font-size:15pt;font-weight:bold;color:{s.report.primary}">{word}</span>
  <span style="color:{INK}">&nbsp;&nbsp;{esc(reasoning)}</span><br/>
  <span style="font-size:8.5pt;color:{MUTED}">{' · '.join(scores)}</span>
</div>
"""


def _tint(hex_colour: str, share: float = 0.22) -> str:
    """The colour mixed with white: a light background in the brand colour."""
    c = hex_colour.lstrip("#")
    r, g, b = (int(c[i : i + 2], 16) for i in (0, 2, 4))
    return "#{:02X}{:02X}{:02X}".format(*(round(255 - (255 - x) * share) for x in (r, g, b)))


def _section(title: str, s: Settings) -> str:
    return (
        f'<p style="margin:16px 0 5px 0;font-size:11pt;font-weight:bold;color:{s.report.primary}">{esc(title)}</p>'
    )


def _bullets(items: list[str], t: dict, marker_colour: str) -> str:
    if not items:
        return f'<p style="margin:0;color:{MUTED}">{t["none"]}</p>'
    return "".join(
        f'<p style="margin:0 0 3px 0"><span style="color:{marker_colour}">■</span>&nbsp; {esc(i)}</p>' for i in items
    )


def _texts(result: Result, t: dict, s: Settings) -> str:
    report = result.report or {}
    if not report:  # offline, no API key, or the report was skipped: the numbers below stand on their own
        return _section(t["summary"], s) + f'<p style="margin:0;color:{MUTED}">{t["no_report"]}</p>'
    out = _section(t["summary"], s) + f'<p style="margin:0">{esc(report.get("summary") or "–")}</p>'
    strengths = _bullets(report.get("strengths") or [], t, LIGHT_COLOUR["green"])
    risks = _bullets(report.get("risks") or [], t, LIGHT_COLOUR["amber"])
    out += f"""
<table style="width:100%;border-collapse:collapse;margin-top:10px"><tr>
  <td style="width:50%;vertical-align:top;padding-right:10px">
    <p style="margin:0 0 4px 0;font-weight:bold">{t['strengths']}</p>{strengths}
  </td>
  <td style="width:50%;vertical-align:top;padding-left:10px">
    <p style="margin:0 0 4px 0;font-weight:bold">{t['risks']}</p>{risks}
  </td>
</tr></table>"""
    out += f'<p style="margin:8px 0 4px 0;font-weight:bold">{t["red_flags"]}</p>'
    out += _bullets(report.get("red_flags") or [], t, LIGHT_COLOUR["red"])
    return out


def _table(head: list[str], rows: list[list[str]], right_from: int = 1) -> str:
    th = "".join(
        f'<th style="text-align:{"left" if i < right_from else "right"};padding:3px 4px;border-bottom:1px solid {INK};'
        f'font-size:8.5pt;color:{MUTED}">{h}</th>'
        for i, h in enumerate(head)
    )
    body = "".join(
        "<tr>"
        + "".join(
            f'<td style="text-align:{"left" if i < right_from else "right"};padding:3px 4px;'
            f'border-bottom:1px solid {LINE}">{cell}</td>'
            for i, cell in enumerate(row)
        )
        + "</tr>"
        for row in rows
    )
    return f'<table style="width:100%;border-collapse:collapse">{"<tr>" + th + "</tr>"}{body}</table>'


def _years(result: Result) -> tuple[str, str]:
    meta = result.statements.metadata
    current = str(meta.period_end.year) if meta.period_end else "–"
    prior = str(meta.prior_period_end.year) if meta.prior_period_end else "–"
    return current, prior


def _key_figures(result: Result, t: dict, s: Settings) -> str:
    lang, (cur, pri) = s.report.language, _years(result)
    rows = []
    for key in KEY_FIGURES:
        a, b = _amount(result, key, "current"), _amount(result, key, "prior")
        if a is None and b is None:
            continue
        change = "–" if a is None or b is None or b == 0 else number((a - b) / abs(b) * 100, 1, lang) + " %"
        rows.append([t["items"][key], number(a and a / 1000, 0, lang), number(b and b / 1000, 0, lang), change])
    if not rows:
        return ""
    return _section(t["key_figures"], s) + _table([t["item"], cur, pri, t["change"]], rows)


def _light(light: str | None, flag: str, t: dict) -> str:
    if light:
        return f'<span style="color:{LIGHT_COLOUR[light]}">●</span> {t["lights"][light]}'
    if flag == "unreliable":  # an input to review or in a failed check: no light until a person confirms it
        return f'<span style="color:{MUTED}">○ {t["check"]}</span>'
    return "–"


def _ratios(result: Result, t: dict, s: Settings) -> str:
    if not result.ratios:
        return ""
    lang, (cur, pri) = s.report.language, _years(result)
    benchmarks = result.benchmarks or {}
    lights = {} if benchmarks.get("mock") else benchmarks.get("ratios") or {}  # mock lights belong to the mock firm
    industry = (result.benchmarks or {}).get("industry_benchmarks") or {}
    rows = []
    for r in result.ratios.ratios:
        bench = industry.get(r.key)
        bench_text = number(bench["value"], 1, lang) + " %" if bench and bench.get("unit") == "percent" else "–"
        rows.append([
            esc(t["ratio_names"].get(r.key, r.name)),
            ratio_value(r.current.value, r.unit, lang),
            ratio_value(r.prior.value, r.unit, lang),
            ratio_change(r.change, r.unit, lang),
            _light((lights.get(r.key) or {}).get("light"), r.current.flag, t),
            bench_text,
        ])  # fmt: skip
    note = ""
    if any(row[-1] != "–" for row in rows):
        period = next(iter(industry.values())).get("period", "")
        note = f'<p style="margin:4px 0 0 0;font-size:8pt;color:{MUTED}">{t["industry_note"].format(period=period)}</p>'
    head = [t["ratio"], cur, pri, t["change"], t["assessment"], t["industry"]]
    return _section(t["ratios"], s) + _table(head, rows) + note


def _bankruptcy(result: Result, t: dict, s: Settings) -> str:
    if not result.bankruptcy or not result.bankruptcy.models:
        return ""
    lang, (cur, pri) = s.report.language, _years(result)
    probability = any(m.current.probability is not None for m in result.bankruptcy.models)
    head = [t["model"], cur, pri, t["zone"]] + ([t["probability"]] if probability else [])
    rows = []
    for m in result.bankruptcy.models:
        row = [
            esc(m.name) + (f" ({esc(m.horizon)})" if m.horizon else ""),
            number(m.current.score, 2, lang),
            number(m.prior.score, 2, lang),
            t["zones"].get(m.current.zone, "–") if m.current.zone else "–",
        ]
        if probability:
            p = m.current.probability
            row.append("–" if p is None else number(p * 100, 1, lang) + " %")
        rows.append(row)
    return _section(t["bankruptcy"], s) + _table(head, rows)


def _data_quality(result: Result, t: dict, s: Settings) -> str:
    report, check = result.report or {}, result.number_check or {}
    errors = [c for c in (result.validation.checks if result.validation else []) if c.severity == "error"]
    review = sum(
        bool(v.needs_review)
        for st in (result.statements.balance_sheet, result.statements.income_statement)
        for item in st.values()
        for v in item.values()
    )
    pages = result.pages or {}
    lines = [
        t["checks"].format(passed=sum(c.passed for c in errors), total=len(errors)),
        t["review"].format(n=review),
        t["pages"].format(bs=_pages(pages.get("balance_sheet")), is_=_pages(pages.get("income_statement"))),
    ]
    if check:
        unverified = check.get("unverified") or []
        bad = t["numbers_bad"].format(numbers=", ".join(map(str, unverified)))
        lines.append(bad if unverified else t["numbers_ok"])
    note = report.get("data_quality_note")
    how = "how_rules" if report.get("source") == "rules" else "how"
    return (
        _section(t["data_quality"], s)
        + (f'<p style="margin:0 0 4px 0">{esc(note)}</p>' if note else "")
        + "".join(f'<p style="margin:0;color:{MUTED}">{esc(line)}</p>' for line in lines)
        + f'<p style="margin:8px 0 0 0;font-size:8pt;color:{MUTED}">{t[how]}</p>'
    )


def _pages(pages: list[int] | None) -> str:
    if not pages:
        return "–"
    if len(pages) > 1 and pages[-1] - pages[0] == len(pages) - 1:
        return f"{pages[0]}–{pages[-1]}"
    return ", ".join(map(str, pages))


def report_html(result: Result, settings: Settings | None = None) -> str:
    """The body of the report as HTML for PyMuPDF's Story."""
    s = settings or get_settings()
    t = LABELS[s.report.language]
    return (
        f'<div style="font-family:sans-serif;font-size:9.5pt;color:{INK};line-height:1.35">'
        + _header(result, t, s)
        + _verdict(result, t, s)
        + _texts(result, t, s)
        + _key_figures(result, t, s)
        + _ratios(result, t, s)
        + _bankruptcy(result, t, s)
        + _data_quality(result, t, s)
        + "</div>"
    )


# --------------------------------------------------------------------------------------------- PDF


def render_report(result: Result, settings: Settings | None = None) -> bytes:
    """The report as a PDF: the body laid out over as many A4 pages as it needs, then the brand band, the footer
    with the Fides credit and page numbers, and the mock mark on every page."""
    s = settings or get_settings()
    t = LABELS[s.report.language]
    archive = pymupdf.Archive(str(ROOT))
    story = pymupdf.Story(html=report_html(result, s), archive=archive)
    body = io.BytesIO()
    writer = pymupdf.DocumentWriter(body)
    verdict: dict[int, pymupdf.Rect] = {}

    def where(position) -> None:
        if position.id == "verdict":
            verdict[position.page_num] = pymupdf.Rect(position.rect)

    more, page_no = 1, 0
    while more:
        device = writer.begin_page(A4)
        more, _ = story.place(BODY)
        story.element_positions(where, {"page_num": page_no})
        story.draw(device)
        writer.end_page()
        page_no += 1
    writer.close()
    buffer = pymupdf.open("pdf", body.getvalue())
    for page_num, rect in verdict.items():  # the text sits 18 pt from the box's left edge and 12 pt from its right
        box = pymupdf.Rect(rect.x0 - 18, rect.y0 - VERDICT_PAD, rect.x1 + 12, rect.y1 + VERDICT_PAD)
        page = buffer[page_num]
        bar = pymupdf.Rect(box.x0, box.y0, box.x0 + VERDICT_BAR, box.y1)
        page.draw_rect(pymupdf.Rect(bar.x1, box.y0, box.x1, box.y1), color=None, fill=_rgb(_tint(s.report.accent)),
                       overlay=False)  # fmt: skip
        page.draw_rect(bar, color=None, fill=_rgb(s.report.primary), overlay=False)
    mock = bool((result.report or {}).get("mock"))
    version = result.audit.tool_version if result.audit else result.tool_version
    run = f" · {result.audit.run_id}" if result.audit else ""
    for i, page in enumerate(buffer, 1):
        page.draw_rect(pymupdf.Rect(0, 0, A4.width, 8), color=None, fill=_rgb(s.report.primary))
        page.draw_rect(pymupdf.Rect(0, 8, A4.width, 10), color=None, fill=_rgb(s.report.accent))
        page.draw_line((MARGIN, A4.height - 58), (A4.width - MARGIN, A4.height - 58), color=_rgb(LINE), width=0.6)
        page.insert_htmlbox(
            pymupdf.Rect(MARGIN, A4.height - 52, A4.width - MARGIN, A4.height - 20),
            f"""<table style="width:100%;border-collapse:collapse;font-family:sans-serif;font-size:7.5pt;
color:{MUTED}"><tr>
<td style="width:62px;vertical-align:middle"><img src="{FIDES_LOGO}" width="56"/></td>
<td style="vertical-align:middle;padding-left:8px">{t['footer']}</td>
<td style="text-align:right;vertical-align:middle;white-space:nowrap">{t['page'].format(i=i, n=buffer.page_count)}
<br/>Fides {esc(version)}{esc(run)}</td>
</tr></table>""",
            archive=archive,
        )
        if mock:
            page.insert_htmlbox(
                pymupdf.Rect(MARGIN, 14, A4.width - MARGIN, 30),
                f'<p style="font-family:sans-serif;font-size:7.5pt;font-weight:bold;color:{LIGHT_COLOUR["red"]};'
                f'text-align:center">{t["mock"]}</p>',
            )
    buffer.subset_fonts()  # each page's footer embeds the fonts again: subsetting takes the PDF from ~800 to ~200 kB
    data = buffer.tobytes(garbage=4, deflate=True)
    buffer.close()
    return data


def _rgb(hex_colour: str) -> tuple[float, float, float]:
    c = hex_colour.lstrip("#")
    return tuple(int(c[i : i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def report_filename(result: Result) -> str:
    meta = result.statements.metadata
    year = meta.period_end.year if meta.period_end else date.today().year
    return f"fides_{meta.ico or Path(result.file).stem}_{year}.pdf"
