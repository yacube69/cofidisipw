"""HTTP API for the Fides web app (CIPW-41).

Python does what it is good at (reading, OCR, parsing, checks, ratios) and serves it as JSON; the React app in web/
renders it. In the Docker image both run in one process: this app also serves the built web app (web/dist) at /.

    uvicorn src.api:app --reload            # API on http://localhost:8000, web app via `npm run dev` in web/

Endpoints (all under /api; with `auth.mode: local` every one but health and auth needs a signed-in user, CIPW-62):
    GET  /health                       liveness probe
    GET  /auth/me                      sign-in mode and the signed-in user
    POST /auth/login, /auth/logout     sign in (name and password) and out
    GET  /samples                      saved sample results the app can open without OCR
    GET  /dictionary                   line items: key, statement, code, Czech label, English name
    GET  /company/{ico}                the company in ARES (name, legal form, address, CZ-NACE), CIPW-44
    POST /analyse                      PDF upload (optionally the statement pages) -> run the pipeline -> result view
    GET  /results                      the stored results, newest first (CIPW-60)
    GET  /results/{id}                 result view (a sample id opens that sample)
    DELETE /results/{id}               delete a stored result (its owner or an admin)
    GET  /results/{id}/pages/{page}    page image (JPEG rendered from the PDF, or the one saved with a sample)
    GET  /results/{id}/export          full Result JSON as a download
    GET  /results/{id}/report.pdf      the PDF report in Cofidis' layout (CIPW-43); rule-based text without an AI model
    POST /results/{id}/corrections     confirm or correct one value by hand (CIPW-42) -> everything after reading again

Results are stored in SQLite (src/storage.py, CIPW-60) and survive a restart; the last `api.max_results` are also kept
in memory, so page images and corrections do not read the database again. Samples are never stored: a correction of
a sample lasts until the restart.

The result view is the `Result` without `page_words` (large, only needed by the parser) plus the size of every
statement page in PDF points, so the app can scale bounding boxes onto the page image.
"""

from __future__ import annotations

import re
import sys
import tempfile
import threading
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal

import pymupdf
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from src.auth import ANONYMOUS, COOKIE, Role, Sessions, Throttle, User, Users, ensure_admin
from src.corrections import NoSuchValue, correct
from src.integrations.ares import lookup
from src.line_items import load_dictionary
from src.pipeline import run_pipeline
from src.report.fallback import rule_based_report
from src.report.pdf import render_report, report_filename
from src.result import PipelineOptions, Result
from src.schema import Period
from src.settings import ROOT, get_settings
from src.storage import Store
from src.version import package_version

SETTINGS = get_settings()
FIXTURES = SETTINGS.paths.fixtures
RAW = SETTINGS.paths.raw
WEB_DIST = ROOT / "web" / "dist"  # part of the app build, not a setting
ZOOM = 2.0  # page images at 144 dpi, the same as scripts/make_sample_result.py
MAX_UPLOAD = SETTINGS.api.max_upload_mb * 1024 * 1024
MAX_RESULTS = SETTINGS.api.max_results  # results also kept in memory, the most recently used ones
PRIVATE = {"Cache-Control": "private, max-age=3600"}  # page images: the browser may keep them, a proxy may not

# id -> (label, saved Result). Make a sample with `python scripts/make_sample_result.py <pdf>`.
SAMPLES = {
    "26701529-2024": ("Digital PDF · abbreviated · 2024", FIXTURES / "result_26701529_2024.json"),
    "mondi-2025": ("Mondi Štětí a.s. · 2025 · shaded scan", FIXTURES / "result_26161516_2025.json"),
}

STORE = Store(SETTINGS.storage.path)
USERS = Users(STORE)
SESSIONS = Sessions(STORE)
THROTTLE = Throttle()


def start() -> None:
    """At start: delete old results and expired sessions; with local accounts make sure the admin account exists."""
    STORE.purge(SETTINGS.storage.keep_days)
    if SETTINGS.auth.mode == "local":
        SESSIONS.purge(SETTINGS.auth)
        line = ensure_admin(USERS, SETTINGS.auth, SETTINGS.storage.path.parent)
        if line:
            print(line, file=sys.stderr, flush=True)


