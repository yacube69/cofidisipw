"""CIPW-60: results, their PDFs and corrections kept in SQLite across restarts."""

import sqlite3
from pathlib import Path

import pytest

from src.corrections import correct
from src.result import Result
from src.storage import Store

ROOT = Path(__file__).resolve().parents[1]
MONDI = ROOT / "data/fixtures/result_26161516_2025.json"


@pytest.fixture
def mondi() -> Result:
    return Result.model_validate_json(MONDI.read_text(encoding="utf-8"))


def test_a_result_survives_a_new_store_on_the_same_file(tmp_path, mondi):
    path = tmp_path / "fides.db"
    Store(path).save("abc123", mondi, b"%PDF-1.7 ...", owner="jnovak")

    again = Store(path).load("abc123")  # as after a restart
    assert again.result == mondi and again.pdf == b"%PDF-1.7 ..." and again.owner == "jnovak"
    assert Store(path).load("nope") is None
    with sqlite3.connect(path) as db:  # compressed: far smaller than the JSON
        size = db.execute("SELECT length(result) FROM results").fetchone()[0]
    assert size < len(mondi.model_dump_json()) / 3


def test_a_correction_keeps_owner_time_and_pdf(tmp_path, mondi):
    store = Store(tmp_path / "fides.db")
    store.save("abc123", mondi, b"%PDF", owner="jnovak")
    created = store.list()[0]["created_at"]
    value = mondi.statements.value("current_assets", "current").value
    fixed = correct(mondi, "current_assets", "current", value, by="jnovak")
    store.save("abc123", fixed, None, owner=None)  # a correction sends neither the PDF nor the owner again

    again = store.load("abc123")
    assert again.pdf == b"%PDF" and again.owner == "jnovak" and again.created_at == created
    assert [c.by for c in again.result.audit.corrections] == ["jnovak"]


def test_the_list_is_newest_first_and_per_owner(tmp_path, mondi):
    store = Store(tmp_path / "fides.db")
    for i, owner in enumerate(["jnovak", "pdvorak", "jnovak"]):
        store.save(f"r{i}", mondi, None, owner)
    rows = store.list()
    assert [r["id"] for r in rows] == ["r2", "r1", "r0"]
    assert rows[0]["company"] == "Mondi Štětí a.s." and rows[0]["ico"] == "26161516" and rows[0]["file"] == mondi.file
    assert [r["id"] for r in store.list(owner="jnovak")] == ["r2", "r0"]
    assert store.owner("r1") == (True, "pdvorak") and store.owner("x") == (False, None)


def test_delete_and_keep_days(tmp_path, mondi):
    store = Store(tmp_path / "fides.db")
    store.save("old", mondi, None, None)
    store.save("new", mondi, None, None)
    with store.connect() as db:
        db.execute("UPDATE results SET created_at = '2020-01-01T00:00:00+01:00' WHERE id = 'old'")
    assert store.purge(None) == 0 and len(store.list()) == 2  # kept by default
    assert store.purge(90) == 1 and [r["id"] for r in store.list()] == ["new"]
    assert store.delete("new") and not store.delete("new") and store.list() == []
