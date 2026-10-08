"""Tesseract OCR layer with OpenCV preprocessing (CIPW-7, tuned in CIPW-25).

Pipeline for one page: render → colour-shading removal → deskew → binarize → table-line removal → Tesseract.
Word boxes are mapped back to PDF points on the original (unrotated) page, so they line up with the
text layer of digital PDFs and can be drawn on the page in the app.
"""

from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass, field

import cv2
import numpy as np
import pymupdf
import pytesseract
from dotenv import load_dotenv

from src.extraction.types import PageWords, Word

load_dotenv()
if os.getenv("TESSERACT_CMD"):
    pytesseract.pytesseract.tesseract_cmd = os.environ["TESSERACT_CMD"]


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
    remove_lines: bool = True
    extra: tuple[str, ...] = field(default=("-c", "preserve_interword_spaces=1"))

    @property
    def tesseract_config(self) -> str:
        return " ".join((f"--psm {self.psm}", *self.extra))

    def describe(self) -> str:
        return (
            f"dpi={self.dpi} psm={self.psm} lang={self.lang} shading={'off' if self.remove_shading else 'kept'} "
            f"deskew={self.deskew} bin={self.binarize}{self.threshold if self.binarize == 'fixed' else ''} "
            f"lines={'removed' if self.remove_lines else 'kept'}"
        )


DEFAULT = OcrConfig()
QUICK = OcrConfig(dpi=150, psm=3, deskew=False, binarize="none", remove_lines=False)  # page locator (CIPW-6)


def render(page: pymupdf.Page, dpi: int) -> np.ndarray:
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csRGB, alpha=False)
    return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3).copy()


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


def binarize(gray: np.ndarray, method: str, threshold: int = 150) -> np.ndarray:
    if method == "none":
        return gray
    if method == "otsu":
        _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        return bw
    if method == "fixed":
        _, bw = cv2.threshold(gray, threshold, 255, cv2.THRESH_BINARY)
        return bw
    if method == "adaptive":
        return cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 15)
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
    img = binarize(gray, config.binarize, config.threshold)
    if config.remove_lines and config.binarize != "none":
        img = remove_table_lines(img)
    return img, matrix, angle


def ocr_page(page: pymupdf.Page, file: str = "", config: OcrConfig = DEFAULT) -> PageWords:
    start = time.perf_counter()
    rgb = render(page, config.dpi)
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

    return PageWords(
        file=file,
        page=page.number + 1,
        source="ocr",
        width=page.rect.width,
        height=page.rect.height,
        dpi=config.dpi,
        ocr_config=config.describe(),
        skew=round(angle, 3),
        seconds=round(time.perf_counter() - start, 2),
        words=words,
    )
