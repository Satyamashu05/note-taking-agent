import os

RABBITMQ_URL = os.environ["RABBITMQ_URL"]
REDIS_URL = os.environ["REDIS_URL"]
NOTION_CLIENT_ID = os.environ["NOTION_CLIENT_ID"]
NOTION_CLIENT_SECRET = os.environ["NOTION_CLIENT_SECRET"]
MAX_ATTEMPTS = int(os.environ["MAX_ATTEMPTS"])
BACKOFF_SECONDS = [int(s) for s in os.environ["BACKOFF_SECONDS"].split(",")]
