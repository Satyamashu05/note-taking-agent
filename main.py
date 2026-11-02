import json, shutil, sys
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

from pipeline.video_info import get_video_info
from pipeline.transcript import get_transcript
from pipeline.summarize import summarize
from pipeline.frames import download_video, extract_frames
from pipeline.render import render_pdf, render_pptx

def main(url):
    info = get_video_info(url)
    out = Path("output") / info["id"]; out.mkdir(parents=True, exist_ok=True)
    work = Path("work") / info["id"]; work.mkdir(parents=True, exist_ok=True)

    tfile = out / "transcript.txt"
    if tfile.exists():
        print("Transcript (cached)"); transcript = tfile.read_text()
    else:
        print("Transcribing...");     transcript = get_transcript(info["id"])
        tfile.write_text(transcript)

    print("Summarizing...");   notes = summarize(transcript, info)
    print("Extracting frames...")
    video = download_video(url, work)
    frames, warnings = extract_frames(video, notes["key_frames"], info["duration"], out)
    shutil.rmtree(work)  # don't let OneDrive sync 100 MB videos
    print("Publishing...")
    render_pdf(notes, frames, info, out / "notes.pdf")
    render_pptx(notes, frames, out / "notes.pptx")
    (out / "transcript.txt").write_text(transcript)
    (out / "notes.json").write_text(json.dumps(notes, indent=2))
    for w in warnings:
        print("WARNING:", w)
    print("Done ->", out)

if __name__ == "__main__":
    main(sys.argv[1])
