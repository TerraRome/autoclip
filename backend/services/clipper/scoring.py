"""Skor viral untuk memilih video sumber.

Tiga sinyal, masing-masing diubah ke persentil di dalam batch kandidat (0..1)
supaya skala channel besar/kecil tidak saling menenggelamkan:

  velocity   (bobot 0.5) : views per jam. Pakai selisih snapshot kalau ada,
                           kalau belum ada pakai views / umur video.
  outlier    (bobot 0.3) : views / subscriber. Video yang jauh melampaui
                           ukuran channel-nya = sedang "meledak".
  engagement (bobot 0.2) : (likes + 2*comments) / views.

Skor akhir 0..100.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .youtube_api import Video

WEIGHTS = {"velocity": 0.5, "outlier": 0.3, "engagement": 0.2}


@dataclass
class Scored:
    video: Video
    score: float
    velocity: float  # views/jam
    velocity_source: str  # "snapshot" | "lifetime"
    outlier: float | None
    engagement: float
    age_hours: float


def _percentiles(values: list[float | None]) -> list[float]:
    """Persentil rank (0..1). None -> 0.5 (netral)."""
    known = sorted(v for v in values if v is not None)
    n = len(known)
    out = []
    for v in values:
        if v is None or n == 0:
            out.append(0.5)
        elif n == 1:
            out.append(1.0)
        else:
            below = sum(1 for k in known if k < v)
            equal = sum(1 for k in known if k == v)
            out.append((below + (equal - 1) / 2) / (n - 1))
    return out


def score_videos(
    videos: list[Video],
    now: datetime,
    previous_views: dict[str, tuple[datetime, int]] | None = None,
) -> list[Scored]:
    previous_views = previous_views or {}
    rows = []
    for v in videos:
        age_h = max((now - v.published_at).total_seconds() / 3600, 1.0)
        prev = previous_views.get(v.id)
        if prev and (now - prev[0]).total_seconds() >= 15 * 60 and v.views >= prev[1]:
            hours = (now - prev[0]).total_seconds() / 3600
            vel, src = (v.views - prev[1]) / hours, "snapshot"
        else:
            vel, src = v.views / age_h, "lifetime"
        outlier = v.views / v.subscribers if v.subscribers else None
        engagement = (v.likes + 2 * v.comments) / v.views if v.views else 0.0
        rows.append((v, vel, src, outlier, engagement, age_h))

    p_vel = _percentiles([r[1] for r in rows])
    p_out = _percentiles([r[3] for r in rows])
    p_eng = _percentiles([r[4] for r in rows])

    scored = [
        Scored(
            video=r[0],
            score=round(
                100 * (WEIGHTS["velocity"] * pv + WEIGHTS["outlier"] * po + WEIGHTS["engagement"] * pe), 1
            ),
            velocity=r[1],
            velocity_source=r[2],
            outlier=r[3],
            engagement=r[4],
            age_hours=r[5],
        )
        for r, pv, po, pe in zip(rows, p_vel, p_out, p_eng)
    ]
    return sorted(scored, key=lambda s: s.score, reverse=True)
