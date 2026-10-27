"""Core pipeline for one Telegram media message:
download -> pair story -> classify -> upload to Drive folder -> log row in Sheet."""
from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import timezone
from zoneinfo import ZoneInfo

from telethon import TelegramClient
from telethon.tl.custom.message import Message

from .classifier import Classifier, Classification
from .proxy import PROXY_ROOT, make_proxy
from .config import Config
from .google_io import Drive, Log
from .story import Msg, build_story
from .taxonomy import TO_SORT, folder_path, is_banned

log = logging.getLogger(__name__)
TMP_DIR = "/tmp/mhn-archiver"


def media_kind(m: Message, include_photos: bool = True) -> str | None:
    """'video', 'photo' or None for messages we don't archive."""
    if m.video or m.video_note or m.gif:
        return None if m.gif else "video"
    if m.photo:
        return "photo" if include_photos else None
    mime = (m.file.mime_type if m.file else "") or ""
    if m.document and mime.startswith("video/"):
        return "video"
    if m.document and mime.startswith("image/") and include_photos:
        return "photo"
    return None


def run_qc(path: str, kind: str) -> tuple[str, list[str]]:
    """Quality check in a child process with a timeout, so a memory spike can't kill the archiver."""
    import json, subprocess, sys
    try:
        r = subprocess.run([sys.executable, "-m", "app.quality", "--json", path, kind],
                           capture_output=True, text=True, timeout=300)
        verdict, notes, _ = json.loads(r.stdout.strip().splitlines()[-1])
        return verdict, notes
    except Exception as e:
        log.warning("QC failed for %s: %s", path, e)
        return "unchecked", ["QC failed - check by eye"]


def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", text or "").strip("-").lower() or "x"


def to_msg(m: Message, include_photos: bool) -> Msg:
    return Msg(id=m.id, date=m.date, sender_id=m.sender_id, text=m.message or "",
               has_media=media_kind(m, include_photos) is not None,
               grouped_id=m.grouped_id, reply_to=m.reply_to_msg_id)


