# Load with: source scripts/dev.sh
submit() {
  local r
  r=$(curl -s -X POST localhost:8000/jobs -H 'Content-Type: application/json' \
    -d "{\"youtube_url\":\"${1:-https://youtu.be/dQw4w9WgXcQ}\",\"notion_token\":\"${2:-test}\"}")
  [ -z "$r" ] && { echo "submit failed: gateway not responding" >&2; return 1; }
  echo "$r" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d["job_id"]) if "job_id" in d else sys.exit("submit failed: " + json.dumps(d["error"]))'
}
setenv() { sed -i "s|^$1=.*|$1=$2|" .env; grep "^$1=" .env; }
apply() {
  docker compose up -d --force-recreate gateway worker
  until curl -sf localhost:8000/healthz >/dev/null; do sleep 1; done
  echo "gateway ready"
}
replay() {   # replay OLD_JOB_ID [token]: reuse OLD's transcript, notes and frames; only publishing runs
  docker compose run --rm --no-deps -T worker test -f "/data/$1/frames.json" 2>/dev/null \
    || { echo "replay: no finished job folder /data/$1" >&2; return 1; }
  docker compose stop worker >/dev/null
  local job; job=$(submit "$VIDEO" "${2:-$TOKEN}") || { docker compose start worker >/dev/null; return 1; }
  docker compose run --rm --no-deps -T worker sh -c \
    "mkdir -p /data/$job && cd /data/$1 && cp info.json transcript.txt notes.json frames.json frame_*.jpg /data/$job/ 2>/dev/null"
  docker compose start worker >/dev/null
  echo "$job"
}
TOKEN="$(cat ~/.notion_test_token 2>/dev/null)"
VIDEO="${VIDEO:-https://www.youtube.com/watch?v=kovF86_WqmQ}"

echo "loaded: submit, setenv, apply, replay (token: ${#TOKEN} chars)"

