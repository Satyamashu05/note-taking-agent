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
echo "loaded: submit, setenv, apply"
