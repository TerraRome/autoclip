"""Sinyal pengganti heatmap 'Most Replayed' (untuk video yang belum punya heatmap).

1. Live chat replay  -> lonjakan chat (plus kata 'hype' seperti www / 草 / wkwk / lol / 😂)
2. Komentar          -> timestamp yang ditulis penonton ("1:07:22 ▶ momen lucu")

Keduanya diubah ke format yang sama dengan heatmap yt-dlp
([{"start_time", "end_time", "value"}], value 0..1) supaya bisa langsung dipakai find_peaks().
Tidak ada video yang di-download: chat hanya file teks JSON, komentar lewat metadata.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from statistics import median

# "1:07:22", "01:07:22", "7:22", "07:22" (tidak menangkap jam seperti "12:30 WIB" secara khusus;
# timestamp di luar durasi video dibuang di bawah)
_TS_RE = re.compile(r"(?<![\d:])(?:(\d{1,2}):)?([0-5]?\d):([0-5]\d)(?![\d:])")
# baris komentar yang cuma penanda "stream mulai di sini", bukan momen seru
_START_MARKER_RE = re.compile(r"\b(start|starts|mulai|begin|skip|intro)\b|開始|スタート", re.I)
_HYPE_RE = re.compile(
    r"w{3,}|ｗ{2,}|草|kusa|lol|lmao|lmfao|rofl|kwkw|wkwk|haha|hehe|kek|omg|wtf|"
    r"😂|🤣|💀|😭|:_laugh:|:_kusa:|!{2,}|\?{2,}|！{2,}|？{2,}",
    re.I,
)


@dataclass
class TimestampNote:
    time: float
    text: str  # baris komentar tempat timestamp itu ditulis
    likes: int


def parse_ts(h: str | None, m: str, s: str) -> int:
    return int(h or 0) * 3600 + int(m) * 60 + int(s)


# ------------------------------------------------------------------ komentar
def comment_timestamps(comments: list[dict], duration: float) -> list[TimestampNote]:
    """Ambil semua timestamp dari komentar (format yt-dlp: {"text", "like_count", ...})."""
    notes: list[TimestampNote] = []
    for c in comments:
        likes = int(c.get("like_count") or 0)
        per_comment: dict[int, list[str]] = {}  # timestamp sama ditulis ulang (mis. versi JP/EN/ID) = satu
        for line in (c.get("text") or "").splitlines():
            for m in _TS_RE.finditer(line):
                t = parse_ts(*m.groups())
                if 0 < t < duration and not _START_MARKER_RE.search(line):
                    per_comment.setdefault(t, []).append(line.strip())
        for t, lines in per_comment.items():
            notes.append(TimestampNote(float(t), " / ".join(dict.fromkeys(lines))[:300], likes))
    notes.sort(key=lambda n: n.time)
    return notes


def comments_to_signal(notes: list[TimestampNote], duration: float, bin_s: float = 5, spread: float = 20) -> list[float]:
    """Tiap timestamp = awal momen, jadi bobotnya disebar ke `spread` detik setelahnya."""
    vals = [0.0] * max(1, math.ceil(duration / bin_s))
    for n in notes:
        w = 1 + math.log1p(n.likes)
        for b in range(int(n.time // bin_s), min(len(vals), int((n.time + spread) // bin_s) + 1)):
            vals[b] += w
    return _normalize(vals)


# ----------------------------------------------------------------- live chat
def parse_live_chat(path: Path) -> list[tuple[float, str]]:
    """File .live_chat.json dari yt-dlp (satu objek JSON per baris) -> [(detik, teks)]."""
    out: list[tuple[float, str]] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            replay = obj.get("replayChatItemAction") or {}
            try:
                t = int(replay.get("videoOffsetTimeMsec", 0)) / 1000
            except (TypeError, ValueError):
                continue
            for act in replay.get("actions", []):
                item = (act.get("addChatItemAction") or {}).get("item") or {}
                r = item.get("liveChatTextMessageRenderer") or item.get("liveChatPaidMessageRenderer")
                if not r:
                    continue  # pesan sistem, membership, dll
                parts = []
                for run in (r.get("message") or {}).get("runs", []):
                    if "text" in run:
                        parts.append(run["text"])
                    elif "emoji" in run:
                        sc = run["emoji"].get("shortcuts") or []
                        parts.append(sc[0] if sc else run["emoji"].get("emojiId", ""))
                out.append((t, "".join(parts)))
    return out


def chat_to_signal(
    messages: list[tuple[float, str]],
    duration: float,
    bin_s: float = 5,
    reaction_delay: float = 8,
    smooth_bins: int = 2,
    baseline_s: float = 300,
    skip_outro: float = 180,
) -> list[float]:
    """Lonjakan chat relatif terhadap keramaian di sekitarnya (bukan jumlah absolut),
    supaya bagian stream yang memang ramai sepanjang waktu tidak mendominasi."""
    n = max(1, math.ceil(duration / bin_s))
    raw = [0.0] * n
    for t, text in messages:
        b = int(max(0.0, t - reaction_delay) // bin_s)  # chat telat beberapa detik dari kejadiannya
        if b < n:
            raw[b] += 2.0 if _HYPE_RE.search(text) else 1.0

    k = smooth_bins
    smooth = [sum(raw[max(0, i - k) : i + k + 1]) / (2 * k + 1) for i in range(n)]
    w = max(1, int(baseline_s // bin_s))
    spike = [max(0.0, smooth[i] - median(smooth[max(0, i - w) : i + w + 1])) for i in range(n)]
    for i in range(max(0, n - int(skip_outro // bin_s)), n):  # "otsukare" di akhir stream
        spike[i] = 0.0
    return _normalize(spike)


# ------------------------------------------------------------------- gabung
def _normalize(vals: list[float]) -> list[float]:
    top = max(vals, default=0)
    return [v / top for v in vals] if top > 0 else vals


def fuse(signals: list[tuple[list[float], float]]) -> list[float]:
    """Rata-rata berbobot dari sinyal yang punya data (sinyal kosong diabaikan)."""
    used = [(s, w) for s, w in signals if s and max(s) > 0]
    if not used:
        return []
    n = max(len(s) for s, _ in used)
    total = sum(w for _, w in used)
    return _normalize([sum(w * (s[i] if i < len(s) else 0) for s, w in used) / total for i in range(n)])


def to_heatmap(vals: list[float], bin_s: float = 5) -> list[dict]:
    return [{"start_time": i * bin_s, "end_time": (i + 1) * bin_s, "value": v} for i, v in enumerate(vals)]


def download_live_chat(video_id: str, cache_dir: Path, cookies_file: str | dict | None = None) -> Path | None:
    """Unduh replay live chat (teks saja, tanpa video). Di-cache: run berikutnya tidak unduh ulang."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{video_id}.live_chat.json"
    if path.exists() and path.stat().st_size > 0:
        return path
    import yt_dlp

    opts = {
        "skip_download": True,
        "writesubtitles": True,
        "subtitleslangs": ["live_chat"],
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
