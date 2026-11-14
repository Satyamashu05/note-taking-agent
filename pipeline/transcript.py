from youtube_transcript_api import (CouldNotRetrieveTranscript, IpBlocked, NoTranscriptFound,
                                    RequestBlocked, TranscriptsDisabled, VideoUnavailable,
                                    YouTubeTranscriptApi)

from common.errors import FatalError, RetryableError

PREFERRED = ["en", "en-US", "en-GB", "en-IN"]

def get_transcript(video_id):
    try:
        transcripts = YouTubeTranscriptApi().list(video_id)
        try:
            transcript = transcripts.find_transcript(PREFERRED)
        except NoTranscriptFound:
            transcript = next(iter(transcripts), None)    # any language; Gemini writes English notes
        if transcript is None:
            raise FatalError("NO_TRANSCRIPT", "This video has no captions, so it cannot be summarised")
        fetched = transcript.fetch()
    except (RequestBlocked, IpBlocked):
        raise RetryableError("YOUTUBE_RATE_LIMITED", "YouTube is blocking transcript requests from this server")
    except TranscriptsDisabled:
        raise FatalError("NO_TRANSCRIPT", "This video has captions turned off, so it cannot be summarised")
    except VideoUnavailable:
        raise RetryableError("VIDEO_UNAVAILABLE", "YouTube says this video is unavailable")
    except CouldNotRetrieveTranscript as e:
        raise RetryableError("TRANSCRIPT_FAILED", str(e)[:200])

    lines = [f"[{int(s.start)}s] {s.text.replace(chr(10), ' ')}" for s in fetched]
    if not lines:
        raise FatalError("NO_TRANSCRIPT", "The transcript is empty")
    return "\n".join(lines)
