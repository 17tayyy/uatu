# syntax=docker/dockerfile:1
FROM python:3.14-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.9.26 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --no-install-project
COPY README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --no-editable

FROM python:3.14-slim
# rasterio's wheels bundle GDAL but expect the system's expat; pg_dump (17, like the server) for backups.
RUN apt-get update && apt-get install -y --no-install-recommends libexpat1 postgresql-client \
    && rm -rf /var/lib/apt/lists/*
RUN useradd --system --uid 10001 --no-create-home uatu \
    && mkdir -p /data/raw /data/sar /data/backups /data/web-cache /data/reports \
    && chown uatu /data/raw /data/sar /data/backups /data/web-cache /data/reports
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
# Alembic reads its config from pyproject.toml.
COPY pyproject.toml ./
COPY migrations ./migrations
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 MPLCONFIGDIR=/tmp/matplotlib
USER uatu
CMD ["uatu-recorder"]
