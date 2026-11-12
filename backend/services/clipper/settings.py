"""Clipper settings for this machine: YouTube Data API key, whisper.cpp paths, kamus, Claude model.

Stored as JSON in the AutoClip data folder (never uploaded). Environment variables win so a
power user can still run everything from `.env` like the standalone Clipper.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

_lock = threading.Lock()

DEFAULTS = {
    "youtube_api_key": "",
    "channels": "",            # one @handle / channel ID / URL per line
    "region": "ID",
    "category": "20",          # 20 = Gaming
    "whisper_cpp_enabled": False,
    "whisper_cpp_binary": "",  # path to whisper-cli(.exe)
    "whisper_cpp_model": "",   # path to ggml-*.bin
    "whisper_language": "auto",
    "kamus": "",               # correction dictionary (Clipper kamus.txt format)
    "claude_model": "sonnet",
    "claude_bridge": True,     # serve the OpenAI-compatible bridge for `claude -p`
}
SECRET_KEYS = {"youtube_api_key"}


def path() -> Path:
    from backend.core.path_utils import get_data_directory
    folder = get_data_directory() / "clipper"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "settings.json"


def load() -> dict:
    with _lock:
        try:
            data = json.loads(path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
    merged = {**DEFAULTS, **{k: v for k, v in data.items() if k in DEFAULTS}}
    if os.environ.get("YOUTUBE_API_KEY"):
        merged["youtube_api_key"] = os.environ["YOUTUBE_API_KEY"]
    return merged


def save(update: dict) -> dict:
    current = load()
    for key, value in update.items():
        if key not in DEFAULTS:
            continue
        if key in SECRET_KEYS and value in (None, ""):
            continue  # blank keeps the saved secret
        kind = type(DEFAULTS[key])
        current[key] = (value is True or str(value).lower() in ("1", "true", "on")) if kind is bool else kind(value or "")
    with _lock:
        target = path()
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(target)
    return current


def public(data: dict | None = None) -> dict:
    """Settings for the UI: secrets only say whether they are set."""
    data = data or load()
    out = {k: v for k, v in data.items() if k not in SECRET_KEYS}
    key = data.get("youtube_api_key") or ""
    out["youtube_api_key_set"] = bool(key)
    out["youtube_api_key_tail"] = key[-4:] if key else ""
    return out
