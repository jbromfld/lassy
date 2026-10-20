# Pin tool and base image versions and digests to keep image contents reproducible.
FROM ghcr.io/astral-sh/uv:0.13.1@sha256:4a1f3666816dafadd20ceb71eac9a7d56d7ef30279f7d64048519ab901ada336 AS uv
FROM python:3.13.16-slim-bookworm@sha256:a1165e272e578941b84abc79e4ab38a0305cd12803a5c4247979ac7655f4d641

# Copy only the uv executable from its image into the Python runtime image.
COPY --from=uv /uv /usr/local/bin/uv
# Disable bytecode files and log buffering, use the image's Python interpreter,
# and copy installed packages because the uv cache lives on a separate mount.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy
WORKDIR /app

# Runtime libraries are inherited from the immutable Debian base (libstdc++).
# Pin the additional OpenMP runtime, rather than resolving the current apt candidate.
# Remove apt metadata after installation to reduce image size, then create an
# unprivileged user and a writable Hugging Face cache for downloaded model files.
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1=12.2.0-14+deb12u1 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 appuser \
    && install -d -o appuser -g appuser /app/.cache/huggingface

    COPY pyproject.toml uv.lock ./

# Install production dependencies from the current lockfile using prebuilt wheels.
# BuildKit retains uv's download cache between builds without adding it to the
# image; copying application code afterward lets source edits reuse this layer.
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --no-build
# Give the runtime user ownership of the application source files.
COPY --chown=appuser:appuser api/ ./api/
COPY --chown=appuser:appuser config/ ./config/
COPY --chown=appuser:appuser core/ ./core/
COPY --chown=appuser:appuser cli.py ./cli.py

# Use executables from uv's virtual environment and the writable model cache.
ENV PATH="/app/.venv/bin:$PATH" HF_HOME=/app/.cache/huggingface
# Run the API without root privileges; document its container-side listening port.
USER appuser
EXPOSE 8000

# Probe the API with Python's standard library, allowing time for startup before
# failed probes count toward marking the container unhealthy.
HEALTHCHECK --interval=10s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1
# Listen on all container interfaces so published ports can reach the API.
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
