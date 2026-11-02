import yt_dlp

MAX_SEC = 20 * 60

def get_video_info(url: str) -> dict:
    with yt_dlp.YoutubeDL({"quiet": True, "skip_download": True}) as ydl:
        info = ydl.extract_info(url, download=False)
    if info["duration"] > MAX_SEC:
        raise ValueError(f"Video is {info['duration'] // 60} min; limit is 20 min")
    return {"id": info["id"], "title": info["title"], "duration": info["duration"]}
