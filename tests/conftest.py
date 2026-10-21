import os
import tempfile

# Runs at import time so skipif conditions in tests already see the configured Tesseract (src/settings.py).
# Test runs write their run logs to a temporary folder, not to outputs/logs.
os.environ.setdefault("FIDES_PATHS__LOGS", os.path.join(tempfile.gettempdir(), "fides-test-logs"))

from src.settings import get_settings  # noqa: E402

get_settings().apply_tesseract()
