FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Sem compilador: todas as dependências têm wheel para Linux x86_64 com Python 3.13, menos
# discord-protos, que é Python puro (instala do código-fonte só com setuptools).
COPY requirements/ requirements/
RUN python -m pip install --no-cache-dir -r requirements/base.txt

# discord.py (painel) e discord.py-self (worker) expõem o mesmo pacote `discord`: cada um no seu lib.
RUN mkdir -p panel/lib worker/lib \
    && python -m pip install --no-cache-dir -r requirements/panel.txt --target panel/lib \
    && python -m pip install --no-cache-dir -r requirements/worker.txt --target worker/lib

COPY . .

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin madman \
    && chown madman:madman /app

USER madman

# /api/health é leve; a raiz renderiza o painel inteiro. O Render ignora este HEALTHCHECK e usa o
# healthCheckPath do serviço (render.yaml).
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; port=os.environ.get('PORT', '8080'); urllib.request.urlopen(f'http://127.0.0.1:{port}/api/health', timeout=3).read()"]

CMD ["python", "-m", "panel.main"]
