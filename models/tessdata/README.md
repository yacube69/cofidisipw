# Tesseract models shipped with Fides

Used on every machine (Windows, CI, the Docker image) through `ocr.tessdata: models/tessdata` in `config/fides.yaml`, so a PDF is read the same everywhere (CIPW-25, `docs/ocr.md`).

| File | What | Source | SHA-256 |
| --- | --- | --- | --- |
| `ces.traineddata` | Czech, the page OCR and the page locator | official `tesseract-ocr/tessdata` (main), 16 238 266 bytes | `ce861ed1dc20e74db449c4636d44a83042cd2aeb8cd95c9008293b5d0f79023a` |
| `eng.traineddata` | English, only for the digits of the re-read of doubted cells (CIPW-11) | installed with Tesseract 5.4 (UB Mannheim build for Windows), 4 113 088 bytes | `7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2` |

License: Apache 2.0, like Tesseract (https://github.com/tesseract-ocr/tessdata).
