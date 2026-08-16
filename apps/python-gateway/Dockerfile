FROM python:3.12-slim AS build

WORKDIR /build
COPY python/pyproject.toml python/README.md python/LICENSE ./
COPY python/src ./src
RUN python -m pip install --no-cache-dir build && python -m build --wheel

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN addgroup --system --gid 10001 app && adduser --system --uid 10001 --ingroup app app
COPY --from=build /build/dist/*.whl /tmp/package.whl
RUN python -m pip install --no-cache-dir '/tmp/package.whl[server]' && rm /tmp/package.whl
COPY python/examples/provider.py ./provider.py
USER app
EXPOSE 4021
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:4021/health/live', timeout=3)"
CMD ["uvicorn", "provider:app", "--host", "0.0.0.0", "--port", "4021", "--proxy-headers"]
