"""Find the viral moments of one YouTube video before downloading it (Clipper `find_viral.py --url`).

Source of truth, in order:
1. YouTube "Most replayed" heatmap (free, no API quota).
2. Without a heatmap: live-chat replay spikes (text only, cached) fused with timestamps that
   viewers wrote in the comments. The first 5 minutes of a stream are skipped (waiting screen).

Each moment carries a plain-language reason (`reasons.explain`) and the transcript lines around it.
Nothing here downloads video.
"""
from __future__ import annotations

import logging
import re
import threading
import time
import uuid
from pathlib import Path
from statistics import median
from typing import Callable

from .heatmap import fetch_video_info, find_peaks
from .reasons import chat_baseline_per_min, explain
from .signals import (
    chat_to_signal,
    comment_timestamps,
    comments_to_signal,
    download_live_chat,
    fuse,
    parse_live_chat,
    to_heatmap,
)
from .transcript import download_transcript, excerpt, parse_json3

logger = logging.getLogger(__name__)

_VIDEO_ID_RE = re.compile(r"(?:v=|youtu\.be/|shorts/|live/|embed/)([\w-]{11})")


def extract_video_id(url: str) -> str | None:
    url = (url or "").strip()
    if re.fullmatch(r"[\w-]{11}", url):
        return url
    m = _VIDEO_ID_RE.search(url)
    return m.group(1) if m else None


def _cache_dir() -> Path:
    from backend.core.path_utils import get_data_directory
    path = get_data_directory() / "clipper"
    path.mkdir(parents=True, exist_ok=True)
    return path


def ydl_options(browser: str | None = None) -> dict:
    """yt-dlp options AutoClip uses everywhere: JS runtime for YouTube, optional browser cookies."""
    from backend.utils.ffmpeg_utils import ytdlp_js_runtimes
    opts = {**ytdlp_js_runtimes()}
    if browser:
        opts["cookiesfrombrowser"] = (browser.lower(),)
    return opts


