"""Snapshot views di SQLite supaya bisa menghitung kecepatan views (views/jam)
antar-run, bukan cuma rata-rata seumur video."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from .youtube_api import Video


class SnapshotStore:
    def __init__(self, path: str | Path):
        self.db = sqlite3.connect(str(path))
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS snapshots (
                   video_id TEXT NOT NULL,
                   taken_at TEXT NOT NULL,
                   views    INTEGER NOT NULL,
                   likes    INTEGER,
                   comments INTEGER,
                   PRIMARY KEY (video_id, taken_at))"""
        )

    def previous(self, video_ids: list[str], now: datetime, min_gap_minutes: int = 15) -> dict[str, tuple[datetime, int]]:
        """Snapshot terbaru yang umurnya minimal `min_gap_minutes` per video."""
        cutoff = (now - timedelta(minutes=min_gap_minutes)).isoformat()
        out: dict[str, tuple[datetime, int]] = {}
        for vid in video_ids:
            row = self.db.execute(
                "SELECT taken_at, views FROM snapshots WHERE video_id=? AND taken_at<=? ORDER BY taken_at DESC LIMIT 1",
                (vid, cutoff),
            ).fetchone()
            if row:
                out[vid] = (datetime.fromisoformat(row[0]), row[1])
        return out

    def save(self, videos: list[Video], now: datetime) -> None:
        ts = now.isoformat()
        self.db.executemany(
            "INSERT OR REPLACE INTO snapshots VALUES (?,?,?,?,?)",
            [(v.id, ts, v.views, v.likes, v.comments) for v in videos],
        )
        self.db.commit()

    def close(self) -> None:
        self.db.close()
