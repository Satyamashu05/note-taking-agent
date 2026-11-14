import json

from google import genai
from google.genai import errors, types

from common import config
from common.errors import FatalError, RetryableError
from common.limiter import wait_for_llm_slot

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

_client = None

def _get_client():
    global _client
    if _client is None:
        _client = genai.Client(http_options=types.HttpOptions(timeout=120_000))
    return _client

def _retry_delay(e):
    try:
        for d in e.details["error"]["details"]:
            if d.get("@type", "").endswith("RetryInfo"):
                return int(float(d["retryDelay"].rstrip("s"))) + 1
    except (KeyError, TypeError, ValueError, AttributeError):
        pass
    return None

def _call(prompt):
    wait_for_llm_slot()
    try:
        response = _get_client().models.generate_content(
            model=config.LLM_MODEL, contents=prompt,
            config=types.GenerateContentConfig(system_instruction=SYSTEM,
                                               response_mime_type="application/json",
                                               response_json_schema=SCHEMA))
    except errors.ClientError as e:
        if e.code == 429:
            raise RetryableError("LLM_RATE_LIMITED", "Gemini rate limit reached",
                                 retry_after=_retry_delay(e))
        if e.code in (401, 403):
            raise FatalError("LLM_AUTH_FAILED", "Gemini rejected the API key")
        if e.code == 404:
            raise FatalError("LLM_MODEL_NOT_FOUND", f"Gemini model {config.LLM_MODEL} does not exist")
        raise RetryableError("LLM_REQUEST_FAILED", f"Gemini error {e.code}: {e.message}")
    except errors.ServerError as e:
        raise RetryableError("LLM_UNAVAILABLE", f"Gemini is unavailable ({e.code})")
    try:
        return json.loads(response.text)
    except (TypeError, ValueError):
        raise RetryableError("LLM_BAD_OUTPUT", "Gemini returned invalid JSON")

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
        result = _call(f"{header}\nTranscript:\n{part}")
        if i == 1:
            notes["title"] = result.get("title") or title
        overviews.append(result.get("overview", ""))
        notes["sections"] += result.get("sections", [])
        notes["key_frames"] += result.get("key_frames", [])
    notes["overview"] = " ".join(o for o in overviews if o)
    notes["key_frames"] = _clean_frames(notes["key_frames"], duration)
    return notes
