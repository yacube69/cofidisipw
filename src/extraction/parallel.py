"""Pages read side by side (CIPW-50).

Almost all the time of a scan goes to Tesseract, which runs as its own process for every page or cell, so plain
threads read several pages at once: while one thread waits for Tesseract, the others render and preprocess. MuPDF
(PyMuPDF) must not be used from two threads at the same time, so every call into a document holds `MUPDF`; the
OCR itself runs outside the lock. Results come back in the order of the input, so a run gives the same output
with any number of workers.

Settings (`config/fides.yaml`, `performance`): `ocr_workers` (0 = one per CPU core) and `tesseract_threads`
(threads each Tesseract may use; 1 when pages run side by side, so the workers do not compete for the cores).
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from typing import TypeVar

import numpy as np
import pymupdf

from src.settings import get_settings

T = TypeVar("T")
R = TypeVar("R")

MUPDF = threading.RLock()  # PyMuPDF is not thread-safe: hold this for every call into a document or page


def pixels(page: pymupdf.Page, dpi: int, clip: pymupdf.Rect | None = None) -> np.ndarray:
    """The page (or the `clip` of it) as an RGB array at `dpi`, rendered under the MuPDF lock."""
    with MUPDF:
        pix = page.get_pixmap(dpi=dpi, clip=clip, colorspace=pymupdf.csRGB, alpha=False)
        return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3).copy()


def workers() -> int:
    configured = get_settings().performance.ocr_workers
    if configured > 0:
        return configured
    # the cores this process may use (a Docker container may get fewer than the host has), else all of them
    return len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else (os.cpu_count() or 1)


def ocr_pool(size: int | None = None) -> ThreadPoolExecutor:
    """A pool of `size` (default: the configured) OCR workers; Tesseract limited to its configured threads."""
    threads = get_settings().performance.tesseract_threads
    if threads > 0:
        os.environ["OMP_THREAD_LIMIT"] = str(threads)  # read by every Tesseract process started from now on
    return ThreadPoolExecutor(max_workers=size or workers(), thread_name_prefix="ocr")


def pmap(fn: Callable[[T], R], items: Iterable[T]) -> list[R]:
    """`[fn(x) for x in items]`, run side by side when there is more than one item and more than one worker."""
    items = list(items)
    n = min(workers(), len(items))
    if n <= 1:
        return [fn(x) for x in items]
    with ocr_pool(n) as pool:
        return list(pool.map(fn, items))