def find_moments(
    url: str,
    *,
    browser: str | None = None,
    peaks: int = 5,
    clip_min: float = 30,
    clip_max: float = 90,
    comments: int = 300,
    hooks: bool = False,
    progress: Callable[[str], None] = lambda message: None,
) -> dict:
    vid = extract_video_id(url)
    if not vid:
        raise ValueError("Link YouTube tidak dikenali")
    url = f"https://www.youtube.com/watch?v={vid}"
    opts = ydl_options(browser)
    cache = _cache_dir()

    progress("Membaca info video…")
    info = fetch_video_info(vid, opts)
    duration = float(info.get("duration") or 0)
    heatmap = info.get("heatmap") or []

    chat: list[tuple[float, str]] = []
    if "live_chat" in (info.get("subtitles") or {}):
        progress("Mengunduh replay live chat (teks saja)…")
        try:
            path = download_live_chat(vid, cache / "chat", opts)
            chat = parse_live_chat(path) if path else []
        except Exception as error:  # noqa: BLE001 - chat is one signal of several
            logger.warning("Live chat unavailable: %s", str(error).splitlines()[0][:160] if str(error) else type(error).__name__)

    notes = []
    if comments > 0:
        progress("Membaca timestamp di komentar…")
        try:
            with_comments = fetch_video_info(vid, opts, max_comments=comments)
            notes = comment_timestamps(with_comments.get("comments") or [], duration)
        except Exception as error:  # noqa: BLE001
            logger.warning("Comments unavailable: %s", type(error).__name__)

    progress("Mencari puncak…")
    if heatmap:
        found = find_peaks(heatmap, top_k=peaks, min_len=clip_min, max_len=clip_max)
        heat_median = median(s["value"] for s in heatmap if s["start_time"] >= 45) or None
        source = "heatmap"
    else:
        chat_sig = chat_to_signal(chat, duration) if chat else []
        comment_sig = comments_to_signal(notes, duration) if notes else []
        fused = fuse([(chat_sig, 0.6), (comment_sig, 0.4)])
        found = find_peaks(to_heatmap(fused), top_k=peaks, min_len=clip_min, max_len=clip_max, skip_intro=300)
        heat_median = None
        source = "+".join(name for name, sig in (("chat", chat_sig), ("komentar", comment_sig)) if sig and max(sig) > 0) or "tidak ada"

    baseline = chat_baseline_per_min(chat, duration) if chat else 0
    moments = []
    for p in found:
        moments.append({
            "start": p.start, "end": p.end, "peak_time": p.peak_time,
            "heat": p.peak_value, "prominence": p.prominence,
            "reason": explain(p.start, p.end, chat, baseline, notes,
                              heat=p.peak_value if heatmap else None, heat_median=heat_median),
        })

    if moments:
        progress("Mengambil transkrip di sekitar momen…")
        try:
            path = download_transcript(vid, info, cache / "subs", opts)
            lines = parse_json3(path) if path else []
        except Exception as error:  # noqa: BLE001
            logger.warning("Transcript unavailable: %s", type(error).__name__)
            lines = []
        for m in moments:
            # setup lelucon biasanya diucapkan sebelum puncaknya
            m["transcript"] = [{"t": t, "text": txt} for t, txt in excerpt(lines, m["start"] - 45, m["end"] + 10)][:60]

    hook_error = ""
    if hooks and moments:
        progress("Meminta hook & judul ke Claude…")
        try:
            from .hook import generate_hooks, sample_chat
            payload = [{**m, "transcript": [(x["t"], x["text"]) for x in m.get("transcript", [])],
                        "chat": sample_chat(chat, m["start"], m["end"])} for m in moments]
            for h in generate_hooks(info.get("title") or vid, info.get("channel") or info.get("uploader") or "", payload):
                if 1 <= h.index <= len(moments):
                    moments[h.index - 1]["hook"] = h.model_dump(exclude={"index"})
        except Exception as error:  # noqa: BLE001 - moments stay usable without hooks
            hook_error = str(error).splitlines()[0][:200] if str(error) else type(error).__name__
            logger.warning("Hooks failed: %s", hook_error)

    return {
        "hook_error": hook_error,
        "video_id": vid,
        "url": url,
        "title": info.get("title") or vid,
        "channel": info.get("channel") or info.get("uploader") or "",
        "duration_s": duration,
        "views": info.get("view_count"),
        "was_live": info.get("was_live"),
        "thumbnail": info.get("thumbnail"),
        "heatmap_available": bool(heatmap),
        "source": source,
        "moments": moments,
        "comment_timestamps": [{"time": n.time, "text": n.text, "likes": n.likes} for n in notes[:30]],
    }


# ------------------------------------------------------------------ background jobs
# Live chat of a long stream can take minutes; the UI polls instead of holding a request open.
_jobs: dict[str, dict] = {}
_lock = threading.Lock()
JOB_TTL = 3600


def start_job(url: str, browser: str | None = None, hooks: bool = False) -> str:
    job_id = uuid.uuid4().hex[:12]
    now = time.time()
    with _lock:
        for key in [k for k, v in _jobs.items() if now - v["created"] > JOB_TTL]:
            _jobs.pop(key, None)
        _jobs[job_id] = {"status": "running", "message": "Memulai…", "result": None, "error": "", "created": now}

    def update(**fields):
        with _lock:
            _jobs[job_id].update(fields)

    def run():
        try:
            result = find_moments(url, browser=browser, hooks=hooks, progress=lambda message: update(message=message))
            update(status="done", result=result, message="Selesai")
        except Exception as error:  # noqa: BLE001 - reported to the UI
            text = str(error).splitlines()[0][:300] if str(error) else type(error).__name__
            logger.warning("Viral finder failed: %s", text)
            update(status="failed", error=text)

    threading.Thread(target=run, name=f"clipper-{job_id}", daemon=True).start()
    return job_id


def job_status(job_id: str) -> dict | None:
    with _lock:
        job = _jobs.get(job_id)
        return None if job is None else {k: v for k, v in job.items() if k != "created"}
