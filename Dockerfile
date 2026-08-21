FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-panel.txt requirements-worker.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt

RUN mkdir -p panel/lib worker/lib \
    && python -m pip install --no-cache-dir -r requirements-panel.txt --target panel/lib \
    && python -m pip install --no-cache-dir -r requirements-worker.txt --target worker/lib

COPY . .

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin madman \
    && chown madman:madman /app

USER madman

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; port=os.environ.get('PORT', '8080'); urllib.request.urlopen(f'http://127.0.0.1:{port}/', timeout=3).read()"]

CMD ["python", "-m", "panel.main"]
