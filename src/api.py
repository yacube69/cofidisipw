"""HTTP API for the Fides web app (CIPW-41).

Python does what it is good at (reading, OCR, parsing, checks, ratios) and serves it as JSON; the React app in web/
renders it. In the Docker image both run in one process: this app also serves the built web app (web/dist) at /.

    uvicorn src.api:app --reload            # API on http://localhost:8000, web app via `npm run dev` in web/

Endpoints (all under /api):
    GET  /health                       liveness probe
    GET  /samples                      saved sample results the app can open without OCR
    GET  /dictionary                   line items: key, statement, code, Czech label, English name
    POST /analyse                      PDF upload -> run the pipeline -> result view
    GET  /results/{id}                 result view (a sample id opens that sample)
    GET  /results/{id}/pages/{page}    page image (JPEG rendered from the PDF, or the one saved with a sample)
    GET  /results/{id}/export          full Result JSON as a download

The result view is the `Result` without `page_words` (large, only needed by the parser) plus the size of every
statement page in PDF points, so the app can scale bounding boxes onto the page image.
"""

from __future__ import annotations

import tempfile
import threading
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal

import pymupdf
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from src.line_items import load_dictionary
from src.pipeline import run_pipeline
from src.result import PipelineOptions, Result
from src.settings import ROOT, get_settings

SETTINGS = get_settings()
FIXTURES = SETTINGS.paths.fixtures
RAW = SETTINGS.paths.raw
WEB_DIST = ROOT / "web" / "dist"  # part of the app build, not a setting
ZOOM = 2.0  # page images at 144 dpi, the same as scripts/make_sample_result.py
MAX_UPLOAD = SETTINGS.api.max_upload_mb * 1024 * 1024
MAX_RESULTS = SETTINGS.api.max_results  # analysed files kept in memory (single-user tool; restart clears them)

# id -> (label, saved Result). Make a sample with `python scripts/make_sample_result.py <pdf>`.
SAMPLES = {
    "26701529-2024": ("Digital PDF · abbreviated · 2024", FIXTURES / "result_26701529_2024.json"),
    "mondi-2025": ("Mondi Štětí a.s. · 2025 · shaded scan", FIXTURES / "result_26161516_2025.json"),
}


@dataclass
class Stored:
    result: Result
    pdf: bytes | None  # None for a sample whose PDF is not on disk (the Docker image ships without data/raw)


_store: OrderedDict[str, Stored] = OrderedDict()
_lock = threading.Lock()


def _remember(result_id: str, stored: Stored) -> None:
    with _lock:
        _store[result_id] = stored
        _store.move_to_end(result_id)
        while len(_store) > MAX_RESULTS:
            _store.popitem(last=False)


def _get(result_id: str) -> Stored:
    with _lock:
        stored = _store.get(result_id)
    if stored is not None:
        return stored
    if result_id in SAMPLES:
        result = Result.model_validate_json(SAMPLES[result_id][1].read_text(encoding="utf-8"))
        pdf = RAW / result.file
        stored = Stored(result, pdf.read_bytes() if pdf.exists() else None)
        _remember(result_id, stored)
        return stored
    raise HTTPException(404, f"No result {result_id!r}: analyse the file again (results are kept in memory only)")


def _view(result_id: str, result: Result) -> dict:
    data = result.model_dump(mode="json", exclude={"page_words"})
    data["id"] = result_id
    data["page_sizes"] = {pw.page: [pw.width, pw.height] for pw in result.page_words}
    data["stubbed_stages"] = result.stubbed_stages
    return data


app = FastAPI(title="Fides API", version="0.1.0", docs_url="/api/docs", openapi_url="/api/openapi.json")


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/samples")
def samples() -> list[dict]:
    return [{"id": key, "label": label} for key, (label, path) in SAMPLES.items() if path.exists()]


@app.get("/api/dictionary")
def dictionary() -> list[dict]:
    return [
        {"key": d.key, "statement": d.statement, "side": d.side, "code": d.code, "label": d.label, "en": d.en}
        for d in load_dictionary().lines
    ]


@app.post("/api/analyse")
def analyse(
    file: Annotated[UploadFile, File(description="Financial statement as PDF")],
    scope: Annotated[Literal["individual", "consolidated"], Form()] = "individual",
    skip_report: Annotated[bool, Form()] = True,
) -> dict:
    """Run the whole pipeline on one PDF. Scans take about 20 s per statement page (OCR)."""
    pdf = file.file.read(MAX_UPLOAD + 1)
    if len(pdf) > MAX_UPLOAD:
        raise HTTPException(413, f"The file is larger than {MAX_UPLOAD // 2**20} MB")
    if not pdf.startswith(b"%PDF"):
        raise HTTPException(415, "That is not a PDF file")
    name = Path(file.filename or "statement.pdf").name
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / name
        path.write_bytes(pdf)
        try:
            result = run_pipeline(path, PipelineOptions(scope=scope, skip_report=skip_report))
        except Exception as exc:  # a PDF pymupdf cannot open
            raise HTTPException(422, f"The PDF could not be read: {exc}") from exc
    result_id = uuid.uuid4().hex[:12]
    _remember(result_id, Stored(result, pdf))
    return _view(result_id, result)


@app.get("/api/results/{result_id}")
def get_result(result_id: str) -> dict:
    return _view(result_id, _get(result_id).result)


@app.get("/api/results/{result_id}/pages/{page}")
def page_image(result_id: str, page: int) -> Response:
    stored = _get(result_id)
    if stored.pdf is not None:
        with pymupdf.open(stream=stored.pdf, filetype="pdf") as doc:
            if not 1 <= page <= doc.page_count:
                raise HTTPException(404, f"The PDF has {doc.page_count} pages")
            jpeg = doc[page - 1].get_pixmap(matrix=pymupdf.Matrix(ZOOM, ZOOM)).tobytes("jpeg", jpg_quality=85)
        return Response(jpeg, media_type="image/jpeg", headers={"Cache-Control": "max-age=3600"})
    saved = FIXTURES / "pages" / f"{Path(stored.result.file).stem}_p{page}.jpg"
    if not saved.exists():
        raise HTTPException(404, "No image of this page")
    return FileResponse(saved, media_type="image/jpeg", headers={"Cache-Control": "max-age=3600"})


@app.get("/api/results/{result_id}/export")
def export(result_id: str) -> Response:
    result = _get(result_id).result
    name = f"{Path(result.file).stem}.result.json"
    return Response(
        result.model_dump_json(indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


class WebApp(StaticFiles):
    """The built React app. index.html is revalidated on every load so an update shows at once; the hashed files
    in assets/ never change and are cached for good."""

    async def get_response(self, path: str, scope) -> Response:  # type: ignore[override]
        response = await super().get_response(path, scope)
        immutable = path.startswith("assets/")
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable" if immutable else "no-cache"
        return response


if WEB_DIST.exists():  # npm run build in web/; in development Vite serves the app instead
    app.mount("/", WebApp(directory=WEB_DIST, html=True), name="web")
