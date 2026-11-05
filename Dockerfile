# Hosted Mirror: one Python service that serves the UI and the API. Data and sign-in live
# in Supabase. Kuzu and fastembed are only for the local app, so they are not installed.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 MIRROR_HOSTED=1 HOST=0.0.0.0 PORT=8080

WORKDIR /app
RUN pip install "starlette>=0.40" "uvicorn>=0.30" "httpx>=0.27" "mcp>=1.0" \
    "psycopg[binary]>=3.2" "pyjwt[crypto]>=2.8"

COPY pyproject.toml ./
COPY mirror ./mirror
COPY engine ./engine
RUN pip install --no-deps .

RUN useradd --create-home app
USER app

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8080/healthz')"
# Several workers: a slow extraction (10-40s) then blocks one worker, not every user.
CMD ["sh", "-c", "exec uvicorn mirror.app:app --host 0.0.0.0 --port ${PORT} --workers ${WEB_CONCURRENCY:-4} --log-level warning"]
