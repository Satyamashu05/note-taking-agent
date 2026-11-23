"""Transcript fallback for videos without captions: speech-to-text on the audio track.

The audio is streamed from YouTube and compressed by ffmpeg to a small mono MP3 (a 20-minute
video is about 5 MB, under Groq's 25 MB limit), then transcribed by Whisper on Groq's free tier.
The result uses the same "[123s] text" lines as a caption transcript, so the rest of the
pipeline (summary, key frames, frame agent) works unchanged.
"""
import os
import subprocess

import httpx
import yt_dlp
from yt_dlp.utils import DownloadError

from common.errors import FatalError, RetryableError

API = "https://api.groq.com/openai/v1/audio/transcriptions"
MODEL = os.environ.get("ASR_MODEL", "whisper-large-v3-turbo")
MAX_BYTES = 25 * 1024 * 1024


def transcribe_audio(youtube_url, outdir):
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        raise FatalError("NO_TRANSCRIPT",
                         "This video has no captions and audio transcription is not configured")
    audio = outdir / "audio.mp3"
    if not (audio.exists() and audio.stat().st_size > 0):     # reuse after a crash or retry
        extract_audio(youtube_url, audio)
    if audio.stat().st_size > MAX_BYTES:
        raise FatalError("AUDIO_TOO_LARGE", "The audio track is too large to transcribe")

    segments = whisper(audio, key)
    lines = [f"[{int(s['start'])}s] {s['text'].strip()}"
             for s in segments if str(s.get("text", "")).strip()]
    if not lines:
        raise FatalError("NO_SPEECH", "No speech was found in the video's audio")
    return "\n".join(lines)


def extract_audio(youtube_url, out):
    try:
        opts = {"quiet": True, "no_warnings": True, "skip_download": True, "format": "bestaudio/best"}
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(youtube_url, download=False)
    except DownloadError as e:
        if any(s in str(e) for s in ("429", "Too Many Requests", "not a bot")):
            raise RetryableError("YOUTUBE_RATE_LIMITED", "YouTube is limiting requests from this server")
        raise RetryableError("AUDIO_DOWNLOAD_FAILED", f"Could not open the audio: {str(e)[:200]}")
    if not info.get("url"):
        raise RetryableError("AUDIO_DOWNLOAD_FAILED", "YouTube returned no audio stream")

    headers = "".join(f"{k}: {v}\r\n" for k, v in info.get("http_headers", {}).items())
    tmp = out.with_suffix(".part.mp3")
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-headers", headers, "-i", info["url"],
           "-vn", "-ac", "1", "-ar", "16000", "-b:a", "32k", str(tmp)]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=600, check=False)
    except subprocess.TimeoutExpired:
        raise RetryableError("AUDIO_DOWNLOAD_FAILED", "Downloading the audio took too long")
    if r.returncode != 0 or not tmp.exists() or tmp.stat().st_size == 0:
        raise RetryableError("AUDIO_DOWNLOAD_FAILED",
                             f"ffmpeg could not extract the audio: {r.stderr.decode()[-200:]}")
    os.replace(tmp, out)


def whisper(audio, key):
    try:
        with audio.open("rb") as f:
            r = httpx.post(API, headers={"Authorization": f"Bearer {key}"},
                           data={"model": MODEL, "response_format": "verbose_json"},
                           files={"file": (audio.name, f, "audio/mpeg")}, timeout=300)
    except httpx.HTTPError as e:
        raise RetryableError("TRANSCRIPTION_FAILED", f"Could not reach Groq: {e!r}"[:200])

    if r.status_code == 429:
        raise RetryableError("TRANSCRIPTION_RATE_LIMITED", "Groq's speech-to-text rate limit was reached",
                             retry_after=_retry_after(r))
    if r.status_code in (401, 403):
        raise FatalError("TRANSCRIPTION_AUTH_FAILED", "Groq rejected the API key")
    if r.status_code == 413:
        raise FatalError("AUDIO_TOO_LARGE", "The audio track is too large to transcribe")
    if r.status_code >= 500:
        raise RetryableError("TRANSCRIPTION_FAILED", f"Groq is unavailable ({r.status_code})")
    if r.status_code >= 400:
        raise FatalError("TRANSCRIPTION_FAILED", f"Groq refused the audio ({r.status_code}): {r.text[:200]}")
    return r.json().get("segments") or []


def _retry_after(r):
    try:
        return int(float(r.headers["retry-after"])) + 1
    except (KeyError, ValueError):
        return None