FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS build

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src/ src/
COPY schemas/ schemas/
RUN uv sync --frozen --no-dev

FROM python:3.12-slim-bookworm

RUN groupadd -r mesh && useradd -r -g mesh mesh
WORKDIR /app

COPY --from=build /app/.venv /app/.venv
COPY --from=build /app/src /app/src
COPY --from=build /app/schemas /app/schemas

RUN mkdir -p /data && chown mesh:mesh /data

USER mesh
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    MESH_ROUTER_PORT=8780 \
    MESH_DB_PATH=/data/router.db \
    MESH_BUFFER_PATH=/data/events-buffer.jsonl

EXPOSE 8780

HEALTHCHECK --interval=10s --timeout=3s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8780/health')"

CMD ["python", "-m", "src.router.server"]
