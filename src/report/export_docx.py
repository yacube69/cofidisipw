"""The reports as one Word file (CIPW-16): the page of `report_markdown`, with a professional look.

Text, numbers, tables, rating and recommendation are those of the report page (`generate.report_markdown`): this
module only lays them out (Calibri, a colour for each risk rating, tables with a dark header and light rows, header and
page number). Nothing is added to or taken out of the text; the review checklist of the page is not part of it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

from src.report.generate import ReportRun
from src.report.tables import figure_tables
from src.report.texts import period_date, texts

FONT = "Calibri"
NAVY, BLUE, GRAY, TEXT, LINE, ZEBRA = "17324D", "2F6690", "6B7785", "262626", "D0D7DE", "F5F8FB"
TONES = {  # text colour, light background
    "low": ("2E7D32", "E8F5E9"),
    "medium": ("B26A00", "FFF4E0"),
    "high": ("C62828", "FDECEA"),
    None: (GRAY, "EEF1F4"),
}
PAGE_WIDTH_CM = 17.0  # A4 with 2 cm margins
COLUMN_WIDTHS = {  # share of the page width by number of columns, then right-aligned columns
    3: ([1.6, 11.4, 4.0], set()),
    4: ([8.5, 3.0, 3.0, 2.5], {1, 2, 3}),
    5: ([3.6, 5.6, 2.6, 2.6, 2.6], {2, 3, 4}),
}
MODEL_WIDTHS = ([3.4, 6.2, 3.7, 3.7], {2, 3})


def _font(
    run_or_style: Any,
    size: float | None = None,
    color: str | None = None,
    bold: bool | None = None,
    italic: bool | None = None,
    name: str = FONT,
) -> None:
    f = run_or_style.font
    f.name = name
    rpr = run_or_style.element.get_or_add_rPr()
    fonts = rpr.find(qn("w:rFonts"))
    if fonts is None:
        fonts = OxmlElement("w:rFonts")
        rpr.insert(0, fonts)
    for attribute in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        fonts.set(qn(attribute), name)
    for attribute in ("w:asciiTheme", "w:hAnsiTheme", "w:cstheme", "w:eastAsiaTheme"):
        if fonts.get(qn(attribute)) is not None:
            del fonts.attrib[qn(attribute)]
    if size:
        f.size = Pt(size)
    if color:
        f.color.rgb = RGBColor.from_string(color)
    if bold is not None:
        f.bold = bold
    if italic is not None:
        f.italic = italic


def _shade(properties: Any, fill: str) -> None:
    for old in properties.findall(qn("w:shd")):
        properties.remove(old)
    shading = OxmlElement("w:shd")
    shading.set(qn("w:val"), "clear")
    shading.set(qn("w:color"), "auto")
    shading.set(qn("w:fill"), fill)
    properties.append(shading)


def _border(paragraph: Any, side: str, color: str, size: int = 8, space: int = 4) -> None:
    properties = paragraph._p.get_or_add_pPr()
    borders = properties.find(qn("w:pBdr"))
    if borders is None:
        borders = OxmlElement("w:pBdr")
        properties.append(borders)
    edge = OxmlElement(f"w:{side}")
    for key, value in (("val", "single"), ("sz", str(size)), ("space", str(space)), ("color", color)):
        edge.set(qn(f"w:{key}"), value)
    borders.append(edge)


def _field(paragraph: Any, instruction: str) -> None:
    for kind in ("begin", None, "end"):
        run = paragraph.add_run()
        if kind:
            mark = OxmlElement("w:fldChar")
            mark.set(qn("w:fldCharType"), kind)
            run._r.append(mark)
        else:
            text = OxmlElement("w:instrText")
            text.set(qn("xml:space"), "preserve")
            text.text = instruction
            run._r.append(text)
        _font(run, 8, GRAY)


def _styles(document: Any) -> None:
    styles = {s.name: s for s in document.styles}
    for name in ("Normal", "List Bullet"):
        _font(styles[name], 10.5, TEXT)
        styles[name].paragraph_format.space_after = Pt(3 if name == "List Bullet" else 5)
        styles[name].paragraph_format.line_spacing = 1.12
    for name, size, color, before, after in (
        ("Heading 1", 18, NAVY, 0, 8),
        ("Heading 2", 12.5, BLUE, 14, 4),
        ("Heading 3", 10.5, GRAY, 10, 4),
    ):
        style = styles[name]
        _font(style, size, color, True, False)
        style.paragraph_format.keep_with_next = True
        style.paragraph_format.space_before = Pt(before)
        style.paragraph_format.space_after = Pt(after)


def _page(document: Any, language: str) -> None:
    section = document.sections[0]
    section.page_width, section.page_height = Cm(21), Cm(29.7)
    section.left_margin = section.right_margin = Cm(2.0)
    section.top_margin, section.bottom_margin = Cm(2.2), Cm(2.0)
    header = section.header.paragraphs[0]
    header.text = ""
    _font(header.add_run("Fides"), 8, GRAY)
    header.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    _border(header, "bottom", LINE, 4, 4)
    footer = section.footer.paragraphs[0]
    footer.text = ""
    _font(footer.add_run(texts(language)["page"] + " "), 8, GRAY)
    _field(footer, "PAGE")
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER


def _paragraph(
    document: Any,
    text: str,
    size: float = 10.5,
    color: str = TEXT,
    bold: bool = False,
    italic: bool = False,
    style: str | None = None,
) -> Any:
    paragraph = document.add_paragraph(style=style)
    _font(paragraph.add_run(text), size, color, bold, italic)
    return paragraph


def _bullets(document: Any, items: list[str]) -> None:
    for item in items:
        paragraph = document.add_paragraph(style="List Bullet")
        _font(paragraph.add_run(item), 10.5, TEXT)


def _table(document: Any, header: list[str], rows: list[list[str]], model_table: bool = False) -> None:
    widths, right = MODEL_WIDTHS if model_table else COLUMN_WIDTHS[len(header)]
    table = document.add_table(rows=1, cols=len(header))
    table.autofit = False
    properties = table._tbl.tblPr
    layout = OxmlElement("w:tblLayout")
    layout.set(qn("w:type"), "fixed")
    properties.append(layout)
    borders = OxmlElement("w:tblBorders")
    for side, color, size in (("top", NAVY, 8), ("bottom", NAVY, 8), ("insideH", "E3E8ED", 4)):
        edge = OxmlElement(f"w:{side}")
        for key, value in (("val", "single"), ("sz", str(size)), ("space", "0"), ("color", color)):
            edge.set(qn(f"w:{key}"), value)
        borders.append(edge)
    properties.append(borders)
    margins = OxmlElement("w:tblCellMar")
    for side, width in (("top", 50), ("left", 90), ("bottom", 50), ("right", 90)):
        edge = OxmlElement(f"w:{side}")
        edge.set(qn("w:w"), str(width))
        edge.set(qn("w:type"), "dxa")
        margins.append(edge)
    properties.append(margins)
    for index, row in enumerate([header, *rows]):
        cells = table.rows[0].cells if index == 0 else table.add_row().cells
        for column, text in enumerate(row):
            cell = cells[column]
            cell.width = Cm(widths[column])
            if index == 0:
                _shade(cell._tc.get_or_add_tcPr(), NAVY)
            elif index % 2 == 0:
                _shade(cell._tc.get_or_add_tcPr(), ZEBRA)
            paragraph = cell.paragraphs[0]
            paragraph.paragraph_format.space_after = Pt(0)
            paragraph.paragraph_format.line_spacing = 1.05
            if column in right:
                paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
            _font(paragraph.add_run(text), 9, "FFFFFF" if index == 0 else TEXT, index == 0)
    for column, width in enumerate(widths):  # the grid too: Word and LibreOffice read the column widths from it
        table.columns[column].width = Cm(width)
    row_properties = table.rows[0]._tr.get_or_add_trPr()
    repeat = OxmlElement("w:tblHeader")
    row_properties.append(repeat)
    document.add_paragraph().paragraph_format.space_after = Pt(2)


def _company(document: Any, run: ReportRun, data: dict[str, Any]) -> None:
    t = texts(run.language)
    company = data["company"]
    title = (
        f"{company['name'] or t['no_company']} (IČO {company['ico'] or '?'}), "
        f"{t['period_ending']} {period_date(run.language, company['period_end'])}"
    )
    heading = document.add_heading(title, level=1)
    _border(heading, "bottom", NAVY, 12, 4)
    if run.report is None:
        _paragraph(document, f"{t['no_report']} {run.error}")
        return
    report = run.report
    color, background = TONES[report.rating.value]
    rating = _paragraph(
        document,
        t["rating_line"].format(rules=", ".join(run.rating.rules), rating=t["rating"][report.rating.value]),
        11.5,
        color,
        True,
    )
    _shade(rating._p.get_or_add_pPr(), background)
    _border(rating, "left", color, 24, 8)
    rating.paragraph_format.left_indent = Cm(0.2)
    rating.paragraph_format.space_after = Pt(6)
    quality = data["data_quality"]["overall"]
    _paragraph(document, f"{t['data_quality_input']} {t['quality'].get(quality, quality)}", 10, GRAY)

    document.add_heading(t["summary"], level=2)
    _paragraph(document, report.summary)
    for key, items in (("strengths", report.strengths), ("risks", report.risks), ("red_flags", report.red_flags)):
        document.add_heading(t[key], level=2)
        _bullets(document, items or [t["none"]])
    document.add_heading(t["rating_reasoning"], level=2)
    _paragraph(document, report.rating.reasoning)
    if report.recommendation:
        document.add_heading(t["recommendation"], level=2)
        _paragraph(document, report.recommendation.label, 13, color, True)
        _paragraph(document, t["recommendation_note"], 9, GRAY, italic=True)
        _paragraph(document, t["criteria"])
        _bullets(document, report.recommendation.criteria)
    document.add_heading(t["data_quality_note"], level=2)
    _paragraph(document, report.data_quality_note)

    document.add_heading(t["figures"], level=2)
    _paragraph(document, t["figures_intro"])
    tables, checks = figure_tables(data, run.language)
    for index, (name, header, rows) in enumerate(tables):
        document.add_heading(name, level=3)
        _table(document, header, rows, model_table=index == 2)
    document.add_heading(t["checks"], level=3)
    _paragraph(document, checks)
    from src.report.recommendation import rule_rows

    rules_title, rules_intro, rules_header, rules_body = rule_rows(run.language, run.rating.partial)
    document.add_heading(rules_title, level=2)
    _paragraph(document, rules_intro)
    _table(document, rules_header, rules_body)
    footer = t["footer"].format(
        model=f"{run.provider}/{run.model}", prompt=run.prompt_version, rules=run.rating.rules_version
    )
    _paragraph(document, footer, 8.5, GRAY, italic=True)


def build_docx(items: list[tuple[ReportRun, dict[str, Any]]], path: Path) -> Path:
    """One Word file with the report of each company (a page break between companies)."""
    if not items:
        raise ValueError("no report to write")
    document = Document()
    _styles(document)
    _page(document, items[0][0].language)
    for index, (run, data) in enumerate(items):
        if index:
            document.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
        _company(document, run, data)
    path.parent.mkdir(parents=True, exist_ok=True)
    document.save(path)
    return path
