# Load with: source scripts/dev.sh
submit() { curl -s -X POST localhost:8000/jobs -H 'Content-Type: application/json' \
  -d "{\"youtube_url\":\"${1:-https://youtu.be/dQw4w9WgXcQ}\",\"notion_token\":\"${2:-test}\"}" \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["job_id"])'; }
setenv() { sed -i "s|^$1=.*|$1=$2|" .env; grep "^$1=" .env; }
apply() { docker compose up -d --force-recreate gateway worker; }
echo "loaded: submit, setenv, apply"
