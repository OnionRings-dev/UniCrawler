FROM ghcr.io/astral-sh/uv:0.9.26-python3.12-bookworm-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY services/vectorizer/pyproject.toml services/vectorizer/uv.lock ./
COPY services/vectorizer/src ./src

RUN uv sync --frozen --no-dev

CMD ["uv", "run", "--frozen", "vectorizer"]
