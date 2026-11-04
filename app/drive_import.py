"""Drive import: photos/videos the owner drops straight into the TIKTOK RAW folder tree (not via Telegram)
get a Log row too, so the TikTok video maker can use them (owner 2026-11-04).

- Scans the whole Drive root of a tenant (skips _HQ PARTS, _PROXY 720p and the voice folder).
- A file is "already known" if its Drive id is in Log column G, or it was uploaded by the archiver itself
  (description contains "Telegram msg") or is an HQ part / proxy.
- Row id (column A) = a stable NEGATIVE number made from the Drive file id, so it never clashes with
  Telegram message ids (always positive) and the existing seen/set_cell logic keeps working.
- posted_at = the file's capture/modified date (keeps old footage "old" for the video maker's mix).
- Folder path gives material + pillar when it is a known taxonomy folder (e.g. UPVC/INSTALLATION);
  otherwise the AI classifier decides from Drive's thumbnail. Files are NEVER moved or renamed.
- Videos under the 9.3 MB connector limit: column T = the original. Bigger (up to DRIVE_IMPORT_MAX_MB):
  full-quality HQ parts like Telegram videos. Bigger still: logged with a note so nothing is hidden.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from zoneinfo import ZoneInfo

SKIP_FOLDERS = {"_HQ PARTS", "_PROXY 720p"}
SENDER = "Drive upload"


def import_id(file_id: str) -> int:
    """Stable negative id for a Drive file (12 decimal digits)."""
    return -(int(hashlib.sha1(file_id.encode()).hexdigest()[:12], 16) % 10**12 or 1)


def is_archiver_file(f: dict) -> bool:
    d = f.get("description") or ""
    n = f.get("name") or ""
    return "Telegram msg" in d or d.startswith("HQ part") or d.startswith("720p proxy") or n.endswith("_proxy.mp4")


def kind_of(f: dict) -> str | None:
    mime = f.get("mimeType") or ""
    if mime.startswith("video/"):
        return "video"
    if mime.startswith("image/"):
        return "photo"
    return None


def posted_at(f: dict, tz: str) -> str:
    """Capture time for photos when Drive knows it, else the file's modified time (kept from the phone/PC)."""
    raw = ((f.get("imageMediaMetadata") or {}).get("time") or "").strip()
    if raw:  # EXIF "YYYY:MM:DD HH:MM:SS"
        try:
            return datetime.strptime(raw, "%Y:%m:%d %H:%M:%S").strftime("%Y-%m-%d %H:%M")
        except ValueError:
            pass
    ts = f.get("modifiedTime") or f.get("createdTime") or ""
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(ZoneInfo(tz))
        return dt.strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return ts[:16].replace("T", " ")


def folder_guess(path: tuple, profile) -> tuple[str, str]:
    """(material, pillar) from a taxonomy folder path like ('UPVC', 'INSTALLATION', ...), else ('', '')."""
    mat = next((p for p in path if p in profile.materials), "")
    pil = next((p for p in path if p in profile.pillars), "")
    return mat, pil


def candidates(files: list[tuple[dict, tuple]], known_ids: set[str]) -> list[tuple[dict, tuple]]:
    """New owner-uploaded media, oldest first; identical copies (same size + name) only once."""
    out, seen_sig = [], set()
    for f, path in files:
        if kind_of(f) is None or f["id"] in known_ids or is_archiver_file(f):
            continue
        sig = (f.get("size"), f.get("name"))
        if sig in seen_sig:
            continue
        seen_sig.add(sig)
        out.append((f, path))
    out.sort(key=lambda fp: fp[0].get("modifiedTime") or "")
    return out
