FROM ubuntu:24.04 AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:${PATH}"

WORKDIR /build

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        python3 \
        python3-venv \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv "${VIRTUAL_ENV}"

COPY pyproject.toml ./
COPY README.md ./README.md
COPY src ./src

RUN python -m pip wheel --no-cache-dir --wheel-dir /wheels .


FROM ubuntu:24.04 AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:${PATH}" \
    MCP_HOST=127.0.0.1 \
    MCP_PORT=8000 \
    OUTPUT_DIR=/app/outputs

WORKDIR /app

# CAIDA's Ark package repository. python3-scamper is a compiled C extension
# built against one Python minor version; the noble build targets 24.04's
# Python 3.12, which is why this image must stay on ubuntu:24.04 (see
# decisions/0004). It is not on PyPI, so it cannot come through the wheelhouse.
#
# The repository publishes no InRelease/Release.gpg, so apt cannot verify
# signatures and [trusted=yes] is required -- the same setting Ark hosts
# themselves use. Security therefore rests on HTTPS transport alone. This is
# the only third-party repository added, for exactly one package.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        passwd \
        python3 \
        python3-venv \
    && echo "deb [trusted=yes] https://pkg.ark.caida.org/ubuntu noble main" \
        > /etc/apt/sources.list.d/caida-ark.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends python3-scamper \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv --system-site-packages "${VIRTUAL_ENV}" \
    && groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --home-dir /app --no-create-home --shell /usr/sbin/nologin app

COPY --from=builder /wheels /wheels

RUN python -m pip install --no-cache-dir --no-index --find-links=/wheels caida-ai-ops \
    && rm -rf /wheels \
    && install -d -m 0770 -o app -g app "${OUTPUT_DIR}" \
    # --system-site-packages is what makes the apt-installed scamper visible
    # inside /opt/venv. Assert it here so a venv built without that flag fails
    # the build rather than shipping an image whose Ark half silently cannot
    # run -- demo mode would still pass every test.
    && python -c "import scamper, caida_ai_ops; print('scamper', scamper.__file__)"

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('MCP_PORT', '8000') + '/healthz', timeout=2).close()"]

STOPSIGNAL SIGTERM

CMD ["sh", "-c", "exec uvicorn caida_ai_ops.itdk.asgi:app --host \"${MCP_HOST}\" --port \"${MCP_PORT}\" --workers 1 --no-access-log"]
