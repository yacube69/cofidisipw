"""Company data of the whole dataset from ARES (CIPW-44): data/companies.csv and config/ares_codebooks.json.

data/companies.csv is the snapshot Fides falls back on without network (offline mode, a closed network): name,
legal form, address, predominant activity (CZ-NACE) and employee band of every company in data/index.csv and
data/raw/. config/ares_codebooks.json names the codes (CZ-NACE, legal forms, employee bands). Answers are saved in
outputs/cache/ares, so a second run asks ARES only for new companies.

Usage:
    python scripts/ares_lookup.py                 # every company of the dataset, then write the snapshot
    python scripts/ares_lookup.py 26161516        # one company (printed, the snapshot is rewritten too)
    python scripts/ares_lookup.py --refresh       # ask ARES again instead of using the saved answers
    python scripts/ares_lookup.py --codebooks     # download the codebooks again first
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.integrations import ares  # noqa: E402
from src.settings import get_settings  # noqa: E402

BOOKS = {"cz_nace": "CzNace", "legal_form": "PravniForma", "employees": "KategoriePoctuPracovniku"}
PAUSE = 0.3  # seconds between companies: ARES is a public service


def dataset_icos() -> list[str]:
    """IČO of every company in data/index.csv and of every PDF in data/raw (file names start with the IČO)."""
    settings = get_settings()
    icos: set[str] = set()
    index = ROOT / "data" / "index.csv"
    if index.exists():
        with index.open(encoding="utf-8", newline="") as f:
            icos |= {r["ico"].strip().zfill(8) for r in csv.DictReader(f) if r["ico"].strip().isdigit()}
    for pdf in settings.paths.raw.glob("*.pdf"):
        m = re.match(r"(\d{6,8})[ _]", pdf.name)
        if m:
            icos.add(m.group(1).zfill(8))
    wrong = sorted(i for i in icos if not ares.valid_ico(i))
    if wrong:
        print(f"skipped, wrong check digit (typo in data/index.csv or the file name): {', '.join(wrong)}")
    return sorted(icos - set(wrong))


def download_codebooks() -> None:
    books = {}
    for key, kind in BOOKS.items():
        items = ares.codebook(kind)
        books[key] = {
            code: {"name": i["nazev"][0]["nazev"], **({"parent": i["kodNadrizeny"]} if i.get("kodNadrizeny") else {})}
            for code, i in sorted(items.items())
        }
        print(f"{kind}: {len(items)} codes")
    ares.CODEBOOKS.write_text(json.dumps(books, ensure_ascii=False, indent=0) + "\n", encoding="utf-8")
    ares._codebooks.cache_clear()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("icos", nargs="*", help="IČO to look up (default: the whole dataset)")
    parser.add_argument("--refresh", action="store_true", help="ask ARES again instead of using saved answers")
    parser.add_argument("--codebooks", action="store_true", help="download the codebooks again")
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):  # Windows consoles default to cp1250
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")

    settings = get_settings()
    settings.ares.enabled, settings.offline = True, False  # this script is the one that asks ARES
    if args.codebooks or not ares.CODEBOOKS.exists():
        download_codebooks()

    snapshot: dict[str, dict[str, str]] = {}
    if ares.SNAPSHOT.exists():
        with ares.SNAPSHOT.open(encoding="utf-8", newline="") as f:
            snapshot = {r["ico"]: r for r in csv.DictReader(f)}
    icos = [i.zfill(8) for i in args.icos] or dataset_icos()
    missing = []
    for n, ico in enumerate(icos, 1):
        cached = ares.cache_file(ico, settings)
        if args.refresh:
            cached.unlink(missing_ok=True)
        asked = not cached.exists()
        company, note = ares.lookup(ico, settings)
        if company is None:
            missing.append(f"{ico} ({note})")
            print(f"{n:3}/{len(icos)} {ico}  -- {note}")
        else:
            snapshot[ico] = {k: str(v) if v is not None else "" for k, v in company.model_dump(mode="json").items()
                             if k in ares.SNAPSHOT_FIELDS}  # fmt: skip
            print(f"{n:3}/{len(icos)} {ico}  {company.name}  |  {company.nace} {company.nace_name or ''}")
        if asked and n < len(icos):
            time.sleep(PAUSE)

    with ares.SNAPSHOT.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=ares.SNAPSHOT_FIELDS)
        writer.writeheader()
        writer.writerows(snapshot[i] for i in sorted(snapshot))
    with_nace = sum(1 for r in snapshot.values() if r["nace"])
    print(f"\n{len(snapshot)} companies in {ares.SNAPSHOT.relative_to(ROOT)}, {with_nace} with CZ-NACE")
    if missing:
        print("not found: " + ", ".join(missing))
    return 0


if __name__ == "__main__":
    sys.exit(main())
