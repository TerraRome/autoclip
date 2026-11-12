"""Alasan kenapa sebuah momen dianggap viral, dari data yang ada (tanpa AI):
seberapa sering diputar ulang (heatmap), seberapa ramai chat dibanding biasanya,
jenis reaksi penonton (tawa / kaget / heboh / sedih / imut), chat yang paling sering muncul,
dan catatan penonton di komentar.
"""
from __future__ import annotations

import re
from collections import Counter
from statistics import median

from .signals import TimestampNote

# (kode, deskripsi, pola). Satu pesan bisa masuk lebih dari satu reaksi.
REACTIONS = [
    ("tawa", "penonton tertawa",
     r"w{3,}|ｗ{2,}|草|kusa|lol|lmao|lmfao|rofl|kwkw|wkwk|haha|hehe|\bxd\b|😂|🤣|:_laugh:|:_kusa:|:face-blue-smiling:|:rolling_on_the_floor_laughing:|"
     r":face_with_tears_of_joy:|笑"),
    ("kaget", "penonton kaget / bingung",
     r"\?{2,}|？{2,}|!\?|！？|\bwtf\b|\bomg\b|\bwhat\b|\beh\b|nani|えっ|は？|😱|😨|😳|anjir|hah\?"),
    ("heboh", "penonton heboh / bersorak",
     r"!{2,}|！{2,}|\bgg\b|pog|\bnice\b|let'?s go|lesgo|yatta|やった|888|すご|sugoi|gila|mantap|"
     r"anjay|keren|clutch|🔥|👏|:clapping_hands:|:_ggez:|:fire:|kyaa|ナイス"),
    ("sedih", "momen sedih / haru",
     r"\bsad\b|😭|\brip\b|;;|泣|sedih|kasihan|かわいそう|nooo+"),
    ("imut", "momen imut / wholesome",
     r"kawaii|かわいい|可愛|\bcute\b|imut|gemes|❤|♥|💕|:_heart:|teetee|てぇてぇ|tete"),
]
_COMPILED = [(code, desc, re.compile(p, re.I)) for code, desc, p in REACTIONS]


def classify(text: str) -> list[str]:
    return [code for code, _, rx in _COMPILED if rx.search(text)]


def _norm_msg(text: str) -> str:
    t = re.sub(r"(:[\w-]+:)(?:\s*\1)+", r"\1", text.strip().lower())  # ":x::x::x:" -> ":x:"
    t = re.sub(r"(.)\1{3,}", r"\1\1\1\1", t)  # "wwwwwwww" -> "wwww"
    return re.sub(r"\s+", " ", t)[:40]


def _readable(note: str) -> str:
    """Komentar highlight sering ditulis dalam beberapa bahasa ("JP / EN / ID"):
    pilih versi berhuruf latin yang terakhir (biasanya EN atau ID)."""
    parts = [p.strip() for p in note.split(" / ") if p.strip()]
    latin = [p for p in parts if sum(c.isascii() for c in p) >= 0.7 * len(p)]
    return (latin or parts or [note])[-1]


def chat_baseline_per_min(messages: list[tuple[float, str]], duration: float, skip_intro: float = 300) -> float:
    minutes = int(duration // 60) + 1
    counts = [0] * minutes
    for t, _ in messages:
        if 0 <= t < duration:
            counts[int(t // 60)] += 1
    body = counts[int(skip_intro // 60):] or counts
    return median(body) or (sum(body) / len(body) if body else 0)


def explain(
    start: float,
    end: float,
    chat: list[tuple[float, str]],
    chat_baseline: float,
    notes: list[TimestampNote],
    heat: float | None = None,
    heat_median: float | None = None,
    reaction_delay: float = 8,
) -> dict:
    """Kembalikan {"summary": str, "tags": [...], "details": [...], angka-angka pendukung}."""
    details: list[str] = []
    summary: list[str] = []
    out: dict = {"tags": []}

    # 1) heatmap: seberapa sering bagian ini diputar ulang
    if heat is not None and heat_median:
        ratio = heat / heat_median
        out["replay_ratio"] = round(ratio, 1)
        details.append(f"Diputar ulang {ratio:.1f}x lebih sering dari rata-rata bagian lain video ini")
        summary.append(f"diputar ulang {ratio:.1f}x")

    # 2) chat: keramaian + jenis reaksi
    window = [txt for t, txt in chat if start <= t - reaction_delay <= end]
    if window:
        per_min = len(window) / max((end - start) / 60, 0.5)
        out["chat_per_min"] = round(per_min, 1)
        if chat_baseline > 0:
            ratio = per_min / chat_baseline
            out["chat_ratio"] = round(ratio, 1)
            details.append(f"Chat {ratio:.1f}x lebih ramai dari biasanya "
                           f"({per_min:.0f} pesan/menit, normalnya {chat_baseline:.0f})")
            if ratio >= 1.5:
                summary.append(f"chat {ratio:.1f}x lebih ramai")

        counts = Counter(code for txt in window for code in classify(txt))
        shares = {code: n / len(window) for code, n in counts.items()}
        out["reactions"] = {code: round(s, 2) for code, s in sorted(shares.items(), key=lambda x: -x[1])}
        top = [(code, s) for code, s in sorted(shares.items(), key=lambda x: -x[1]) if s >= 0.15][:2]
        if top:
            desc = {code: d for code, d, _ in REACTIONS}
            out["tags"] = [code for code, _ in top]
            details.append("Reaksi dominan: " + ", ".join(f"{desc[c]} ({s:.0%} chat)" for c, s in top))
            summary.insert(0, desc[top[0][0]].capitalize())

        common = [(m, n) for m, n in Counter(_norm_msg(t) for t in window if t.strip()).most_common(4) if n >= 2]
        if common:
            out["top_chat"] = [{"text": m, "count": n} for m, n in common]
            details.append("Chat paling sering: " + ", ".join(f'"{m}" x{n}' for m, n in common))

    # 3) komentar: penonton menandai momen ini
    marked = [n for n in notes if start - 10 <= n.time <= end]
    if marked:
        out["comment_notes"] = [n.text for n in marked]
        details.append(f'Ditandai penonton di komentar: "{_readable(marked[0].text)[:120]}"')
        summary.append("ditandai di komentar")

    text = " — ".join([summary[0], ", ".join(summary[1:])] if len(summary) > 1 else summary)
    out["summary"] = (text[:1].upper() + text[1:]) if text else "Lonjakan sinyal tanpa detail tambahan"
    out["details"] = details
    return out
