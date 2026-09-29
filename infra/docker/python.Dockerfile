# syntax=docker/dockerfile:1.7
# One image for the API, the investigation worker and the production simulator (different commands).

FROM python:3.12-slim AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY pyproject.toml README.md ./
COPY src ./src
# Optional corporate/proxy CA via BuildKit secret (never stored in a layer): BUILD_CA_BUNDLE=/path/ca.crt
RUN --mount=type=secret,id=build_ca \
    if [ -s /run/secrets/build_ca ]; then export PIP_CERT=/run/secrets/build_ca; fi; \
    pip install ".[llm]"

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/opt/venv/bin:$PATH"
RUN groupadd --system --gid 10001 veriops && useradd --system --uid 10001 --gid veriops --no-create-home veriops
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY --chown=veriops:veriops alembic.ini ./
COPY --chown=veriops:veriops migrations ./migrations
COPY --chown=veriops:veriops knowledge ./knowledge
COPY --chown=veriops:veriops evals ./evals
COPY --chown=veriops:veriops src ./src
# Installed package resolves knowledge/ and evals/ relative to the source tree.
ENV PYTHONPATH=/app/src
USER veriops
EXPOSE 8000 8100
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=5 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen(os.environ.get('HEALTHCHECK_URL','http://127.0.0.1:8000/health'), timeout=2)" || exit 1
CMD ["uvicorn", "veriops.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
