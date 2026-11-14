from common import config
from pipeline.llm import call_llm

MAX_FRAMES = 8

SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "overview": {"type": "string"},
        "sections": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "heading": {"type": "string"},
                "start_sec": {"type": "integer"},
                "bullets": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["heading", "start_sec", "bullets"],
            "additionalProperties": False,
        }},
        "key_frames": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "timestamp_sec": {"type": "integer"},
                "caption": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["timestamp_sec", "caption", "reason"],
            "additionalProperties": False,
        }},
    },
    "required": ["title", "overview", "sections", "key_frames"],
    "additionalProperties": False,
}

SYSTEM = """You turn lecture and tutorial transcripts into study notes, written in English.
Each transcript line starts with [Ns] = seconds from the start of the video.
1. Split the content into sections, in order. For each: a heading, its start time in seconds,
   and concise study-note bullets (definitions, formulas, steps, key facts).
2. Pick up to 8 key_frames: moments when something important is likely on screen - a diagram,
   slide, equation, code or chart. Use speech cues such as "as you can see", "this diagram",
   "on this slide", "let me draw". For each: timestamp_sec, caption (what is shown) and
   reason (why it matters for the notes). If nothing visual is referenced, return an empty list.
Use only information from the transcript."""

def _chunks(transcript, max_chars):
    chunk, size = [], 0
    for line in transcript.splitlines():
        if chunk and size + len(line) > max_chars:
            yield "\n".join(chunk)
            chunk, size = [], 0
        chunk.append(line)
        size += len(line) + 1
    if chunk:
        yield "\n".join(chunk)

def _clean_frames(frames, duration):
    kept = []
    for f in sorted(frames, key=lambda f: f.get("timestamp_sec", -1)):
        t = f.get("timestamp_sec")
        if not isinstance(t, int) or not 0 <= t < duration:
            continue
        if not str(f.get("caption", "")).strip() or not str(f.get("reason", "")).strip():
            continue
        if any(abs(t - k["timestamp_sec"]) < 5 for k in kept):
            continue
        kept.append(f)
    return kept[:MAX_FRAMES]

def summarize(transcript, title, duration):
    parts = list(_chunks(transcript, config.LLM_MAX_CHARS))
    if len(parts) > 1:
        print(f"transcript split into {len(parts)} parts")
    notes = {"title": title, "overview": "", "sections": [], "key_frames": []}
    overviews = []
    for i, part in enumerate(parts, 1):
        header = f"Video title: {title}\nVideo length: {duration}s\n"
        if len(parts) > 1:
            header += (f"This is part {i} of {len(parts)} of the transcript. "
                       "Timestamps are from the start of the whole video.\n")
        result = call_llm(f"{header}\nTranscript:\n{part}", SYSTEM, SCHEMA)
        if i == 1:
            notes["title"] = result.get("title") or title
        overviews.append(result.get("overview", ""))
        notes["sections"] += result.get("sections", [])
        notes["key_frames"] += result.get("key_frames", [])
    notes["overview"] = " ".join(o for o in overviews if o)
    notes["key_frames"] = _clean_frames(notes["key_frames"], duration)
    return notes
