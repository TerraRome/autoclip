"""Thin YouTube Data API v3 client with quota accounting.

Biaya kuota (per panggilan):
  channels.list / playlistItems.list / videos.list  -> 1 unit (videos.list: s/d 50 ID sekaligus)
  search.list                                        -> bucket terpisah (100 panggilan/hari) — sengaja TIDAK dipakai
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable

import requests

API_BASE = "https://www.googleapis.com/youtube/v3"


class QuotaExceeded(RuntimeError):
    pass


class YouTubeAPIError(RuntimeError):
    pass


@dataclass
class Channel:
    id: str
    title: str
    subscribers: int | None  # None kalau channel menyembunyikan jumlah subscriber
    uploads_playlist: str


@dataclass
class Video:
    id: str
    title: str
    channel_id: str
    channel_title: str
    published_at: datetime
    duration_s: int
    views: int
    likes: int
    comments: int
    was_live: bool
    subscribers: int | None = None
    extra: dict = field(default_factory=dict)

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.id}"


_DURATION_RE = re.compile(
    r"P(?:(?P<d>\d+)D)?(?:T(?:(?P<h>\d+)H)?(?:(?P<m>\d+)M)?(?:(?P<s>\d+)S)?)?"
)


def parse_iso_duration(value: str) -> int:
    """'PT1H2M3S' -> 3723. Live yang sedang berjalan bernilai 'P0D' -> 0."""
    m = _DURATION_RE.fullmatch(value or "")
    if not m:
        return 0
    d, h, mi, s = (int(m.group(k) or 0) for k in ("d", "h", "m", "s"))
    return ((d * 24 + h) * 60 + mi) * 60 + s


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _chunks(items: list[str], size: int = 50) -> Iterable[list[str]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


class YouTubeAPI:
    def __init__(self, api_key: str, session: requests.Session | None = None, timeout: float = 20):
        if not api_key:
            raise ValueError("API key kosong. Isi YOUTUBE_API_KEY di file .env")
        self.api_key = api_key
        self.session = session or requests.Session()
        self.timeout = timeout
        self.units_used = 0

    # ------------------------------------------------------------------ core
    def _get(self, endpoint: str, cost: int = 1, **params) -> dict:
        params["key"] = self.api_key
        resp = self.session.get(f"{API_BASE}/{endpoint}", params=params, timeout=self.timeout)
        self.units_used += cost  # request gagal pun tetap memakan kuota
        if resp.status_code == 403 and "quotaExceeded" in resp.text:
            raise QuotaExceeded("Kuota harian habis. Reset tiap tengah malam waktu Pasifik (~14:00-15:00 WIB).")
        if resp.status_code >= 400:
            try:
                msg = resp.json()["error"]["message"]
            except Exception:
                msg = resp.text[:300]
            raise YouTubeAPIError(f"{endpoint} HTTP {resp.status_code}: {msg}")
        return resp.json()

    # -------------------------------------------------------------- channels
    def resolve_channel_ids(self, refs: Iterable[str]) -> list[str]:
        """Terima '@handle', 'handle', 'UC...' atau URL channel. 1 unit per handle."""
        ids: list[str] = []
        for raw in refs:
            ref = raw.strip()
            if not ref or ref.startswith("#"):
                continue
            m = re.search(r"youtube\.com/(?:channel/(UC[\w-]{22})|@([\w.\-]+))", ref)
            if m:
                ref = m.group(1) or "@" + m.group(2)
            if re.fullmatch(r"UC[\w-]{22}", ref):
                ids.append(ref)
                continue
            data = self._get("channels", part="id", forHandle=ref.lstrip("@"))
            items = data.get("items") or []
            if not items:
                print(f"  ! channel tidak ditemukan: {raw}")
                continue
            ids.append(items[0]["id"])
        return list(dict.fromkeys(ids))

    def channels(self, channel_ids: list[str]) -> dict[str, Channel]:
        out: dict[str, Channel] = {}
        for batch in _chunks(channel_ids):
            data = self._get("channels", part="snippet,statistics,contentDetails", id=",".join(batch), maxResults=50)
            for it in data.get("items", []):
                stats = it.get("statistics", {})
                subs = None if stats.get("hiddenSubscriberCount") else int(stats.get("subscriberCount", 0))
                out[it["id"]] = Channel(
                    id=it["id"],
                    title=it["snippet"]["title"],
                    subscribers=subs,
                    uploads_playlist=it["contentDetails"]["relatedPlaylists"]["uploads"],
                )
        return out

    def recent_upload_ids(self, uploads_playlist: str, max_results: int = 10) -> list[str]:
        data = self._get(
            "playlistItems", part="contentDetails", playlistId=uploads_playlist, maxResults=min(max_results, 50)
        )
        return [it["contentDetails"]["videoId"] for it in data.get("items", [])]

    # ---------------------------------------------------------------- videos
    def videos(self, video_ids: list[str]) -> list[Video]:
        out: list[Video] = []
        for batch in _chunks(list(dict.fromkeys(video_ids))):
            data = self._get(
                "videos", part="snippet,statistics,contentDetails,liveStreamingDetails", id=",".join(batch), maxResults=50
            )
            out.extend(self._to_video(it) for it in data.get("items", []))
        return out

    def most_popular(self, region: str = "ID", category_id: str | None = "20", max_results: int = 50) -> list[Video]:
        """Video trending. Kategori 20 = Gaming. 1 unit."""
        params = dict(
            part="snippet,statistics,contentDetails,liveStreamingDetails",
            chart="mostPopular",
            regionCode=region,
            maxResults=min(max_results, 50),
        )
        if category_id:
            params["videoCategoryId"] = category_id
        data = self._get("videos", **params)
        return [self._to_video(it) for it in data.get("items", [])]

    @staticmethod
    def _to_video(it: dict) -> Video:
        sn, st = it["snippet"], it.get("statistics", {})
        return Video(
            id=it["id"],
            title=sn["title"],
            channel_id=sn["channelId"],
            channel_title=sn["channelTitle"],
            published_at=_parse_time(sn["publishedAt"]),
            duration_s=parse_iso_duration(it.get("contentDetails", {}).get("duration", "")),
            views=int(st.get("viewCount", 0)),
            likes=int(st.get("likeCount", 0)),  # bisa 0 kalau like disembunyikan
            comments=int(st.get("commentCount", 0)),
            was_live="liveStreamingDetails" in it,
            extra={"live_status": sn.get("liveBroadcastContent", "none")},
        )
