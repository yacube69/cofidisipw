"""Sign-in (CIPW-62): the common layer of CIPW-59 and local accounts (docs/users.md).

`auth.mode: none`: no sign-in, as before (one computer, development, the tests); every request acts as an anonymous
admin. `auth.mode: local`: users kept by Fides in its database (src/storage.py), managed with scripts/users.py.

- Passwords are stored only as scrypt hashes (standard library). Checking one takes some tens of milliseconds and
  happens only at sign-in; a request after that costs one read of the session.
- A session is a random token in an HttpOnly, SameSite=Strict cookie; the database keeps only its SHA-256. It ends
  after `auth.idle_minutes` without a request, after `auth.max_hours` in any case, and at sign-out.
- After `auth.max_failures` wrong passwords for one name its sign-in is blocked for `auth.block_minutes`.
- One admin account, `auth.admin_name` (default "admin"), is created at the first start. Its password comes from
  FIDES_AUTH__ADMIN_PASSWORD in .env (applied at every start, so changing it there and restarting resets it), or is
  generated once and written next to the database (`admin-password.txt`). No password is ever in the code.

Roles: viewer (reads), analyst (also uploads and corrects), admin (also deletes stored results and manages users).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from src.settings import AuthSettings
from src.storage import Store

Role = Literal["viewer", "analyst", "admin"]
ROLES: tuple[Role, ...] = ("viewer", "analyst", "admin")
RANK = {role: i for i, role in enumerate(ROLES)}
COOKIE = "fides_session"
MIN_PASSWORD = 10
NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")
SCRYPT = {"n": 2**14, "r": 8, "p": 1}  # about 16 MB and some tens of ms per check
SEEN_EVERY = 60.0  # seconds: a session's last use is written at most this often


class AuthError(ValueError):
    """A user that cannot be created or changed: a bad name, role or password."""


@dataclass(frozen=True)
class User:
    name: str | None  # None without sign-in (auth.mode none)
    role: Role

    def may(self, role: Role) -> bool:
        return RANK[self.role] >= RANK[role]


ANONYMOUS = User(None, "admin")


# ---------------------------------------------------------------------------------------------------- passwords


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, dklen=32, maxmem=64 * 2**20, **SCRYPT)
    b64 = base64.b64encode
    return f"scrypt${SCRYPT['n']}${SCRYPT['r']}${SCRYPT['p']}${b64(salt).decode()}${b64(digest).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        kind, n, r, p, salt, digest = stored.split("$")
        if kind != "scrypt":
            return False
        expected = base64.b64decode(digest)
        actual = hashlib.scrypt(password.encode("utf-8"), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
                                dklen=len(expected), maxmem=64 * 2**20)  # fmt: skip
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


_DUMMY = hash_password(secrets.token_urlsafe(16))  # an unknown name takes as long to check as a known one


def check_new_password(password: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise AuthError(f"The password needs at least {MIN_PASSWORD} characters")


# -------------------------------------------------------------------------------------------------------- users


class Users:
    def __init__(self, store: Store):
        self.store = store

    @staticmethod
    def _name(name: str) -> str:
        name = name.strip().lower()
        if not NAME.match(name):
            raise AuthError("A name is 2–64 lower-case letters, digits, '.', '_' or '-', e.g. jnovak")
        return name

    @staticmethod
    def _role(role: str) -> Role:
        if role not in ROLES:
            raise AuthError(f"The role is one of {', '.join(ROLES)}")
        return role  # type: ignore[return-value]

    def add(self, name: str, role: str, password: str) -> None:
        name, role = self._name(name), self._role(role)
        check_new_password(password)
        with self.store.connect() as db:
            if db.execute("SELECT 1 FROM users WHERE name = ?", (name,)).fetchone():
                raise AuthError(f"The user {name} exists already")
            created = datetime.now().astimezone().isoformat(timespec="seconds")
            db.execute("INSERT INTO users (name, role, password, disabled, created_at) VALUES (?, ?, ?, 0, ?)",
                       (name, role, hash_password(password), created))  # fmt: skip

    def _update(self, name: str, column: str, value: object) -> None:
        with self.store.connect() as db:
            if db.execute(f"UPDATE users SET {column} = ? WHERE name = ?", (value, self._name(name))).rowcount == 0:
                raise AuthError(f"No user {name}")

    def set_password(self, name: str, password: str) -> None:
        check_new_password(password)
        self._update(name, "password", hash_password(password))
        Sessions(self.store).end_all(self._name(name))  # signed out everywhere

    def set_role(self, name: str, role: str) -> None:
        self._update(name, "role", self._role(role))

    def set_disabled(self, name: str, disabled: bool) -> None:
        self._update(name, "disabled", int(disabled))
        if disabled:
            Sessions(self.store).end_all(self._name(name))

    def exists(self, name: str) -> bool:
        with self.store.connect() as db:
            return db.execute("SELECT 1 FROM users WHERE name = ?", (self._name(name),)).fetchone() is not None

    def list(self) -> list[dict]:
        with self.store.connect() as db:
            rows = db.execute("SELECT name, role, disabled, created_at FROM users ORDER BY name").fetchall()
        return [dict(r) for r in rows]

    def check(self, name: str, password: str) -> User | None:
        """The user when the name and password are right and the account is enabled; None otherwise."""
        try:
            name = self._name(name)
        except AuthError:
            verify_password(password, _DUMMY)
            return None
        with self.store.connect() as db:
            row = db.execute("SELECT role, password, disabled FROM users WHERE name = ?", (name,)).fetchone()
        if row is None:
            verify_password(password, _DUMMY)
            return None
        if not verify_password(password, row["password"]) or row["disabled"]:
            return None
        return User(name, row["role"])


def ensure_admin(users: Users, settings: AuthSettings, folder: Path) -> str | None:
    """The one admin account (`auth.admin_name`). Its password from FIDES_AUTH__ADMIN_PASSWORD is applied at every
    start; without it, a missing admin gets a generated password, written once to `folder/admin-password.txt`.
    Returns a line for the server log, or None when nothing changed."""
    name = settings.admin_name
    given = settings.admin_password.get_secret_value() if settings.admin_password else ""
    if given:
        if users.exists(name):
            if users.check(name, given) != User(name, "admin"):  # changed, disabled or not admin: reset it
                users.set_password(name, given)  # (only then: a new password signs the admin out everywhere)
                users.set_role(name, "admin")
                users.set_disabled(name, False)
                return f"Fides: admin account {name!r} reset to the password from FIDES_AUTH__ADMIN_PASSWORD"
            return None
        users.add(name, "admin", given)
        return f"Fides: admin account {name!r} created with the password from FIDES_AUTH__ADMIN_PASSWORD"
    if users.exists(name):
        return None
    password = secrets.token_urlsafe(12)
    users.add(name, "admin", password)
    note = folder / "admin-password.txt"
    note.write_text(
        f"Fides admin account\nname: {name}\npassword: {password}\n\n"
        "Change it with `python scripts/users.py password admin`, or set FIDES_AUTH__ADMIN_PASSWORD in .env and\n"
        "restart, then delete this file.\n",
        encoding="utf-8",
    )
    try:
        note.chmod(0o600)
    except OSError:  # Windows: the folder's permissions apply
        pass
    return f"Fides: admin account {name!r} created; its password is in {note}"


# ----------------------------------------------------------------------------------------------------- sessions


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("ascii", "replace")).hexdigest()


class Sessions:
    def __init__(self, store: Store):
        self.store = store

    def start(self, user: User) -> str:
        token = secrets.token_urlsafe(32)
        now = time.time()
        with self.store.connect() as db:
            db.execute("INSERT INTO sessions (token, user, created_at, seen_at) VALUES (?, ?, ?, ?)",
                       (_digest(token), user.name, now, now))  # fmt: skip
        return token

    def user(self, token: str, settings: AuthSettings) -> User | None:
        """The signed-in user of a session token, or None when it is unknown, ended or expired, or the account is
        disabled. Costs one read; the time of use is written at most once a minute."""
        now = time.time()
        key = _digest(token)
        with self.store.connect() as db:
            row = db.execute(
                "SELECT s.created_at, s.seen_at, u.name, u.role, u.disabled FROM sessions s "
                "JOIN users u ON u.name = s.user WHERE s.token = ?",
                (key,),
            ).fetchone()
            if row is None:
                return None
            idle, age = now - row["seen_at"], now - row["created_at"]
            expired = idle > settings.idle_minutes * 60 or age > settings.max_hours * 3600
            if expired or row["disabled"]:
                db.execute("DELETE FROM sessions WHERE token = ?", (key,))
                return None
            if now - row["seen_at"] > SEEN_EVERY:
                db.execute("UPDATE sessions SET seen_at = ? WHERE token = ?", (now, key))
        return User(row["name"], row["role"])

    def end(self, token: str) -> None:
        with self.store.connect() as db:
            db.execute("DELETE FROM sessions WHERE token = ?", (_digest(token),))

    def end_all(self, name: str) -> None:
        with self.store.connect() as db:
            db.execute("DELETE FROM sessions WHERE user = ?", (name,))

    def purge(self, settings: AuthSettings) -> None:
        """Delete expired sessions (at start)."""
        now = time.time()
        with self.store.connect() as db:
            db.execute("DELETE FROM sessions WHERE seen_at < ? OR created_at < ?",
                       (now - settings.idle_minutes * 60, now - settings.max_hours * 3600))  # fmt: skip


# ----------------------------------------------------------------------------------------------------- blocking


class Throttle:
    """Wrong passwords per name in memory: after `max_failures` within `block_minutes` the name is blocked for that
    long. A restart clears it, which is fine for one server process."""

    def __init__(self):
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def blocked_for(self, name: str, settings: AuthSettings) -> int:
        """Seconds until this name may try again; 0 when it may now."""
        window = settings.block_minutes * 60
        now = time.time()
        with self._lock:
            recent = [t for t in self._failures.get(name.lower(), []) if now - t < window]
            self._failures[name.lower()] = recent
            if len(recent) < settings.max_failures:
                return 0
            return int(window - (now - recent[-settings.max_failures])) + 1

    def failed(self, name: str) -> None:
        with self._lock:
            self._failures.setdefault(name.lower(), []).append(time.time())

    def clear(self, name: str) -> None:
        with self._lock:
            self._failures.pop(name.lower(), None)
