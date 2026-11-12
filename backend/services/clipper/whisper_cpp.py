"""Local transcription with whisper.cpp (`whisper-cli`), ported from Project Clipper `whisper_asr.py`.

Runs on the GPU with a CUDA build. Text comes from one pass over the whole clip (full context),
word times from DTW and, when Silero VAD is next to the model, from a second pass per speech
region (`align_words`). Clipper's `kamus` fixes names and game terms and is passed to whisper as
spelling hints.

Configure in Settings → Clipper: path to whisper-cli(.exe) and a ggml model, e.g.
ggml-large-v3-turbo-q5_0.bin. Optional VAD: whisper-vad-speech-segments(.exe) next to
whisper-cli and ggml-silero-*.bin next to the model.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

# Silero often misses shouting over game audio at the default threshold (0.5)
VAD_THRESHOLD = 0.15


@dataclass
class Cue:
    start: float
    end: float
    text: str


def extract_audio(ffmpeg: str, video: Path, wav: Path) -> None:
    """whisper.cpp butuh WAV 16 kHz mono."""
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(video),
           "-vn", "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(wav)]
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    res = subprocess.run(cmd, capture_output=True, text=True, creationflags=flags)
    if res.returncode != 0 or not wav.exists():
        raise RuntimeError((res.stderr or "ffmpeg gagal").strip().splitlines()[-1][:200])


def _words(seg: dict) -> list[tuple[float, float, str]]:
    """Token DTW satu segmen -> [(mulai, akhir, kata)]. Token yang diawali spasi memulai kata baru;
    tanda baca / potongan kata ditempel ke kata sebelumnya dan memperpanjang waktunya."""
    words: list[list] = []
    for tok in seg.get("tokens") or []:
        text, t = tok.get("text", ""), tok.get("t_dtw", -1)
        if text.startswith("[_") or t is None or t < 0:
            continue
        t /= 100  # t_dtw dalam satuan 10 ms
        if text.startswith(" ") or not words:
            words.append([t, t, text.strip()])
        else:
            words[-1][1] = max(words[-1][1], t)
            words[-1][2] += text
    return [(a, b, w) for a, b, w in words if w]


def cues_from_words(words: list[tuple[float, float, str]], max_chars: int = 42,
                    gap: float = 0.6, lead: float = 0.1, tail: float = 0.4,
                    min_dur: float = 0.8) -> list[Cue]:
    """Susun baris subtitle dari waktu per kata: baris baru kalau ada jeda > gap detik,
    kalimat selesai (. ! ?), atau baris sudah mencapai max_chars."""
    groups: list[list] = []
    for w in words:
        cur = groups[-1] if groups else None
        if cur and (w[0] - cur[-1][1] <= gap
                    and not cur[-1][2][-1:] in ".!?"
                    and len(" ".join(x[2] for x in cur)) + 1 + len(w[2]) <= max_chars):
            cur.append(w)
        else:
            groups.append([w])
    cues = [Cue(max(0.0, g[0][0] - lead), g[-1][1] + tail, " ".join(x[2] for x in g)) for g in groups]
    for a, b in zip(cues, cues[1:]):  # jangan tumpang tindih, tapi beri waktu baca minimal
        a.end = max(min(a.end, b.start), min(a.start + min_dur, b.start))
        if b.start - a.end < 1.0:  # jeda pendek: biarkan baris tetap tampil (tidak berkedip)
            a.end = b.start
    return cues


def parse_output(data: dict, max_chars: int = 42) -> tuple[list[Cue], str]:
    """JSON whisper-cli -> (cues, kode bahasa terdeteksi).
    Kalau ada waktu per token (DTW, -ojf -dtw), baris disusun dari waktu per kata (akurat);
    kalau tidak, pakai waktu segmen (kasar, sering membulat ke 2 detik)."""
    lang = (data.get("result") or {}).get("language", "")
    segs = [s for s in data.get("transcription", []) if not _is_noise(s.get("text", "").strip() or "-")]
    words = [w for s in segs for w in _words(s)]
    if words:
        cues = []
        for c in cues_from_words(words, max_chars):
            if cues and cues[-1].text == c.text:  # halusinasi berulang -> gabung
                cues[-1].end = c.end
            else:
                cues.append(c)
        return cues, lang
    cues = []
    for seg in data.get("transcription", []):
        text = " ".join(seg.get("text", "").split())
        off = seg.get("offsets") or {}
        start, end = off.get("from", 0) / 1000, off.get("to", 0) / 1000
        if not text or end <= start or _is_noise(text):
            continue
        if cues and cues[-1].text == text:  # halusinasi berulang -> gabung
            cues[-1].end = end
            continue
        cues.append(Cue(start, end, text))
    return cues, lang


def _is_noise(text: str) -> bool:
    """Penanda non-ucapan yang sering ditulis whisper: [Music], (tertawa), ♪ ..."""
    t = text.strip()
    return (t[:1] in "[(" and t[-1:] in "])") or set(t) <= set("♪♫ .-")




def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=flags)
    if res.returncode != 0:
        raise RuntimeError((res.stderr or "gagal").strip().splitlines()[-1][:200])
    return res


def detect_language(wav: Path, binary: Path, model: Path) -> str:
    res = _run([str(binary), "-m", str(model), "-f", str(wav), "-l", "auto", "-dl"])
    m = re.search(r"auto-detected language: (\w+)", res.stderr + res.stdout)
    return m.group(1) if m else "auto"


def speech_regions(wav: Path, vad_bin: Path, vad_model: Path, merge_gap: float = 0.5, pad: float = 0.2) -> list[tuple[float, float]]:
    """Bagian audio yang berisi ucapan (Silero VAD), digabung kalau jedanya pendek."""
    res = _run([str(vad_bin), "-vm", str(vad_model), "-f", str(wav), "-vt", str(VAD_THRESHOLD),
                "-vp", "100", "-np"])
    raw = [(float(a) / 100, float(b) / 100) for a, b in
           re.findall(r"start = ([\d.]+), end = ([\d.]+)", res.stdout + res.stderr)]
    regions: list[list[float]] = []
    for a, b in raw:
        a, b = max(0.0, a - pad), b + pad
        if regions and a - regions[-1][1] <= merge_gap:
            regions[-1][1] = max(regions[-1][1], b)
        else:
            regions.append([a, b])
    return [(a, b) for a, b in regions]


def _cut_wav(src: Path, regions: list[tuple[float, float]], out_dir: Path) -> list[Path]:
    import wave

    files = []
    with wave.open(str(src)) as w:
        rate, frames = w.getframerate(), w.getnframes()
        params = w.getparams()
        for i, (a, b) in enumerate(regions):
            w.setpos(min(frames, int(a * rate)))
            data = w.readframes(max(0, int((b - a) * rate)))
            f = out_dir / f"chunk_{i:03d}.wav"
            with wave.open(str(f), "wb") as o:
                o.setparams(params)
                o.writeframes(data)
            files.append(f)
    return files


def _key(w: str) -> str:
    """Bentuk kata untuk dicocokkan: huruf kecil, tanpa tanda baca, huruf berulang diringkas (IJOO -> ijo)."""
    return re.sub(r"(.)\1+", r"\1", re.sub(r"[^\w]", "", w.lower()))


def align_words(full: list[tuple[float, float, str]], anchor_sets: list[list[tuple[float, float, str]]],
                seg_ids: list[int] | None = None, max_shift: float = 6.0) -> list[tuple[float, float, str]]:
    """Teks dari transkripsi seluruh klip (`full`: konteks lengkap -> kata akurat, tapi waktunya bisa
    meleset beberapa detik) + waktu dari sumber lain yang waktunya pas (`anchor_sets`, urut prioritas:
    mis. caption YouTube per kata, lalu transkripsi per bagian ucapan VAD).

    Kata yang sama di kedua hasil memakai waktu anchor (selama urutannya konsisten dan tidak bergeser
    lebih dari max_shift). Kata lain mengikuti anchor dalam kalimat whisper yang sama (seg_ids),
    atau anchor terdekat; jarak antarkata dari DTW dipertahankan. Tidak ada kata yang dibuang."""
    import difflib

    seg_ids = seg_ids or [0] * len(full)
    t_new: list[float | None] = [None] * len(full)
    for anchors in anchor_sets:
        if not anchors:
            continue
        sm = difflib.SequenceMatcher(None, [_key(w[2]) for w in full], [_key(w[2]) for w in anchors],
                                     autojunk=False)
        for blk in sm.get_matching_blocks():
            for k in range(blk.size):
                i, t = blk.a + k, anchors[blk.b + k][0]
                if t_new[i] is not None or abs(t - full[i][0]) > max_shift:
                    continue
                before = [t_new[j] for j in range(i) if t_new[j] is not None]
                after = [t_new[j] for j in range(i + 1, len(full)) if t_new[j] is not None]
                if (not before or t >= before[-1]) and (not after or t <= after[0]):
                    t_new[i] = t
    matched = [i for i, t in enumerate(t_new) if t is not None]
    if not matched:
        return list(full)

    out = []
    for i, (t0, t1, w) in enumerate(full):
        if t_new[i] is not None:
            start = t_new[i]
        else:
            prev = max((j for j in matched if j < i), default=None)
            nxt = min((j for j in matched if j > i), default=None)
            cand = []  # (posisi, bukan-kalimat-sama, jarak DTW)
            # dalam satu kalimat kata diucapkan beruntun: jarak DTW dibatasi 0,5 dtk per kata
            if prev is not None:
                gap = t0 - full[prev][0]
                same = seg_ids[prev] == seg_ids[i]
                s = t_new[prev] + (min(gap, 0.5 * (i - prev)) if same else gap)
                s = min(s, t_new[nxt] - 0.05) if nxt is not None else s
                cand.append((s, not same, gap))
            if nxt is not None:
                gap = full[nxt][0] - t0
                same = seg_ids[nxt] == seg_ids[i]
                s = t_new[nxt] - (min(gap, 0.5 * (nxt - i)) if same else gap)
                s = max(s, t_new[prev] + 0.05) if prev is not None else s
                cand.append((s, not same, gap))
            # utamakan anchor di kalimat yang sama ("IJOO finally ..." ikut "finally"), lalu yang terdekat
            start = min(cand, key=lambda c: (c[1], c[2]))[0]
        start = max(0.0, start)
        if out and start < out[-1][0]:
            start = out[-1][0]
        out.append((start, start + min(1.2, max(0.05, t1 - t0)), w))  # DTW kadang memberi durasi kata berlebihan
    for k in range(len(out) - 1):  # akhir kata tidak melewati awal kata berikutnya
        s, e, w = out[k]
        out[k] = (s, min(e, out[k + 1][0]) if out[k + 1][0] > s else e, w)
    return out


# ------------------------------------------------------------------ AutoClip integration
def paths(settings: dict | None = None) -> dict | None:
    """{binary, model, vad_bin, vad_model} when whisper.cpp is configured and present, else None."""
    from . import settings as clipper_settings
    data = settings or clipper_settings.load()
    if not data.get("whisper_cpp_enabled"):
        return None
    binary, model = Path(data.get("whisper_cpp_binary") or ""), Path(data.get("whisper_cpp_model") or "")
    if not binary.is_file() or not model.is_file():
        return None
    suffix = ".exe" if binary.suffix.lower() == ".exe" else ""
    vad_bin = binary.with_name(f"whisper-vad-speech-segments{suffix}")
    vad_model = next(iter(sorted(model.parent.glob("ggml-silero*.bin"))), None)
    return {"binary": binary, "model": model,
            "vad_bin": vad_bin if vad_bin.is_file() else None, "vad_model": vad_model}


def _dtw_preset(model: Path) -> str | None:
    """whisper.cpp alignment-head preset from the model file name (DTW needs it)."""
    name = model.name.lower()
    for preset in ("large.v3.turbo", "large.v3", "large.v2", "large.v1", "medium.en", "medium", "small.en",
                   "small", "base.en", "base", "tiny.en", "tiny"):
        if preset.replace(".", "-") in name:
            return preset
    return None


def _whisper_words_cfg(cfg: dict, lang: str, prompt: str, wavs: list[Path]):
    cmd = [str(cfg["binary"]), "-m", str(cfg["model"]), "-l", lang, "-ojf", "-np", "-nfa"]
    dtw = _dtw_preset(cfg["model"])
    if dtw:
        cmd += ["-dtw", dtw]
    if prompt:
        cmd += ["--prompt", prompt]
    for f in wavs:
        cmd += ["-f", str(f)]
    _run(cmd)
    result = []
    for f in wavs:
        out = Path(str(f) + ".json")
        data = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
        segs = [s for s in data.get("transcription", []) if not _is_noise(s.get("text", "").strip() or "-")]
        words, ids = [], []
        for n, s in enumerate(segs):
            for w in _words(s):
                words.append(w)
                ids.append(n)
        if not words:  # no DTW times: fall back to segment times split by length
            for n, s in enumerate(segs):
                off = s.get("offsets") or {}
                a, b = off.get("from", 0) / 1000, off.get("to", 0) / 1000
                tokens = " ".join(s.get("text", "").split()).split()
                for k, tok in enumerate(tokens):
                    span = (b - a) / max(1, len(tokens))
                    words.append((a + k * span, a + (k + 1) * span, tok))
                    ids.append(n)
        result.append((words, ids))
    return result


def _srt_time(t: float) -> str:
    ms = int(round(max(0.0, t) * 1000))
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(cues: list[Cue], path: Path) -> None:
    body = "\n".join(f"{i}\n{_srt_time(c.start)} --> {_srt_time(c.end)}\n{c.text}\n" for i, c in enumerate(cues, 1))
    Path(path).write_text(body, encoding="utf-8")


def transcribe(video: Path, output: Path | None = None, language: str = "auto", cfg: dict | None = None) -> Path:
    """Transcribe a video to SRT with whisper.cpp; kamus corrections applied. Returns the SRT path."""
    from backend.utils.ffmpeg_utils import get_ffmpeg_path
    from . import settings as clipper_settings
    from .kamus import parse_kamus
    data = clipper_settings.load()
    cfg = cfg or paths(data)
    if cfg is None:
        raise RuntimeError("whisper.cpp belum diatur")
    kamus = parse_kamus(data.get("kamus") or "")
    prompt = kamus.whisper_prompt()
    language = (language if language not in (None, "", "auto") else data.get("whisper_language")) or "auto"
    video = Path(video)
    output = Path(output) if output else video.with_suffix(".srt")
    with tempfile.TemporaryDirectory(prefix="whisper_cpp_") as tmp:
        tmp = Path(tmp)
        wav = tmp / "full.wav"
        extract_audio(get_ffmpeg_path(), video, wav)
        lang = detect_language(wav, cfg["binary"], cfg["model"]) if language == "auto" else language
        full_words, seg_ids = _whisper_words_cfg(cfg, lang, prompt, [wav])[0]
        vad_words: list[tuple[float, float, str]] = []
        if cfg.get("vad_bin") and cfg.get("vad_model"):
            try:
                regions = speech_regions(wav, cfg["vad_bin"], cfg["vad_model"])
                if regions:
                    chunks = _cut_wav(wav, regions, tmp)
                    for (a, b), (chunk_words, _) in zip(regions, _whisper_words_cfg(cfg, lang, prompt, chunks)):
                        for ws, we, w in chunk_words:
                            if a + ws < b:
                                vad_words.append((a + ws, min(a + we, b), w))
            except RuntimeError:
                vad_words = []  # VAD is a timing refinement only
        words = align_words(full_words, [vad_words], seg_ids)
    cues = cues_from_words(words)
    if kamus:
        for cue in cues:
            cue.text = kamus.apply(cue.text)
    output.parent.mkdir(parents=True, exist_ok=True)
    write_srt(cues, output)
    return output
