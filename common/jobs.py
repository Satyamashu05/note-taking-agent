import json
import time

import redis

from common import config

r = redis.Redis.from_url(config.REDIS_URL, decode_responses=True)

STAGES = ["TRANSCRIBING", "SUMMARIZING", "EXTRACTING_FRAMES", "PUBLISHING"]
TERMINAL = {"COMPLETED", "COMPLETED_WITH_WARNINGS", "FAILED", "DEAD_LETTERED"}
PROGRESS = {"QUEUED": 0, "TRANSCRIBING": 15, "SUMMARIZING": 40,
            "EXTRACTING_FRAMES": 65, "PUBLISHING": 85}
JSON_FIELDS = ("warnings", "error", "result")

def _key(job_id):
    return f"job:{job_id}"

def create(job_id, video_id):
    r.hset(_key(job_id), mapping={
        "status": "QUEUED", "progress": 0, "video_id": video_id,
        "video_title": "", "duration_sec": "",
        "warnings": "[]", "error": "null", "result": "null",
        "created_at": int(time.time()),
    })

def get(job_id):
    data = r.hgetall(_key(job_id))
    if not data:
        return None
    for f in JSON_FIELDS:
        data[f] = json.loads(data[f])
    data["progress"] = int(data["progress"])
    data["duration_sec"] = int(data["duration_sec"]) if data["duration_sec"] else None
    data["video_title"] = data["video_title"] or None
    return data

def update(job_id, **fields):
    for f in JSON_FIELDS:
        if f in fields:
            fields[f] = json.dumps(fields[f])
    if "status" in fields and "progress" not in fields:
        fields["progress"] = 100 if fields["status"] in TERMINAL else PROGRESS[fields["status"]]
    r.hset(_key(job_id), mapping=fields)
