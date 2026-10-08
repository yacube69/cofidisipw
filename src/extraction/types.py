"""Shared output format of the reading layer (CIPW-7): words with positions, same for text layer and OCR."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from src.schema import BBox


class Word(BaseModel):
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    conf: float | None = Field(None, description="Tesseract confidence 0–100; None for the PDF text layer.")

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def bbox(self) -> BBox:
        return BBox(x0=self.x0, y0=self.y0, x1=self.x1, y1=self.y1)


class PageWords(BaseModel):
    """All words of one page. Coordinates in PDF points (1/72 inch), origin top-left, for both sources."""

    file: str
    page: int = Field(description="1-based page number.")
    source: Literal["text_layer", "ocr"]
    width: float
    height: float
    dpi: int | None = Field(None, description="Render resolution for OCR.")
    ocr_config: str | None = None
    seconds: float | None = None
    words: list[Word] = Field(default_factory=list)

    @property
    def mean_conf(self) -> float | None:
        confs = [w.conf for w in self.words if w.conf is not None]
        return sum(confs) / len(confs) if confs else None

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)
