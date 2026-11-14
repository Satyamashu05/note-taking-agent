import json
import os
import time
from pathlib import Path
from common import config

from pipeline.transcript import get_transcript
from pipeline.video_info import get_video_info
from pipeline.summarize import summarize as summarize_transcript
from pipeline.frames import capture_frames
from pipeline.verify import verify_frames
from pipeline.notion import publish_page


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
    d = job_dir(msg["job_id"])
    notes_path = d / "notes.json"
    if not notes_path.exists():                       # don't spend LLM quota twice after a crash
        info = json.loads((d / "info.json").read_text())
        notes = summarize_transcript((d / "transcript.txt").read_text(), info["title"], info["duration"])
        save_json(notes_path, notes)
    return {}


def extract_frames(msg):
    d = job_dir(msg["job_id"])
    frames_path = d / "frames.json"
    if not frames_path.exists():
        duration = json.loads((d / "info.json").read_text())["duration"]
        key_frames = json.loads((d / "notes.json").read_text())["key_frames"]
        fault = os.environ.get("FAULT_FRAME_FAILURES", "")
        fail_first = len(key_frames) if fault == "all" else int(fault or 0)
        moments, warnings = capture_frames(msg["youtube_url"], key_frames, duration, d, fail_first)
        if moments and os.environ.get("FRAME_CHECK", "on") == "on":
            frames, rejected = verify_frames(moments, d)
        else:
            frames, rejected = [{"timestamp_sec": c["timestamp_sec"], "caption": m["caption"],
                                 "reason": m["reason"], "file": c["file"]}
                                for m in moments
                                for c in [m["candidates"].get("A") or m["candidates"]["B"]]], []
        save_json(frames_path, {"frames": frames, "warnings": warnings + rejected})
    return {"warnings": json.loads(frames_path.read_text())["warnings"]}


def publish(msg):
    d = job_dir(msg["job_id"])
    done_path = d / "published.json"
    if not done_path.exists():
        info = json.loads((d / "info.json").read_text())
        notes = json.loads((d / "notes.json").read_text())
        frames = json.loads((d / "frames.json").read_text())
        result = publish_page(msg, info, notes, frames["frames"], frames["warnings"], d)
        result["frames_requested"] = len(notes["key_frames"])
        result["warnings"] = frames["warnings"]
        save_json(done_path, result)
    r = json.loads(done_path.read_text())
    complete = not r["warnings"] and r["frames_published"] == r["frames_requested"]
    return {"status": "COMPLETED" if complete else "COMPLETED_WITH_WARNINGS",
            "warnings": r["warnings"],
            "result": {"notion_page_url": r["url"], "frames_requested": r["frames_requested"],
                       "frames_published": r["frames_published"]}}

RUNNERS = {"TRANSCRIBING": transcribe, "SUMMARIZING": summarize,
           "EXTRACTING_FRAMES": extract_frames, "PUBLISHING": publish}
