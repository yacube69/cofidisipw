"""Synthetic Czech statements for tests: a digital PDF and a 'scan' of it (image only, shading, skew, noise)."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pymupdf

ASSET_ROWS = [
    # (označení, label, row, gross, adjustment, net, prior, shaded)
    ("", "AKTIVA CELKEM", "001", "1 250 000", "-250 000", "1 000 000", "900 000", True),
    ("B.", "Stálá aktiva", "003", "850 000", "-250 000", "600 000", "550 000", True),
    ("B. II.", "Dlouhodobý hmotný majetek", "014", "850 000", "-250 000", "600 000", "550 000", False),
    ("C.", "Oběžná aktiva", "037", "390 000", "0", "390 000", "340 000", True),
    ("C. I.", "Zásoby", "038", "120 000", "0", "120 000", "100 000", False),
    ("C. II.", "Pohledávky", "046", "200 000", "0", "200 000", "190 000", False),
    ("C. IV.", "Peněžní prostředky", "075", "70 000", "0", "70 000", "50 000", False),
    ("D.", "Časové rozlišení aktiv", "078", "10 000", "0", "10 000", "10 000", False),
]

LIABILITY_ROWS = [
    ("", "PASIVA CELKEM", "082", "1 000 000", "900 000", True),
    ("A.", "Vlastní kapitál", "083", "400 000", "350 000", True),
    ("A. I.", "Základní kapitál", "084", "200 000", "200 000", False),
    ("A. V.", "Výsledek hospodaření běžného účetního období (+/-)", "102", "50 000", "-20 000", False),
    ("B. + C.", "Cizí zdroje", "104", "600 000", "550 000", True),
    ("C.", "Závazky", "110", "600 000", "550 000", False),
    ("C. II.", "Krátkodobé závazky", "126", "300 000", "250 000", False),
]

_STYLE = """
<style>
body { font-family: sans-serif; font-size: 9px; }
h1 { text-align: center; font-size: 14px; margin: 0 0 2px 0; }
p.sub { text-align: center; margin: 0 0 8px 0; }
table { border-collapse: collapse; width: 100%; }
td, th { border: 0.6px solid #000; padding: 2px 4px; }
td.n { text-align: right; }
tr.s td { background: #e89a9a; color: #7a1f1f; font-weight: bold; }
</style>
"""


def asset_page_html(title: str = "ROZVAHA") -> str:
    rows = "".join(
        f'<tr class="{"s" if s else ""}"><td>{c}</td><td>{label}</td><td>{r}</td>'
        f'<td class="n">{g}</td><td class="n">{a}</td><td class="n">{n}</td><td class="n">{p}</td></tr>'
        for c, label, r, g, a, n, p, s in ASSET_ROWS
    )
    return (
        f"{_STYLE}<p>Obchodní firma: Testovací firma s.r.o.<br>Identifikační číslo: 12345678<br>"
        f"Rozvahový den: 31. prosince 2025</p><h1>{title}</h1>"
        '<p class="sub">ve zkráceném rozsahu (v celých tisících Kč)</p>'
        "<table><tr><th>označ.</th><th>AKTIVA</th><th>řád.</th><th>Brutto</th><th>Korekce</th>"
        "<th>Netto</th><th>Netto</th></tr>"
        '<tr><td></td><td></td><td></td><td colspan="3">31.12.2025</td><td>31.12.2024</td></tr>'
        f"{rows}</table>"
    )


def liability_page_html() -> str:
    rows = "".join(
        f'<tr class="{"s" if s else ""}"><td>{c}</td><td>{label}</td><td>{r}</td>'
        f'<td class="n">{cur}</td><td class="n">{p}</td></tr>'
        for c, label, r, cur, p, s in LIABILITY_ROWS
    )
    return (
        f"{_STYLE}<table><tr><th>označ.</th><th>PASIVA</th><th>řád.</th><th>31.12.2025</th>"
        f"<th>31.12.2024</th></tr>{rows}</table>"
    )


def make_digital_pdf(path: Path) -> Path:
    doc = pymupdf.open()
    for html in (asset_page_html(), liability_page_html()):
        page = doc.new_page(width=595, height=842)
        page.insert_htmlbox(pymupdf.Rect(40, 40, 555, 800), html)
    doc.save(path)
    return path


def make_scanned_pdf(digital: Path, path: Path, skew_deg: float = 1.2, dpi: int = 200, seed: int = 0) -> Path:
    """Rasterize each page, rotate slightly, add noise and JPEG artefacts, save as image-only PDF."""
    rng = np.random.default_rng(seed)
    src = pymupdf.open(digital)
    out = pymupdf.open()
    for page in src:
        pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csRGB, alpha=False)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3).copy()
        h, w = img.shape[:2]
        m = cv2.getRotationMatrix2D((w / 2, h / 2), skew_deg, 1.0)
        img = cv2.warpAffine(img, m, (w, h), borderValue=(255, 255, 255))
        img = np.clip(img.astype(np.int16) + rng.normal(0, 8, img.shape), 0, 255).astype(np.uint8)
        ok, jpg = cv2.imencode(".jpg", cv2.cvtColor(img, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 70])
        assert ok
        new = out.new_page(width=page.rect.width, height=page.rect.height)
        new.insert_image(new.rect, stream=jpg.tobytes())
    out.save(path)
    return path
