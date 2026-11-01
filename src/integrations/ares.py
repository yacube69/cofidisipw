"""ARES lookup by IČO (CIPW-44): name, legal form, address, industry (CZ-NACE) and size of a company.

ARES is the public register of economic subjects run by the Ministry of Finance (ares.gov.cz). Two REST endpoints
are used, without a key:

    ekonomicke-subjekty/{ico}       name, legal form, registered address, date of foundation
    ekonomicke-subjekty-res/{ico}   predominant activity (CZ-NACE) and employee band (statistical register of ČSÚ)

The IČO is the only thing sent. Every answer is kept in `outputs/cache/ares/{ico}.json`, so a company is asked once.
Without network (offline mode, `ares.enabled: false`, a closed network) the saved answer or `data/companies.csv`,
the snapshot of every company of our dataset, is used; with neither the company stays unknown and the result says
so. Codes are named from `config/ares_codebooks.json` (CZ-NACE, legal forms, employee bands, from the same API).
Refresh both files with `python scripts/ares_lookup.py`.
"""

from __future__ import annotations

import csv
import json
import re
import unicodedata
from datetime import date
from functools import cache
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field
from rapidfuzz import fuzz

from src.settings import ROOT, Settings, get_settings

SNAPSHOT = ROOT / "data" / "companies.csv"
CODEBOOKS = ROOT / "config" / "ares_codebooks.json"
SNAPSHOT_FIELDS = ["ico", "name", "legal_form", "address", "founded", "nace", "nace_2008", "employees", "retrieved"]
# rapidfuzz token_set_ratio of two names without their legal form, from which they name one company. A misread IČO
# belongs to an unrelated company (similarity well below 50); OCR noise in a printed name stays above 70
# ("Morxfi Štětí a.s" / "Mondi Štětí a.s.": 78).
SAME_NAME = 60


class Company(BaseModel):
    """A company as ARES knows it. Codes are kept as ARES gives them; names come from its codebooks."""

    ico: str
    name: str | None = None
    legal_form: str | None = Field(None, description="Legal form, e.g. 'Akciová společnost'.")
    address: str | None = Field(None, description="Registered address.")
    founded: date | None = None
    nace: str | None = Field(None, description="Predominant activity, CZ-NACE code (2025 version), e.g. '17120'.")
    nace_name: str | None = None
    nace_section: str | None = Field(None, description="Section of the CZ-NACE code, e.g. 'C'.")
    nace_section_name: str | None = None
    nace_2008: str | None = Field(None, description="Predominant activity in CZ-NACE 2008, used by older statistics.")
    employees: str | None = Field(None, description="Employee band, e.g. '1000 - 1499 zaměstnanců'.")
    source: Literal["ares", "cache", "snapshot"] = Field(
        description="'ares' asked now, 'cache' a saved answer, 'snapshot' data/companies.csv (offline)."
    )
    retrieved: date | None = Field(None, description="When ARES gave this answer.")


class AresUnavailable(Exception):
    """ARES did not answer (no network, timeout, an error on its side)."""


# --------------------------------------------------------------------------------------------- requests


def fetch(ico: str, settings: Settings | None = None) -> dict[str, Any] | None:
    """The raw answers of ARES for one IČO, or None when ARES does not know it. Raises AresUnavailable."""
    settings = settings or get_settings()
    basic = _get(f"/ekonomicke-subjekty/{ico}", settings)
    if basic is None:
        return None
    try:
        res = _get(f"/ekonomicke-subjekty-res/{ico}", settings)  # the statistical register may lag behind
    except AresUnavailable:
        res = None
    return {"retrieved": date.today().isoformat(), "basic": basic, "res": res}


def _get(path: str, settings: Settings) -> dict[str, Any] | None:
    try:
        response = httpx.get(
            settings.ares.url.rstrip("/") + path, timeout=settings.ares.timeout, headers={"Accept": "application/json"}
        )
    except httpx.HTTPError as exc:
        raise AresUnavailable(f"ARES not reachable ({type(exc).__name__})") from exc
    if response.status_code == 404:
        return None
    if response.status_code != 200:
        raise AresUnavailable(f"ARES answered HTTP {response.status_code}")
    return response.json()


