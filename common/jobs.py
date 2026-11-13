import json
import time

import redis

from common import config

r = redis.Redis.from_url(config.REDIS_URL, decode_responses=True)

STAGES = ["TRANSCRIBING", "SUMMARIZING", "EXTRACTING_FRAMES", "PUBLISHING"]
TERMINAL = {"COMPLETED", "COMPLETED_WITH_WARNINGS", "FAILED", "DEAD_LETTERED"}
PROGRESS = {"QUEUED": 0, "TRANSCRIBING": 15, "SUMMARIZING": 40,
            "EXTRACTING_FRAMES": 65, "PUBLISHING": 85}
JSON_DEFAULTS = {"warnings": [], "error": None, "result": None, "last_error": None}

def _key(job_id):
    return f"job:{job_id}"

def _lock(job_id):
    return f"lock:{job_id}"

def create(job_id, video_id):
    r.hset(_key(job_id), mapping={
        "status": "QUEUED", "progress": 0, "video_id": video_id,
        "video_title": "", "duration_sec": "", "retry_at": "",
        "created_at": int(time.time()),
        **{f: json.dumps(v) for f, v in JSON_DEFAULTS.items()},
    })

def get(job_id):
    data = r.hgetall(_key(job_id))
    if not data:
        return None
    for f, default in JSON_DEFAULTS.items():
        data[f] = json.loads(data[f]) if f in data else default
    data["progress"] = int(data["progress"])
    data["duration_sec"] = int(data["duration_sec"]) if data.get("duration_sec") else None
    data["retry_at"] = int(data["retry_at"]) if data.get("retry_at") else None
    data["video_title"] = data.get("video_title") or None
    return data

def update(job_id, **fields):
    for f in JSON_DEFAULTS:
        if f in fields:
            fields[f] = json.dumps(fields[f])
    if "status" in fields and "progress" not in fields:
        fields["progress"] = 100 if fields["status"] in TERMINAL else PROGRESS[fields["status"]]
    r.hset(_key(job_id), mapping=fields)

def is_done(job_id, stage):
    return r.hexists(_key(job_id), f"done:{stage}")

def next_attempt(job_id, stage):
    return r.hincrby(_key(job_id), f"attempts:{stage}", 1)

def acquire_lock(job_id, owner, ttl):
    return bool(r.set(_lock(job_id), owner, nx=True, ex=ttl))

def renew_lock(job_id, owner, ttl):
    if r.get(_lock(job_id)) == owner:
        r.expire(_lock(job_id), ttl)

def release_lock(job_id, owner):
    if r.get(_lock(job_id)) == owner:
        r.delete(_lock(job_id))
