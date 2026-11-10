import pika
import redis
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from common import config

app = FastAPI()
r = redis.Redis.from_url(config.REDIS_URL, decode_responses=True)

@app.get("/healthz")
def healthz():
    checks = {}
    try:
        r.ping()
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
