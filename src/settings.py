"""All configuration in one place (CIPW-45): `config/fides.yaml`, overridden by `.env` and environment variables.

Precedence, highest first:
    1. environment variables with the FIDES_ prefix, nested with "__":  FIDES_OFFLINE=true, FIDES_OCR__DPI=400
    2. the same names in .env
    3. the familiar names, from the environment or .env: ANTHROPIC_API_KEY, LLM_MODEL, TESSERACT_CMD, TESSDATA_PREFIX
    4. config/fides.yaml (committed, no secrets)
    5. the defaults below

Secrets (the API key) come only from the environment or .env, never from the YAML, and never appear in
`Settings.public()`, the settings hash, a `Result` or a log.

    from src.settings import get_settings
    settings = get_settings()
    settings.paths.raw, settings.ocr.config(), settings.llm_enabled
"""

from __future__ import annotations

import hashlib
import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from dotenv import dotenv_values
from pydantic import BaseModel, Field, SecretStr, field_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

ROOT = Path(__file__).resolve().parents[1]
CONFIG_FILE = ROOT / "config" / "fides.yaml"
ENV_FILE = ROOT / ".env"


class Paths(BaseModel):
    """Folders and files. Relative paths are relative to the repository root (/workspace in the Docker image)."""

    raw: Path = Path("data/raw")
    ground_truth: Path = Path("data/ground_truth")
    fixtures: Path = Path("data/fixtures")
    outputs: Path = Path("outputs")
    logs: Path = Path("outputs/logs")
    dictionary: Path = Path("config/line_items.yaml")
    prompts: Path = Path("prompts")

    @field_validator("*", mode="after")
    @classmethod
    def _absolute(cls, value: Path) -> Path:
        return value if value.is_absolute() else ROOT / value


class OcrSettings(BaseModel):
    """The reading-stage OCR (CIPW-25); same fields as `src.extraction.ocr.OcrConfig`."""

    dpi: int = Field(300, ge=72, le=600)
    lang: str = "ces"
    psm: int = 6
    remove_shading: bool = True
    deskew: bool = True
    binarize: Literal["none", "otsu", "adaptive", "fixed"] = "adaptive"
    threshold: int = 150
    block: int = 31
    offset: int = 15
    median: int = 0
    grey_profile: bool = True
    remove_lines: bool = True
    tessdata: str = Field("", description="Folder with the Czech model; '' = the one installed with Tesseract.")

    def config(self):
        from src.extraction.ocr import OcrConfig  # local import: settings must not depend on OpenCV at import

        values = self.model_dump()
        if values["tessdata"] and not Path(values["tessdata"]).is_absolute():
            values["tessdata"] = str(ROOT / values["tessdata"])  # relative to the repository, like `paths`
        return OcrConfig(**values)


class RereadSettings(BaseModel):
    """Targeted re-read of the cells the accounting checks doubt (CIPW-11)."""

    enabled: bool = True
    dpi: int = Field(400, ge=150, le=600, description="Resolution of the cell crops.")


class PerformanceSettings(BaseModel):
    """How pages are read side by side (CIPW-50, src/extraction/parallel.py)."""

    ocr_workers: int = Field(0, ge=0, description="Pages OCR'd at the same time; 0 = one per CPU core, 1 = one by one.")
    tesseract_threads: int = Field(1, ge=0, description="Threads per Tesseract process when pages run side by side "
                                   "(OMP_THREAD_LIMIT); 0 = Tesseract's own default.")
    light_locator: bool = Field(True, description="Scans: find the statements from the top of each page first and "
                                "read whole pages only where needed; the full read is the fallback.")


class TesseractSettings(BaseModel):
    cmd: str | None = Field(None, description="Full path to the tesseract binary when it is not on PATH.")
    tessdata_prefix: str | None = Field(None, description="Folder with *.traineddata (Czech without admin rights).")


class LlmSettings(BaseModel):
    provider: Literal["anthropic"] = "anthropic"
    model: str = "claude-sonnet-5-5"
    max_cost_usd: float = Field(0.50, gt=0, description="Upper limit for one AI report; the report stops above it.")
    api_key: SecretStr | None = Field(None, description="From ANTHROPIC_API_KEY in the environment or .env only.")


