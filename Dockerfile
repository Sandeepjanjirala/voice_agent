# syntax=docker/dockerfile:1

# LiveKit Cloud worker only. Django/Celery are built with deploy/Dockerfile.web.
# The glibc base and fixed start command follow LiveKit's container requirements:
# https://docs.livekit.io/deploy/agents/builds/
ARG PYTHON_VERSION=3.12
FROM python:${PYTHON_VERSION}-slim-trixie AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.9 /uv /bin/uv
ENV PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy \
    HF_HOME=/app/.cache/huggingface TORCH_HOME=/app/.cache/torch
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-default-groups --group voice --no-install-project
# Download only installed voice-plugin assets, once at build time. No credentials
# or provider inference calls are used. The backend never contains these assets.
RUN .venv/bin/python -m livekit.agents download-files
COPY voice_agent ./voice_agent
COPY dialing ./dialing

FROM python:${PYTHON_VERSION}-slim-trixie
LABEL io.vaani.application="voice-agent" io.vaani.component="voice-worker"
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PATH="/app/.venv/bin:$PATH" \
    HF_HOME=/app/.cache/huggingface TORCH_HOME=/app/.cache/torch
ARG UID=10001
RUN adduser --disabled-password --gecos "" --home /app --shell /sbin/nologin \
    --uid "${UID}" appuser
# Refresh Debian security fixes, then remove unused mount/account-management
# tools after creating the runtime user. Apt retains Essential/protected packages
# and all required native libraries; keep dpkg metadata for vulnerability scans.
# Immutable runtimes never install dependencies. Remove pip and its ensurepip
# bootstrap archive as functionality, not just scanner metadata; the build stage
# retains its installers and the locked application environment is unchanged.
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && apt-get purge -y --auto-remove mount passwd \
    && apt-get check \
    && python -m pip uninstall -y pip \
    && python -c "import ensurepip, pathlib, shutil, sysconfig; bootstrap = pathlib.Path(ensurepip.__file__).resolve().parent; stdlib = pathlib.Path(sysconfig.get_path('stdlib')).resolve(); assert bootstrap == stdlib / 'ensurepip' and bootstrap.is_dir(); shutil.rmtree(bootstrap)" \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build --chown=appuser:appuser /app /app
WORKDIR /app
USER appuser
CMD ["python", "-m", "voice_agent.agent", "start"]
