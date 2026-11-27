# syntax=docker/dockerfile:1

# LiveKit Cloud worker only. Django/Celery are built with deploy/Dockerfile.web.
# The glibc base and fixed start command follow LiveKit's container requirements:
# https://docs.livekit.io/deploy/agents/builds/
ARG PYTHON_VERSION=3.12
FROM python:${PYTHON_VERSION}-slim-bookworm AS build
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

FROM python:${PYTHON_VERSION}-slim-bookworm
# Apply the available Bookworm PAM security fix even when the base image lags.
RUN apt-get update \
    && apt-get install -y --no-install-recommends --only-upgrade \
        libpam0g libpam-modules libpam-modules-bin libpam-runtime \
    && dpkg --compare-versions "$(dpkg-query -W -f='${Version}' libpam0g)" ge '1.5.2-6+deb12u3' \
    && rm -rf /var/lib/apt/lists/*
LABEL io.vaani.application="voice-agent" io.vaani.component="voice-worker"
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PATH="/app/.venv/bin:$PATH" \
    HF_HOME=/app/.cache/huggingface TORCH_HOME=/app/.cache/torch
ARG UID=10001
RUN adduser --disabled-password --gecos "" --home /app --shell /sbin/nologin \
    --uid "${UID}" appuser
COPY --from=build --chown=appuser:appuser /app /app
WORKDIR /app
USER appuser
CMD ["python", "-m", "voice_agent.agent", "start"]
