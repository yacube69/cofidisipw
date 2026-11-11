"""CIPW-44: company data from ARES by IČO, with a recorded answer (no network in tests)."""

import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from src.integrations import ares
from src.settings import get_settings

ROOT = Path(__file__).resolve().parents[1]
RECORDED = json.loads((ROOT / "tests/fixtures/ares_26161516.json").read_text(encoding="utf-8"))  # Mondi Štětí a.s.


@pytest.fixture
def settings(tmp_path):
    s = get_settings().model_copy(deep=True)
    s.ares.cache, s.offline, s.ares.enabled = str(tmp_path / "ares"), False, True
    return s


@pytest.fixture
def ares_answers(monkeypatch):
    """ARES as an HTTP mock: the recorded answers for Mondi, 404 for any other IČO. Counts the requests."""
    calls = []

    def get(url, **_):
        calls.append(url)
        ico = url.rstrip("/").rsplit("/", 1)[-1]
        if ico != "26161516":
            return httpx.Response(404, json={"kod": "NENALEZENO"})
        return httpx.Response(200, json=RECORDED["res" if "-res/" in url else "basic"])

    monkeypatch.setattr(ares.httpx, "get", get)
    return calls


def test_a_recorded_answer_is_read_with_the_codes_named():
    c = ares.parse("26161516", RECORDED, "ares")
    assert (c.name, c.legal_form) == ("Mondi Štětí a.s.", "Akciová společnost")
    assert c.address == "Litoměřická 272, 41108 Štětí"
    assert (c.nace, c.nace_name, c.nace_section, c.nace_section_name) == (
        "17120", "Výroba papíru a lepenky", "C", "Zpracovatelský průmysl"
    )  # fmt: skip
    assert (c.employees, c.founded, c.retrieved) == ("1000 - 1499 zaměstnanců", date(2000, 3, 6), date(2026, 11, 1))


def test_ares_is_asked_once_then_the_saved_answer_is_used(settings, ares_answers):
    company, note = ares.lookup("26161516", settings)
    assert (company.source, note, len(ares_answers)) == ("ares", "ARES", 2)  # register + statistical register
    company, note = ares.lookup("26161516", settings)
    assert company.source == "cache" and note.startswith("ARES, saved answer of") and len(ares_answers) == 2


def test_an_ico_ares_does_not_know(settings, ares_answers):
    assert ares.lookup("27920992", settings) == (None, "IČO 27920992 is not in ARES")


def test_a_wrong_check_digit_is_not_sent(settings, ares_answers):
    """25556149 is a typo in a file name of our dataset (PLOMA, a.s. is 25556045)."""
    company, note = ares.lookup("25556149", settings)
    assert company is None and "wrong check digit" in note and ares_answers == []
    assert ares.valid_ico("25556045") and ares.valid_ico("00014915") and not ares.valid_ico("1234567")


def test_without_network_the_dataset_snapshot_answers(settings, monkeypatch):
    def unreachable(url, **_):
        raise httpx.ConnectTimeout("no network")

    monkeypatch.setattr(ares.httpx, "get", unreachable)
    company, note = ares.lookup("26161516", settings)  # in data/companies.csv
    assert company.source == "snapshot" and company.nace_section == "C" and company.employees
    assert note.startswith("ARES not reachable (ConnectTimeout): dataset snapshot of")
    assert ares.lookup("12345679", settings) == (None, "ARES not reachable (ConnectTimeout): company not known")


def test_offline_mode_sends_nothing(settings, ares_answers):
    settings.offline = True
    company, note = ares.lookup("26161516", settings)
    assert company.source == "snapshot" and note.startswith("offline mode:") and ares_answers == []


def test_every_company_of_the_dataset_is_in_the_snapshot():
    """Done when of CIPW-44: name and CZ-NACE for every company in data/index.csv."""
    import csv

    with (ROOT / "data/index.csv").open(encoding="utf-8") as f:
        icos = {r["ico"].zfill(8) for r in csv.DictReader(f)}
    with ares.SNAPSHOT.open(encoding="utf-8") as f:
        snapshot = {r["ico"]: r for r in csv.DictReader(f)}
    assert all(ares.valid_ico(i) for i in icos), sorted(i for i in icos if not ares.valid_ico(i))
    assert icos <= set(snapshot), sorted(icos - set(snapshot))
    assert all(snapshot[i]["name"] and snapshot[i]["nace"] for i in icos)


@pytest.mark.parametrize(
    "printed, registered, differs",
    [
        ("MONDI ŠTĚTÍ A.S.", "Mondi Štětí a.s.", False),
        ("Morxfi Štětí a.s", "Mondi Štětí a.s.", False),  # OCR noise in the printed name
        ("E*tech,spol.s r.o.", "E-tech, spol. s r.o.", False),
        ("Česká pošta, s.p.", "Mondi Štětí a.s.", True),  # the IČO belongs to another company
        (None, "Mondi Štětí a.s.", False),  # name not printed: nothing to compare
    ],
)
def test_a_name_that_differs_from_ares(printed, registered, differs):
    assert ares.name_differs(printed, registered) is differs


@pytest.mark.parametrize(
    ("printed", "registered", "shown", "source"),
    [
        ("Morxfi Štětí a.s", "Mondi Štětí a.s.", "Mondi Štětí a.s.", "ares"),  # OCR noise: ARES' spelling
        (None, "Mondi Štětí a.s.", "Mondi Štětí a.s.", "ares"),  # nothing printed
        ("PLOMA, a.s.", "Mondi Štětí a.s.", "PLOMA, a.s.", "statement"),  # a misread IČO: ARES names another company
        ("PLOMA, a.s.", None, "PLOMA, a.s.", "statement"),  # ARES does not know the IČO
        (None, None, None, None),
    ],
)
def test_the_name_to_show(printed, registered, shown, source):
    """CIPW-56: the app and the PDF report show one name, ARES' when it is the statement's company."""
    from src.result import Result
    from src.schema import FinancialStatements, Metadata

    company = ares.Company(ico="26161516", name=registered, source="snapshot") if registered else None
    result = Result(file="a.pdf", statements=FinancialStatements(metadata=Metadata(company_name=printed)),
                    company=company)  # fmt: skip
    assert (result.display_name, result.name_source) == (shown, source)
    saved = json.loads(result.model_dump_json())
    assert (saved["display_name"], saved["name_source"]) == (shown, source)  # in the API view and the export too
    assert Result.model_validate_json(result.model_dump_json()).display_name == shown
