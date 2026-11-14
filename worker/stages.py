import json
import os
import time
from pathlib import Path
from common import config

from pipeline.transcript import get_transcript
from pipeline.video_info import get_video_info


FAKE_SECONDS = int(os.environ.get("FAKE_STAGE_SECONDS", "5"))

def job_dir(job_id):
    d = Path(config.DATA_DIR) / job_id
    d.mkdir(parents=True, exist_ok=True)
    return d

def save_json(path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    os.replace(tmp, path)

def save_text(path, text):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def _fake(stage, msg):
    time.sleep(FAKE_SECONDS)
    save_json(job_dir(msg["job_id"]) / f"{stage.lower()}.json", {"stage": stage, "fake": True})

def transcribe(msg):
    d = job_dir(msg["job_id"])
    info_path = d / "info.json"
    if not info_path.exists():                       # skip YouTube if a crashed run already saved it
        info = get_video_info(msg["youtube_url"])    # checks the 20-minute limit first
        save_text(d / "transcript.txt", get_transcript(info["id"]))
        save_json(info_path, info)                   # written last: its existence means "both saved"
    info = json.loads(info_path.read_text())
    return {"video_title": info["title"], "duration_sec": info["duration"]}


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
