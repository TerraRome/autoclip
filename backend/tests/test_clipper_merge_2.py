"""Fork (Clipper merge, part 2): Claude CLI bridge + hooks, whisper.cpp + kamus, channel/trending
scan, Clipper word captions. Clipper tests are ported from tests/test_viral.py.
"""
import json
import threading
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer

import pytest

from backend.services.clipper import captions, claude_bridge, kamus, whisper_cpp
from backend.services.clipper.scoring import score_videos
from backend.services.clipper.youtube_api import Video, YouTubeAPI

NOW = datetime(2026, 11, 10, 6, 0, tzinfo=timezone.utc)


# ------------------------------------------------------------------ kamus / whisper.cpp
def test_kamus_rules():
    k = kamus.parse_kamus("# komentar\nIJU, ijau => hijau\nI'm dead => aku mati  # frasa\nKaela\n\n")
    assert k.apply("IJU! Dia drop di iju, ijau") == "HIJAU! Dia drop di hijau, hijau"
    assert k.apply("Iju finally") == "Hijau finally"
    assert k.apply("tiju juice") == "tiju juice"
    assert k.apply("Aduh, I'm dead") == "Aduh, aku mati"
    assert k.whisper_prompt() == "hijau, aku mati, Kaela."
    assert not kamus.parse_kamus("# kosong")


def test_whisper_word_timing():
    def tok(text, cs):
        return {"text": text, "t_dtw": cs}

    data = {"result": {"language": "id"}, "transcription": [
        {"offsets": {"from": 0, "to": 14000}, "text": " Oh, gacha.", "tokens": [
            tok("[_BEG_]", -1), tok(" Oh", 1262), tok(",", 1278), tok(" ga", 1290), tok("cha", 1302), tok(".", 1320)]},
        {"offsets": {"from": 14000, "to": 18000}, "text": " Dia drop di hijau", "tokens": [
            tok(" Dia", 1500), tok(" drop", 1530), tok(" di", 1560), tok(" hijau", 1590)]},
    ]}
    cues, lang = whisper_cpp.parse_output(data, max_chars=12)
    assert lang == "id" and [c.text for c in cues] == ["Oh, gacha.", "Dia drop di", "hijau"]
    assert abs(cues[0].start - 12.52) < 1e-6


def test_align_words_keeps_text_and_takes_vad_times():
    full = [(1.0, 1.3, "Oh,"), (11.0, 11.3, "Dead"), (11.3, 11.5, "di"), (11.5, 11.8, "mana")]
    vad = [(14.0, 14.3, "That"), (14.3, 14.5, "di"), (14.5, 14.8, "mana")]
    out = whisper_cpp.align_words(full, [vad], [0, 1, 1, 1])
    assert [w for _, _, w in out] == ["Oh,", "Dead", "di", "mana"]
    assert [round(s, 2) for s, _, _ in out][2:] == [14.3, 14.5]


def test_whisper_cpp_paths_need_enabled_and_files(tmp_path):
    binary, model = tmp_path / "whisper-cli.exe", tmp_path / "ggml-large-v3-turbo-q5_0.bin"
    binary.write_text("x")
    model.write_text("x")
    base = {"whisper_cpp_enabled": True, "whisper_cpp_binary": str(binary), "whisper_cpp_model": str(model)}
    assert whisper_cpp.paths({**base, "whisper_cpp_enabled": False}) is None
    assert whisper_cpp.paths({**base, "whisper_cpp_model": str(tmp_path / "missing.bin")}) is None
    cfg = whisper_cpp.paths(base)
    assert cfg["binary"] == binary and cfg["vad_bin"] is None and cfg["vad_model"] is None
    (tmp_path / "whisper-vad-speech-segments.exe").write_text("x")
    (tmp_path / "ggml-silero-v5.1.2.bin").write_text("x")
    cfg = whisper_cpp.paths(base)
    assert cfg["vad_bin"].name == "whisper-vad-speech-segments.exe" and cfg["vad_model"].name.startswith("ggml-silero")
    assert whisper_cpp._dtw_preset(model) == "large.v3.turbo"
    assert whisper_cpp._dtw_preset(tmp_path / "ggml-small.bin") == "small"


def test_srt_written(tmp_path):
    out = tmp_path / "a.srt"
    whisper_cpp.write_srt([whisper_cpp.Cue(0.5, 1.25, "halo"), whisper_cpp.Cue(61, 62, "dunia")], out)
    assert out.read_text(encoding="utf-8") == "1\n00:00:00,500 --> 00:00:01,250\nhalo\n\n2\n00:01:01,000 --> 00:01:02,000\ndunia\n"


def test_readiness_accepts_whisper_cpp(monkeypatch):
    from backend.services import ai_model_settings as ai_models
    from backend.services.studio import readiness
    monkeypatch.setattr(whisper_cpp, "paths", lambda settings=None: {"binary": "x"})
    check = readiness._transcription(ai_models.ModelSettings())
    assert check["ok"] and check["code"] == "whisper_installed"


# ------------------------------------------------------------------ Claude CLI bridge
def test_split_messages():
    system, prompt = claude_bridge.split_messages([
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": [{"type": "text", "text": "hello"}, {"type": "image_url", "image_url": {}}]},
    ])
    assert system == "be brief" and prompt == "hello"


