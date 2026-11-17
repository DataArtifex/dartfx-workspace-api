# =============================================================================
# Stage 1: Builder (uses uv for fast dependency resolution into /opt/venv)
# =============================================================================
FROM dartfx/docker-api-base:latest AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

WORKDIR /app

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY pyproject.toml ./
RUN uv pip install --no-cache -r pyproject.toml

COPY src ./src
RUN uv pip install --no-cache --no-deps .

# =============================================================================
# Stage 2: Runtime (hardened non-root image)
# =============================================================================
FROM dartfx/docker-api-base:latest

WORKDIR /app

COPY --from=builder --chown=appuser:appgroup /opt/venv /opt/venv
COPY --from=builder --chown=appuser:appgroup /app /app

RUN mkdir -p /app/cache /app/cache/storage && chown -R appuser:appgroup /app/cache

ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONPATH=/app/src/dartfx
ENV FORWARDED_ALLOW_IPS="*"

RUN date -u +"%Y-%m-%dT%H:%M:%SZ" > /app/src/dartfx/workspace_api/build_time.txt && \
    chown appuser:appgroup /app/src/dartfx/workspace_api/build_time.txt

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:8000/healthz || exit 1

CMD ["uvicorn", "workspace_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
