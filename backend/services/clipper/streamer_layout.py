"""Streamer layout: facecam / VTuber avatar on top, gameplay below (Clipper `reframe.py`, layout `split`).

The facecam is the face that stays in the same place in almost every sampled frame; faces that
only appear sometimes (NPCs, game characters) are ignored. Human faces come from AutoClip's
YuNet model, anime avatars from lbpcascade_animeface. When no steady facecam is found the
caller keeps AutoClip's normal speaker framing.

Geometry is computed for a 9:16 output and stored on each scene as source-pixel crops, so
`framing.layout_filter` can draw it at any output size.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

ANIME_MODEL = Path(__file__).resolve().parents[2] / "assets" / "models" / "lbpcascade_animeface.xml"
OUT_W, OUT_H = 1080, 1920
SPLIT = 0.40       # facecam panel height as a share of the frame (Clipper default)
CAM_ZOOM = 2.6     # facecam width = face box + (zoom - 1) x one face width (Clipper default)
SAMPLES = 24       # frames looked at across all scenes


@dataclass
class Box:
    x: float
    y: float
    w: float
    h: float
    kind: str = "face"  # "face" (human) or "anime"

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2


def _iou(a: Box, b: Box) -> float:
    ix = max(0.0, min(a.x + a.w, b.x + b.w) - max(a.x, b.x))
    iy = max(0.0, min(a.y + a.h, b.y + b.h) - max(a.y, b.y))
    inter = ix * iy
    return inter / (a.w * a.h + b.w * b.h - inter + 1e-9)


def _inside(a: Box, b: Box) -> bool:
    return b.x <= a.cx <= b.x + b.w and b.y <= a.cy <= b.y + b.h


class FaceDetector:
    def __init__(self, cv2, yunet_model: Path, anime_model: Path = ANIME_MODEL):
        self.cv2 = cv2
        self.anime = None
        cascade = getattr(cv2, "CascadeClassifier", None)
        if cascade and anime_model.exists():
            self.anime = cascade(str(anime_model))
        self.yunet = None
        try:
            self.yunet = cv2.FaceDetectorYN.create(str(yunet_model), "", (960, 540), 0.75)
        except Exception as error:  # noqa: BLE001 - old OpenCV
            logger.warning("YuNet unavailable: %s", type(error).__name__)

    def detect(self, frame) -> list[Box]:
        cv2 = self.cv2
        H, W = frame.shape[:2]
        scale = 960 / W
        small = cv2.resize(frame, (960, round(H * scale)))
        out: list[Box] = []
        if self.yunet is not None:
            self.yunet.setInputSize((small.shape[1], small.shape[0]))
            _, faces = self.yunet.detect(small)
            for f in faces if faces is not None else []:
                x, y, w, h = (float(v) / scale for v in f[:4])
                out.append(Box(x, y, w, h, "face"))
        if self.anime is not None:
            gray = cv2.equalizeHist(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY))
            for x, y, w, h in self.anime.detectMultiScale(gray, 1.1, 5, minSize=(24, 24)):
                b = Box(x / scale, y / scale, w / scale, h / scale, "anime")
                same = [o for o in out if _iou(b, o) > 0.3 or _inside(o, b)]
                # an avatar is often also caught by YuNet with a smaller box -> keep the anime box
                out = [o for o in out if o not in same]
                out.append(b if not same or b.w >= max(o.w for o in same) else max(same, key=lambda o: o.w))
        return out


@dataclass
class Cluster:
    boxes: list[Box] = field(default_factory=list)
    frames: set[int] = field(default_factory=set)

    def median(self) -> Box:
        def med(vals):
            s = sorted(vals)
            return s[len(s) // 2]
        return Box(med([b.x for b in self.boxes]), med([b.y for b in self.boxes]),
                   med([b.w for b in self.boxes]), med([b.h for b in self.boxes]), self.boxes[0].kind)


def cluster_faces(frames: list[list[Box]], W: int) -> list[Cluster]:
    clusters: list[Cluster] = []
    for fi, boxes in enumerate(frames):
        for b in boxes:
            best = None
            for c in clusters:
                m = c.median()
                if math.hypot(b.cx - m.cx, b.cy - m.cy) < max(0.06 * W, 0.6 * m.w):
                    best = c
                    break
            if best is None:
                best = Cluster()
                clusters.append(best)
            best.boxes.append(b)
            best.frames.add(fi)
    return sorted(clusters, key=lambda c: len(c.frames), reverse=True)


def find_facecam(frames: list[list[Box]], W: int, H: int, min_ratio: float = 0.4) -> dict | None:
    """The steady facecam / avatar row, or None when the footage is not a gameplay stream."""
    n = max(1, len(frames))
    steady = [c for c in cluster_faces(frames, W) if len(c.frames) / n >= min_ratio]
    if not steady:
        return None
    main = steady[0].median()
    corner = main.cx < 0.3 * W or main.cx > 0.7 * W or main.cy > 0.62 * H or main.cy < 0.25 * H
    small = main.w < 0.12 * W
    fixed_cam = len(steady[0].frames) / n >= 0.9
    if len(steady) >= 2 and main.kind != "anime":
        a, b = sorted((steady[0].median(), steady[1].median()), key=lambda f: f.cx)
        if abs(a.cx - b.cx) > 0.25 * W and 0.5 < a.w / b.w < 2 and abs(a.cy - b.cy) < 0.25 * H:
            return None  # two people side by side: a podcast, not a facecam
    if not (fixed_cam and (main.kind == "anime" or (small and corner))):
        return None
    # collab: other avatars / facecams on the same row join the top panel
    row = [c.median() for c in steady
           if c.boxes[0].kind == main.kind and abs(c.median().cy - main.cy) < 0.12 * H
           and 0.5 < c.median().w / main.w < 2]
    x0, y0 = min(b.x for b in row), min(b.y for b in row)
    x1, y1 = max(b.x + b.w for b in row), max(b.y + b.h for b in row)
    return {"face": [round(x0), round(y0), round(x1 - x0), round(y1 - y0)],
            "face_w": round(sum(b.w for b in row) / len(row)), "kind": main.kind, "cams": len(row)}


# ------------------------------------------------------------------ geometry
def _even(v: float) -> int:
    return max(2, int(round(v / 2)) * 2)


def fit_crop(cx: float, cy: float, w: float, aspect: float, W: int, H: int) -> tuple[int, int, int, int]:
    w = min(w, W, H * aspect)
    h = w / aspect
    w, h = _even(w), _even(h)
    x = min(max(0, cx - w / 2), W - w)
    y = min(max(0, cy - h / 2), H - h)
    return w, h, _even(x) if x > 1 else 0, _even(y) if y > 1 else 0


def cam_crop_top(face: list[int], W: int, H: int, top_h: int, zoom: float,
                 face_w: float | None = None, headroom: float = 0.15) -> tuple[tuple[int, int, int, int], int]:
    """Facecam crop with the head near the top edge of the panel. A wide avatar row that does not
    fit shortens the panel (at least 22% of the frame). -> ((w, h, x, y), top_h)"""
    x, y, w, h = face
    fw = face_w or w
    need_w = min(W, w + fw * (zoom - 1))
    crop_top = max(0.0, y - 0.5 * fw - headroom * fw)
    max_h = H - crop_top
    aspect = OUT_W / top_h
    ch = need_w / aspect
    if ch > max_h:
        ch = max_h
        top_h = max(_even(OUT_H * 0.22), min(top_h, _even(OUT_W * ch / need_w)))
        aspect = OUT_W / top_h
    cw = min(W, ch * aspect)
    ch = cw / aspect
    cw, ch = _even(cw), _even(ch)
    cx = min(max(0, x + w / 2 - cw / 2), W - cw)
    cy = min(max(0, crop_top), H - ch)
    return (cw, ch, _even(cx) if cx > 1 else 0, _even(cy) if cy > 1 else 0), top_h


def game_crop(W: int, H: int, bottom_h: int, offset: float = 0.0, avoid: list | None = None) -> tuple[int, int, int, int]:
    """Gameplay: full height at the bottom panel's ratio, centred (+offset -1..1). When the avatar
    sits low inside that area, the gameplay is cut above it so it does not show twice."""
    aspect = OUT_W / bottom_h
    gh = H
    gw = min(W, _even(gh * aspect))
    cx = W / 2 + offset * (W - gw) / 2
    if avoid:
        ax, ay, aw, ah = avoid
        top = ay - 0.5 * ah
        overlap = ax < cx + gw / 2 and ax + aw > cx - gw / 2
        if overlap and top > 0.6 * H:
            gh = _even(top)
            gw = min(W, _even(gh * aspect))
            cx = W / 2 + offset * (W - gw) / 2
            return fit_crop(cx, gh / 2, gw, aspect, W, gh)
    return fit_crop(cx, H / 2, gw, aspect, W, H)


def split_geometry(cam: dict, W: int, H: int, split: float = SPLIT, zoom: float = CAM_ZOOM) -> dict:
    top_h = _even(OUT_H * split)
    (cw, ch, cx, cy), top_h = cam_crop_top(cam["face"], W, H, top_h, zoom, cam.get("face_w"))
    gw, gh, gx, gy = game_crop(W, H, OUT_H - top_h, 0.0, cam["face"])
    return {"cam": [cw, ch, cx, cy], "game": [gw, gh, gx, gy], "top": round(top_h / OUT_H, 4),
            "source": [W, H], "kind": cam.get("kind", "face")}


# ------------------------------------------------------------------ entry point
def sample_frames(cv2, video: Path, ranges: list[tuple[float, float]], total: int = SAMPLES):
    cap = cv2.VideoCapture(str(video))
    try:
        length = sum(max(0.0, b - a) for a, b in ranges) or 1.0
        for a, b in ranges:
            count = max(1, round(total * (b - a) / length))
            for i in range(count):
                cap.set(cv2.CAP_PROP_POS_MSEC, (a + (b - a) * (i + 0.5) / count) * 1000)
                ok, frame = cap.read()
                if ok:
                    yield frame
    finally:
        cap.release()


def plan(video: Path, scenes: list[dict]) -> dict | None:
    """Split geometry for the whole clip, or None (no steady facecam, or no detector installed)."""
    from backend.services.studio import framing
    cv2 = framing.load_cv2()
    if cv2 is None or not framing.MODEL.exists():
        return None
    detector = FaceDetector(cv2, framing.MODEL)
    frames, W, H = [], 0, 0
    for frame in sample_frames(cv2, Path(video), [(float(s["start"]), float(s["end"])) for s in scenes]):
        H, W = frame.shape[:2]
        frames.append(detector.detect(frame))
    if len(frames) < 4 or not W:
        return None
    cam = find_facecam(frames, W, H)
    return split_geometry(cam, W, H) if cam else None


def filter_chain(split: dict, w: int, h: int, label: str = "base") -> str:
    """`[0:v]…[label]`: facecam scaled into the top band, gameplay into the rest, stacked."""
    top = _even(h * float(split["top"]))
    cw, ch, cx, cy = split["cam"]
    gw, gh, gx, gy = split["game"]
    return (f"[0:v]split=2[spa][spb];"
            f"[spa]crop={cw}:{ch}:{cx}:{cy},scale={w}:{top}:flags=lanczos,setsar=1[spt];"
            f"[spb]crop={gw}:{gh}:{gx}:{gy},scale={w}:{h - top}:flags=lanczos,setsar=1[spg];"
            f"[spt][spg]vstack=2[{label}]")
