import json
import os
import time
from pathlib import Path

from common import config

FAKE_SECONDS = int(os.environ.get("FAKE_STAGE_SECONDS", "5"))

def job_dir(job_id):
    d = Path(config.DATA_DIR) / job_id
    d.mkdir(parents=True, exist_ok=True)
    return d

def save_json(path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    os.replace(tmp, path)

def _fake(stage, msg):
    time.sleep(FAKE_SECONDS)
    save_json(job_dir(msg["job_id"]) / f"{stage.lower()}.json", {"stage": stage, "fake": True})

def transcribe(msg):
    _fake("TRANSCRIBING", msg)
    return {"video_title": f"Fake title for {msg['video_id']}", "duration_sec": 600}

def summarize(msg):
    _fake("SUMMARIZING", msg)
    return {}

def extract_frames(msg):
    _fake("EXTRACTING_FRAMES", msg)
    return {}

def publish(msg):
    _fake("PUBLISHING", msg)
    return {"status": "COMPLETED",
            "result": {"notion_page_url": "https://www.notion.so/fake-page",
                       "frames_requested": 0, "frames_published": 0}}

RUNNERS = {"TRANSCRIBING": transcribe, "SUMMARIZING": summarize,
           "EXTRACTING_FRAMES": extract_frames, "PUBLISHING": publish}
