FROM python:3.12-slim-trixie AS bot-api

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential cmake gperf git zlib1g-dev libssl-dev ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN git clone --recursive --depth 1 --shallow-submodules https://github.com/tdlib/telegram-bot-api.git /src \
    && cmake -S /src -B /src/build -DCMAKE_BUILD_TYPE=Release \
    && cmake --build /src/build --target telegram-bot-api -j"$(nproc)"


FROM python:3.12-slim-trixie

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg tini curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=denoland/deno:bin /deno /usr/local/bin/deno
COPY --from=bot-api /src/build/telegram-bot-api /usr/local/bin/telegram-bot-api

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

ENV PYTHONUNBUFFERED=1 \
    DATABASE_PATH=/data/bot.db \
    TELEGRAM_API_URL=http://127.0.0.1:8081

VOLUME /data

HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -fsS "$TELEGRAM_API_URL/bot$BOT_TOKEN/getMe" > /dev/null || exit 1

ENTRYPOINT ["tini", "-g", "--", "/app/entrypoint.sh"]
