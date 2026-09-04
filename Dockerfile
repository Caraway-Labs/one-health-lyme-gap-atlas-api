ARG TYPST_VERSION=0.15.1

FROM debian:bookworm-slim AS typst
ARG TYPST_VERSION
RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates curl xz-utils \
    && rm -rf /var/lib/apt/lists/*
RUN case "$(dpkg --print-architecture)" in \
      amd64) asset="typst-x86_64-unknown-linux-musl.tar.xz"; checksum="a6d077d0a95eed5a2eba715b2dae06be954f624ccbf85758a03f389ded33118c" ;; \
      arm64) asset="typst-aarch64-unknown-linux-musl.tar.xz"; checksum="5aa8d74a3d906e60ea12a66ac2f37f8eef1b14cbad7182a745e393a10c23dcee" ;; \
      *) echo "Unsupported Typst architecture" >&2; exit 1 ;; \
    esac \
    && curl --fail --location --retry 3 --output /tmp/typst.tar.xz "https://github.com/typst/typst/releases/download/v${TYPST_VERSION}/${asset}" \
    && echo "${checksum}  /tmp/typst.tar.xz" | sha256sum --check --status \
    && mkdir /opt/typst \
    && tar --extract --xz --file /tmp/typst.tar.xz --directory /opt/typst --strip-components=1 \
    && /opt/typst/typst --version | grep --fixed-strings "typst ${TYPST_VERSION}"

FROM ghcr.io/astral-sh/uv:0.8-python3.12-bookworm-slim AS builder
WORKDIR /app
RUN apt-get update \
    && apt-get install --yes --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --locked --no-dev

FROM python:3.12-slim-bookworm
ARG TYPST_VERSION
ENV PYTHONUNBUFFERED=1 PATH="/app/.venv/bin:$PATH" TYPST_VERSION=${TYPST_VERSION}
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY --from=typst /opt/typst/typst /usr/local/bin/typst
COPY src ./src
RUN typst --version | grep --fixed-strings "typst ${TYPST_VERSION}"
EXPOSE 8080
CMD ["uvicorn", "lyme_gap_atlas_api.app:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips", "*"]
