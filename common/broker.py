import json

import pika

from common import config

JOBS = "jobs"
DLQ = "jobs.dlq"

def retry_queue(seconds):
    return f"retry.{seconds}"

def connect():
    conn = pika.BlockingConnection(pika.URLParameters(config.RABBITMQ_URL))
    ch = conn.channel()
    ch.confirm_delivery()
    ch.queue_declare(JOBS, durable=True)
    ch.queue_declare(DLQ, durable=True)
    for s in sorted(set(config.BACKOFF_SECONDS)):
        ch.queue_declare(retry_queue(s), durable=True, arguments={
            "x-message-ttl": s * 1000,
            "x-dead-letter-exchange": "",
            "x-dead-letter-routing-key": JOBS,
        })
    return conn, ch

def publish(ch, queue, msg):
    ch.basic_publish(exchange="", routing_key=queue, body=json.dumps(msg), mandatory=True,
                     properties=pika.BasicProperties(delivery_mode=2,
                                                     content_type="application/json"))

def publish_once(queue, msg):
    conn, ch = connect()
    try:
        publish(ch, queue, msg)
    finally:
        conn.close()
