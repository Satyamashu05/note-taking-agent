"""Live ops dashboard: GET /dashboard serves the page, GET /dashboard/data the JSON it polls.

Data comes from the two systems of record: RabbitMQ's management API (queues and the workers
connected to the jobs queue) and Redis (job state, and which worker holds which job's lock).
"""
import json
import os
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx
import redis
from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from common import broker, config, jobs

router = APIRouter()
PAGE = Path(__file__).with_name("dashboard.html")
RECENT_FINISHED = 30   # finished jobs sent to the page; unfinished ones are always sent
MAX_ACTIVE = 200
_amqp = urlparse(config.RABBITMQ_URL)
RABBIT_API = f"http://{_amqp.hostname}:15672/api"
RABBIT_AUTH = (_amqp.username or "guest", _amqp.password or "guest")


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard_page():
    return PAGE.read_text(encoding="utf-8")


@router.get("/dashboard/data")
def dashboard_data():
    now = time.time()
    out = {"now": now, "scale": {"min": int(os.environ.get("SCALE_MIN", "2")),
                                 "max": int(os.environ.get("SCALE_MAX", "5"))}}
    try:
        out["rabbit"] = rabbit_state()
    except (httpx.HTTPError, KeyError, ValueError) as e:
        out["rabbit"], out["rabbit_error"] = None, repr(e)
    try:
        out.update(job_state(now))
    except redis.RedisError as e:
        out.update(counts={}, jobs=[], busy={}, errors={}, done_60s=0,
                   totals={"jobs": 0, "retries": 0}, redis_error=repr(e))
    return out


def rabbit_state():
    with httpx.Client(base_url=RABBIT_API, auth=RABBIT_AUTH, timeout=3) as api:
        def get(path):
            r = api.get(path)
            r.raise_for_status()
            return r.json()
        queues, consumers, connections = get("/queues/%2F"), get("/consumers/%2F"), get("/connections")

    # Workers name their AMQP connection after their hostname (common/broker.py).
    names = {con["name"]: (con.get("client_properties") or {}).get("connection_name")
             or con.get("peer_host") for con in connections}
    connected_at = {con["name"]: con.get("connected_at") for con in connections}
    workers, since = set(), {}
    for con in consumers:
        if (con.get("queue") or {}).get("name") == broker.JOBS:
            ch = con.get("channel_details") or {}
            name = names.get(ch.get("connection_name")) or ch.get("peer_host") or "?"
            workers.add(name)
            if connected_at.get(ch.get("connection_name")):  # RabbitMQ reports milliseconds
                since[name] = connected_at[ch["connection_name"]] // 1000
    ours = {broker.JOBS, broker.DLQ}
    return {
        "workers": sorted(workers),
        "worker_since": since,
        "queues": {q["name"]: {"ready": q.get("messages_ready", 0),
                               "in_progress": q.get("messages_unacknowledged", 0)}
                   for q in queues if q["name"] in ours or q["name"].startswith("retry.")},
    }


def job_state(now):
    r = jobs.r
    owners = {}
    for key in r.scan_iter("lock:*", count=500):
        owner = r.get(key)
        if owner:
            owners[key.split(":", 1)[1]] = owner

    keys = list(r.scan_iter("job:*", count=500))
    pipe = r.pipeline()
    for key in keys:
        pipe.hgetall(key)
    data = {key.split(":", 1)[1]: d for key, d in zip(keys, pipe.execute(raise_on_error=False))
            if isinstance(d, dict) and d}

    counts, errors, retries, done_60s = {}, {}, 0, 0
    for d in data.values():
        status = d.get("status", "UNKNOWN")
        counts[status] = counts.get(status, 0) + 1
        if status not in jobs.TERMINAL and d.get("retry_at") and int(d["retry_at"]) > now:
            counts["WAITING_RETRY"] = counts.get("WAITING_RETRY", 0) + 1
        # Each job counts once, under its most recent error code.
        code = (_json(d, "error") or {}).get("code") or (_json(d, "last_error") or {}).get("code")
        if code:
            errors[code] = errors.get(code, 0) + 1
        # attempts:<STAGE> counts runs of that stage, so anything past 1 is a retry.
        retries += sum(max(0, int(v) - 1) for f, v in d.items()
                       if f.startswith("attempts:") and str(v).isdigit())
        if status.startswith("COMPLETED") and int(d.get("finished_at") or 0) >= now - 60:
            done_60s += 1

    # The stage board needs every unfinished job; finished ones are capped to the most recent.
    newest = sorted(data, key=lambda j: int(data[j].get("created_at") or 0), reverse=True)
    active = [j for j in newest if data[j].get("status") not in jobs.TERMINAL][:MAX_ACTIVE]
    finished = [j for j in newest if data[j].get("status") in jobs.TERMINAL][:RECENT_FINISHED]
    shown = sorted(active + finished, key=lambda j: int(data[j].get("created_at") or 0), reverse=True)
    busy = {owner.split(":")[0]: {"job_id": job_id,
                                  "status": data.get(job_id, {}).get("status"),
                                  "title": _title(data.get(job_id, {}))}
            for job_id, owner in owners.items()}
    return {"counts": counts, "busy": busy, "errors": errors, "done_60s": done_60s,
            "totals": {"jobs": len(data), "retries": retries},
            "jobs": [job_row(j, data[j], owners.get(j)) for j in shown]}


def _json(d, field):
    try:
        return json.loads(d.get(field) or "null")
    except ValueError:
        return None


def _title(d):
    return d.get("video_title") or d.get("video_id")


def job_row(job_id, d, owner):
    status = d.get("status", "UNKNOWN")
    return {
        "job_id": job_id,
        "title": _title(d),
        "status": status,
        "progress": int(d.get("progress") or 0),
        "attempt": int(d.get(f"attempts:{status}") or 0),
        "retry_at": int(d["retry_at"]) if d.get("retry_at") else None,
        "last_error": (_json(d, "last_error") or {}).get("code"),
        "error": (_json(d, "error") or {}).get("code"),
        "warnings": len(_json(d, "warnings") or []),
        "notion_url": (_json(d, "result") or {}).get("notion_page_url"),
        "worker": owner,
        "created_at": int(d.get("created_at") or 0),
        "finished_at": int(d["finished_at"]) if d.get("finished_at") else None,
    }
