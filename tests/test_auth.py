"""CIPW-62: sign-in with local accounts, roles, sessions, blocking and the one admin account."""

import time
import uuid

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from scripts.users import main as users_cli
from src import api
from src.auth import AuthError, Sessions, User, Users, ensure_admin, hash_password, verify_password
from src.settings import AuthSettings
from src.storage import Store
from tests.synthetic import make_digital_pdf

PASSWORD = "correct horse battery"


@pytest.fixture
def local(monkeypatch):
    """The API with auth.mode local, and a fresh name for each user a test adds."""
    monkeypatch.setattr(api.SETTINGS.auth, "mode", "local")

    def add(role: str) -> tuple[TestClient, str]:
        name = f"{role}-{uuid.uuid4().hex[:8]}"
        api.USERS.add(name, role, PASSWORD)
        client = TestClient(api.app)
        assert client.post("/api/auth/login", json={"name": name, "password": PASSWORD}).status_code == 200
        return client, name

    return add


def test_passwords_are_scrypt_hashes():
    stored = hash_password(PASSWORD)
    assert stored.startswith("scrypt$16384$8$1$") and PASSWORD not in stored
    assert verify_password(PASSWORD, stored) and not verify_password("wrong password", stored)
    assert hash_password(PASSWORD) != stored  # a new salt each time
    assert not verify_password(PASSWORD, "garbage")


def test_names_roles_and_passwords_are_checked(tmp_path):
    users = Users(Store(tmp_path / "fides.db"))
    users.add("JNovak", "analyst", PASSWORD)
    assert users.check("jnovak", PASSWORD) == User("jnovak", "analyst")  # names are lower case
    for name, role, password in [("j novák", "analyst", PASSWORD), ("pdvorak", "boss", PASSWORD),
                                 ("pdvorak", "viewer", "short"), ("jnovak", "viewer", PASSWORD)]:  # fmt: skip
        with pytest.raises(AuthError):
            users.add(name, role, password)
    assert users.check("jnovak", "wrong password") is None and users.check("nobody", PASSWORD) is None
    users.set_disabled("jnovak", True)
    assert users.check("jnovak", PASSWORD) is None


def test_the_admin_account_without_and_with_a_password_in_env(tmp_path):
    users = Users(Store(tmp_path / "fides.db"))
    line = ensure_admin(users, AuthSettings(), tmp_path)
    note = (tmp_path / "admin-password.txt").read_text(encoding="utf-8")
    generated = note.split("password: ")[1].split("\n")[0]
    assert "admin-password.txt" in line and users.check("admin", generated) == User("admin", "admin")
    assert ensure_admin(users, AuthSettings(), tmp_path) is None  # created once

    settings = AuthSettings(admin_password=SecretStr("from the .env file"))
    users.set_disabled("admin", True)
    ensure_admin(users, settings, tmp_path)  # applied at every start: resets the password and enables the account
    assert users.check("admin", "from the .env file") and users.check("admin", generated) is None

    sessions = Sessions(users.store)
    token = sessions.start(User("admin", "admin"))
    assert ensure_admin(users, settings, tmp_path) is None  # a restart with the same password keeps the sessions
    assert sessions.user(token, settings) == User("admin", "admin")


def test_sessions_expire_and_end(tmp_path):
    store = Store(tmp_path / "fides.db")
    Users(store).add("jnovak", "viewer", PASSWORD)
    sessions, settings = Sessions(store), AuthSettings(idle_minutes=30)
    token = sessions.start(User("jnovak", "viewer"))
    assert sessions.user(token, settings) == User("jnovak", "viewer") and sessions.user("forged", settings) is None
    with store.connect() as db:
        db.execute("UPDATE sessions SET seen_at = ?", (time.time() - 31 * 60,))
    assert sessions.user(token, settings) is None  # idle too long
    token = sessions.start(User("jnovak", "viewer"))
    Users(store).set_password("jnovak", "another long password")
    assert sessions.user(token, settings) is None  # a new password signs out everywhere
    with store.connect() as db:
        assert token not in {r[0] for r in db.execute("SELECT token FROM sessions")}  # only the hash is stored


