FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

# Build dependencies once; source changes reuse this stage.
FROM base AS dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*
RUN python -m venv /opt/venv
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt

# Keep compilers and download caches out of the runtime image.
FROM base AS runtime
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    libpq5 \
    libstdc++6 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 appuser \
    && install -d -o appuser -g appuser /app/.cache/huggingface

ENV HF_HOME=/app/.cache/huggingface
COPY --from=dependencies /opt/venv /opt/venv
COPY --chown=appuser:appuser api/ ./api/
COPY --chown=appuser:appuser config/ ./config/
COPY --chown=appuser:appuser core/ ./core/
COPY --chown=appuser:appuser cli.py ./cli.py

USER appuser
EXPOSE 8000
HEALTHCHECK --interval=360s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]


# docker build --progress=plain -t lassy:docker-optimized .