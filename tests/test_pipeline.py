"""CIPW-40 / CIPW-24: the pipeline runs end to end and produces a schema-valid Result."""

from pathlib import Path

import pymupdf
import pytest

from src.pipeline import REQUIRES, STAGES, main, run_pipeline, write_result
from src.result import PipelineOptions, Result
from tests.synthetic import make_digital_pdf, make_scanned_pdf

ROOT = Path(__file__).resolve().parents[1]
MICRO = ROOT / "data/raw/25080776_2025_statement.pdf"


@pytest.fixture
def tiny_pdf(tmp_path: Path) -> Path:
    return make_digital_pdf(tmp_path / "12345678_2025_statement.pdf")


def test_missing_statements_are_reported(tmp_path):
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "Výroční zpráva")
    doc.save(tmp_path / "empty.pdf")
    result = run_pipeline(tmp_path / "empty.pdf", PipelineOptions(skip_report=True))
    assert any("no balance sheet" in e for e in result.errors)
    status = {s.stage: s.status for s in result.stages}
    assert status["locate"] == "error"
    # nothing downstream runs on empty data; report/check_numbers are skipped either way
    assert all(status[name] == "skipped" for name, _ in STAGES[1:])
    assert {s.stage: s.note for s in result.stages}["read"] == "needs locate"


def test_cli_fails_when_a_stage_fails(tmp_path):
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "Výroční zpráva")
    doc.save(tmp_path / "empty.pdf")
    assert main([str(tmp_path / "empty.pdf"), "--out", str(tmp_path), "--skip-report"]) == 1


def test_stubs_return_mock_data(tiny_pdf):
    result = run_pipeline(tiny_pdf)
    assert set(result.stubbed_stages) == {"benchmarks", "report", "check_numbers"}
    assert {s.stage: s.status for s in result.stages}["reread"] == "skipped"  # a text layer is read exactly
    assert result.benchmarks["ratios"]["current_ratio"]["light"] == "red"
    assert result.report["summary"] and result.report["rating"]["value"] in ("low", "medium", "high")
    assert result.number_check["passed"] is True
    assert all(d["mock"] for d in (result.benchmarks, result.report, result.number_check))


def test_mock_data_is_a_fresh_copy(tiny_pdf):
    first = run_pipeline(tiny_pdf, PipelineOptions(skip_report=True))
    first.benchmarks["ratios"]["current_ratio"]["light"] = "green"
    again = run_pipeline(tiny_pdf, PipelineOptions(skip_report=True))
    assert again.benchmarks["ratios"]["current_ratio"]["light"] == "red"


def test_requires_names_real_stages():
    names = [name for name, _ in STAGES]
    for stage, deps in REQUIRES.items():
        assert stage in names and all(names.index(d) < names.index(stage) for d in deps), stage


def test_page_override_out_of_range(tiny_pdf):
    result = run_pipeline(tiny_pdf, PipelineOptions(skip_report=True, pages={"balance_sheet": [1, 7]}))
    assert result.stages[0].status == "error"
    assert "pages [7] out of range (the PDF has 2 pages)" in result.errors[0]


def test_text_mode_on_scan_fails_read(tiny_pdf, tmp_path):
    scan = make_scanned_pdf(tiny_pdf, tmp_path / "scan.pdf")
    result = run_pipeline(scan, PipelineOptions(skip_report=True, read_mode="text"))
    status = {s.stage: s.status for s in result.stages}
    assert status["read"] == "error" and status["parse"] == "skipped"
    assert any("no text on pages [1, 2]" in e and "--mode auto" in e for e in result.errors)


def test_page_without_text_is_a_warning(tiny_pdf, tmp_path):
    doc = pymupdf.open(tiny_pdf)
    doc.new_page()  # blank page 3
    doc.save(tmp_path / "with_blank.pdf")
    pages = {"balance_sheet": [1, 2, 3], "income_statement": []}
    result = run_pipeline(tmp_path / "with_blank.pdf", PipelineOptions(skip_report=True, read_mode="text", pages=pages))
    assert not result.failed
    assert result.warnings == ["read: no text on pages [3] (read mode 'text' on a scan? use --mode auto or ocr)"]


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


def test_group_report_reads_the_individual_statements(tmp_path):
    from tests.synthetic import asset_page_html, liability_page_html
    from tests.test_locate import _pdf

    pdf = _pdf(
        tmp_path / "12345678_2025_report.pdf",
        ["<p>Konsolidovaná účetní závěrka</p>" + asset_page_html(), liability_page_html(),
         "<p>Individuální účetní závěrka</p>" + asset_page_html(), liability_page_html()],
    )  # fmt: skip
    result = run_pipeline(pdf, PipelineOptions(skip_report=True))
    assert result.pages["balance_sheet"] == [3, 4] and result.consolidation == "individual"
    assert "locate: the PDF also has consolidated statements (pages [1, 2]), not read" in result.warnings
    result = run_pipeline(pdf, PipelineOptions(skip_report=True, scope="consolidated"))
    assert result.pages["balance_sheet"] == [1, 2] and result.consolidation == "consolidated"


def test_ratios_are_computed_from_the_statements(tiny_pdf):
    result = run_pipeline(tiny_pdf, PipelineOptions(skip_report=True))
    assert {s.stage: s.status for s in result.stages}["ratios"] == "ok"
    fs = result.statements
    expected = fs.get("current_assets") / fs.get("liabilities_short_term")
    assert result.ratios.get("current_ratio").current.value == pytest.approx(expected, rel=1e-3)
