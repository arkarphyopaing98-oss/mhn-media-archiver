"""Small 720p 'proxy' copy of each archived video (<~8 MB) so Claude's Drive connector can fetch it
for editing. Uses the ffmpeg binary bundled by the imageio-ffmpeg wheel (no system ffmpeg needed)."""
from __future__ import annotations

import logging
import os
import subprocess

log = logging.getLogger(__name__)
PROXY_ROOT = "_PROXY 720p"


def make_proxy(src: str, max_seconds: int = 90) -> str | None:
    try:
        import imageio_ffmpeg
        ff = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        log.warning("imageio-ffmpeg not available; skipping proxy")
        return None
    out = os.path.splitext(src)[0] + "_proxy.mp4"
    cmd = [ff, "-loglevel", "error", "-y", "-threads", "2", "-i", src, "-t", str(max_seconds),
           "-vf", "scale='if(gt(iw,ih),-2,720)':'if(gt(iw,ih),720,-2)'", "-c:v", "libx264", "-preset", "veryfast", "-threads", "2",
           "-crf", "30", "-maxrate", "700k", "-bufsize", "1400k", "-c:a", "aac", "-b:a", "64k", "-movflags", "+faststart", out]
    try:
        subprocess.run(cmd, check=True, timeout=600)
        return out
    except Exception:
        log.exception("Proxy encode failed for %s", src)
        return None
