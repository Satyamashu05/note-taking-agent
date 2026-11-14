from google.genai import types

from pipeline.llm import call_llm

SYSTEM = """You check video frames for study notes. For each moment you get the caption that was
planned from the transcript and up to two candidate frames: A at the planned time, B a few
seconds later. Choose the candidate that best shows useful visual content (slide, diagram, chart,
map, table, code, equation, or a scene that clearly illustrates the point). Then write:
- caption: exactly what is visible in the chosen frame. Never mention anything that is not visible.
- reason: why this image matters for the notes.
Choose "none" if neither frame shows anything useful (only a person talking, a blank or
transition screen, or something unrelated to the planned topic)."""

SCHEMA = {
    "type": "object",
    "properties": {"moments": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "index": {"type": "integer"},
            "choice": {"type": "string", "enum": ["A", "B", "none"]},
            "caption": {"type": "string"},
            "reason": {"type": "string"},
        },
        "required": ["index", "choice", "caption", "reason"],
        "additionalProperties": False,
    }}},
    "required": ["moments"],
    "additionalProperties": False,
}

def verify_frames(moments, outdir):
    contents = ["Check each moment and choose its best frame."]
    for i, m in enumerate(moments):
        contents.append(f"Moment {i}. Planned caption: {m['caption']} Planned reason: {m['reason']}")
        for label, c in sorted(m["candidates"].items()):
            contents.append(f"Moment {i}, candidate {label}:")
            contents.append(types.Part.from_bytes(data=(outdir / c["file"]).read_bytes(),
                                                  mime_type="image/jpeg"))
    answers = {a["index"]: a for a in call_llm(contents, SYSTEM, SCHEMA).get("moments", [])}

    frames, rejected = [], []
    for i, m in enumerate(moments):
        a = answers.get(i)
        if a is None:                       # the model skipped it: keep the planned frame and caption
            c = m["candidates"].get("A") or m["candidates"]["B"]
            frames.append({"timestamp_sec": c["timestamp_sec"], "caption": m["caption"],
                           "reason": m["reason"], "file": c["file"]})
        elif a["choice"] in m["candidates"] and a["caption"].strip() and a["reason"].strip():
            c = m["candidates"][a["choice"]]
            frames.append({"timestamp_sec": c["timestamp_sec"], "caption": a["caption"],
                           "reason": a["reason"], "file": c["file"]})
        else:
            rejected.append({"code": "FRAME_NOT_RELEVANT", "timestamp_sec": m["timestamp_sec"]})
    return frames, rejected
