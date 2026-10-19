"""CIPW-7: batch run of the reading layer (scripts/run_reading.py)."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MICRO = ROOT / "data/raw/25080776_2025_statement.pdf"

spec = importlib.util.spec_from_file_location("run_reading", ROOT / "scripts/run_reading.py")
run_reading = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run_reading)


@pytest.mark.skipif(not MICRO.exists(), reason="sample statement not available")
def test_process_file_reads_located_pages_and_writes_debug_output(tmp_path, monkeypatch):
    monkeypatch.setattr(run_reading, "OUT", tmp_path)
    rows = run_reading.process_file(str(MICRO))
    assert [(r["page"], r["statement"], r["source"]) for r in rows] == [
        (1, "balance_sheet", "text_layer"),
        (2, "balance_sheet", "text_layer"),
    ]
    assert all(r["words"] > 10 and r["error"] == "" for r in rows)


def test_unreadable_file_is_reported_not_raised(tmp_path):
    bad = tmp_path / "broken.pdf"
    bad.write_bytes(b"not a pdf")
    rows = run_reading.process_file(str(bad))
    assert len(rows) == 1 and rows[0]["error"]


def test_summary_reports_confidence_and_time():
    rows = [
        {"file": "a.pdf", "page": 1, "source": "ocr", "mean_conf": 80.0, "seconds": 10.0, "fallback_to_ocr": False},
        {"file": "a.pdf", "page": 2, "source": "ocr", "mean_conf": 90.0, "seconds": 8.0, "fallback_to_ocr": True},
        {"file": "b.pdf", "page": 1, "source": "text_layer", "mean_conf": "", "seconds": 0.01,
         "fallback_to_ocr": False},
        {"file": "c.pdf", "error": "no statement pages found (3 pages)"},
    ]
    text = run_reading.summarize(rows)
    assert "mean confidence 85.0" in text and "time per page 9.0 s" in text
    assert "broken text layer: 1" in text and "c.pdf: no statement pages found" in text
