# Fides: one container with the web app, the API and the pipeline (Tesseract with Czech data included).
#   docker build -t fides .
#   docker run --rm -p 8000:8000 fides          -> open http://localhost:8000

# 1) The web app (React + TypeScript in web/), built to static files
FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# 2) Python, Tesseract and the API, which also serves the built web app
FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-ces libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
COPY --from=web /web/dist web/dist

# The image has no .git folder: CI passes the commit so every result's audit block names the code (CIPW-45)
ARG GIT_COMMIT=""
ARG GIT_DESCRIBE=""
ENV FIDES_GIT_COMMIT=${GIT_COMMIT} FIDES_GIT_DESCRIBE=${GIT_DESCRIBE}

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health')"
CMD ["uvicorn", "src.api:app", "--host", "0.0.0.0", "--port", "8000"]
