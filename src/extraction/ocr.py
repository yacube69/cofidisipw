"""Tesseract OCR layer with OpenCV preprocessing (CIPW-7, tuned in CIPW-25).

Pipeline for one page: render → colour-shading removal → deskew → binarize → table-line removal → Tesseract.
Word boxes are mapped back to PDF points on the original (unrotated) page, so they line up with the
text layer of digital PDFs and can be drawn on the page in the app.
"""

from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

import cv2
import numpy as np
import pymupdf
import pytesseract

from src.extraction.parallel import MUPDF, pixels
from src.extraction.types import PageWords, Word
from src.settings import get_settings

get_settings().apply_tesseract()  # TESSERACT_CMD / TESSDATA_PREFIX from .env or the environment (src/settings.py)


@dataclass(frozen=True)
class OcrConfig:
    """One reproducible OCR setting. CIPW-25 compares grids of these."""

    dpi: int = 300
    lang: str = "ces"
    psm: int = 6
    remove_shading: bool = True  # read the channel where coloured row shading is lightest (see shading_channel)
    deskew: bool = True
    binarize: str = "adaptive"  # "none" | "otsu" | "adaptive" | "fixed"
    threshold: int = 150  # for binarize="fixed"
    block: int = 31  # adaptive threshold neighbourhood (pixels at `dpi`)
    offset: int = 15  # adaptive threshold constant C
    median: int = 0  # median blur kernel before binarizing (0 = off); removes halftone dots of grey shading
    grey_profile: bool = True  # weak pages without coloured shading get a second pass with GREY (see ocr_page)
    remove_lines: bool = True
    tessdata: str = ""  # folder with the language model (tessdata_fast / tessdata_best); "" = the installed one
    extra: tuple[str, ...] = field(default=("-c", "preserve_interword_spaces=1"))

    @property
    def model_args(self) -> str:
        """`--tessdata-dir …` for a model folder other than the configured default (the default is set once through
        TESSDATA_PREFIX by `Settings.apply_tesseract`, which also takes paths with spaces); else ''."""
        if not self.tessdata:
            return ""
        folder = Path(self.tessdata).resolve()
        if os.environ.get("TESSDATA_PREFIX") and Path(os.environ["TESSDATA_PREFIX"]).resolve() == folder:
            return ""
        # no quotes: pytesseract splits the config on Windows without removing them (the path must have no spaces)
        return f"--tessdata-dir {folder.as_posix()}"

    @property
    def tesseract_config(self) -> str:
        model = (self.model_args,) if self.model_args else ()
        return " ".join((f"--psm {self.psm}", *model, *self.extra))

    def describe(self) -> str:
        return (
            f"dpi={self.dpi} psm={self.psm} lang={self.lang} shading={'off' if self.remove_shading else 'kept'} "
            f"deskew={self.deskew} median={self.median} bin={self.binarize}"
            f"{self.threshold if self.binarize == 'fixed' else ''}"
            f"{f'{self.block}/{self.offset}' if self.binarize == 'adaptive' else ''} "
            f"lines={'removed' if self.remove_lines else 'kept'}"
            f"{f' model={Path(self.tessdata).name}' if self.tessdata else ''}"
        )


DEFAULT = OcrConfig()  # the code default; the pipeline reads `settings.ocr` (config/fides.yaml) instead
QUICK = OcrConfig(dpi=150, psm=3, deskew=False, binarize="none", remove_lines=False)  # page locator (CIPW-6)


def render(page: pymupdf.Page, dpi: int) -> np.ndarray:
    return pixels(page, dpi)


def shading_channel(rgb: np.ndarray) -> int | None:
    """Colour channel in which the coloured row shading is lightest, or None for a page without shading.

    Statements often shade total rows (e.g. pink: R≈230, G≈140). In the red channel that shading is almost
    white while black and dark-red text stays dark, so OCR sees clean text instead of dark blobs.
    """
    sample = rgb[::4, ::4].reshape(-1, 3).astype(np.int16)
    saturation = sample.max(axis=1) - sample.min(axis=1)
    colored = sample[(saturation > 50) & (sample.max(axis=1) > 150)]
    if len(colored) < 0.005 * len(sample):
        return None
    return int(np.argmax(colored.mean(axis=0)))


def grey_shading_share(gray: np.ndarray) -> float:
    """Share of mid-grey pixels: high on scanned forms whose total rows are shaded with grey halftone dots."""
    sample = gray[::4, ::4]
    return float(((sample > 100) & (sample < 215)).mean())


GREY_SHADING = 0.15  # share of mid-grey pixels above which a page is retried with the grey profile
WEAK_CONF = 75.0  # mean word confidence below which a page is retried with the grey profile


def to_gray(rgb: np.ndarray, remove_shading: bool) -> np.ndarray:
    if remove_shading:
        channel = shading_channel(rgb)
        if channel is not None:
            return np.ascontiguousarray(rgb[:, :, channel])
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)


