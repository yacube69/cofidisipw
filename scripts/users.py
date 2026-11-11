"""Local accounts of Fides (CIPW-62, docs/users.md): add, change and list the users of `auth.mode: local`.

    python scripts/users.py add jnovak --role analyst     # asks for the password twice
    python scripts/users.py password jnovak               # a new password; signs the user out everywhere
    python scripts/users.py role jnovak admin             # viewer | analyst | admin
    python scripts/users.py disable jnovak                # no sign-in any more (enable: the opposite)
    python scripts/users.py list

Inside the Docker container: `docker compose exec fides python scripts/users.py add jnovak --role analyst`.
`--password-stdin` reads the password from the input instead of asking (for scripts). The users are in the database
`storage.path` (default outputs/fides.db); the admin account is described in docs/users.md.
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.auth import MIN_PASSWORD, ROLES, AuthError, Users  # noqa: E402
from src.settings import get_settings  # noqa: E402
from src.storage import Store  # noqa: E402


def _password(from_stdin: bool) -> str:
    if from_stdin:
        return sys.stdin.readline().rstrip("\n")
    first = getpass.getpass(f"Password (at least {MIN_PASSWORD} characters): ")
    if getpass.getpass("Once more: ") != first:
        raise AuthError("The two passwords differ")
    return first


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    add = sub.add_parser("add", help="a new user")
    add.add_argument("name")
    add.add_argument("--role", choices=ROLES, default="analyst")
    add.add_argument("--password-stdin", action="store_true")
    pw = sub.add_parser("password", help="a new password")
    pw.add_argument("name")
    pw.add_argument("--password-stdin", action="store_true")
    role = sub.add_parser("role", help="change the role")
    role.add_argument("name")
    role.add_argument("role", choices=ROLES)
    for command in ("disable", "enable"):
        sub.add_parser(command, help=f"{command} a user").add_argument("name")
    sub.add_parser("list", help="all users")
    args = ap.parse_args(argv)

    settings = get_settings()
    users = Users(Store(settings.storage.path))
    try:
        if args.command == "add":
            users.add(args.name, args.role, _password(args.password_stdin))
            print(f"Added {args.name.lower()} ({args.role}).")
        elif args.command == "password":
            users.set_password(args.name, _password(args.password_stdin))
            print(f"New password for {args.name.lower()}; signed out everywhere.")
        elif args.command == "role":
            users.set_role(args.name, args.role)
            print(f"{args.name.lower()} is now {args.role}.")
        elif args.command in ("disable", "enable"):
            users.set_disabled(args.name, args.command == "disable")
            print(f"{args.name.lower()} {args.command}d.")
        else:
            rows = users.list()
            for u in rows:
                state = "disabled" if u["disabled"] else "active"
                print(f"{u['name']:<24} {u['role']:<8} {state:<9} {u['created_at']}")
            print(f"{len(rows)} users in {settings.storage.path}")
    except AuthError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if settings.auth.mode != "local":
        print("Note: auth.mode is 'none', so nobody needs to sign in yet (FIDES_AUTH__MODE=local).", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