start()


# ---------------------------------------------------------------------------------------------- who is asking


def current_user(request: Request) -> User:
    """The signed-in user; without sign-in (auth.mode none) an anonymous admin, as before CIPW-62."""
    if SETTINGS.auth.mode == "none":
        return ANONYMOUS
    token = request.cookies.get(COOKIE)
    user = SESSIONS.user(token, SETTINGS.auth) if token else None
    if user is None:
        raise HTTPException(401, "Sign in first")
    return user


def role(minimum: Role):
    def check(user: Annotated[User, Depends(current_user)]) -> User:
        if not user.may(minimum):
            raise HTTPException(403, f"This needs the role {minimum}; you are {user.role}")
        return user

    return check


Viewer = Annotated[User, Depends(role("viewer"))]
Analyst = Annotated[User, Depends(role("analyst"))]


# --------------------------------------------------------------------------------------------------- results


@dataclass
class Stored:
    result: Result
    pdf: bytes | None  # None for a sample whose PDF is not on disk (the Docker image ships without data/raw)
    owner: str | None = None
    sample: bool = False


_cache: OrderedDict[str, Stored] = OrderedDict()
_lock = threading.Lock()


def _remember(result_id: str, stored: Stored) -> None:
    """Keep a result in memory and, unless it is a sample, in storage."""
    if not stored.sample:
        STORE.save(result_id, stored.result, stored.pdf, stored.owner)
    with _lock:
        _cache[result_id] = stored
        _cache.move_to_end(result_id)
        while len(_cache) > MAX_RESULTS:
            _cache.popitem(last=False)


def _visible(stored: Stored, user: User) -> bool:
    """storage.visibility own: an analyst or viewer sees only their own results; an admin sees all."""
    own = SETTINGS.storage.visibility == "own" and user.role != "admin" and SETTINGS.auth.mode != "none"
    return stored.sample or not own or stored.owner in (None, user.name)


def _get(result_id: str, user: User = ANONYMOUS) -> Stored:
    with _lock:
        stored = _cache.get(result_id)
    if stored is None and result_id in SAMPLES:
        result = Result.model_validate_json(SAMPLES[result_id][1].read_text(encoding="utf-8"))
        pdf = RAW / result.file
        stored = Stored(result, pdf.read_bytes() if pdf.exists() else None, sample=True)
        _remember(result_id, stored)
    if stored is None and (found := STORE.load(result_id)) is not None:
        stored = Stored(found.result, found.pdf, found.owner)
        with _lock:
            _cache[result_id] = stored
    if stored is None or not _visible(stored, user):
        raise HTTPException(404, f"No result {result_id!r}: it was deleted, or analyse the file again")
    return stored


def _view(result_id: str, stored: Stored | Result) -> dict:
    result = stored.result if isinstance(stored, Stored) else stored
    data = result.model_dump(mode="json", exclude={"page_words"})
    data["id"] = result_id
    data["page_sizes"] = {pw.page: [pw.width, pw.height] for pw in result.page_words}
    data["stubbed_stages"] = result.stubbed_stages
    if isinstance(stored, Stored):
        data["owner"], data["sample"] = stored.owner, stored.sample
    return data


app = FastAPI(title="Fides API", version=package_version(), docs_url="/api/docs", openapi_url="/api/openapi.json")


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


# ------------------------------------------------------------------------------------------------- sign-in


class LoginIn(BaseModel):
    name: str = Field(max_length=64)
    password: str = Field(max_length=200)


def _user_json(user: User) -> dict:
    return {"name": user.name, "role": user.role}


@app.get("/api/auth/me")
def me(request: Request) -> dict:
    """The sign-in mode and the signed-in user (None when signed out); never 401, so the app knows what to show."""
    try:
        user: User | None = current_user(request)
    except HTTPException:
        user = None
    return {"mode": SETTINGS.auth.mode, "user": _user_json(user) if user else None}


