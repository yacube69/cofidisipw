"""CIPW-45: one settings object, run log and audit block."""

import json
from pathlib import Path

import pytest

from src.pipeline import run_pipeline
from src.result import PipelineOptions
from src.runlog import read_log
from src.settings import CONFIG_FILE, ROOT, Settings, reload_settings
from tests.synthetic import make_digital_pdf

# names that would leak a key or a machine's paths into results and logs
FAMILIAR = ["ANTHROPIC_API_KEY", "LLM_MODEL", "TESSERACT_CMD", "TESSDATA_PREFIX"]


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    """Settings from config/fides.yaml only: no .env, no FIDES_ or familiar variables of this computer."""
    for name in [*FAMILIAR, *(k for k in __import__("os").environ if k.startswith("FIDES_"))]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setitem(Settings.model_config, "env_file", tmp_path / "missing.env")
    monkeypatch.setenv("FIDES_PATHS__LOGS", str(tmp_path / "logs"))
    yield monkeypatch
    monkeypatch.undo()  # restore the environment and config before reading the settings again
    reload_settings()


def test_defaults_come_from_the_yaml(clean_env):
    s = reload_settings()
    assert CONFIG_FILE.exists()
    assert s.ocr.dpi == 300 and s.ocr.binarize == "adaptive" and s.llm.provider == "anthropic"
    assert s.paths.raw == ROOT / "data" / "raw" and s.paths.dictionary.exists()
    assert not s.offline and not s.llm_enabled  # no key


def test_environment_overrides_the_yaml(clean_env):
    clean_env.setenv("FIDES_OCR__DPI", "400")
    clean_env.setenv("FIDES_OFFLINE", "true")
    clean_env.setenv("LLM_MODEL", "claude-opus-5-5")
    s = reload_settings()
    assert s.ocr.dpi == 400 and s.ocr.config().dpi == 400
    assert s.offline and s.llm.model == "claude-opus-5-5"


def test_the_api_key_never_leaves_the_settings(clean_env):
    clean_env.setenv("ANTHROPIC_API_KEY", "sk-ant-secret")
    s = reload_settings()
    assert s.llm_enabled and s.llm.api_key.get_secret_value() == "sk-ant-secret"
    assert "sk-ant-secret" not in json.dumps(s.public()) and "api_key" not in json.dumps(s.result_settings())
    assert "sk-ant-secret" not in repr(s)


def test_offline_disables_the_llm_even_with_a_key(clean_env):
    clean_env.setenv("ANTHROPIC_API_KEY", "sk-ant-secret")
    clean_env.setenv("FIDES_OFFLINE", "1")
    assert not reload_settings().llm_enabled


def test_hash_follows_result_settings_not_machine_paths(clean_env):
    base = reload_settings().hash()
    clean_env.setenv("TESSERACT_CMD", r"C:\Other\tesseract.exe")
    clean_env.setenv("FIDES_PATHS__OUTPUTS", "/elsewhere")
    assert reload_settings().hash() == base
    clean_env.setenv("FIDES_OCR__PSM", "4")
    assert reload_settings().hash() != base


def test_a_key_in_the_committed_yaml_is_refused(clean_env, tmp_path):
    bad = tmp_path / "fides.yaml"
    bad.write_text("llm:\n  api_key: sk-ant-oops\n", encoding="utf-8")
    clean_env.setitem(Settings.model_config, "yaml_file", bad)
    with pytest.raises(ValueError, match="must not contain llm.api_key"):
        Settings()


def test_every_result_has_the_audit_block_and_a_run_log(clean_env, tmp_path):
    reload_settings()
    result = run_pipeline(make_digital_pdf(tmp_path / "12345678_2025_statement.pdf"), PipelineOptions(skip_report=True))
    audit = result.audit
    assert audit is not None and len(audit.run_id) == 12
    assert audit.tool_version and audit.settings_hash and audit.dictionary_version.startswith("v1 ")
    assert audit.settings["ocr"]["dpi"] == 300 and audit.ocr_config.startswith("dpi=300")
    assert audit.finished_at >= audit.started_at and audit.model is None and audit.corrections == []
    # the JSON-lines log: one start, one line per stage, one end
    log = read_log(Path(audit.log_file) if Path(audit.log_file).is_absolute() else ROOT / audit.log_file)
    assert [e["event"] for e in log] == ["run_start"] + ["stage"] * len(result.stages) + ["run_end"]
    assert {e["run_id"] for e in log} == {audit.run_id}
    assert log[0]["audit"]["settings_hash"] == audit.settings_hash
    assert log[-1]["failed"] is False and log[-1]["values"] > 0 and "checks_failed" in log[-1]


def test_offline_mode_runs_without_an_api_key(clean_env, tmp_path):
    clean_env.setenv("FIDES_OFFLINE", "true")
    reload_settings()
    result = run_pipeline(make_digital_pdf(tmp_path / "12345678_2025_statement.pdf"))
    status = {s.stage: (s.status, s.note) for s in result.stages}
    assert status["report"] == ("skipped", "offline mode: no LLM call")
    assert status["check_numbers"][0] == "skipped"
    assert result.report is None and result.audit.offline and not result.failed
    assert result.statements.get("total_assets") == 1_000_000_000  # everything else still runs


def test_logging_can_be_switched_off(clean_env, tmp_path):
    clean_env.setenv("FIDES_LOG_RUNS", "false")
    reload_settings()
    result = run_pipeline(make_digital_pdf(tmp_path / "a.pdf"), PipelineOptions(skip_report=True))
    assert result.audit.log_file is None and not (tmp_path / "logs").exists()
