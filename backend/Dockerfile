# syntax=docker/dockerfile:1.6
#
# One image, three roles (selected by the compose `command`):
#   * api            – uvicorn app.main:app
#   * worker         – python -m app.workers.main           (WORKER_CAPABILITIES=general)
#   * browser-worker – python -m app.workers.main           (WORKER_CAPABILITIES=browser,general)
#
# The Playwright base image ships Chromium plus every system library it needs, so the
# browser worker works out of the box. The API never launches browsers (EMBEDDED_WORKER=false).
ARG PLAYWRIGHT_VERSION=1.49.1
FROM mcr.microsoft.com/playwright/python:v${PLAYWRIGHT_VERSION}-noble AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DATA_DIR=/data

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir "playwright==${PLAYWRIGHT_VERSION}" \
    && python -m playwright install chromium

COPY alembic.ini pyproject.toml ./
COPY alembic ./alembic
COPY app ./app

# Run as the unprivileged user provided by the Playwright image.
RUN mkdir -p /data && chown -R pwuser:pwuser /app /data
USER pwuser

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=40s --retries=5 \
  CMD python -c "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
