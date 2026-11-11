"""CIPW-42: values confirmed or corrected by hand, and everything after the reading computed again."""

from pathlib import Path

import pymupdf
import pytest
from fastapi.testclient import TestClient

from src.api import app
from src.corrections import NoSuchValue, correct
from src.report.pdf import render_report
from src.result import Result
from src.settings import get_settings
from tests.synthetic import make_digital_pdf

ROOT = Path(__file__).resolve().parents[1]
MONDI = ROOT / "data/fixtures/result_26161516_2025.json"  # current assets: gross and adjustment misread by OCR
NET_CHECK = "Net = gross − adjustment (current_assets)"


@pytest.fixture
def mondi() -> Result:
    return Result.model_validate_json(MONDI.read_text(encoding="utf-8"))


def _ratio(result: Result, key: str):
    return next(r for r in result.ratios.ratios if r.key == key)


def _failed(result: Result) -> set[str]:
    return {c.name for c in result.validation.checks if c.severity == "error" and not c.passed}


def test_confirming_a_value_clears_its_doubt_and_keeps_the_original(mondi):
    read = mondi.statements.value("current_assets", "current")
    assert read.needs_review and _ratio(mondi, "current_ratio").current.flag == "unreliable"

    fixed = correct(mondi, "current_assets", "current", read.value)
    value = fixed.statements.value("current_assets", "current")
    assert (value.value, value.source, value.needs_review) == (read.value, "manual", False)
    assert value.note == "confirmed by hand"
    assert [(c.key, c.period, c.old, c.new, c.reason) for c in fixed.audit.corrections] == [
        ("current_assets", "current", read.value, read.value, "confirmed")
    ]
    # the failed check now points at the other amounts of the line; the ratio on the net value is reliable
    assert NET_CHECK in _failed(fixed) and fixed.statements.value("current_assets", "gross").needs_review
    assert _ratio(fixed, "current_ratio").current.flag == "ok"
    assert fixed.benchmarks["ratios"]["current_ratio"]["light"] is not None
    assert mondi.statements.value("current_assets", "current").source == "ocr"  # the original is not changed


def test_a_correction_runs_the_checks_ratios_and_assessment_again(mondi):
    # OCR read the adjustment of current assets as 10 525; gross 5 454 435 and net 4 443 661 give −1 010 774
    fixed = correct(mondi, "current_assets", "adjustment", -1_010_774_000)
    assert NET_CHECK in _failed(mondi) and NET_CHECK not in _failed(fixed)
    assert len(_failed(fixed)) == len(_failed(mondi)) - 1
    assert fixed.statements.value("current_assets", "current").needs_review is False  # the passing check confirms it
    assert fixed.statements.value("current_assets", "adjustment").note == "corrected by hand, read as '010 525'"
    assert fixed.report["source"] == "rules" and fixed.report["rating"]["value"]
    stages = {s.stage: s.status for s in fixed.stages}
    assert stages["validate"] == stages["ratios"] == stages["report"] == "ok" and len(fixed.stages) == len(mondi.stages)
    assert len(fixed.warnings) == len(set(fixed.warnings))  # the notes of the checks are replaced, not repeated


def test_the_checks_never_turn_the_sign_of_a_value_set_by_hand(mondi):
    profit = mondi.statements.value("operating_result", "current").value
    fixed = correct(mondi, "operating_result", "current", -profit)
    assert fixed.statements.value("operating_result", "current").value == -profit  # a lost minus would be restored
    assert "Operating result + financial result = profit before tax" in _failed(fixed)


def test_unknown_value(mondi):
    with pytest.raises(NoSuchValue):
        correct(mondi, "no_such_line", "current", 1.0)


def test_the_pdf_lists_the_corrections(mondi):
    fixed = correct(mondi, "current_assets", "adjustment", -1_010_774_000)
    fixed = correct(fixed, "current_assets", "current", fixed.statements.value("current_assets", "current").value)
    flags = pymupdf.TEXTFLAGS_TEXT & ~pymupdf.TEXT_PRESERVE_LIGATURES
    settings = get_settings().model_copy(deep=True)
    settings.report.language, settings.report.logo = "cs", ""
    with pymupdf.open("pdf", render_report(fixed, settings)) as doc:
        text = "".join(page.get_text(flags=flags) for page in doc)
    assert "Ručně ověřeno v aplikaci – potvrzeno: 1, opraveno: 1." in text
    assert "Oběžná aktiva (korekce): přečteno 10 525, opraveno na −1 010 774 tis. Kč" in text


def test_the_api_stores_a_correction_and_returns_the_new_result(tmp_path):
    client = TestClient(app)
    pdf = make_digital_pdf(tmp_path / "12345679_2025_statement.pdf")
    with pdf.open("rb") as f:
        view = client.post("/api/analyse", files={"file": (pdf.name, f, "application/pdf")}).json()
    url = f"/api/results/{view['id']}/corrections"

    response = client.post(url, json={"key": "total_assets", "period": "current", "value": 1_000_000_000})
    assert response.status_code == 200, response.text
    fixed = response.json()
    assert fixed["statements"]["balance_sheet"]["total_assets"]["current"]["source"] == "manual"
    assert fixed["audit"]["corrections"][0]["reason"] == "confirmed"
    again = client.get(f"/api/results/{view['id']}").json()  # kept for the page images, the export and the PDF
    assert again["audit"]["corrections"] == fixed["audit"]["corrections"]
    assert "corrections" in client.get(f"/api/results/{view['id']}/export").json()["audit"]

    assert client.post(url, json={"key": "nope", "period": "current", "value": 1}).status_code == 404
    assert client.post(url, json={"key": "total_assets", "period": "later", "value": 1}).status_code == 422
