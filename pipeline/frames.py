import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

import yt_dlp
from yt_dlp.utils import DownloadError

# Prefer H.264 at ≤720p (fast to decode everywhere), else any video stream ≤720p.
FORMAT = "bv*[height<=720][vcodec^=avc1]/bv*[height<=720]/b[height<=720]/b"
OFFSET = 4          # second candidate, a few seconds later, catches animations that finish drawing

def get_stream(youtube_url, tries=3):
    last = None
    for i in range(tries):
        try:
            opts = {"quiet": True, "no_warnings": True, "skip_download": True, "format": FORMAT}
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(youtube_url, download=False)
            headers = "".join(f"{k}: {v}\r\n" for k, v in info.get("http_headers", {}).items())
            return info["url"], headers
        except (DownloadError, KeyError) as e:
            last = e
            time.sleep(5 * (i + 1))
    print(f"could not get video stream: {last}")
    return None, None

def grab_frame(stream_url, headers, t, out, tries=2):
    if out.exists() and out.stat().st_size > 0:      # already captured by an earlier attempt
        return True
    if stream_url is None:
        return False
    tmp = out.with_suffix(".part.jpg")
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-headers", headers, "-ss", str(t),
           "-i", stream_url, "-frames:v", "1", "-q:v", "2", str(tmp)]
    for _ in range(tries):
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=60)
            if r.returncode == 0 and tmp.exists() and tmp.stat().st_size > 0:
                os.replace(tmp, out)
                return True
        except subprocess.TimeoutExpired:
            pass
        time.sleep(2)
    return False

def capture_frames(youtube_url, key_frames, duration, outdir, fail_first=0):
    stream_url, headers = get_stream(youtube_url) if key_frames else (None, None)
    tasks = [(i, label, ts)
             for i, kf in enumerate(key_frames)
             for label, ts in (("A", kf["timestamp_sec"]), ("B", kf["timestamp_sec"] + OFFSET))
             if ts < duration]

    def work(task):
        i, label, ts = task
        out = outdir / f"frame_{ts:05d}.jpg"
        return task, out, i >= fail_first and grab_frame(stream_url, headers, ts, out)

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(work, tasks))

    candidates = {}
    for (i, label, ts), out, ok in results:
        if ok:
            candidates.setdefault(i, {})[label] = {"file": out.name, "timestamp_sec": ts}

    moments, warnings = [], []
    for i, kf in enumerate(key_frames):
        if i in candidates:
            moments.append({**kf, "candidates": candidates[i]})
        else:
            warnings.append({"code": "FRAME_EXTRACTION_FAILED", "timestamp_sec": kf["timestamp_sec"]})
    return moments, warnings
