"""Transkrip otomatis YouTube (teks saja, tanpa video) untuk tahu apa yang diucapkan di sekitar momen."""
from __future__ import annotations

import json
from pathlib import Path


def pick_caption_lang(info: dict) -> tuple[str, bool] | None:
    """(kode bahasa, is_auto). Utamakan subtitle manual, lalu auto-caption bahasa asli video."""
    lang = (info.get("language") or "").split("-")[0]
    manual = {k: v for k, v in (info.get("subtitles") or {}).items() if k != "live_chat"}
    auto = info.get("automatic_captions") or {}
    for cand in (lang, "id", "en", "ja"):
        if cand and cand in manual:
            return cand, False
    for cand in (f"{lang}-orig", lang, "id-orig", "en-orig", "ja-orig", "id", "en"):
        if cand and cand in auto:
            return cand, True
    return None


def download_transcript(video_id: str, info: dict, cache_dir: Path, cookies_file: str | dict | None = None) -> Path | None:
    choice = pick_caption_lang(info)
    if not choice:
        return None
    lang, is_auto = choice
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{video_id}.{lang}.json3"
    if path.exists() and path.stat().st_size > 0:
        return path
    import yt_dlp

    opts = {
        "skip_download": True,
        "writesubtitles": not is_auto,
        "writeautomaticsub": is_auto,
        "subtitleslangs": [lang],
        "subtitlesformat": "json3",
        "outtmpl": str(cache_dir / "%(id)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
    }
    if cookies_file:  # AutoClip passes a dict of yt-dlp options (browser cookies, JS runtime)
        opts.update(cookies_file if isinstance(cookies_file, dict) else {"cookiefile": cookies_file})
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([f"https://www.youtube.com/watch?v={video_id}"])
    return path if path.exists() else None


def parse_json3(path: Path) -> list[tuple[float, str]]:
    """Format json3 YouTube -> [(detik_mulai, teks)], baris kosong dan duplikat beruntun dibuang."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    out: list[tuple[float, str]] = []
    for ev in data.get("events", []):
        text = "".join(s.get("utf8", "") for s in ev.get("segs") or []).replace("\n", " ").strip()
        if text and (not out or out[-1][1] != text):
            out.append((ev.get("tStartMs", 0) / 1000, text))
    return out


def excerpt(lines: list[tuple[float, str]], start: float, end: float) -> list[tuple[float, str]]:
    return [(t, txt) for t, txt in lines if start <= t <= end]
