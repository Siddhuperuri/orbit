# One image, two entrypoints: the API and the Celery worker run the same build
# with different commands (ADR-0001). The worker therefore carries FastAPI it
# does not use -- a few megabytes, in exchange for a guarantee that the two
# processes can never be running different code.

# ---------------------------------------------------------------------------
# Builder -- resolves and installs dependencies into a self-contained venv.
# ---------------------------------------------------------------------------
FROM python:3.11-slim-bookworm AS builder

COPY --from=ghcr.io/astral-sh/uv:0.12.3 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependency manifests are copied alone first so that the expensive install layer
# is cached and only re-runs when dependencies actually change -- not on every
# source edit.
COPY backend/pyproject.toml backend/uv.lock ./

# --frozen fails if uv.lock disagrees with pyproject.toml, so an image can never
# be built from a dependency set that no developer has tested.
# --no-install-project installs only third-party packages at this stage.
RUN uv sync --frozen --no-dev --no-install-project

COPY backend/ ./

# Now install the project itself into the same venv.
RUN uv sync --frozen --no-dev

# ---------------------------------------------------------------------------
# Runtime -- no build tools, no uv, no dev dependencies.
# ---------------------------------------------------------------------------
FROM python:3.11-slim-bookworm AS runtime

# curl is required by the container healthcheck below. Installed explicitly so
# that its presence is a decision rather than an accident of the base image.
RUN apt-get update \
    && apt-get install --no-install-recommends -y curl \
    && rm -rf /var/lib/apt/lists/*

# Runs as an unprivileged user. The worker parses untrusted documents, so a
# parser vulnerability must not land on a root shell (ADR-0002).
RUN groupadd --system --gid 1001 orbit \
    && useradd --system --uid 1001 --gid orbit --create-home orbit

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONFAULTHANDLER=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

COPY --from=builder --chown=orbit:orbit /app /app

USER orbit

EXPOSE 8000

# Liveness only -- it must not check dependencies, or a database blip would
# cause Docker to restart healthy containers (ADR-0015).
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
    CMD curl --fail --silent --show-error http://localhost:8000/healthz || exit 1

# Overridden by the worker service in docker-compose.
CMD ["uvicorn", "orbit.composition.asgi:app", "--host", "0.0.0.0", "--port", "8000"]