def codebook(kind: str, settings: Settings | None = None) -> dict[str, Any]:
    """One codebook of ARES (CzNace, PravniForma, KategoriePoctuPracovniku): code -> item, current items only."""
    settings = settings or get_settings()
    try:
        response = httpx.post(
            settings.ares.url.rstrip("/") + "/ciselniky-nazevniky/vyhledat",
            json={"kodCiselniku": kind, "zdrojCiselniku": "res"},
            timeout=max(settings.ares.timeout, 30.0),
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise AresUnavailable(f"ARES codebook {kind}: {type(exc).__name__}") from exc
    items = response.json()["ciselniky"][0]["polozkyCiselniku"]
    today = date.today().isoformat()
    return {i["kod"]: i for i in items if i.get("platnostDo", "9999") >= today}


# --------------------------------------------------------------------------------------------- answers


@cache
def _codebooks() -> dict[str, dict[str, Any]]:
    if not CODEBOOKS.exists():
        return {"cz_nace": {}, "legal_form": {}, "employees": {}}
    return json.loads(CODEBOOKS.read_text(encoding="utf-8"))


def _section(code: str | None) -> str | None:
    """The CZ-NACE section (a letter) above a code, following the parents in the codebook."""
    nace = _codebooks()["cz_nace"]
    seen = 0
    while code and code in nace and seen < 10:
        if code.isalpha():
            return code
        code, seen = nace[code].get("parent"), seen + 1
    return code if code and code.isalpha() else None


def _name(book: str, code: str | None) -> str | None:
    item = _codebooks()[book].get(code or "")
    return item["name"] if item else None


def parse(ico: str, raw: dict[str, Any], source: str) -> Company:
    """A Company from the raw answers of `fetch`, its codes named from the codebooks."""
    basic = raw["basic"]
    res = ((raw.get("res") or {}).get("zaznamy") or [{}])[0]
    nace = res.get("czNacePrevazujici")
    section = _section(nace)
    employees = (res.get("statistickeUdaje") or {}).get("kategoriePoctuPracovniku")
    return Company(
        ico=ico,
        name=basic.get("obchodniJmeno"),
        legal_form=_name("legal_form", basic.get("pravniForma")) or basic.get("pravniForma"),
        address=(basic.get("sidlo") or {}).get("textovaAdresa"),
        founded=basic.get("datumVzniku"),
        nace=nace,
        nace_name=_name("cz_nace", nace),
        nace_section=section,
        nace_section_name=_name("cz_nace", section),
        nace_2008=res.get("czNacePrevazujici2008"),
        employees=_name("employees", employees) if employees != "000" else None,  # 000: not given
        source=source,  # type: ignore[arg-type]
        retrieved=raw.get("retrieved"),
    )


def cache_file(ico: str, settings: Settings) -> Path:
    folder = Path(settings.ares.cache) if settings.ares.cache else settings.paths.outputs / "cache" / "ares"
    return folder / f"{ico}.json"


def _from_snapshot(ico: str) -> Company | None:
    if not SNAPSHOT.exists():
        return None
    with SNAPSHOT.open(encoding="utf-8", newline="") as f:
        row = next((r for r in csv.DictReader(f) if r["ico"] == ico and r["name"]), None)
    if row is None:
        return None
    row = {k: v or None for k, v in row.items()}
    section = _section(row["nace"])
    return Company(
        **row,
        nace_name=_name("cz_nace", row["nace"]),
        nace_section=section,
        nace_section_name=_name("cz_nace", section),
        source="snapshot",
    )


def valid_ico(ico: str) -> bool:
    """IČO check digit: weights 8..2 on the first seven digits, (11 - sum mod 11) mod 10 is the eighth."""
    if not re.fullmatch(r"\d{8}", ico or ""):
        return False
    total = sum(int(d) * w for d, w in zip(ico[:7], range(8, 1, -1), strict=True))
    return (11 - total % 11) % 10 == int(ico[7])


def lookup(ico: str, settings: Settings | None = None) -> tuple[Company | None, str]:
    """The company with this IČO and a note on where its data came from (or why there is none).

    Order: the saved answer, ARES (when enabled and not offline), the dataset snapshot."""
    settings = settings or get_settings()
    if not valid_ico(ico):
        return None, f"IČO {ico} has a wrong check digit (misread, or a typo in the file name)"
    cached = cache_file(ico, settings)
    if cached.exists():
        raw = json.loads(cached.read_text(encoding="utf-8"))
        return parse(ico, raw, "cache"), f"ARES, saved answer of {raw.get('retrieved')}"
    reason = "offline mode" if settings.offline else "ARES lookup disabled" if not settings.ares.enabled else None
    if reason is None:
        try:
            raw = fetch(ico, settings)
        except AresUnavailable as exc:
            reason = str(exc)
        else:
            if raw is None:
                return None, f"IČO {ico} is not in ARES"
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
            return parse(ico, raw, "ares"), "ARES"
    found = _from_snapshot(ico)
    if found is not None:
        return found, f"{reason}: dataset snapshot of {found.retrieved}"
    return None, f"{reason}: company not known"


# --------------------------------------------------------------------------------------------- cross-check

_LEGAL_FORM = re.compile(
    r"\b(?:a\s*\.?\s*s|s\s*\.?\s*r\s*\.?\s*o|spol\s*\.?\s*s\s*r\s*\.?\s*o|v\s*\.?\s*o\s*\.?\s*s|k\s*\.?\s*s|s\s*\.?\s*e"
    r"|akciova spolecnost|spolecnost s rucenim omezenym|druzstvo|statni podnik|o\s*\.?\s*p\s*\.?\s*s)\b\.?"
)


def plain_name(name: str) -> str:
    """'Mondi Štětí a.s.' -> 'mondi steti': lower case, no diacritics, no legal form, no punctuation."""
    text = unicodedata.normalize("NFKD", name)
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    text = _LEGAL_FORM.sub(" ", text)
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def name_differs(printed: str | None, registered: str | None) -> bool:
    """True when the statement names another company than ARES does (an IČO read wrongly or a wrong file)."""
    if not printed or not registered:
        return False
    a, b = plain_name(printed), plain_name(registered)
    return bool(a and b) and fuzz.token_set_ratio(a, b) < SAME_NAME
