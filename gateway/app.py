import re
import uuid
from urllib.parse import parse_qs, urlparse

import httpx
import pika
import redis
from fastapi import FastAPI, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from common import broker, config, jobs

app = FastAPI()

VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
NOTION_ID = re.compile(r"^[0-9a-fA-F]{32}$")
YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}

class JobRequest(BaseModel):
    youtube_url: str
    notion_token: str
    notion_parent_id: str | None = None

class OAuthExchange(BaseModel):
    code: str
    redirect_uri: str

def error(status, code, message):
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)

@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    first = exc.errors()[0]
    field = ".".join(str(p) for p in first["loc"][1:]) or "body"
    return error(400, "INVALID_REQUEST", f"{field}: {first['msg']}")

@app.exception_handler(StarletteHTTPException)
async def http_error(request, exc):
    return error(exc.status_code, "HTTP_ERROR", str(exc.detail))

def parse_video_id(url):
    u = urlparse(url.strip())
    if u.scheme not in ("http", "https"):
        return None
    host = (u.hostname or "").lower()
    if host == "youtu.be":
        vid = u.path.strip("/").split("/")[0]
    elif host in YOUTUBE_HOSTS:
        if u.path == "/watch":
            vid = parse_qs(u.query).get("v", [""])[0]
        else:
            parts = u.path.strip("/").split("/")
            vid = parts[1] if len(parts) >= 2 and parts[0] in ("shorts", "embed", "live") else ""
    else:
        return None
    return vid if VIDEO_ID.match(vid) else None

@app.post("/jobs", status_code=202)
def submit_job(req: JobRequest, response: Response):
    video_id = parse_video_id(req.youtube_url)
    if not video_id:
        return error(400, "INVALID_YOUTUBE_URL",
                     "Expected a YouTube video link, e.g. https://www.youtube.com/watch?v=...")
    if not req.notion_token.strip():
        return error(400, "MISSING_NOTION_TOKEN", "notion_token must not be empty")
    parent_id = None
    if req.notion_parent_id:
        parent_id = req.notion_parent_id.replace("-", "")
        if not NOTION_ID.match(parent_id):
            return error(400, "INVALID_NOTION_PARENT_ID",
                         "notion_parent_id must be a 32-character Notion page ID")

    job_id = str(uuid.uuid4())
    jobs.create(job_id, video_id)
    try:
        broker.publish_once(broker.JOBS, {
            "job_id": job_id, "stage": "TRANSCRIBING", "video_id": video_id,
            "youtube_url": f"https://www.youtube.com/watch?v={video_id}",
            "notion_token": req.notion_token, "notion_parent_id": parent_id,
        })
    except pika.exceptions.AMQPError:
        jobs.update(job_id, status="FAILED",
                    error={"code": "BROKER_UNAVAILABLE", "message": "Could not queue the job"})
        return error(503, "BROKER_UNAVAILABLE", "Job queue is unavailable, try again shortly")

    response.headers["Location"] = f"/jobs/{job_id}"
    return {"job_id": job_id, "status": "QUEUED", "video_id": video_id}

@app.get("/jobs/{job_id}")
def job_status(job_id: str):
    job = jobs.get(job_id)
    if job is None:
        return error(404, "JOB_NOT_FOUND", f"No job with id {job_id}")
    return {"job_id": job_id, "status": job["status"], "progress": job["progress"],
            "video_id": job["video_id"], "video_title": job["video_title"],
            "duration_sec": job["duration_sec"], "warnings": job["warnings"],
            "error": job["error"], "result": job["result"]}

@app.get("/jobs/{job_id}/results")
def job_results(job_id: str):
    job = jobs.get(job_id)
    if job is None:
        return error(404, "JOB_NOT_FOUND", f"No job with id {job_id}")
    if job["status"] not in jobs.TERMINAL:
        return error(409, "JOB_NOT_FINISHED",
                     f"Job is still {job['status']}; poll GET /jobs/{job_id}")
    return {"status": job["status"], "result": job["result"],
            "warnings": job["warnings"], "error": job["error"]}

@app.post("/oauth/notion/exchange")
def notion_exchange(req: OAuthExchange):
    try:
        resp = httpx.post("https://api.notion.com/v1/oauth/token",
                          auth=(config.NOTION_CLIENT_ID, config.NOTION_CLIENT_SECRET),
                          json={"grant_type": "authorization_code", "code": req.code,
                                "redirect_uri": req.redirect_uri},
                          timeout=15)
    except httpx.HTTPError:
        return error(502, "NOTION_UNREACHABLE", "Could not reach Notion")
    try:
        body = resp.json()
    except ValueError:
        body = {}
    if resp.status_code != 200:
        detail = body.get("message") or body.get("error_description") or body.get("error") or resp.text
        return error(400 if resp.status_code < 500 else 502, "NOTION_OAUTH_FAILED", detail)
    return {"access_token": body["access_token"], "refresh_token": body.get("refresh_token"),
            "workspace_name": body.get("workspace_name")}

@app.get("/healthz")
def healthz():
    checks = {}
    try:
        jobs.r.ping()
        checks["redis"] = "ok"
    except redis.RedisError as e:
        checks["redis"] = repr(e)
    try:
        pika.BlockingConnection(pika.URLParameters(config.RABBITMQ_URL)).close()
        checks["rabbitmq"] = "ok"
    except pika.exceptions.AMQPError as e:
        checks["rabbitmq"] = repr(e)
    ok = all(v == "ok" for v in checks.values())
    return JSONResponse({"status": "ok" if ok else "degraded", **checks},
                        status_code=200 if ok else 503)
