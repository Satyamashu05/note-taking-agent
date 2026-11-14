import yt_dlp
from yt_dlp.utils import DownloadError

from common.errors import FatalError, RetryableError

MAX_SEC = 20 * 60

def get_video_info(url):
    try:
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True}) as ydl:
            info = ydl.extract_info(url, download=False)
    except DownloadError as e:
        text = str(e)
        if "429" in text or "Too Many Requests" in text or "not a bot" in text:
            raise RetryableError("YOUTUBE_RATE_LIMITED", "YouTube is limiting requests from this server")
        raise RetryableError("VIDEO_UNAVAILABLE", f"Could not open the video: {text[:200]}")

    duration = info.get("duration")
    if info.get("is_live") or not duration:
        raise FatalError("UNSUPPORTED_VIDEO", "Live streams and videos without a length are not supported")
    if duration > MAX_SEC:
        raise FatalError("VIDEO_TOO_LONG", f"Video is {duration // 60} min; the limit is 20 min")
    return {"id": info["id"], "title": info["title"], "duration": duration}
