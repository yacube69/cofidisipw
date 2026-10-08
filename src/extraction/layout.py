"""Geometry helpers shared by the parser (CIPW-32) and the number helpers (CIPW-9).

- `group_lines`: words → text lines by vertical overlap (robust to small skew and mixed font sizes).
- `merge_number_tokens`: Czech numbers are printed with spaces as thousands separators, so "1 234 567"
  arrives as three words. Tokens are joined only when the horizontal gap is small compared with the text
  height; the wider gap between two table columns keeps them apart.
"""

from __future__ import annotations

import math
import re
import statistics
from dataclasses import dataclass, field

from src.extraction.types import PageWords, Word
from src.schema import BBox

NUMERIC = re.compile(r"^[-−–(]?[\d.,]+\)?$")
GROUP = re.compile(r"^\d{3}\)?$")


def _rotation(skew: float, width: float, height: float) -> tuple[float, float, float, float, float, float]:
    """Same affine map as cv2.getRotationMatrix2D((w/2, h/2), skew, 1): original page → straightened page."""
    a = math.radians(skew)
    c, s = math.cos(a), math.sin(a)
    cx, cy = width / 2, height / 2
    return c, s, (1 - c) * cx - s * cy, -s, c, s * cx + (1 - c) * cy


def straighten(pw: PageWords) -> list[Word]:
    """Words moved into the straightened (deskewed) frame, same order. Text-layer pages are returned as is.

    OCR boxes are stored on the original page so they can be drawn on it; for grouping rows and columns we
    need the straight frame, otherwise a 1° skew moves a row by ~9 pt across an A4 page.
    """
    if not pw.skew:
        return list(pw.words)
    a, b, c, d, e, f = _rotation(pw.skew, pw.width, pw.height)
    out = []
    for w in pw.words:
        nx, ny = a * w.cx + b * w.cy + c, d * w.cx + e * w.cy + f
        hw, hh = (w.x1 - w.x0) / 2, (w.y1 - w.y0) / 2
        out.append(w.model_copy(update={"x0": nx - hw, "x1": nx + hw, "y0": ny - hh, "y1": ny + hh}))
    return out


def unstraighten(bbox: BBox, pw: PageWords) -> BBox:
    """Map a box from the straightened frame back to the original page (inverse of `straighten`)."""
    if not pw.skew:
        return bbox
    a, b, c, d, e, f = _rotation(-pw.skew, pw.width, pw.height)
    xs, ys = [], []
    for x, y in ((bbox.x0, bbox.y0), (bbox.x1, bbox.y0), (bbox.x0, bbox.y1), (bbox.x1, bbox.y1)):
        xs.append(a * x + b * y + c)
        ys.append(d * x + e * y + f)
    return BBox(x0=min(xs), y0=min(ys), x1=max(xs), y1=max(ys))


@dataclass
class Line:
    words: list[Word] = field(default_factory=list)

    @property
    def y0(self) -> float:
        return min(w.y0 for w in self.words)

    @property
    def y1(self) -> float:
        return max(w.y1 for w in self.words)

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def height(self) -> float:
        return statistics.median(w.height for w in self.words)

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)


def group_lines(words: list[Word], overlap: float = 0.5) -> list[Line]:
    """Assign each word to the line whose vertical span it overlaps by at least `overlap` of its height."""
    lines: list[Line] = []
    for w in sorted(words, key=lambda w: (w.cy, w.x0)):
        best, best_overlap = None, 0.0
        for line in lines[-6:]:  # only recent lines can overlap, words are sorted by y
            top, bottom = max(line.y0, w.y0), min(line.y1, w.y1)
            ov = (bottom - top) / max(min(w.height, line.height), 1e-6)
            if ov > best_overlap:
                best, best_overlap = line, ov
        if best is not None and best_overlap >= overlap:
            best.words.append(w)
        else:
            lines.append(Line(words=[w]))
    for line in lines:
        line.words.sort(key=lambda w: w.x0)
    lines.sort(key=lambda line: line.cy)
    return lines


def is_numeric(text: str) -> bool:
    return bool(NUMERIC.match(text)) and any(c.isdigit() for c in text)


@dataclass
class NumberToken:
    text: str
    words: list[Word]

    @property
    def x0(self) -> float:
        return self.words[0].x0

    @property
    def x1(self) -> float:
        return self.words[-1].x1

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2


def merge_number_tokens(words: list[Word], max_gap_ratio: float = 0.75) -> list[NumberToken | Word]:
    """Join thousands groups ("1", "234", "567") into one token; other words pass through unchanged.

    A following token is joined only when it is exactly three digits (a thousands group) and the gap to the
    previous token is below `max_gap_ratio` × text height.
    """
    out: list[NumberToken | Word] = []
    for w in sorted(words, key=lambda w: w.x0):
        prev = out[-1] if out else None
        if (
            isinstance(prev, NumberToken)
            and GROUP.match(w.text)
            and not prev.text.endswith(")")
            and w.x0 - prev.x1 <= max_gap_ratio * max(w.height, prev.words[-1].height)
        ):
            prev.text += " " + w.text
            prev.words.append(w)
        elif is_numeric(w.text):
            out.append(NumberToken(text=w.text, words=[w]))
        else:
            out.append(w)
    return out
