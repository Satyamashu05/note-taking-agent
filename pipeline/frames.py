import subprocess
from pathlib import Path

def download_video(url: str, workdir: Path) -> Path:
    path = workdir / "video.mp4"
    subprocess.run(["yt-dlp", "-f", "bv*[height<=720][ext=mp4]/b[height<=720]/b",
                    "-o", str(path), url], check=True)
    return path

def extract_frames(video: Path, key_frames: list, duration: int, outdir: Path):
    ok, warnings = [], []
    for kf in key_frames:
        t = min(max(kf["timestamp_sec"], 0), duration - 1)
        out = outdir / f"frame_{t}.jpg"
        r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", str(t),
                            "-i", str(video), "-frames:v", "1", "-q:v", "2", str(out)])
        if r.returncode == 0 and out.exists() and out.stat().st_size > 0:
            ok.append({**kf, "image": out})
        else:
            warnings.append(f"Frame at {t}s failed - omitted")
    return ok, warnings
