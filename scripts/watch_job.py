import json
import sys
import time
import urllib.request

TERMINAL = {"COMPLETED", "COMPLETED_WITH_WARNINGS", "FAILED", "DEAD_LETTERED"}

job_id = sys.argv[1]
start, last = time.time(), None
while True:
    with urllib.request.urlopen(f"http://localhost:8000/jobs/{job_id}") as resp:
        job = json.load(resp)
    state = (job["status"], job.get("retry_at"))
    if state != last:
        line = f"[{time.time() - start:6.1f}s] {job['status']} {job['progress']}%"
        if job.get("retry_at"):
            line += f"  waiting to retry ({job['last_error']['code']})"
        if job["error"]:
            line += f"  error: {job['error']}"
        print(line, flush=True)
        last = state
    if job["status"] in TERMINAL:
        break
    time.sleep(1)
