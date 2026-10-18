FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HOME=/app/.cache/huggingface

WORKDIR /app
# Only runtime libraries; every Python dependency must have a binary wheel.
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    libgomp1 \
    libstdc++6 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 appuser \
    && install -d -o appuser -g appuser /app/.cache/huggingface

# Source-only changes reuse installed dependencies. Downloads remain in BuildKit.
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --only-binary=:all: --no-compile -r requirements.txt

COPY --chown=appuser:appuser api/ ./api/
COPY --chown=appuser:appuser config/ ./config/
COPY --chown=appuser:appuser core/ ./core/
COPY --chown=appuser:appuser cli.py ./cli.py

USER appuser
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]

# docker build --progress=plain -t lassy:docker-optimized .
