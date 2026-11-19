"""Scales the worker pool with the job queue.

Every SCALE_POLL_SECONDS it reads the backlog of the jobs queue (waiting + in progress) from
RabbitMQ and makes the number of worker containers match it, between SCALE_MIN and SCALE_MAX.
Scale-up is immediate. Scale-down waits until the queue has been empty for SCALE_DOWN_AFTER
seconds, so every worker is idle when one is removed. New workers are clones of a running
worker, created through the Docker socket.
"""
import json
import os
import socket
import time
from urllib.parse import urlparse

import httpx

from common import broker, config

MIN = int(os.environ.get("SCALE_MIN", "2"))
MAX = int(os.environ.get("SCALE_MAX", "5"))
POLL = int(os.environ.get("SCALE_POLL_SECONDS", "5"))
DOWN_AFTER = int(os.environ.get("SCALE_DOWN_AFTER", "60"))
SERVICE = "worker"
NUMBER = "com.docker.compose.container-number"

docker = httpx.Client(transport=httpx.HTTPTransport(uds="/var/run/docker.sock"),
                      base_url="http://docker", timeout=30)


def call(method, path, **kwargs):
    r = docker.request(method, path, **kwargs)
    r.raise_for_status()
    return r.json() if r.content else None


# This container's hostname is its container ID, so it can look up its own Compose project.
PROJECT = call("GET", f"/containers/{socket.gethostname()}/json")["Config"]["Labels"][
    "com.docker.compose.project"]


def backlog():
    """Messages waiting in the jobs queue plus messages a worker is processing right now."""
    u = urlparse(config.RABBITMQ_URL)
    r = httpx.get(f"http://{u.hostname}:15672/api/queues/%2F/{broker.JOBS}",
                  auth=(u.username, u.password), timeout=10)
    if r.status_code == 404:  # queue not declared yet
        return 0
    r.raise_for_status()
    q = r.json()
    return q.get("messages_ready", 0) + q.get("messages_unacknowledged", 0)


def workers(include_stopped=False):
    filters = json.dumps({"label": [f"com.docker.compose.project={PROJECT}",
                                    f"com.docker.compose.service={SERVICE}"]})
    found = call("GET", "/containers/json",
                 params={"filters": filters, "all": str(include_stopped).lower()})
    return sorted(found, key=lambda c: int(c["Labels"][NUMBER]))


def add_worker():
    """Start one more worker with the same image, env, volumes and network as an existing one."""
    running = workers()
    if not running:
        raise RuntimeError("no running worker to copy")
    src = call("GET", f"/containers/{running[0]['Id']}/json")
    used = {int(c["Labels"][NUMBER]) for c in workers(include_stopped=True)}
    number = next(n for n in range(1, len(used) + 2) if n not in used)

    spec = dict(src["Config"])
    spec.pop("Hostname", None)  # let Docker assign one; the worker ID is built from it
    spec["Labels"] = {**spec["Labels"], NUMBER: str(number)}
    network = next(iter(src["NetworkSettings"]["Networks"]))
    spec["HostConfig"] = src["HostConfig"]
    spec["NetworkingConfig"] = {"EndpointsConfig": {network: {"Aliases": [SERVICE]}}}

    name = f"{PROJECT}-{SERVICE}-{number}"
    created = call("POST", "/containers/create", params={"name": name}, json=spec)
    call("POST", f"/containers/{created['Id']}/start")
    return name


def remove_worker(container):
    call("POST", f"/containers/{container['Id']}/stop", params={"t": 10})
    call("DELETE", f"/containers/{container['Id']}")
    return container["Names"][0].lstrip("/")


def main():
    print(f"autoscaler: project={PROJECT} min={MIN} max={MAX} scale-down after {DOWN_AFTER}s idle")
    idle_since = None
    while True:
        try:
            waiting, current = backlog(), workers()
            wanted = max(MIN, min(MAX, waiting))
            if wanted > len(current):
                idle_since = None
                for _ in range(wanted - len(current)):
                    print(f"autoscaler: backlog={waiting}, scaling up: started {add_worker()}")
            elif waiting == 0 and len(current) > MIN:
                idle_since = idle_since or time.time()
                if time.time() - idle_since >= DOWN_AFTER:
                    for extra in reversed(current[MIN:]):
                        print(f"autoscaler: queue idle, scaling down: removed {remove_worker(extra)}")
                    idle_since = None
            else:
                idle_since = None
        except Exception as e:
            print(f"autoscaler: {e!r}")
        time.sleep(POLL)


if __name__ == "__main__":
    main()