def _secure(request: Request) -> bool:
    setting = SETTINGS.auth.secure_cookie
    if setting != "auto":
        return setting == "always"
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"


@app.post("/api/auth/login")
def login(body: LoginIn, request: Request) -> JSONResponse:
    if SETTINGS.auth.mode == "none":
        raise HTTPException(400, "Sign-in is off (auth.mode none)")
    wait = THROTTLE.blocked_for(body.name, SETTINGS.auth)
    if wait:
        raise HTTPException(429, f"Too many wrong passwords: try again in {wait // 60 + 1} min")
    user = USERS.check(body.name, body.password)
    if user is None:
        THROTTLE.failed(body.name)
        raise HTTPException(401, "Wrong name or password")
    THROTTLE.clear(body.name)
    response = JSONResponse({"mode": SETTINGS.auth.mode, "user": _user_json(user)})
    response.set_cookie(COOKIE, SESSIONS.start(user), max_age=SETTINGS.auth.max_hours * 3600, httponly=True,
                        samesite="strict", secure=_secure(request), path="/")  # fmt: skip
    return response


@app.post("/api/auth/logout")
def logout(request: Request) -> JSONResponse:
    token = request.cookies.get(COOKIE)
    if token:
        SESSIONS.end(token)
    response = JSONResponse({"mode": SETTINGS.auth.mode, "user": None})
    response.delete_cookie(COOKIE, path="/")
    return response


# ------------------------------------------------------------------------------------------------- analysis


@app.get("/api/samples")
def samples(user: Viewer) -> list[dict]:
    return [{"id": key, "label": label} for key, (label, path) in SAMPLES.items() if path.exists()]


@app.get("/api/dictionary")
def dictionary(user: Viewer) -> list[dict]:
    return [
        {"key": d.key, "statement": d.statement, "side": d.side, "code": d.code, "label": d.label, "en": d.en}
        for d in load_dictionary().lines
    ]


@app.get("/api/company/{ico}")
def company(ico: str, user: Viewer) -> dict:
    """The company with this IČO in ARES, or the dataset snapshot without network: for an IČO the user corrects."""
    found, note = lookup(ico)
    if found is None:
        raise HTTPException(404, note)
    return found.model_dump(mode="json")


@app.post("/api/analyse")
def analyse(
    user: Analyst,
    file: Annotated[UploadFile, File(description="Financial statement as PDF")],
    scope: Annotated[Literal["individual", "consolidated"], Form()] = "individual",
    skip_report: Annotated[bool, Form()] = False,
    balance_sheet_pages: Annotated[str, Form(description="e.g. '75-76'; with the next one replaces the locator")] = "",
    income_statement_pages: Annotated[str, Form(description="e.g. '77'")] = "",
) -> dict:
    """Run the whole pipeline on one PDF. Scans take about 20 s per statement page (OCR).

    The statement pages are found automatically; when the user enters them (an annual report where the locator
    picks the wrong pages), those pages are read instead, and a statement left empty is not read."""
    pages = {"balance_sheet": _page_list(balance_sheet_pages), "income_statement": _page_list(income_statement_pages)}
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
            override = pages if any(pages.values()) else None
            options = PipelineOptions(scope=scope, skip_report=skip_report, pages=override)
            result = run_pipeline(path, options, user=user.name)
        except Exception as exc:  # a PDF pymupdf cannot open
            raise HTTPException(422, f"The PDF could not be read: {exc}") from exc
    result_id = uuid.uuid4().hex[:12]
    stored = Stored(result, pdf, user.name)
    _remember(result_id, stored)
    STORE.purge(SETTINGS.storage.keep_days)
    return _view(result_id, stored)


def _page_list(text: str) -> list[int]:
    """'75-76, 80' -> [75, 76, 80]."""
    pages: set[int] = set()
    for part in re.split(r"[\s,;]+", re.sub(r"\s*[-–]\s*", "-", text.strip())):
        m = re.fullmatch(r"(\d{1,4})(?:-(\d{1,4}))?", part)
        if part and (not m or int(m[2] or m[1]) - int(m[1]) not in range(20)):
            raise HTTPException(422, f"Pages: '{part}' is not a page number or a range such as 75-76")
        if m:
            pages.update(range(int(m[1]), int(m[2] or m[1]) + 1))
    return sorted(pages)