def estimate_skew(gray: np.ndarray) -> float:
    """Skew angle in degrees from long, nearly horizontal lines (table rules, text baselines)."""
    edges = cv2.Canny(gray, 50, 150, apertureSize=3)
    min_len = gray.shape[1] // 4
    lines = cv2.HoughLinesP(edges, 1, np.pi / 1800, threshold=200, minLineLength=min_len, maxLineGap=20)
    if lines is None:
        return 0.0
    angles = []
    for x1, y1, x2, y2 in np.asarray(lines).reshape(-1, 4):
        angle = math.degrees(math.atan2(y2 - y1, x2 - x1))
        if abs(angle) < 5:
            angles.append(angle)
    return float(np.median(angles)) if angles else 0.0


def rotate(img: np.ndarray, angle: float) -> tuple[np.ndarray, np.ndarray]:
    h, w = img.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    out = cv2.warpAffine(img, m, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_CONSTANT, borderValue=255)
    return out, m


def binarize(gray: np.ndarray, method: str, threshold: int = 150, block: int = 31, offset: int = 15) -> np.ndarray:
    if method == "none":
        return gray
    if method == "otsu":
        _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        return bw
    if method == "fixed":
        _, bw = cv2.threshold(gray, threshold, 255, cv2.THRESH_BINARY)
        return bw
    if method == "adaptive":
        return cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, block, offset)
    raise ValueError(f"Unknown binarization: {method}")


def remove_table_lines(bw: np.ndarray) -> np.ndarray:
    """Erase long horizontal and vertical rules; they confuse Tesseract's layout analysis."""
    inv = 255 - bw
    h, w = bw.shape
    horizontal = cv2.morphologyEx(inv, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (max(w // 30, 20), 1)))
    vertical = cv2.morphologyEx(inv, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(h // 40, 20))))
    lines = cv2.dilate(cv2.bitwise_or(horizontal, vertical), np.ones((3, 3), np.uint8))
    cleaned = bw.copy()
    cleaned[lines > 0] = 255
    return cleaned


def preprocess(rgb: np.ndarray, config: OcrConfig) -> tuple[np.ndarray, np.ndarray | None, float]:
    """Return the image for Tesseract, the rotation matrix applied (None if not rotated) and the angle."""
    gray = to_gray(rgb, config.remove_shading)
    matrix, angle = None, 0.0
    if config.deskew:
        angle = estimate_skew(gray)
        if abs(angle) > 0.05:
            gray, matrix = rotate(gray, angle)
        else:
            angle = 0.0
    if config.median:
        gray = cv2.medianBlur(gray, config.median)
    img = binarize(gray, config.binarize, config.threshold, config.block, config.offset)
    if config.remove_lines and config.binarize != "none":
        img = remove_table_lines(img)
    return img, matrix, angle


def _ocr_pass(page: pymupdf.Page, rgb: np.ndarray, file: str, config: OcrConfig, start: float) -> PageWords:
    img, matrix, angle = preprocess(rgb, config)
    data = pytesseract.image_to_data(img, lang=config.lang, config=config.tesseract_config, output_type="dict")

    inverse = cv2.invertAffineTransform(matrix) if matrix is not None else None
    scale = 72.0 / config.dpi
    words: list[Word] = []
    for i, text in enumerate(data["text"]):
        text = text.strip()
        conf = float(data["conf"][i])
        if not text or conf < 0:
            continue
        x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
        corners = np.array([[x, y], [x + w, y], [x, y + h], [x + w, y + h]], dtype=np.float64)
        if inverse is not None:
            corners = corners @ inverse[:, :2].T + inverse[:, 2]
        x0, y0 = corners.min(axis=0) * scale
        x1, y1 = corners.max(axis=0) * scale
        words.append(Word(text=text, x0=x0, y0=y0, x1=x1, y1=y1, conf=conf))

    with MUPDF:
        number, width, height = page.number + 1, page.rect.width, page.rect.height
    return PageWords(
        file=file,
        page=number,
        source="ocr",
        width=width,
        height=height,
        dpi=config.dpi,
        ocr_config=config.describe(),
        skew=round(angle, 3),
        seconds=round(time.perf_counter() - start, 2),
        words=words,
    )


def _quality(pw: PageWords) -> tuple[int, float]:
    """(numbers read with confidence >= 80, mean confidence): higher is better."""
    numbers = sum(1 for w in pw.words if w.conf is not None and w.conf >= 80 and any(c.isdigit() for c in w.text))
    return numbers, pw.mean_conf or 0.0


def ocr_page(page: pymupdf.Page, file: str = "", config: OcrConfig = DEFAULT) -> PageWords:
    """OCR one page. Weak pages without coloured shading are read a second time with the GREY profile
    (median blur + wider adaptive window, for grey halftone shading on scanned forms) and the better reading
    is kept, judged by how many numbers were read confidently. Deterministic: same page, same result."""
    start = time.perf_counter()
    rgb = render(page, config.dpi)
    first = _ocr_pass(page, rgb, file, config, start)
    if not (config.grey_profile and config.binarize == "adaptive" and config.median == 0):
        return first
    if shading_channel(rgb) is not None:
        return first
    weak = (first.mean_conf or 0.0) < WEAK_CONF
    if not weak and grey_shading_share(to_gray(rgb, False)) <= GREY_SHADING:
        return first
    second = _ocr_pass(page, rgb, file, replace(config, median=5, block=51, offset=25), start)
    best = second if _quality(second) > _quality(first) else first
    best.seconds = round(time.perf_counter() - start, 2)
    return best