@pytest.fixture
def bridge(monkeypatch):
    from backend.services.clipper import claude_cli
    monkeypatch.setattr(claude_cli, "run_text", lambda system, prompt, model=None, timeout=900: f"{model}:{prompt}")
    server = ThreadingHTTPServer(("127.0.0.1", 0), claude_bridge._Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/v1"
    server.shutdown()


def _post(url, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.read().decode()


def test_bridge_models_and_completion(bridge):
    with urllib.request.urlopen(bridge + "/models", timeout=10) as resp:
        assert [m["id"] for m in json.load(resp)["data"]] == ["sonnet", "opus", "haiku"]
    answer = json.loads(_post(bridge + "/chat/completions", {"model": "opus", "messages": [{"role": "user", "content": "hi"}]}))
    assert answer["choices"][0]["message"]["content"] == "opus:hi"


def test_bridge_streams_sse(bridge):
    text = _post(bridge + "/chat/completions", {"model": "sonnet", "stream": True, "messages": [{"role": "user", "content": "x"}]})
    events = [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: {")]
    assert events[0]["choices"][0]["delta"]["content"] == "sonnet:x"
    assert events[-1]["choices"][0]["finish_reason"] == "stop" and text.rstrip().endswith("data: [DONE]")


def test_hook_prompt_has_all_moments():
    from backend.services.clipper.hook import build_prompt
    moments = [{"start": 60, "end": 90, "peak_time": 75, "reason": {"summary": "s"}, "transcript": [(61, "halo")], "chat": []},
               {"start": 3600, "end": 3630, "peak_time": 3615, "reason": {"details": ["d"]}}]
    prompt = build_prompt("Judul", "Channel", moments)
    assert "MOMEN 1: 1:00 - 1:30" in prompt and "MOMEN 2: 1:00:00 - 1:00:30" in prompt and "[1:01] halo" in prompt


# ------------------------------------------------------------------ scan
def _video(vid, views, subs, hours_old, likes=0, comments=0):
    return Video(vid, vid, "c", "c", NOW - timedelta(hours=hours_old), 3600, views, likes, comments, False, subs)


def test_scoring_prefers_fast_outlier():
    ranked = score_videos([_video("big_old", 2_000_000, 5_000_000, 150, 20_000, 1_000),
                           _video("small_hot", 300_000, 20_000, 10, 15_000, 2_000),
                           _video("dud", 1_000, 100_000, 100, 10, 1)], NOW)
    assert ranked[0].video.id == "small_hot" and ranked[-1].video.id == "dud"


def test_api_client_batches_and_counts_quota():
    class Resp:
        def __init__(self, data):
            self._d, self.status_code, self.text = data, 200, ""

        def json(self):
            return self._d

    class Session:
        def get(self, url, params=None, timeout=None):
            return Resp({"items": [{
                "id": i, "snippet": {"title": i, "channelId": "UC" + "x" * 22, "channelTitle": "X",
                                     "publishedAt": "2026-11-09T00:00:00Z", "liveBroadcastContent": "none"},
                "statistics": {"viewCount": "10", "likeCount": "1", "commentCount": "0"},
                "contentDetails": {"duration": "PT20M"}} for i in params["id"].split(",")]})

    api = YouTubeAPI("k", session=Session())
    videos = api.videos([f"v{i}" for i in range(120)])
    assert len(videos) == 120 and api.units_used == 3 and videos[0].duration_s == 1200


def test_scan_needs_api_key(monkeypatch):
    from backend.services.clipper import scanner, settings
    monkeypatch.setattr(settings, "load", lambda: {**settings.DEFAULTS})
    with pytest.raises(ValueError, match="API key"):
        scanner.scan()


def test_settings_hide_the_key_and_keep_it_on_blank(tmp_path, monkeypatch):
    from backend.services.clipper import settings
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    monkeypatch.setattr(settings, "path", lambda: tmp_path / "settings.json")
    settings.save({"youtube_api_key": "AIzaSECRET1234", "whisper_cpp_enabled": "true", "unknown": 1})
    settings.save({"youtube_api_key": "", "channels": "@foo"})
    data = settings.load()
    assert data["youtube_api_key"] == "AIzaSECRET1234" and data["whisper_cpp_enabled"] is True and data["channels"] == "@foo"
    shown = settings.public(data)
    assert "youtube_api_key" not in shown and shown["youtube_api_key_set"] and shown["youtube_api_key_tail"] == "1234"


# ------------------------------------------------------------------ captions
def test_clipper_captions_pop_and_highlight():
    events = captions.clipper_caption_events("IJOO finally kita menang 3 kali", 0, 3, {"menang"}, {"top": 0.4, "kind": "anime"})
    assert all("\\pos(540,1148)" in text and "\\3c&H00A0389A&" in text for _, _, text in events)
    joined = " ".join(text for _, _, text in events)
    assert "&H0000E1FF&}IJOO" in joined and "&H0000E1FF&}menang" in joined and "&H0000E1FF&}3" in joined
    assert events[0][0] == 0 and abs(events[-1][1] - 3) < 1e-9
    assert captions.caption_y(None) == 1360


def test_split_scene_uses_clipper_captions():
    from backend.services.studio.models import Packaging, PackagingCue, Scene
    from backend.services.studio.packaging_render import scene_ass
    split = {"cam": [400, 320, 0, 0], "game": [1000, 1080, 460, 0], "top": 0.4, "source": [1920, 1080], "kind": "face"}
    packaging = Packaging.model_validate({"template": "podcast_en", "audience_language": "en",
                                          "cues": [PackagingCue(start=0, end=2, text="what a clutch play").model_dump()]})
    ass = scene_ass(packaging, [Scene(id="a", start=0, end=5, split=split)], 0)
    assert "Dialogue: 2," in ass and ",Clipper,," in ass
    assert ",Clipper,," not in scene_ass(packaging, [Scene(id="a", start=0, end=5)], 0)
