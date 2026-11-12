"""Kamus koreksi subtitle: nama streamer/member, istilah game, kata yang sering salah dengar.

Format kamus.txt (satu aturan per baris, # = komentar):
  IJU, ijau => hijau      # kata kiri (boleh beberapa, dipisah koma) diganti kata kanan
  Kaela                   # tanpa "=>": istilah saja, dipakai sebagai petunjuk ejaan untuk whisper

Pencocokan per kata utuh dan tidak peka huruf besar/kecil ("iju", "IJU", "Iju" kena semua,
"tiju" tidak). Kata kanan juga dikirim ke whisper sebagai petunjuk ejaan supaya lebih
sering langsung benar.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Kamus:
    rules: list[tuple[re.Pattern, str]] = field(default_factory=list)
    terms: list[str] = field(default_factory=list)

    def apply(self, text: str) -> str:
        for pat, repl in self.rules:
            text = pat.sub(lambda m, r=repl: _match_case(m, r), text)
        return text

    def whisper_prompt(self, max_terms: int = 40) -> str:
        """Petunjuk ejaan untuk whisper (--prompt). Kosong kalau tidak ada istilah."""
        return ", ".join(self.terms[:max_terms]) + "." if self.terms else ""

    def __bool__(self) -> bool:
        return bool(self.rules or self.terms)


def _match_case(m: re.Match, repl: str) -> str:
    """Ikuti gaya huruf aslinya: 'IJU!' (teriak) -> 'HIJAU'; di awal kalimat 'Iju' -> 'Hijau';
    selain itu kata kanan dipakai persis seperti ditulis di kamus."""
    found = m.group(0)
    if len(found) > 1 and found.isupper() and any(ch.isalpha() for ch in found[1:]):
        return repl.upper()
    before = m.string[:m.start()].rstrip()
    if repl and found[:1].isupper() and (not before or before[-1] in ".!?"):
        return repl[0].upper() + repl[1:]
    return repl


def _word_pattern(word: str) -> re.Pattern:
    # (?<!\w) / (?!\w) alih-alih \b supaya kata berawalan/berakhiran tanda baca tetap bisa dicocokkan
    return re.compile(rf"(?<!\w){re.escape(word)}(?!\w)", re.IGNORECASE)


def parse_kamus(text: str) -> Kamus:
    k = Kamus()
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        if "=>" in line:
            left, right = (s.strip() for s in line.split("=>", 1))
            for wrong in (w.strip() for w in left.split(",")):
                if wrong:
                    k.rules.append((_word_pattern(wrong), right))
            if right and right not in k.terms:
                k.terms.append(right)
        elif line not in k.terms:
            k.terms.append(line)
    # aturan dengan kata kiri lebih panjang dulu ("I'm dead" sebelum "dead")
    k.rules.sort(key=lambda r: -len(r[0].pattern))
    return k


def load_kamus(path: Path) -> Kamus:
    path = Path(path)
    return parse_kamus(path.read_text(encoding="utf-8")) if path.exists() else Kamus()
