"""Frame-hunting agent.

For every key moment whose frames were rejected (FRAME_NOT_RELEVANT: a talking head, a blurry or
transition frame), Gemini looks at one frame at a time and decides the next action: accept it,
look at another timestamp near the planned one, give up on this moment, or stop for the whole
video when it has no visuals at all (podcast, interview). Every look captures a real frame from
the video with ffmpeg, so Constraint B (real frames only) still holds. The search is bounded by
FRAME_AGENT_LOOKS captures per moment and stops after FRAME_AGENT_MAX_MISSES moments in a row
with nothing found. Every LLM call goes through the shared rate limiter.
"""
import os

from google.genai import types

from common import config
from common.errors import RetryableError
from pipeline.frames import get_stream, grab_frame
from pipeline.llm import call_llm

MAX_LOOKS = int(os.environ.get("FRAME_AGENT_LOOKS", "3"))
MAX_MISSES = int(os.environ.get("FRAME_AGENT_MAX_MISSES", "2"))
WINDOW = 90   # never look further than this many seconds from the planned time

SYSTEM = """You are a frame-hunting agent for video study notes. A key moment was planned from the
transcript, but the frame captured there was not useful. You see one frame at a time and choose
ONE action:
- "accept": the current frame clearly shows the planned visual (slide, diagram, chart, table,
  code, equation or an illustrative scene). Fill caption (exactly what is visible, nothing else)
  and reason (why it matters for the notes).
- "look_at": the frame is not useful. Fill timestamp_sec with another moment to capture. Use what
  you see: if the speaker is introducing the visual, look later; if it already disappeared, look
  earlier; slides usually stay on screen for 5-30 seconds. Never repeat a tried timestamp.
- "give_up": the visual probably does not exist near this time.
- "no_visuals": the whole video is a podcast, interview, vlog or talking-head recording with no
  slides, screen share or graphics (for example a studio set with people and microphones).
  This stops the search for every moment of the video, so use it only when you are confident.
Always fill observation with a short note on what the current frame shows."""

SCHEMA = {
    "type": "object",
    "properties": {
        "observation": {"type": "string"},
        "action": {"type": "string", "enum": ["accept", "look_at", "give_up", "no_visuals"]},
        "timestamp_sec": {"type": "integer"},
        "caption": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["observation", "action"],
    "additionalProperties": False,
}


def hunt_frames(youtube_url, moments, rejected, frames, duration, outdir):
    """Try to replace rejected moments with better frames. Returns (frames, rejected, trace)."""
    rejected_at = {w["timestamp_sec"] for w in rejected}
    todo = [m for m in moments if m["timestamp_sec"] in rejected_at]
    if not todo or MAX_LOOKS <= 0:
        return frames, rejected, []
    try:
        stream_url, headers = get_stream(youtube_url)
    except RetryableError as e:
        print(f"[frame-agent] skipped: {e}")
        return frames, rejected, []
    if stream_url is None:
        return frames, rejected, []

    taken = {f["timestamp_sec"] for f in frames}
    found, trace, misses = [], [], 0
    for n, m in enumerate(todo):
        try:
            frame, steps, no_visuals = hunt(m, stream_url, headers, duration, outdir, taken)
        except RetryableError as e:   # e.g. Gemini 429: keep what we have, never fail the job
            print(f"[frame-agent] stopped early: {e}")
            break
        trace.append({"planned_sec": m["timestamp_sec"], "rescued": frame is not None, "steps": steps})
        if frame:
            found.append(frame)
            taken.add(frame["timestamp_sec"])
            misses = 0
        else:
            misses += 1
        if no_visuals or misses >= MAX_MISSES:
            why = ("the video has no visuals" if no_visuals
                   else f"{misses} moments in a row had nothing to find")
            print(f"[frame-agent] stopping: {why}, skipped {len(todo) - n - 1} more moment(s)")
            break

    rescued = {t["planned_sec"] for t in trace if t["rescued"]}
    print(f"[frame-agent] rescued {len(rescued)} of {len(todo)} rejected frames")
    rejected = [w for w in rejected if w["timestamp_sec"] not in rescued]
    frames = sorted(frames + found, key=lambda f: f["timestamp_sec"])
    return frames, rejected, trace


def hunt(m, stream_url, headers, duration, outdir, taken):
    """Search around one moment. Returns (frame or None, steps, no_visuals)."""
    planned = m["timestamp_sec"]
    lo, hi = max(0, planned - WINDOW), min(int(duration) - 1, planned + WINDOW)
    start = m["candidates"].get("A") or m["candidates"]["B"]
    ts, file = start["timestamp_sec"], start["file"]
    tried = {c["timestamp_sec"] for c in m["candidates"].values()} | taken
    steps = []

    for look in range(MAX_LOOKS + 1):
        left = MAX_LOOKS - look
        history = "\n".join(f"- {s['timestamp_sec']}s: {s['observation']}" for s in steps) or "- none yet"
        prompt = [
            f"Planned moment at {planned}s of a {duration}s video.\n"
            f"Planned caption: {m['caption']}\nPlanned reason: {m['reason']}\n"
            f"Frames you already looked at:\n{history}\n"
            f"Timestamps already used or tried: {sorted(tried)}\n"
            f"Allowed range: {lo}-{hi}s. Looks left after this one: {left}"
            + ("" if left else " (last look: accept, give_up or no_visuals)")
            + f"\nCurrent frame, captured at {ts}s:",
            types.Part.from_bytes(data=(outdir / file).read_bytes(), mime_type="image/jpeg"),
        ]
        a = call_llm(prompt, SYSTEM, SCHEMA, model=config.LLM_CHECK_MODEL, rpm=config.LLM_CHECK_RPM)
        action, nxt = a.get("action"), a.get("timestamp_sec")
        steps.append({"timestamp_sec": ts, "observation": a.get("observation", ""), "action": action})
        print(f"[frame-agent] moment @{planned}s, look {look + 1}: frame {ts}s shows "
              f"'{steps[-1]['observation'][:80]}' -> {action}"
              + (f" {nxt}s" if action == "look_at" else ""))

        if action == "no_visuals":
            return None, steps, True
        if action == "accept" and a.get("caption", "").strip() and a.get("reason", "").strip():
            return {"timestamp_sec": ts, "caption": a["caption"], "reason": a["reason"],
                    "file": file}, steps, False
        if action != "look_at" or not left or not isinstance(nxt, int):
            return None, steps, False

        nxt = max(lo, min(hi, nxt))
        if nxt in tried:                       # loop guard: the agent is going in circles
            return None, steps, False
        tried.add(nxt)
        out = outdir / f"frame_{nxt:05d}.jpg"
        if not grab_frame(stream_url, headers, nxt, out):
            steps[-1]["observation"] += f" (capture at {nxt}s failed)"
            return None, steps, False
        ts, file = nxt, out.name

    return None, steps, False