class ApiSettings(BaseModel):
    max_upload_mb: int = Field(60, gt=0)
    max_results: int = Field(20, gt=0, description="Analysed files kept in memory by the API.")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FIDES_",
        env_nested_delimiter="__",
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        yaml_file=CONFIG_FILE,
        yaml_file_encoding="utf-8",
    )

    offline: bool = Field(False, description="No LLM call at all: the AI report step is skipped.")
    log_runs: bool = Field(True, description="Write a JSON-lines log of every pipeline run to paths.logs.")
    paths: Paths = Field(default_factory=Paths)
    ocr: OcrSettings = Field(default_factory=OcrSettings)
    reread: RereadSettings = Field(default_factory=RereadSettings)
    performance: PerformanceSettings = Field(default_factory=PerformanceSettings)
    tesseract: TesseractSettings = Field(default_factory=TesseractSettings)
    llm: LlmSettings = Field(default_factory=LlmSettings)
    api: ApiSettings = Field(default_factory=ApiSettings)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            _FamiliarNames(settings_cls),
            _YamlWithoutSecrets(settings_cls),
        )

    @property
    def llm_enabled(self) -> bool:
        """An LLM may be called: not offline and an API key is set."""
        return not self.offline and self.llm.api_key is not None and bool(self.llm.api_key.get_secret_value())

    def public(self) -> dict[str, Any]:
        """Every setting except secrets, with paths relative to the repository root where possible."""
        data = self.model_dump(mode="json", exclude={"llm": {"api_key"}})
        for key, value in data["paths"].items():
            path = Path(value)
            data["paths"][key] = path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else path.as_posix()
        return data

    def result_settings(self) -> dict[str, Any]:
        """The settings that change a result (OCR, LLM, offline). Paths, the Tesseract location and API limits
        differ per computer without changing a single number, so they stay out of the hash."""
        public = self.public()
        return {"offline": public["offline"], "ocr": public["ocr"], "llm": public["llm"]}

    def hash(self) -> str:
        """Short fingerprint of `result_settings()`: two results with the same hash used the same configuration."""
        return hashlib.sha256(json.dumps(self.result_settings(), sort_keys=True).encode()).hexdigest()[:12]

    def apply_tesseract(self) -> None:
        """Point pytesseract (and Tesseract's data lookup) at the configured binary and language data."""
        import pytesseract

        if self.tesseract.cmd:
            pytesseract.pytesseract.tesseract_cmd = self.tesseract.cmd
        if self.tesseract.tessdata_prefix:
            os.environ["TESSDATA_PREFIX"] = self.tesseract.tessdata_prefix
        if self.ocr.tessdata:
            # the models shipped with Fides (models/tessdata: ces for the pages, eng for the digits of the re-read)
            # are used on every machine; an environment variable takes a path with spaces, --tessdata-dir does not
            folder = Path(self.ocr.tessdata)
            os.environ["TESSDATA_PREFIX"] = str(folder if folder.is_absolute() else ROOT / folder)


class _FamiliarNames(PydanticBaseSettingsSource):
    """ANTHROPIC_API_KEY, LLM_MODEL, TESSERACT_CMD and TESSDATA_PREFIX: the names in .env.example, the README and
    every tool's documentation. The environment wins over .env."""

    NAMES = {
        "ANTHROPIC_API_KEY": ("llm", "api_key"),
        "LLM_MODEL": ("llm", "model"),
        "TESSERACT_CMD": ("tesseract", "cmd"),
        "TESSDATA_PREFIX": ("tesseract", "tessdata_prefix"),
    }

    def get_field_value(self, field, field_name):  # required by the base class, unused
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        env_file = self.settings_cls.model_config.get("env_file")
        file_values = dotenv_values(env_file) if env_file and Path(env_file).exists() else {}
        out: dict[str, dict[str, str]] = {}
        for name, (section, key) in self.NAMES.items():
            value = os.environ.get(name) or file_values.get(name)
            if value:
                out.setdefault(section, {})[key] = value
        return out


class _YamlWithoutSecrets(YamlConfigSettingsSource):
    """config/fides.yaml is committed: an API key there is refused instead of silently used."""

    def __call__(self) -> dict[str, Any]:
        data = super().__call__()
        if (data.get("llm") or {}).get("api_key"):
            raise ValueError(f"{CONFIG_FILE.name} must not contain llm.api_key; set ANTHROPIC_API_KEY in .env instead")
        return data


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reload_settings() -> Settings:
    """Read the configuration again (tests, or after editing config/fides.yaml in a running process)."""
    get_settings.cache_clear()
    return get_settings()
