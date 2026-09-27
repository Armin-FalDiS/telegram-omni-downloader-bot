#!/usr/bin/env bash
set -euo pipefail

mkdir -p /data/bot-api /tmp/bot-api

telegram-bot-api \
    --local \
    --api-id="$TELEGRAM_API_ID" \
    --api-hash="$TELEGRAM_API_HASH" \
    --dir=/data/bot-api \
    --temp-dir=/tmp/bot-api \
    --http-port=8081 &

until (exec 3<>/dev/tcp/127.0.0.1/8081) 2>/dev/null; do
    sleep 1
done

python bot.py &

wait -n
