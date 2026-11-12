"""Ambil heatmap 'Most Replayed' lewat yt-dlp (tanpa download, tanpa kuota API)
lalu ubah jadi jendela timestamp kandidat klip.

Format heatmap dari yt-dlp: [{"start_time": 0.0, "end_time": 76.8, "value": 1.0}, ...]
Biasanya 100 segmen sama panjang, value dinormalisasi 0..1 (1 = paling sering diputar ulang).
"""
from __future__ import annotations

from dataclasses import dataclass
from statistics import median


@dataclass
class Peak:
    start: float
    end: float
    peak_value: float  # nilai heatmap tertinggi di jendela ini (0..1)
    peak_time: float  # titik tengah segmen tertinggi
    prominence: float  # seberapa jauh di atas median video

    @property
    def duration(self) -> float:
        return self.end - self.start

    def download_sections_arg(self) -> str:
        return f"*{self.start:.0f}-{self.end:.0f}"


def fetch_video_info(video_id: str, cookies_file: str | dict | None = None, max_comments: int = 0) -> dict:
    """Metadata lengkap via yt-dlp (heatmap, chapters, subtitles, dll). Tidak mendownload video.
    max_comments > 0 ikut mengambil komentar teratas (lebih lambat)."""
    import yt_dlp  # import lokal: modul lain tetap bisa dipakai tanpa yt-dlp

    opts = {"skip_download": True, "quiet": True, "no_warnings": True}
    if cookies_file:  # AutoClip passes a dict of yt-dlp options (browser cookies, JS runtime)
        opts.update(cookies_file if isinstance(cookies_file, dict) else {"cookiefile": cookies_file})
    if max_comments > 0:
        opts["getcomments"] = True
        # sengaja tanpa comment_sort=top: urutan "top" bisa menyembunyikan komentar highlight yang baru diposting
        opts["extractor_args"] = {"youtube": {"max_comments": [str(max_comments)]}}
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)


def fetch_heatmap(video_id: str, cookies_file: str | dict | None = None) -> list[dict]:
    return fetch_video_info(video_id, cookies_file).get("heatmap") or []


def find_peaks(
    heatmap: list[dict],
    top_k: int = 5,
    min_len: float = 30,
    max_len: float = 90,
    pad: float = 8,
    skip_intro: float = 45,
    rel_threshold: float = 0.35,
) -> list[Peak]:
    """Cari momen paling sering di-replay.

    - Segmen 'panas' = value >= median + rel_threshold * (max - median).
    - Segmen panas yang bersebelahan digabung jadi satu jendela.
    - Jendela dipadding, lalu dipaksa ke rentang [min_len, max_len] dengan
      pusat di segmen tertinggi.
    - Detik-detik awal video (skip_intro) diabaikan karena heatmap hampir selalu
      tinggi di awal (orang baru mulai nonton), bukan karena momennya seru.
    """
    segs = sorted(
        (s for s in heatmap if s.get("end_time", 0) > s.get("start_time", 0)), key=lambda s: s["start_time"]
    )
    if not segs:
        return []
    video_end = segs[-1]["end_time"]
    # intro dibuang sepenuhnya, termasuk dari perhitungan ambang batas
    segs = [s for s in segs if s["start_time"] >= skip_intro] or segs
    values = [s["value"] for s in segs]
    med, top = median(values), max(values)
    if top <= med:  # heatmap datar = tidak ada momen menonjol
        return []
    threshold = med + rel_threshold * (top - med)

    # 1) kelompokkan segmen panas yang bersebelahan
    groups: list[list[dict]] = []
    for s in segs:
        if s["value"] < threshold:
            continue
        if groups and s["start_time"] - groups[-1][-1]["end_time"] <= 1.0:
            groups[-1].append(s)
        else:
            groups.append([s])

    # 2) ubah tiap kelompok jadi jendela klip
    peaks: list[Peak] = []
    for g in groups:
        best = max(g, key=lambda s: s["value"])
        center = (best["start_time"] + best["end_time"]) / 2
        start, end = g[0]["start_time"] - pad, g[-1]["end_time"] + pad

        length = end - start
        if length < min_len:
            start, end = center - min_len / 2, center + min_len / 2
        elif length > max_len:
            start = max(start, center - max_len / 2)
            end = start + max_len

        # geser ke dalam batas video tanpa mengubah panjang
        if start < 0:
            start, end = 0, end - start
        if end > video_end:
            start, end = max(0.0, start - (end - video_end)), video_end

        peaks.append(
            Peak(
                start=round(start, 1),
                end=round(end, 1),
                peak_value=round(best["value"], 3),
                peak_time=round(center, 1),
                prominence=round((best["value"] - med) / (top - med), 3),
            )
        )

    peaks.sort(key=lambda p: p.peak_value, reverse=True)

    # 3) buang jendela yang tumpang tindih dengan jendela yang lebih kuat
    chosen: list[Peak] = []
    for p in peaks:
        if all(p.end <= c.start or p.start >= c.end for c in chosen):
            chosen.append(p)
        if len(chosen) >= top_k:
            break
    return chosen
