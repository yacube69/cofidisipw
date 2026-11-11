import os
import tempfile

# Runs at import time so skipif conditions in tests already see the configured Tesseract (src/settings.py).
# Test runs write their run logs to a temporary folder, not to outputs/logs.
os.environ.setdefault("FIDES_PATHS__LOGS", os.path.join(tempfile.gettempdir(), "fides-test-logs"))
# No network in tests: company data comes from the dataset snapshot; tests of the lookup mock ARES (test_ares.py).
os.environ.setdefault("FIDES_ARES__ENABLED", "false")
os.environ.setdefault("FIDES_ARES__CACHE", tempfile.mkdtemp(prefix="fides-test-ares-"))  # not this computer's answers
# Tests never call the paid LLM API, whatever key this computer's .env has; LLM calls are mocked (test_llm.py).
os.environ["FIDES_LLM__API_KEY"] = ""
# Stored results, users and sessions of the test run go to a temporary database, not outputs/fides.db (CIPW-60).
os.environ.setdefault("FIDES_STORAGE__PATH", os.path.join(tempfile.mkdtemp(prefix="fides-test-db-"), "fides.db"))
# Tests run without sign-in whatever this computer's .env says; the sign-in tests switch it on (test_auth.py).
os.environ["FIDES_AUTH__MODE"] = "none"
os.environ["FIDES_AUTH__ADMIN_PASSWORD"] = ""

from src.settings import get_settings  # noqa: E402

get_settings().apply_tesseract()
