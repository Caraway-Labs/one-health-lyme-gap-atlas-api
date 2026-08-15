FROM ghcr.io/astral-sh/uv:0.8-python3.12-bookworm-slim AS builder
WORKDIR /app
RUN apt-get update \
    && apt-get install --yes --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --locked --no-dev

FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PATH="/app/.venv/bin:$PATH"
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY src ./src
EXPOSE 8080
CMD ["uvicorn", "lyme_gap_atlas_api.app:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips", "*"]
