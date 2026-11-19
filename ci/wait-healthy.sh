#!/bin/sh
# Usage: sh ci/wait-healthy.sh <docker compose args, e.g. -p NAME -f FILE>
# Waits up to 2 minutes for the gateway's /healthz (which pings Redis and RabbitMQ) to return 200,
# then checks that at least one worker is running. Probes from inside the gateway container so it
# works without published ports and when Jenkins itself runs in a container.
i=0
until docker compose "$@" exec -T gateway python -c \
    "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz', timeout=3)" 2>/dev/null; do
  i=$((i + 1))
  if [ "$i" -ge 60 ]; then
    echo "gateway did not become healthy within 2 minutes" >&2
    exit 1
  fi
  sleep 2
done
echo "gateway healthy"

running=$(docker compose "$@" ps -q --status running worker | wc -l)
if [ "$running" -lt 1 ]; then
  echo "no worker container is running" >&2
  exit 1
fi
echo "$running worker(s) running"
