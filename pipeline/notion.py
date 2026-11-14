import json
import os

import httpx

from common.errors import FatalError, RetryableError

API = "https://api.notion.com/v1"
VERSION = "2026-03-11"

def _seconds(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None

class Notion:
    def __init__(self, token):
        self.http = httpx.Client(base_url=API, timeout=60, headers={
            "Authorization": f"Bearer {token}", "Notion-Version": VERSION})

    def request(self, method, path, **kwargs):
        try:
            r = self.http.request(method, path, **kwargs)
        except httpx.HTTPError as e:
            raise RetryableError("NOTION_UNREACHABLE", f"Could not reach Notion: {e}")
        if r.status_code == 429:
            raise RetryableError("NOTION_RATE_LIMITED", "Notion rate limit reached",
                                 retry_after=_seconds(r.headers.get("Retry-After")))
        if r.status_code in (409, 529) or r.status_code >= 500:
            raise RetryableError("NOTION_UNAVAILABLE", f"Notion is busy ({r.status_code})")
        if r.status_code == 401:
            raise FatalError("NOTION_UNAUTHORIZED", "Notion access was revoked or expired; reconnect Notion")
        if r.status_code in (403, 404):
            raise FatalError("NOTION_NO_ACCESS", "The integration cannot access the target Notion page")
        if r.status_code >= 400:
            raise FatalError("NOTION_REQUEST_FAILED",
                             f"Notion rejected the request ({r.status_code}): {r.text[:300]}")
        return r.json()

    def find_pages(self, marker):
        found = self.request("POST", "/search", json={
            "query": marker, "filter": {"property": "object", "value": "page"}})
        return [p["id"] for p in found.get("results", [])
                if not p.get("in_trash") and marker in _title(p)]

    def trash(self, page_id):
        try:
            self.request("PATCH", f"/pages/{page_id}", json={"in_trash": True})
        except FatalError:
            pass                                  # already gone: nothing to clean up

    def upload_image(self, path):
        up = self.request("POST", "/file_uploads",
                          json={"filename": path.name, "content_type": "image/jpeg"})
        with open(path, "rb") as f:
            self.request("POST", f"/file_uploads/{up['id']}/send",
                         files={"file": (path.name, f, "image/jpeg")})
        return up["id"]

    def create_page(self, parent_id, title, children):
        parent = {"page_id": parent_id} if parent_id else {"workspace": True}
        return self.request("POST", "/pages", json={
            "parent": parent,
            "properties": {"title": {"title": [_text(title)]}},
            "children": children})

    def append(self, block_id, children):
        for i in range(0, len(children), 100):
            self.request("PATCH", f"/blocks/{block_id}/children", json={"children": children[i:i + 100]})

def _title(page):
    for prop in page.get("properties", {}).values():
        if isinstance(prop, dict) and prop.get("type") == "title":
            return "".join(t.get("plain_text", "") for t in prop.get("title", []))
    return ""

def _text(content, link=None):
    t = {"type": "text", "text": {"content": content[:2000]}}
    if link:
        t["text"]["link"] = {"url": link}
    return t

def _block(kind, *rich):
    return {"type": kind, kind: {"rich_text": list(rich)}}

def _mmss(sec):
    return f"{sec // 60}:{sec % 60:02d}"

def _section_of(t, starts):
    idx = 0
    for i, s in enumerate(starts):
        if s <= t:
            idx = i
    return idx

def build_blocks(notes, frames, uploads, info, url, warnings):
    blocks = [
        _block("paragraph", _text("Source: "), _text(info["title"], url),
               _text(f" · {_mmss(info['duration'])}")),
        _block("heading_2", _text("Overview")),
        _block("paragraph", _text(notes["overview"] or "-")),
    ]
    sections = notes["sections"] or [{"heading": "Notes", "start_sec": 0, "bullets": []}]
    starts = [s["start_sec"] for s in sections]
    for i, s in enumerate(sections):
        blocks.append(_block("heading_2", _text(f"{s['heading']} ({_mmss(s['start_sec'])})")))
        blocks += [_block("bulleted_list_item", _text(b)) for b in s["bullets"] if b.strip()]
        for f in frames:
            if f["file"] in uploads and _section_of(f["timestamp_sec"], starts) == i:
                blocks.append({"type": "image", "image": {
                    "type": "file_upload", "file_upload": {"id": uploads[f["file"]]},
                    "caption": [_text(f"[{_mmss(f['timestamp_sec'])}] {f['caption']} - {f['reason']}")]}})
    if warnings:
        blocks.append({"type": "callout", "callout": {
            "icon": {"type": "emoji", "emoji": "⚠️"},
            "rich_text": [_text(f"{len(warnings)} planned image(s) were left out: they could not be "
                                "captured or did not show relevant content.")]}})
    return blocks

def publish_page(msg, info, notes, frames, warnings, outdir):
    notion = Notion(msg["notion_token"])
    marker = f"[{msg['job_id'][:8]}]"
    page_file = outdir / "notion_page.json"
    first_attempt = not page_file.exists()

    # 1. Trash any page an earlier, interrupted attempt created, so only one page ever remains.
    old = set(notion.find_pages(marker))
    if not first_attempt:
        old.add(json.loads(page_file.read_text())["id"])
    for page_id in old:
        print(f"trashing Notion page from an earlier attempt: {page_id}")
        notion.trash(page_id)

    # 2. Upload the frames, create the page, and remember it straight away.
    uploads = {f["file"]: notion.upload_image(outdir / f["file"]) for f in frames}
    blocks = build_blocks(notes, frames, uploads, info, msg["youtube_url"], warnings)
    page = notion.create_page(msg.get("notion_parent_id"), f"{notes['title']} {marker}", blocks[:100])
    tmp = page_file.with_suffix(".tmp")
    tmp.write_text(json.dumps({"id": page["id"], "url": page["url"]}))
    os.replace(tmp, page_file)
    print(f"Notion page created: {page['url']}")
    if first_attempt and os.environ.get("FAULT_CRASH_AFTER_PAGE") == "1":
        print("simulated crash right after creating the Notion page")
        os._exit(1)

    # 3. Add the rest of the content (Notion accepts at most 100 blocks per request).
    notion.append(page["id"], blocks[100:])
    return {"url": page["url"], "frames_published": len(uploads)}
