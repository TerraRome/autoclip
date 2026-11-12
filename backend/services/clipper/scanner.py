"""Find viral videos before picking moments (Clipper `find_viral.py --channels/--trending`).

Uses the YouTube Data API (key in Settings → Clipper): ~3-10 quota units per run, search is never
used. Score = views/hour (50%), views per subscriber (30%), engagement (20%), as percentiles
among the candidates. Snapshots of earlier runs make views/hour reflect the current surge.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .scoring import score_videos
from .store import SnapshotStore
from .youtube_api import QuotaExceeded, YouTubeAPI


def scan(*, channels: list[str] | None = None, trending: bool = True, region: str = "ID", category: str | None = "20",
         per_channel: int = 10, max_age_days: float = 7, min_duration: int = 600, top: int = 15) -> dict:
    from backend.core.path_utils import get_data_directory
    from . import settings
    key = settings.load().get("youtube_api_key") or ""
    if not key:
        raise ValueError("Isi YouTube Data API key di Pengaturan → Clipper dulu")
    api = YouTubeAPI(key)
    now = datetime.now(timezone.utc)
    db = get_data_directory() / "clipper" / "snapshots.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    store = SnapshotStore(db)
    try:
        video_ids: list[str] = []
        subs: dict[str, int | None] = {}
        refs = [line.strip() for line in (channels or []) if line.strip() and not line.strip().startswith("#")]
        if refs:
            channel_info = api.channels(api.resolve_channel_ids(refs))
            for ch in channel_info.values():
                subs[ch.id] = ch.subscribers
                video_ids += api.recent_upload_ids(ch.uploads_playlist, per_channel)
        videos = api.videos(video_ids) if video_ids else []
        if trending:
            known = {v.id for v in videos}
            videos += [v for v in api.most_popular(region, category or None) if v.id not in known]
        missing = sorted({v.channel_id for v in videos} - subs.keys())
        if missing:
            subs.update({cid: ch.subscribers for cid, ch in api.channels(missing).items()})
        for v in videos:
            v.subscribers = subs.get(v.channel_id)
        candidates = [v for v in videos
                      if v.duration_s >= min_duration and v.extra.get("live_status") == "none"
                      and (now - v.published_at).total_seconds() <= max_age_days * 86400]
        prev = store.previous([v.id for v in candidates], now)
        scored = score_videos(candidates, now, prev)[:top] if candidates else []
        store.save(candidates, now)
        return {
            "videos": [{
                "video_id": s.video.id, "url": s.video.url, "title": s.video.title, "channel": s.video.channel_title,
                "published_at": s.video.published_at.isoformat(), "duration_s": s.video.duration_s,
                "views": s.video.views, "subscribers": s.video.subscribers, "was_live": s.video.was_live,
                "score": round(s.score, 1), "views_per_hour": round(s.velocity, 1), "velocity_source": s.velocity_source,
                "views_per_subscriber": round(s.outlier, 3) if s.outlier is not None else None,
            } for s in scored],
            "candidates": len(videos), "kept": len(candidates), "units_used": api.units_used,
        }
    except QuotaExceeded as error:
        raise ValueError(f"Kuota YouTube Data API habis: {error}") from None
    finally:
        store.close()
