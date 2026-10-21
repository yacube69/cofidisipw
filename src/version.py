"""Which code produced a result (CIPW-45): package version, git commit and the versions of the tools it ran.

The Docker image has no .git folder: the build passes the commit as FIDES_GIT_COMMIT (see Dockerfile).
"""

from __future__ import annotations

import hashlib
import os
import platform
import subprocess
import tomllib
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@lru_cache(maxsize=1)
def package_version() -> str:
    try:
        return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    except (OSError, KeyError):
        return "unknown"


def _git(*args: str) -> str | None:
    try:
        out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=5, check=True)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


@lru_cache(maxsize=1)
def git_info() -> dict[str, str | bool | None]:
    """commit (full hash), describe (`v1.0.0`, `v1.0.0-3-gabc1234` or a short hash), dirty (uncommitted changes)."""
    if env := os.environ.get("FIDES_GIT_COMMIT"):
        return {"commit": env, "describe": os.environ.get("FIDES_GIT_DESCRIBE") or env[:7], "dirty": False}
    commit = _git("rev-parse", "HEAD")
    if commit is None:
        return {"commit": None, "describe": None, "dirty": None}
    status = _git("status", "--porcelain", "--untracked-files=no")
    return {"commit": commit, "describe": _git("describe", "--tags", "--always"), "dirty": bool(status)}


@lru_cache(maxsize=1)
def tesseract_version() -> str | None:
    try:
        import pytesseract

        return str(pytesseract.get_tesseract_version())
    except Exception:  # not installed: the result says so instead of failing
        return None


def python_version() -> str:
    return platform.python_version()


def file_version(path: Path) -> str:
    """Short content hash of a file (dictionary, prompt): the same text gives the same version."""
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]