def test_every_endpoint_needs_a_signed_in_user(local):
    client = TestClient(api.app)
    assert client.get("/api/auth/me").json() == {"mode": "local", "user": None}
    assert client.get("/api/health").status_code == 200
    for route in api.app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api/") or path.startswith(("/api/auth/", "/api/health", "/api/docs", "/api/openapi")):
            continue
        for method in route.methods - {"HEAD"}:
            url = path.replace("{result_id}", "mondi-2025").replace("{page}", "20").replace("{ico}", "26161516")
            assert client.request(method, url).status_code == 401, (method, path)


def test_roles(local, tmp_path):
    viewer, _ = local("viewer")
    analyst, analyst_name = local("analyst")
    assert viewer.get("/api/results/mondi-2025").status_code == 200
    assert viewer.get("/api/results/mondi-2025/report.pdf").status_code == 200
    correction = {"key": "total_assets", "period": "current", "value": 29_457_798_000}
    assert viewer.post("/api/results/mondi-2025/corrections", json=correction).status_code == 403

    pdf = make_digital_pdf(tmp_path / "12345679_2025_statement.pdf")
    with pdf.open("rb") as f:
        assert viewer.post("/api/analyse", files={"file": (pdf.name, f, "application/pdf")}).status_code == 403
    with pdf.open("rb") as f:
        view = analyst.post("/api/analyse", files={"file": (pdf.name, f, "application/pdf")}).json()
    assert view["owner"] == analyst_name and view["audit"]["user"] == analyst_name
    fixed = analyst.post(f"/api/results/{view['id']}/corrections", json=correction | {"value": 1_000_000_000}).json()
    assert fixed["audit"]["corrections"][-1]["by"] == analyst_name

    other, _ = local("analyst")
    admin, _ = local("admin")
    assert other.delete(f"/api/results/{view['id']}").status_code == 403  # not its owner
    assert admin.delete(f"/api/results/{view['id']}").status_code == 200
    assert analyst.get(f"/api/results/{view['id']}").status_code == 404


def test_own_results_only(local, monkeypatch, tmp_path):
    monkeypatch.setattr(api.SETTINGS.storage, "visibility", "own")
    first, first_name = local("analyst")
    second, _ = local("analyst")
    admin, _ = local("admin")
    pdf = make_digital_pdf(tmp_path / "12345679_2025_statement.pdf")
    with pdf.open("rb") as f:
        view = first.post("/api/analyse", files={"file": (pdf.name, f, "application/pdf")}).json()
    assert view["id"] in {r["id"] for r in first.get("/api/results").json()}
    assert view["id"] not in {r["id"] for r in second.get("/api/results").json()}
    assert second.get(f"/api/results/{view['id']}").status_code == 404
    assert admin.get(f"/api/results/{view['id']}").json()["owner"] == first_name
    assert second.get("/api/results/mondi-2025").status_code == 200  # samples are everyone's


def test_wrong_passwords_block_the_name_and_sign_out_ends_the_session(local):
    client, name = local("viewer")
    assert client.get("/api/auth/me").json()["user"] == {"name": name, "role": "viewer"}
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/results").status_code == 401

    stranger = TestClient(api.app)
    for _ in range(api.SETTINGS.auth.max_failures):
        assert stranger.post("/api/auth/login", json={"name": name, "password": "guess guess"}).status_code == 401
    blocked = stranger.post("/api/auth/login", json={"name": name, "password": PASSWORD})
    assert blocked.status_code == 429 and "try again" in blocked.json()["detail"]
    api.THROTTLE.clear(name)
    response = stranger.post("/api/auth/login", json={"name": name, "password": PASSWORD})
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "secure" not in cookie  # plain HTTP in the test


def test_without_sign_in_nothing_changes():
    client = TestClient(api.app)
    assert client.get("/api/auth/me").json() == {"mode": "none", "user": {"name": None, "role": "admin"}}
    assert client.get("/api/results/mondi-2025").status_code == 200
    assert client.post("/api/auth/login", json={"name": "x", "password": "y"}).status_code == 400


def test_the_users_script(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(api.SETTINGS.storage, "path", tmp_path / "fides.db")
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(PASSWORD + "\n"))
    assert users_cli(["add", "jnovak", "--role", "analyst", "--password-stdin"]) == 0
    assert users_cli(["role", "jnovak", "admin"]) == 0
    assert users_cli(["disable", "jnovak"]) == 0
    assert users_cli(["list"]) == 0
    out = capsys.readouterr().out
    assert "jnovak" in out and "admin" in out and "disabled" in out
    assert users_cli(["role", "nobody", "admin"]) == 1
