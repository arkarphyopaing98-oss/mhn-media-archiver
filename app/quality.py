"""Raw footage quality check: decide if a clip/photo is good enough to use in content.

Checks (video): resolution, length, brightness, sharpness, camera shake, audio level/clipping.
Checks (photo): resolution, brightness, sharpness.
Returns (verdict, notes): verdict is "good", "usable" (minor issues) or "reject".
"""
from __future__ import annotations

import json
import re
import subprocess

import cv2
import numpy as np

MIN_SHORT_SIDE = 720
SHARP_MIN = 60.0          # Laplacian variance on a 720px frame; below = blurry
DARK, BRIGHT = 45, 215    # mean luma limits
SHAKE_MAX = 18.0          # median frame-to-frame global motion (px at 360px width)


def _frames(path: str, n: int = 8) -> list[np.ndarray]:
    cap = cv2.VideoCapture(path)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
    out = []
    for i in range(n):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int((i + 0.5) * total / n))
        ok, f = cap.read()
        if ok:
            out.append(f)
    cap.release()
    return out


def _sharp_bright(img: np.ndarray) -> tuple[float, float]:
    h, w = img.shape[:2]
    s = 720 / min(h, w)
    g = cv2.cvtColor(cv2.resize(img, (int(w * s), int(h * s))), cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(g, cv2.CV_64F).var()), float(g.mean())


def _shake(path: str) -> float:
    cap = cv2.VideoCapture(path)
    prev, moves, i = None, [], 0
    while len(moves) < 60:
        ok, f = cap.read()
        if not ok:
            break
        i += 1
        if i % 3:
            continue
        g = cv2.cvtColor(cv2.resize(f, (360, int(360 * f.shape[0] / f.shape[1]))), cv2.COLOR_BGR2GRAY)
        if prev is not None:
            pts = cv2.goodFeaturesToTrack(prev, 120, 0.01, 8)
            if pts is not None:
                nxt, st, _ = cv2.calcOpticalFlowPyrLK(prev, g, pts, None)
                d = (nxt - pts)[st.flatten() == 1].reshape(-1, 2)
                if len(d):
                    moves.append(float(np.linalg.norm(np.median(d, axis=0))))
        prev = g
    cap.release()
    return float(np.median(moves)) if moves else 0.0


def _audio(path: str) -> tuple[float | None, float | None]:
    r = subprocess.run(["ffmpeg", "-v", "info", "-i", path, "-af", "volumedetect", "-vn", "-f", "null", "-"],
                       capture_output=True, text=True)
    mean = re.search(r"mean_volume: (-?[\d.]+)", r.stderr)
    peak = re.search(r"max_volume: (-?[\d.]+)", r.stderr)
    return (float(mean.group(1)) if mean else None, float(peak.group(1)) if peak else None)


def check_video(path: str) -> tuple[str, list[str], dict]:
    info = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=width,height,codec_type",
                                      "-of", "json", path], capture_output=True, text=True).stdout)
    v = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), {})
    dur = float(info.get("format", {}).get("duration", 0))
    w, h = v.get("width", 0), v.get("height", 0)
    frames = _frames(path)
    sb = [_sharp_bright(f) for f in frames] or [(0, 0)]
    sharp, bright = float(np.median([s for s, _ in sb])), float(np.median([b for _, b in sb]))
    shake = _shake(path)
    mean_db, peak_db = _audio(path)
    m = {"duration": round(dur, 1), "size": f"{w}x{h}", "sharpness": round(sharp), "brightness": round(bright),
         "shake": round(shake, 1), "audio_mean_db": mean_db, "audio_peak_db": peak_db}
    hard, soft = [], []
    if min(w, h) < MIN_SHORT_SIDE: hard.append(f"low resolution {w}x{h} (need 720p+)")
    if dur < 2: hard.append("too short (<2 s)")
    if dur > 180: soft.append("long clip, will need trimming")
    if sharp < SHARP_MIN * 0.5: hard.append("very blurry")
    elif sharp < SHARP_MIN: soft.append("slightly soft focus")
    if bright < DARK * 0.7 or bright > BRIGHT + 25: hard.append("far too dark/bright")
    elif bright < DARK or bright > BRIGHT: soft.append("lighting needs correction")
    if shake > SHAKE_MAX * 1.6: hard.append("very shaky")
    elif shake > SHAKE_MAX: soft.append("some camera shake")
    if mean_db is None: soft.append("no audio track")
    elif mean_db < -45: soft.append("almost silent audio (fine for B-roll)")
    if peak_db is not None and peak_db >= -0.1 and (mean_db or -99) > -12: soft.append("audio clipping")
    return ("reject" if hard else "usable" if soft else "good"), hard + soft, m


def check_photo(path: str) -> tuple[str, list[str], dict]:
    img = cv2.imread(path)
    if img is None:
        return "reject", ["cannot read image"], {}
    h, w = img.shape[:2]
    sharp, bright = _sharp_bright(img)
    m = {"size": f"{w}x{h}", "sharpness": round(sharp), "brightness": round(bright)}
    hard, soft = [], []
    if min(w, h) < 900: (hard if min(w, h) < 600 else soft).append(f"small image {w}x{h}")
    if sharp < SHARP_MIN * 0.5: hard.append("blurry")
    if bright < DARK * 0.7 or bright > BRIGHT + 25: hard.append("far too dark/bright")
    elif bright < DARK or bright > BRIGHT: soft.append("lighting needs correction")
    return ("reject" if hard else "usable" if soft else "good"), hard + soft, m


if __name__ == "__main__":
    import sys
    for p in sys.argv[1:]:
        fn = check_photo if p.lower().endswith((".jpg", ".jpeg", ".png", ".heic", ".webp")) else check_video
        verdict, notes, metrics = fn(p)
        print(f"{p.split('/')[-1]:<16} {verdict.upper():<7} {metrics} {'; '.join(notes)}")