class Archiver:
    def __init__(self, cfg: Config, client: TelegramClient, chat, drive: Drive, sheet: Log,
                 classifier: Classifier) -> None:
        self.cfg, self.client, self.chat = cfg, client, chat
        self.drive, self.sheet, self.classifier = drive, sheet, classifier
        self.seen: set[int] = set()
        self.lock = asyncio.Lock()
        self.tz = ZoneInfo(cfg.timezone)
        self.stats = {"archived": 0, "to_sort": 0, "excluded": 0, "skipped": 0, "errors": 0}
        os.makedirs(TMP_DIR, exist_ok=True)

    async def load_seen(self) -> None:
        self.seen = await asyncio.to_thread(self.sheet.seen_ids)
        log.info("Loaded %d already-archived message ids", len(self.seen))

    async def story_for(self, m: Message) -> str:
        window = []
        async for n in self.client.iter_messages(self.chat, min_id=max(m.id - 40, 0), max_id=m.id + 80):
            window.append(to_msg(n, self.cfg.include_photos))
        return build_story(to_msg(m, self.cfg.include_photos), window, self.cfg.story_window_minutes)

    async def thumbnail(self, m: Message, kind: str, local_path: str) -> bytes | None:
        try:
            if kind == "photo" and os.path.getsize(local_path) < 4_500_000:
                with open(local_path, "rb") as fh:
                    return fh.read()
            data = await self.client.download_media(m, bytes, thumb=-1)
            return data if isinstance(data, bytes) and data else None
        except Exception:
            log.warning("No thumbnail for msg %s", m.id)
            return None

    async def process(self, m: Message) -> None:
        kind = media_kind(m, self.cfg.include_photos)
        if kind is None:
            return
        async with self.lock:
            if m.id in self.seen:
                return
            try:
                await self._process(m, kind)
            except Exception:
                self.stats["errors"] += 1
                log.exception("Failed to archive msg %s (will retry on next restart)", m.id)

    async def _process(self, m: Message, kind: str) -> None:
        size_mb = round((m.file.size or 0) / 1_048_576, 1) if m.file else 0
        sender = await m.get_sender()
        sender_name = " ".join(filter(None, [getattr(sender, "first_name", ""), getattr(sender, "last_name", "")])) \
            or getattr(sender, "title", "") or str(m.sender_id)
        local_date = m.date.replace(tzinfo=timezone.utc).astimezone(self.tz) if m.date.tzinfo is None \
            else m.date.astimezone(self.tz)
        story = await self.story_for(m)
        duration = getattr(m.file, "duration", None) if m.file else None

        if size_mb > self.cfg.max_file_mb:
            self.stats["skipped"] += 1
            await self._log(m, local_date, sender_name, kind, "", "", "", Classification(), "",
                            story, duration, size_mb, f"skipped: over {self.cfg.max_file_mb} MB")
            return

        # Crash guard: mark the message first. If the process dies on this file (e.g. out of memory),
        # the restart sees the id as done and moves on instead of looping on the same file.
        await asyncio.to_thread(self.sheet.append, [m.id, local_date.strftime("%Y-%m-%d %H:%M"), sender_name, kind,
                                                    "", "", "", "", "", "", story, "", "", duration or "", size_mb,
                                                    "processing (if this stays, the file crashed the archiver)"])
        self.seen.add(m.id)
        ext = (m.file.ext if m.file else "") or (".mp4" if kind == "video" else ".jpg")
        local_path = os.path.join(TMP_DIR, f"{m.id}{ext}")
        try:
            await self.client.download_media(m, file=local_path)
            qc_verdict, qc_notes = await asyncio.to_thread(run_qc, local_path, kind)
            thumb = await self.thumbnail(m, kind, local_path)
            c = await self.classifier.classify(story, kind, thumb)
            banned = c.banned or is_banned(story)
            path = folder_path(c.material, c.pillar, c.confident)
            pillar_label = c.pillar if path != [TO_SORT] else TO_SORT
            name = (f"{local_date:%Y%m%d}_{slug(c.material)}_{slug(c.product_type)}_"
                    f"{slug(pillar_label)}_{m.id}{ext}")
            folder_id = await asyncio.to_thread(self.drive.folder_for, path)
            mime = (m.file.mime_type if m.file else None) or ("video/mp4" if kind == "video" else "image/jpeg")
            uploaded = await asyncio.to_thread(
                self.drive.upload, local_path, name, mime, folder_id,
                f"{story}\n\n{c.summary_my}\nFrom {sender_name}, Telegram msg {m.id}")
            if banned:
                status, key = "excluded: banned product", "excluded"
            elif qc_verdict == "reject":
                status, key = "rejected: quality", "skipped"
            elif path == [TO_SORT]:
                status, key = "to sort", "to_sort"
            else:
                status, key = "new", "archived"
            self.stats[key] += 1
            await self._log(m, local_date, sender_name, kind, name, uploaded.get("webViewLink", ""),
                            uploaded.get("id", ""), c, pillar_label, story, duration, size_mb, status,
                            qc_verdict, "; ".join(qc_notes))
            log.info("Archived msg %s -> %s (%s)", m.id, "/".join(path), status)
            if kind == "video" and qc_verdict != "reject" and not banned:
                proxy = await asyncio.to_thread(make_proxy, local_path)
                if proxy:
                    try:
                        pid = await asyncio.to_thread(self.drive.folder_for, [PROXY_ROOT, *path])
                        up = await asyncio.to_thread(self.drive.upload, proxy, name.rsplit(".", 1)[0] + "_proxy.mp4",
                                                     "video/mp4", pid, f"720p proxy of {name}")
                        await asyncio.to_thread(self.sheet.set_cell, m.id, "T", up.get("webViewLink", ""))
                    except Exception:
                        log.exception("Proxy upload failed for msg %s", m.id)
                    finally:
                        if os.path.exists(proxy):
                            os.remove(proxy)
        finally:
            if os.path.exists(local_path):
                os.remove(local_path)

    async def _log(self, m, local_date, sender, kind, name, link, file_id, c: Classification,
                   pillar_label, story, duration, size_mb, status, qc="", qc_notes="", proxy="") -> None:
        row = [m.id, local_date.strftime("%Y-%m-%d %H:%M"), sender, kind, name, link, file_id,
               c.material, c.product_type, pillar_label, story, c.summary_my, c.hook_idea,
               duration or "", size_mb, status, "", qc, qc_notes, proxy]
        await asyncio.to_thread(self.sheet.set_row, m.id, row)
        self.seen.add(m.id)

    async def catch_up(self, full_history: bool, limit: int = 0) -> None:
        """Archive history oldest-first: everything (backfill) or only since the last archived id."""
        min_id = 0 if full_history or not self.seen else max(self.seen)
        if not full_history and not self.seen:
            log.info("No history archived yet and BACKFILL is off: live mode only")
            return
        log.info("Catch-up from message id %s (limit %s)", min_id, limit or "none")
        count = 0
        async for m in self.client.iter_messages(self.chat, min_id=min_id, reverse=True):
            if media_kind(m, self.cfg.include_photos) and m.id not in self.seen:
                await self.process(m)
                count += 1
                if limit and count >= limit:
                    break
        log.info("Catch-up done: %d media messages processed", count)

    async def on_new_message(self, m: Message) -> None:
        if media_kind(m, self.cfg.include_photos) is None:
            return
        await asyncio.sleep(self.cfg.story_wait_seconds)  # give the team time to type the story
        await self.process(m)
