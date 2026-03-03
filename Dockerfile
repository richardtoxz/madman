FROM python:3.13-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p panel/lib worker/lib
RUN python -m pip install --no-cache-dir discord.py -t panel/lib
RUN python -m pip install --no-cache-dir discord.py-self -t worker/lib

RUN sed -i 's/import audioop/try:\n    import audioop\nexcept ImportError:\n    audioop = None/g' panel/lib/discord/player.py worker/lib/discord/player.py

CMD ["python", "panel/main.py"]
