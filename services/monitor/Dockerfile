FROM ghcr.io/astral-sh/uv:0.9.26-python3.12-bookworm-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY services/monitor/pyproject.toml services/monitor/uv.lock ./
COPY services/monitor/src ./src

RUN uv sync --frozen --no-dev

EXPOSE 8080
ENV PORT=8080
CMD ["sh", "-c", "uv run --frozen uvicorn monitor.app:app --host 0.0.0.0 --port $PORT"]
