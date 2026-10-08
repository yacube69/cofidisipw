"""Line-item dictionary (config/line_items.yaml): the list of schema fields and how they are printed."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATH = ROOT / "config" / "line_items.yaml"

Statement = Literal["balance_sheet", "income_statement"]
Side = Literal["assets", "liabilities"]


class LineDef(BaseModel):
    key: str
    statement: Statement
    side: Side | None = None
    code: str = ""
    row: str | None = None
    labels: list[str] = Field(min_length=1)
    abbreviated: bool = True
    en: str = ""

    @property
    def label(self) -> str:
        return self.labels[0]


class LineDictionary(BaseModel):
    version: int
    lines: list[LineDef]

    def for_statement(self, statement: Statement, side: Side | None = None) -> list[LineDef]:
        return [d for d in self.lines if d.statement == statement and (side is None or d.side == side)]

    def get(self, key: str) -> LineDef:
        for d in self.lines:
            if d.key == key:
                return d
        raise KeyError(key)

    def keys(self, statement: Statement) -> list[str]:
        return [d.key for d in self.for_statement(statement)]


@lru_cache(maxsize=4)
def load_dictionary(path: Path = DEFAULT_PATH) -> LineDictionary:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    lines = []
    for statement in ("balance_sheet", "income_statement"):
        for item in raw.get(statement) or []:
            lines.append(LineDef(statement=statement, **item))
    keys = [d.key for d in lines]
    duplicates = {k for k in keys if keys.count(k) > 1}
    if duplicates:
        raise ValueError(f"Duplicate keys in {path}: {sorted(duplicates)}")
    return LineDictionary(version=raw["version"], lines=lines)
