"""Fork (Clipper merge): viral moment finder, clip-range import and the streamer split layout.

Ported from Project Clipper's tests/test_viral.py and tests/test_reframe.py (offline, no video).
"""
import json

import pytest

from backend.services.clipper.finder import extract_video_id
from backend.services.clipper.heatmap import find_peaks
from backend.services.clipper.reasons import chat_baseline_per_min, classify, explain
from backend.services.clipper.signals import (
    TimestampNote, chat_to_signal, comment_timestamps, fuse, parse_live_chat, to_heatmap,
)
from backend.services.clipper.streamer_layout import (
    Box, filter_chain, find_facecam, fit_crop, game_crop, split_geometry,
)
from backend.services.clipper.transcript import excerpt, parse_json3, pick_caption_lang

W, H = 1920, 1080


def make_heatmap(duration=6000, n=100, hot=None, base=0.2):
    hot = hot or {}
    seg = duration / n
    return [{"start_time": i * seg, "end_time": (i + 1) * seg, "value": hot.get(i, base)} for i in range(n)]


def frames_with(*boxes, n=10, every=1):
    return [list(boxes) if i % every == 0 else [] for i in range(n)]


# ------------------------------------------------------------------ finder
def test_video_id_from_any_youtube_link():
    assert extract_video_id("https://www.youtube.com/watch?v=O3I0BcLkFGc&t=10") == "O3I0BcLkFGc"
    assert extract_video_id("https://youtu.be/O3I0BcLkFGc") == "O3I0BcLkFGc"
    assert extract_video_id("https://www.youtube.com/live/O3I0BcLkFGc") == "O3I0BcLkFGc"
    assert extract_video_id("https://www.bilibili.com/video/BV1xx") is None


def test_peaks_found_sorted_and_intro_skipped():
    peaks = find_peaks(make_heatmap(hot={0: 1.0, 30: 0.9, 31: 0.7, 70: 0.6}), top_k=5, min_len=30, max_len=90)
    assert [round(p.peak_time) for p in peaks] == [1830, 4230]
    assert all(30 <= p.duration <= 90 for p in peaks)


def test_flat_heatmap_has_no_moment():
    assert find_peaks([]) == [] and find_peaks(make_heatmap()) == []


def test_comment_timestamps_parsed():
    comments = [
        {"text": "Highlights\n00:30:02 ▶ upgrade talk\n1:07:22 ▶ green!!\n3:07 Start", "like_count": 10},
        {"text": "lol at 45:10", "like_count": 0},
    ]
    assert [n.time for n in comment_timestamps(comments, duration=9000)] == [1802, 2710, 4042]


def test_chat_spike_found():
    msgs = [(float(t), "hello") for t in range(0, 6000, 5)]
    msgs += [(3000 + i * 0.2 + 8, "wwwww") for i in range(100)]
    peaks = find_peaks(to_heatmap(chat_to_signal(msgs, duration=6000)), min_len=30, max_len=90, skip_intro=180)
    assert peaks and 2990 <= peaks[0].peak_time <= 3030


def test_live_chat_file_parsed(tmp_path):
    line = {"replayChatItemAction": {"videoOffsetTimeMsec": "12500", "actions": [{"addChatItemAction": {"item": {
        "liveChatTextMessageRenderer": {"message": {"runs": [{"text": "lol "}, {"emoji": {"shortcuts": [":_laugh:"]}}]}}}}}]}}
    path = tmp_path / "x.live_chat.json"
    path.write_text(json.dumps(line) + "\n", encoding="utf-8")
    assert parse_live_chat(path) == [(12.5, "lol :_laugh:")]


def test_fuse_ignores_empty():
    assert fuse([([], 0.6), ([0, 0], 0.4)]) == []


def test_reason_explains_laughing_chat():
    chat = [(float(t), "hello") for t in range(0, 6000, 6)]
    chat += [(3010 + i * 0.3 + 8, "wwwww") for i in range(150)]
    base = chat_baseline_per_min(chat, 6000)
    reason = explain(3000, 3060, chat, base, [TimestampNote(3005, "50:05 jatuh dari tebing", 3)])
    assert classify("wwwwww") == ["tawa"]
    assert reason["tags"][0] == "tawa" and reason["chat_ratio"] > 5
    assert reason["summary"].startswith("Penonton tertawa")