@app.get("/api/results")
def stored_results(user: Viewer) -> list[dict]:
    """The stored results, newest first: id, file, company, IČO, owner, when (CIPW-60)."""
    own = SETTINGS.storage.visibility == "own" and user.role != "admin" and SETTINGS.auth.mode != "none"
    return STORE.list(owner=user.name if own else None)


@app.get("/api/results/{result_id}")
def get_result(result_id: str, user: Viewer) -> dict:
    return _view(result_id, _get(result_id, user))


@app.delete("/api/results/{result_id}")
def delete_result(result_id: str, user: Analyst) -> dict:
    """Delete a stored result with its PDF and corrections: its owner or an admin."""
    stored, owner = STORE.owner(result_id)
    if not stored:
        raise HTTPException(404, f"No stored result {result_id!r}")
    if user.role != "admin" and owner != user.name:
        raise HTTPException(403, "Only its owner or an admin may delete a result")
    STORE.delete(result_id)
    with _lock:
        _cache.pop(result_id, None)
    return {"deleted": result_id}


@app.get("/api/results/{result_id}/pages/{page}")
def page_image(result_id: str, page: int, user: Viewer) -> Response:
    stored = _get(result_id, user)
    if stored.pdf is not None:
        with pymupdf.open(stream=stored.pdf, filetype="pdf") as doc:
            if not 1 <= page <= doc.page_count:
                raise HTTPException(404, f"The PDF has {doc.page_count} pages")
            jpeg = doc[page - 1].get_pixmap(matrix=pymupdf.Matrix(ZOOM, ZOOM)).tobytes("jpeg", jpg_quality=85)
        return Response(jpeg, media_type="image/jpeg", headers=PRIVATE)
    saved = FIXTURES / "pages" / f"{Path(stored.result.file).stem}_p{page}.jpg"
    if not saved.exists():
        raise HTTPException(404, "No image of this page")
    return FileResponse(saved, media_type="image/jpeg", headers=PRIVATE)


@app.get("/api/results/{result_id}/export")
def export(result_id: str, user: Viewer) -> Response:
    result = _get(result_id, user).result
    name = f"{Path(result.file).stem}.result.json"
    return Response(
        result.model_dump_json(indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@app.get("/api/results/{result_id}/report.pdf")
def report_pdf(result_id: str, user: Viewer) -> Response:
    """The PDF report. A result without an assessment (saved before CIPW-16's fallback, or analysed with
    skip_report) gets the rule-based one, so the PDF never lacks its text."""
    result = _get(result_id, user).result
    if result.report is None:
        result = result.model_copy(update={"report": rule_based_report(result, SETTINGS.report.language)})
    return Response(
        render_report(result, SETTINGS),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{report_filename(result)}"'},
    )


class CorrectionIn(BaseModel):
    key: str = Field(description="Line item key, e.g. 'current_assets'.")
    period: Period
    value: float | None = Field(description="The right amount in CZK; the amount it has confirms it.")
    reason: str | None = Field(None, max_length=500)


@app.post("/api/results/{result_id}/corrections")
def add_correction(result_id: str, body: CorrectionIn, user: Analyst) -> dict:
    """Confirm or correct one value by hand. The correction is kept in the result's audit block with the user's
    name, the checks, ratios, bankruptcy models, traffic lights and the assessment are computed again, the result is
    stored, and the new result view returns."""
    stored = _get(result_id, user)
    try:
        result = correct(stored.result, body.key, body.period, body.value, body.reason, by=user.name,
                         settings=SETTINGS)  # fmt: skip
    except NoSuchValue as exc:
        raise HTTPException(404, exc.args[0]) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    fixed = Stored(result, stored.pdf, stored.owner, stored.sample)
    _remember(result_id, fixed)
    return _view(result_id, fixed)


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
