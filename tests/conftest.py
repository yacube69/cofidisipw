import os

from dotenv import load_dotenv

# Runs at import time so skipif conditions in tests already see TESSERACT_CMD.
load_dotenv()
if os.getenv("TESSERACT_CMD"):
    import pytesseract

    pytesseract.pytesseract.tesseract_cmd = os.environ["TESSERACT_CMD"]
