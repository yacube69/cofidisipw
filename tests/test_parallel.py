"""CIPW-50: pages read side by side give the same result, in the same order, as one by one."""

import threading

import pymupdf

from src.extraction.parallel import MUPDF, pixels, pmap
from src.settings import get_settings


def test_results_keep_the_order_of_the_input():
    perf = get_settings().performance
    seen_threads: set[str] = set()

    def slow_square(x: int) -> int:
        seen_threads.add(threading.current_thread().name)
        threading.Event().wait(0.01 * (5 - x % 5))  # later items finish first
        return x * x

    try:
        perf.ocr_workers = 4
        assert pmap(slow_square, range(10)) == [x * x for x in range(10)]
        assert len(seen_threads) > 1
        perf.ocr_workers = 1
        assert pmap(slow_square, range(3)) == [0, 1, 4]
    finally:
        perf.ocr_workers = 0


def test_pages_are_rendered_under_the_lock():
    doc = pymupdf.open()
    doc.new_page(width=200, height=100)
    with MUPDF:  # the lock is re-entrant: a caller that holds it may render
        img = pixels(doc[0], 72)
    assert img.shape == (100, 200, 3)
