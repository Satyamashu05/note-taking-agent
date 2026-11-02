from youtube_transcript_api import YouTubeTranscriptApi

def get_transcript(video_id: str) -> str:
    fetched = YouTubeTranscriptApi().fetch(video_id, languages=["en"])
    return "\n".join(f"[{int(s.start)}s] {s.text}" for s in fetched)
