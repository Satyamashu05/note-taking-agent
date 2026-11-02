import json
import time
from google import genai
from google.genai import errors, types

client = genai.Client()

MODELS = ["gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-flash-latest"]

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

SYSTEM = """You turn lecture/tutorial transcripts into study notes.
Each transcript line starts with [Ns] = seconds into the video.
1. Split the video into sections, in order. For each: a heading, its start time,
   and concise study-note bullets (definitions, formulas, steps, key facts).
2. Pick 3-8 key_frames: moments when something important is on screen - a diagram,
   slide, equation, code or chart. Use speech cues like "as you can see",
   "this diagram", "on this slide". For each: a timestamp inside the video,
   a caption saying what is shown, and the reason it matters.
Only use information from the transcript."""

def summarize(transcript: str, info: dict) -> dict:
    prompt = f"Title: {info['title']}\nDuration: {info['duration']}s\n\nTranscript:\n{transcript}"
    last_err = None
    for model in MODELS:
        for wait in (0, 10, 30):          # retry each model with a growing pause
            time.sleep(wait)
            try:
                response = client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=SYSTEM,
                        response_mime_type="application/json",
                        response_json_schema=SCHEMA,
                    ),
                )
                print(f"  summarized with {model}")
                return json.loads(response.text)
            except errors.ServerError as e:        # 500/503: overloaded
                last_err = e
                print(f"  {model} busy ({e.code}), retrying...")
            except errors.ClientError as e:        # 429: free-tier quota hit
                if e.code != 429:
                    raise                          # real errors (bad key, bad request) stop here
                last_err = e
                print(f"  {model} rate-limited, retrying...")
    raise RuntimeError(f"All Gemini models unavailable: {last_err}")