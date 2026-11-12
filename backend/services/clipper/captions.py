"""Word-by-word captions in Project Clipper's style (`reframe.write_ass`).

Chunks of 1-3 words (max 16 characters) pop in one after another; numbers, shouted words and the
longest word of the line turn yellow. On the streamer split layout the caption sits just under the
facecam panel, over the gameplay; a VTuber avatar gets Clipper's pink outline.
"""
from __future__ import annotations

import re

OUT_H = 1920
YELLOW = {'face': '&H0000D7FF&', 'anime': '&H0000E1FF&'}
OUTLINE = {'face': '&H00000000&', 'anime': '&H00A0389A&'}
WHITE = '&H00FFFFFF&'
_EMOJI = re.compile("[\U00010000-\U0010FFFF☀-➿️‍]")


def clean_text(text: str) -> str:
    """libass cannot draw colour emoji (they become boxes)."""
    return re.sub(r"\s{2,}", " ", _EMOJI.sub("", text or "")).strip(" -—|")


def _esc(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "(").replace("}", ")").replace("\n", " ")


def keywords(words: list[str], highlights: set[str] | None = None) -> set[str]:
    """Numbers, ALL-CAPS words, the longest word (6+ letters) of the line, plus the model's highlights."""
    hi = {w for w in words if any(ch.isdigit() for ch in w) or (len(w) >= 2 and w.isupper())}
    plain = [w for w in words if len(w.strip(".,!?\"'")) >= 6]
    if plain:
        hi.add(max(plain, key=lambda w: len(w.strip(".,!?\"'"))))
    for w in words:
        if highlights and w.strip(".,!?;:\"'").lower() in highlights:
            hi.add(w)
    return hi


def word_chunks(text: str, start: float, end: float, max_words: int = 3, max_chars: int = 16):
    """[(start, end, [words])], time shared by word length; a pause mark ends a chunk."""
    words = clean_text(text).split()
    if not words:
        return []
    groups, cur = [], []
    for w in words:
        if cur and (len(cur) >= max_words or len(" ".join(cur + [w])) > max_chars):
            groups.append(cur)
            cur = []
        cur.append(w)
        if w[-1:] in ".!?,":
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    weights = [sum(len(w) for w in g) + 2 for g in groups]
    total, t, out = sum(weights), start, []
    for g, weight in zip(groups, weights):
        d = (end - start) * weight / total
        out.append((t, t + d, g))
        t += d
    return out


def caption_y(split) -> int:
    """Centre of the caption line: under the facecam panel on split, else the lower third."""
    if split is not None:
        top = float(split.top if hasattr(split, "top") else split["top"])
        return min(round(OUT_H * top) + 380, OUT_H - 520)
    return OUT_H - 560


def clipper_caption_events(text: str, start: float, end: float, highlights: set[str] | None = None, split=None):
    """[(start, end, ASS text)] for one caption cue, on the cue's own clock."""
    kind = (getattr(split, "kind", None) or (split or {}).get("kind") if split is not None else None) or "face"
    yellow, outline = YELLOW.get(kind, YELLOW["face"]), OUTLINE.get(kind, OUTLINE["face"])
    y = caption_y(split)
    hi = keywords(clean_text(text).split(), highlights)
    out = []
    for t0, t1, group in word_chunks(text, start, end):
        body = " ".join(f"{{\\c{yellow}}}{_esc(w)}{{\\c{WHITE}}}" if w in hi else _esc(w) for w in group)
        tags = f"{{\\an5\\pos(540,{y})\\3c{outline}\\fscx85\\fscy85\\t(0,90,\\fscx100\\fscy100)}}"
        out.append((t0, t1, tags + body))
    return out
