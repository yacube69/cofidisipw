"""CIPW-41: the HTTP API the web app runs on."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api import SAMPLES, app
from src.version import package_version
from tests.synthetic import make_digital_pdf

client = TestClient(app)


def test_health():
    assert client.get("/api/health").json() == {"status": "ok"}


def test_one_version_for_the_api_the_web_app_and_the_results():
    """pyproject.toml is the version; the web app shows the one in web/package.json, so both must agree."""
    root = Path(__file__).resolve().parents[1]
    web = json.loads((root / "web" / "package.json").read_text(encoding="utf-8"))["version"]
    assert app.version == package_version() == web


def test_every_sample_opens_with_page_sizes_and_without_page_words():
    samples = client.get("/api/samples").json()
    assert {s["id"] for s in samples} == set(SAMPLES)
    for sample in samples:
        view = client.get(f"/api/results/{sample['id']}").json()
        assert view["id"] == sample["id"] and "page_words" not in view
        pages = {p for ps in view["pages"].values() for p in ps}
        assert pages and pages <= {int(p) for p in view["page_sizes"]}
        assert view["statements"]["balance_sheet"], "the sample must have extracted values"


@pytest.mark.parametrize("sample", list(SAMPLES))
def test_sample_page_images_exist(sample):
    """The Docker image ships without data/raw/*.pdf: every sample page needs its saved JPEG."""
    view = client.get(f"/api/results/{sample}").json()
    for page in {p for ps in view["pages"].values() for p in ps}:
        assert (Path("data/fixtures/pages") / f"{Path(view['file']).stem}_p{page}.jpg").exists()
        response = client.get(f"/api/results/{sample}/pages/{page}")
        assert response.status_code == 200 and response.headers["content-type"] == "image/jpeg"


def test_dictionary_has_names_and_labels():
    lines = client.get("/api/dictionary").json()
    total = next(d for d in lines if d["key"] == "total_assets")
    assert total["en"] == "Total assets" and total["label"] == "AKTIVA CELKEM"


def test_analyse_a_digital_pdf(tmp_path):
    pdf = make_digital_pdf(tmp_path / "12345678_2025_statement.pdf")
    with pdf.open("rb") as f:
        response = client.post("/api/analyse", files={"file": (pdf.name, f, "application/pdf")})
    assert response.status_code == 200, response.text
    view = response.json()
    assert view["pages"]["balance_sheet"] == [1, 2] and view["consolidation"] == "individual"
    assert view["statements"]["balance_sheet"]["total_assets"]["current"]["value"] == 1_000_000_000
    assert view["audit"]["run_id"] and view["audit"]["settings_hash"]  # "About this result" in the app
    # the analysed file is kept for its page images and the export
    assert client.get(f"/api/results/{view['id']}/pages/1").headers["content-type"] == "image/jpeg"
    export = client.get(f"/api/results/{view['id']}/export")
    assert "attachment" in export.headers["content-disposition"] and "page_words" in export.json()


def test_analyse_reads_the_pages_the_user_entered(tmp_path):
    """An annual report where the locator picks the wrong pages: the user enters them, the locator is not used."""
    pdf = make_digital_pdf(tmp_path / "12345678_2025_statement.pdf")
    form = {"balance_sheet_pages": "1 - 2", "income_statement_pages": ""}
    with pdf.open("rb") as f:
        response = client.post("/api/analyse", files={"file": (pdf.name, f, "application/pdf")}, data=form)
    view = response.json()
    assert view["pages"] == {"balance_sheet": [1, 2], "income_statement": []} and view["consolidation"] is None
    assert view["statements"]["balance_sheet"]["total_assets"]["current"]["value"] == 1_000_000_000
    assert not any(w.startswith("locate:") for w in view["warnings"])
    with pdf.open("rb") as f:
        bad = client.post("/api/analyse", files={"file": (pdf.name, f)}, data={"balance_sheet_pages": "1 a 2"})
    assert bad.status_code == 422 and "'a' is not a page number" in bad.json()["detail"]


def test_analyse_rejects_a_file_that_is_not_a_pdf():
    response = client.post("/api/analyse", files={"file": ("notes.pdf", b"hello", "application/pdf")})
    assert response.status_code == 415 and response.json()["detail"] == "That is not a PDF file"


def test_unknown_result_and_page():
    assert client.get("/api/results/nope").status_code == 404
    assert client.get("/api/results/mondi-2025/pages/999").status_code == 404
