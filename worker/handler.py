import json
import os
import socket
import threading
import time
import traceback

from common import broker, config, jobs
from common.errors import FatalError, RetryableError
from worker import stages

WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"
LOCK_TTL = 10

def log(job_id, text):
    print(f"[{WORKER_ID}] job {job_id[:8]} {text}")

def handle(body):
    msg = json.loads(body)
    job_id, stage = msg["job_id"], msg["stage"]
    if not jobs.acquire_lock(job_id, WORKER_ID, LOCK_TTL):
        delay = min(config.BACKOFF_SECONDS)
        log(job_id, f"{stage}: another worker holds this job, checking again in {delay}s")
        broker.publish_once(broker.retry_queue(delay), msg)
        return
    stop = threading.Event()
    threading.Thread(target=keep_lock, args=(job_id, stop), daemon=True).start()
    should_hand_off = False
    try:
        job = jobs.get(job_id)
        if job is None or job["status"] in jobs.TERMINAL:
            log(job_id, f"{stage}: job is finished or unknown, dropping message")
        else:
            should_hand_off = run(msg, job)
    finally:
        stop.set()
        jobs.release_lock(job_id, WORKER_ID)
    if should_hand_off:
        hand_off(msg)


def keep_lock(job_id, stop):
    while not stop.wait(LOCK_TTL / 3):
        jobs.renew_lock(job_id, WORKER_ID, LOCK_TTL)

def run(msg, job):
    """Run one stage. Returns True when the job should move to the next stage."""
    job_id, stage = msg["job_id"], msg["stage"]
    if jobs.is_done(job_id, stage):
        if job["status"] in jobs.STAGES and \
                jobs.STAGES.index(job["status"]) > jobs.STAGES.index(stage):
            log(job_id, f"{stage}: duplicate message, next stage already started")
            return False
        log(job_id, f"{stage}: already done, handing off again")
        return True

    attempt = jobs.next_attempt(job_id, stage)
    if attempt > config.MAX_ATTEMPTS:
        dead_letter(msg, job["last_error"] or
                    {"code": "RETRIES_EXHAUSTED", "message": f"{stage} kept failing"})
        return False
    jobs.update(job_id, status=stage, retry_at="")
    log(job_id, f"{stage}: attempt {attempt}")
    try:
        inject_fault(stage, attempt)
        updates = stages.RUNNERS[stage](msg)
    except FatalError as e:
        log(job_id, f"{stage}: failed: {e}")
        jobs.update(job_id, status="FAILED", error={"code": e.code, "message": str(e)})
        return False
    except Exception as e:
        retry_later(msg, attempt, e)
        return False
    jobs.update(job_id, **{**updates, f"done:{stage}": 1})
    log(job_id, f"{stage}: done")
    return True


def hand_off(msg):
    i = jobs.STAGES.index(msg["stage"])
    if i + 1 < len(jobs.STAGES):
        broker.publish_once(broker.JOBS, {**msg, "stage": jobs.STAGES[i + 1]})

def retry_later(msg, attempt, exc):
    job_id, stage = msg["job_id"], msg["stage"]
    if isinstance(exc, RetryableError):
        err, retry_after = {"code": exc.code, "message": str(exc)}, exc.retry_after
    else:
        traceback.print_exc()
        err, retry_after = {"code": "INTERNAL_ERROR", "message": repr(exc)[:300]}, None
    if attempt >= config.MAX_ATTEMPTS:
        dead_letter(msg, err)
        return
    delay = backoff_delay(attempt, retry_after)
    log(job_id, f"{stage}: {err['code']}, retrying in {delay}s")
    jobs.update(job_id, retry_at=int(time.time()) + delay, last_error=err)
    broker.publish_once(broker.retry_queue(delay), msg)

def backoff_delay(attempt, retry_after):
    steps = config.BACKOFF_SECONDS
    delay = steps[min(attempt - 1, len(steps) - 1)]
    if retry_after and retry_after > delay:
        delay = next((s for s in sorted(steps) if s >= retry_after), max(steps))
    return delay

def dead_letter(msg, err):
    log(msg["job_id"], f"{msg['stage']}: out of attempts, moving to DLQ")
    broker.publish_once(broker.DLQ, {**msg, "notion_token": "<removed>", "error": err})
    jobs.update(msg["job_id"], status="DEAD_LETTERED", error=err, retry_at="")

def inject_fault(stage, attempt):
    if os.environ.get("FAULT_STAGE") != stage or attempt > int(os.environ.get("FAULT_TIMES", "2")):
        return
    if os.environ.get("FAULT_KIND", "429") == "fatal":
        raise FatalError("SIMULATED_FAILURE", f"Simulated permanent failure during {stage}")
    raise RetryableError("RATE_LIMITED", f"Simulated 429 during {stage}")
