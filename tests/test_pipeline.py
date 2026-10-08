"""CIPW-40 / CIPW-24: the pipeline runs end to end and produces a schema-valid Result."""

from pathlib import Path

import pymupdf
import pytest

from src.pipeline import STAGES, main, run_pipeline, write_result
from src.result import PipelineOptions, Result

ROOT = Path(__file__).resolve().parents[1]
MICRO = ROOT / "data/raw/25080776_2025_statement.pdf"


@pytest.fixture
def tiny_pdf(tmp_path: Path) -> Path:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "ROZVAHA")
    path = tmp_path / "12345678_2025_statement.pdf"
    doc.save(path)
    return path


def test_every_stage_runs_and_is_recorded(tiny_pdf):
    result = run_pipeline(tiny_pdf, PipelineOptions(skip_report=True))
    assert [s.stage for s in result.stages] == [name for name, _ in STAGES]
    assert all(s.status in ("ok", "stub", "skipped") for s in result.stages), result.errors
    assert result.errors == []
    assert {s.stage: s.status for s in result.stages}["report"] == "skipped"


def test_result_roundtrips_through_json(tiny_pdf, tmp_path):
    result = run_pipeline(tiny_pdf)
    path = write_result(result, tmp_path / "out")
    again = Result.model_validate_json(path.read_text(encoding="utf-8"))
    assert again.file == tiny_pdf.name


def test_failing_stage_is_reported_not_raised(tiny_pdf, monkeypatch):
    def boom(result, ctx):
        raise RuntimeError("broken module")

    import src.pipeline as pipeline

    monkeypatch.setattr(pipeline, "STAGES", [("parse", boom)] + pipeline.STAGES[3:])
    result = run_pipeline(tiny_pdf)
    assert result.stages[0].status == "error"
    assert any("broken module" in e for e in result.errors)
    assert len(result.stages) == len(pipeline.STAGES)


def test_cli_writes_result_json(tiny_pdf, tmp_path, capsys):
    assert main([str(tiny_pdf), "--out", str(tmp_path), "--skip-report"]) == 0
    assert (tmp_path / f"{tiny_pdf.stem}.result.json").exists()
    assert tiny_pdf.name in capsys.readouterr().out


@pytest.mark.skipif(not MICRO.exists(), reason="sample statement not available")
def test_real_statement_end_to_end(tmp_path):
    result = run_pipeline(MICRO, PipelineOptions(skip_report=True))
    assert result.errors == []
    assert result.statements.metadata.source_file == MICRO.name
    assert result.statements.get("total_assets") == 10_000_000
