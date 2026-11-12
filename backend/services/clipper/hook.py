"""Kesimpulan per momen dengan Claude: apa yang terjadi, kenapa lucu/menarik, hook pembuka, judul.

Input yang dikirim hanya teks: potongan transkrip otomatis, contoh chat penonton, catatan komentar,
dan ringkasan sinyal. Satu request per video (semua momen sekaligus).
Lewat langganan Claude (`claude -p`, lihat claude_cli.py) atau ANTHROPIC_API_KEY di .env.
"""
from __future__ import annotations

import random
from typing import Literal

from pydantic import BaseModel, Field

from .reasons import classify

MODEL = "claude-opus-5-5"

SYSTEM = """Kamu editor klip pendek (YouTube Shorts / TikTok / Reels) untuk konten streamer dan VTuber, \
dengan penonton Indonesia.

Untuk tiap momen kamu menerima: potongan transkrip otomatis (sering salah dengar, campur bahasa \
Indonesia/Inggris/Jepang), contoh chat penonton pada saat itu, catatan penonton di komentar, \
dan ringkasan sinyal (seberapa ramai chat, jenis reaksi).

Tugasmu per momen:
- kejadian: apa yang sebenarnya terjadi, 1-2 kalimat, konkret (siapa melakukan apa).
- kenapa_menarik: kenapa penonton bereaksi (lucu karena apa: salah paham, gagal konyol, reaksi \
berlebihan, plot twist, dll). Sebutkan bukti dari transkrip/chat.
- hook: kalimat pembuka untuk 1-3 detik pertama klip, bahasa Indonesia santai, maksimal 12 kata. \
Bikin penasaran tapi JANGAN bocorkan punchline-nya.
- judul: judul klip maksimal 60 karakter, bahasa Indonesia santai, boleh 1 emoji.
- yakin: "tinggi" kalau transkrip dan chat jelas mendukung, "sedang" kalau sebagian ditebak, \
"rendah" kalau transkrip terlalu kacau.

Jangan mengarang detail yang tidak ada di data. Kalau tidak jelas, katakan apa yang tidak jelas \
di kejadian dan beri yakin "rendah"."""


class MomentHook(BaseModel):
    index: int = Field(description="nomor momen sesuai input, mulai dari 1")
    kejadian: str
    kenapa_menarik: str
    hook: str
    judul: str
    yakin: Literal["tinggi", "sedang", "rendah"]


class HookResult(BaseModel):
    moments: list[MomentHook]


def _fmt(sec: float) -> str:
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def sample_chat(chat: list[tuple[float, str]], start: float, end: float, k: int = 40,
                reaction_delay: float = 8) -> list[tuple[float, str]]:
    """Contoh chat di jendela momen: utamakan pesan bertipe reaksi, sisanya acak, urut waktu."""
    window = [(t, txt) for t, txt in chat if start <= t - reaction_delay <= end and txt.strip()]
    reactive = [m for m in window if classify(m[1])]
    other = [m for m in window if not classify(m[1])]
    rng = random.Random(int(start))  # deterministik: hasil sama tiap run
    picked = rng.sample(reactive, min(len(reactive), k // 2))
    picked += rng.sample(other, min(len(other), k - len(picked)))
    return sorted(picked)


def build_prompt(title: str, channel: str, moments: list[dict]) -> str:
    """moments: dict berisi start, end, peak_time, reason, transcript [(t, teks)], chat [(t, teks)]."""
    parts = [f"Video: {title}\nChannel: {channel}\n"]
    for i, m in enumerate(moments, 1):
        r = m["reason"]
        parts.append(f"=== MOMEN {i}: {_fmt(m['start'])} - {_fmt(m['end'])} (puncak {_fmt(m['peak_time'])}) ===")
        parts.append("Sinyal: " + "; ".join(r.get("details") or [r.get("summary", "")]))
        tr = m.get("transcript") or []
        parts.append("Transkrip otomatis:\n" + ("\n".join(f"[{_fmt(t)}] {txt}" for t, txt in tr)
                                              if tr else "(tidak ada transkrip)"))
        ch = m.get("chat") or []
        if ch:
            parts.append("Contoh chat penonton:\n" + "\n".join(f"[{_fmt(t)}] {txt[:120]}" for t, txt in ch))
        parts.append("")
    parts.append("Buat kesimpulan untuk SEMUA momen di atas, satu entri per momen, index sesuai nomor momen.")
    return "\n".join(parts)


def generate_hooks(title: str, channel: str, moments: list[dict]) -> list[MomentHook]:
    """Hook & judul per momen lewat langganan Claude (`claude -p`)."""
    from . import claude_cli
    return claude_cli.ask(SYSTEM, build_prompt(title, channel, moments), HookResult).moments
