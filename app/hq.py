"""Full-quality "HQ parts" of a video for the TikTok video maker.

Claude's Drive connector can only download files under 10 MB. The old 720p proxy solved that by
compressing every video ~4x, which made the TikTok edits look blocky. Instead:

- original under the limit  -> no copy at all; the video maker downloads the original itself.
- original over the limit   -> cut it into consecutive parts that each fit under the limit, by STREAM COPY
  (no re-encode = exactly the original quality, and almost no CPU). A part that is still too big
  (sparse keyframes) is re-encoded at high quality (CRF 20) with a bitrate cap that fits.

split_for_drive() returns [(path, start_s, end_s), ...] or [] when the original already fits.
"""
from __future__ import annotations

import csv
import logging
import math
import os
import subprocess

from .quality import _ffmpeg, _probe

log = logging.getLogger(__name__)
HQ_ROOT = "_HQ PARTS"
LIMIT_MB = 9.3  # stay safely under the 10 MB connector limit


def _mb(path: str) -> float:
    return os.path.getsize(path) / 1_048_576


def _reencode(src: str, start: float, dur: float, out: str, limit_mb: float) -> None:
    # total budget in kbit/s, minus 128k audio, with 8% headroom for container overhead
    kbps = max(800, int(limit_mb * 8192 * 0.92 / max(dur, 0.5)) - 128)
    subprocess.run([_ffmpeg(), "-loglevel", "error", "-y", "-ss", f"{start:.3f}", "-i", src, "-t", f"{dur:.3f}",
                    "-c:v", "libx264", "-preset", "veryfast", "-threads", "2", "-crf", "20",
                    "-maxrate", f"{kbps}k", "-bufsize", f"{kbps * 2}k", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", out], check=True, timeout=900)


def split_for_drive(src: str, limit_mb: float = LIMIT_MB) -> list[tuple[str, float, float]]:
    size = _mb(src)
    if size <= limit_mb:
        return []
    dur, _, _ = _probe(src)
    if dur <= 0:
        log.warning("HQ: cannot read duration of %s", src)
        return []
    seg = max(3, math.floor(dur * (limit_mb * 0.85) / size))  # seconds per part, 15% margin
    base = os.path.splitext(src)[0] + "_hq"
    listing = base + "_list.csv"
    subprocess.run([_ffmpeg(), "-loglevel", "error", "-y", "-i", src, "-map", "0:v:0", "-map", "0:a?", "-c", "copy",
                    "-f", "segment", "-segment_time", str(seg), "-reset_timestamps", "1",
                    "-segment_list", listing, "-segment_list_type", "csv", f"{base}_%03d.mp4"],
                   check=True, timeout=900)
    parts: list[tuple[str, float, float]] = []
    folder = os.path.dirname(src)
    with open(listing, newline="") as fh:
        for row in csv.reader(fh):
            name, start, end = row[0], float(row[1]), float(row[2])
            path = os.path.join(folder, name)
            if _mb(path) > limit_mb:  # sparse keyframes -> too long a part; re-encode it at high quality
                fixed = path.replace(".mp4", "_re.mp4")
                _reencode(src, start, end - start, fixed, limit_mb)
                os.remove(path)
                path = fixed
            parts.append((path, round(start, 2), round(end, 2)))
    os.remove(listing)
    log.info("HQ: %s (%.1f MB, %.0fs) -> %d parts of ~%ss", os.path.basename(src), size, dur, len(parts), seg)
    return parts