def test_transcript_parse_and_lang(tmp_path):
    path = tmp_path / "x.id-orig.json3"
    path.write_text(json.dumps({"events": [
        {"tStartMs": 1000, "segs": [{"utf8": "Kay mati"}, {"utf8": " sumpah"}]},
        {"tStartMs": 3000, "segs": [{"utf8": "Kay mati sumpah"}]},
        {"tStartMs": 4500, "segs": [{"utf8": "terus aku turun"}]},
    ]}), encoding="utf-8")
    lines = parse_json3(path)
    assert lines == [(1.0, "Kay mati sumpah"), (4.5, "terus aku turun")]
    assert excerpt(lines, 4, 10) == [(4.5, "terus aku turun")]
    assert pick_caption_lang({"language": "id", "automatic_captions": {"id-orig": []}}) == ("id-orig", True)


# ------------------------------------------------------------------ streamer layout
def test_vtuber_corner_avatar_is_a_facecam():
    cam = find_facecam(frames_with(Box(166, 852, 178, 178, "anime")), W, H)
    assert cam["face"] == [166, 852, 178, 178] and cam["kind"] == "anime"


def test_webcam_corner_is_a_facecam():
    assert find_facecam(frames_with(Box(1650, 80, 120, 140)), W, H) is not None


@pytest.mark.parametrize("frames", [
    frames_with(Box(860, 300, 220, 260)),                                  # one big centred face: podcast/vlog
    frames_with(Box(400, 300, 200, 240), Box(1300, 320, 190, 230)),        # two people: podcast
    frames_with(Box(900, 400, 150, 150), every=5),                         # NPC seen in 2 of 10 frames
    [[Box(353, 296, 207, 279, "anime")] if i < 7 else [] for i in range(10)],  # multicam: not steady
])
def test_not_a_gameplay_stream(frames):
    assert find_facecam(frames, W, H) is None


def test_split_crops_fit_the_source_and_panels():
    for face in ([166, 852, 178, 178], [1650, 80, 120, 140], [0, 0, 300, 300]):
        geo = split_geometry({"face": face, "face_w": face[2]}, W, H)
        for cw, ch, cx, cy in (geo["cam"], geo["game"]):
            assert 0 <= cx and cx + cw <= W and 0 <= cy and cy + ch <= H
        top = geo["top"]
        assert 0.22 <= top <= 0.41
        cw, ch = geo["cam"][:2]
        assert abs(cw / ch - 1080 / (1920 * top)) < 0.03
    assert game_crop(W, H, 1152, 1)[0] + game_crop(W, H, 1152, 1)[2] == W
    assert fit_crop(10, 10, 5000, 1.0, W, H)[:2] == (1080, 1080)


def test_split_filter_stacks_into_base():
    geo = split_geometry({"face": [166, 852, 178, 178], "face_w": 178}, W, H)
    chain = filter_chain(geo, 1080, 1920)
    assert chain.startswith("[0:v]split=2") and chain.endswith("vstack=2[base]")
    assert "scale=1080:" in chain


def test_layout_filter_uses_the_split_scene():
    from backend.services.studio.framing import layout_filter
    from backend.services.studio.models import Scene
    geo = split_geometry({"face": [1650, 80, 120, 140], "face_w": 120}, W, H)
    scene = Scene(id="s1", start=0, end=10, split=geo)
    assert "vstack=2[base]" in layout_filter(scene, 0.5, 1080, 1920)
    assert "vstack" not in layout_filter(Scene(id="s2", start=0, end=10), 0.5, 1080, 1920)


# ------------------------------------------------------------------ import
def test_import_options_accept_clip_range_and_streamer():
    from backend.services.studio.jobs import _clip_range
    from backend.services.studio.models import ImportOptions
    options = ImportOptions(portrait_style="streamer", clip_range={"start": 3000, "end": 3060}, platforms=["tiktok"])
    assert _clip_range(options) == (3000.0, 3060.0)
    assert _clip_range(options.model_dump()) == (3000.0, 3060.0)
    assert _clip_range(ImportOptions(platforms=["tiktok"])) is None
