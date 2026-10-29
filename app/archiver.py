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
import json
import time

from .hq import HQ_ROOT, LIMIT_MB, split_for_drive
from .config import Config
from .google_io import Drive, Log
from .story import Msg, build_story, link_target
from .transcribe import Transcriber
from .taxonomy import TO_SORT, PROFILES

log = logging.getLogger(__name__)
TMP_DIR = "/tmp/mhn-archiver"


VOICE_MARK = "🎙️ "  # prefix for transcribed voice notes inside a story


def media_kind(m: Message, include_photos: bool = True, include_voice: bool = True) -> str | None:
    """'video', 'photo', 'voice' or None for messages we don't archive."""
    mime = (m.file.mime_type if m.file else "") or ""
    if m.voice or m.audio or (m.document and mime.startswith("audio/")):
        return "voice" if include_voice else None
    if m.video or m.video_note or m.gif:
        return None if m.gif else "video"
    if m.photo:
        return "photo" if include_photos else None
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


def to_msg(m: Message, include_photos: bool, transcripts: dict | None = None) -> Msg:
    """Voice notes count as text for story pairing (their transcript is the text), not as media,
    so a voice note never cuts off the story of the video before it."""
    kind = media_kind(m, include_photos)
    text = m.message or ""
    if kind == "voice" and (transcripts or {}).get(m.id):
        text = "\n".join(t for t in (text, VOICE_MARK + transcripts[m.id]) if t)
    return Msg(id=m.id, date=m.date, sender_id=m.sender_id, text=text,
               has_media=kind in ("video", "photo"),
               grouped_id=m.grouped_id, reply_to=m.reply_to_msg_id)


class Archiver:
    def __init__(self, cfg: Config, client: TelegramClient, chat, drive: Drive, sheet: Log,
                 classifier: Classifier, profile=None, lock: asyncio.Lock | None = None) -> None:
        self.cfg, self.client, self.chat = cfg, client, chat
        self.drive, self.sheet, self.classifier = drive, sheet, classifier
        self.profile = profile or PROFILES["mhn"]
        self.seen: set[int] = set()
        self.lock = lock or asyncio.Lock()  # shared lock = one file at a time across all groups (low memory)
        self.tz = ZoneInfo(cfg.timezone)
        self.stats = {"archived": 0, "to_sort": 0, "excluded": 0, "skipped": 0, "errors": 0}
        self.transcriber = Transcriber(cfg.elevenlabs_api_key, cfg.gemini_api_key)
        self.transcripts: dict[int, str] = {}  # msg id -> transcript (in-memory cache, voice notes only)
        os.makedirs(TMP_DIR, exist_ok=True)

    async def load_seen(self) -> None:
        self.seen = await asyncio.to_thread(self.sheet.seen_ids)
        log.info("Loaded %d already-archived message ids", len(self.seen))

    def kind(self, m: Message) -> str | None:
        return media_kind(m, self.cfg.include_photos, self.cfg.include_voice)

    async def transcript(self, m: Message) -> str:
        """Download + transcribe one voice note (cached). "" if transcription is off or failed."""
        if m.id in self.transcripts:
            return self.transcripts[m.id]
        if not self.transcriber.enabled:
            return ""
        path = os.path.join(TMP_DIR, f"stt_{m.id}{(m.file.ext if m.file else '') or '.ogg'}")
        try:
            await self.client.download_media(m, file=path)
            text = await self.transcriber.transcribe(path, (m.file.mime_type if m.file else "") or "audio/ogg")
        except Exception:
            log.exception("Voice download failed for msg %s", m.id)
            text = ""
        finally:
            if os.path.exists(path):
                os.remove(path)
        if text:
            self.transcripts[m.id] = text
        return text

    async def _window(self, m: Message) -> list[Message]:
        return [n async for n in self.client.iter_messages(self.chat, min_id=max(m.id - 40, 0), max_id=m.id + 80)]

    async def story_for(self, m: Message, window: list[Message] | None = None) -> str:
        window = window if window is not None else await self._window(m)
        # Voice notes near this item from the same person (or replying to it) are part of its story.
        span = self.cfg.story_window_minutes * 60
        for n in window:
            if self.kind(n) == "voice" and (n.sender_id == m.sender_id or n.reply_to_msg_id == m.id) \
                    and abs((n.date - m.date).total_seconds()) <= span:
                await self.transcript(n)
        msgs = [to_msg(n, self.cfg.include_photos, self.transcripts) for n in window]
        return build_story(to_msg(m, self.cfg.include_photos, self.transcripts), msgs, self.cfg.story_window_minutes)

    async def _linked_folder(self, video_id: int) -> list[str] | None:
        """Folder of the already-archived video a voice note belongs to (None if not archived/confident yet)."""
        for r in await asyncio.to_thread(self.sheet.rows, "A2:P"):
            r = r + [""] * (16 - len(r))
            if r[0] == str(video_id) and r[7] and r[9] and r[9] != TO_SORT and r[15].startswith("new"):
                return [r[7], r[9]]
        return None

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
        kind = self.kind(m)
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
        window = await self._window(m)
        story = await self.story_for(m, window)
        duration = getattr(m.file, "duration", None) if m.file else None
        if kind == "voice":
            return await self._process_voice(m, window, story, local_date, sender_name, duration, size_mb)

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
            banned = c.banned or self.profile.is_banned(story)
            path = self.profile.folder_path(c.material, c.pillar, c.confident)
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
                await self._hq_parts(m.id, local_path, name, path, uploaded)
        finally:
            if os.path.exists(local_path):
                os.remove(local_path)

    async def _process_voice(self, m: Message, window: list[Message], story: str, local_date, sender_name: str,
                             duration, size_mb: float) -> None:
        """Voice note -> same Drive folder as the video it talks about (else classified from its transcript),
        original .ogg kept, transcript in the story column, linked video msg id in column V."""
        await asyncio.to_thread(self.sheet.append, [m.id, local_date.strftime("%Y-%m-%d %H:%M"), sender_name, "voice",
                                                    "", "", "", "", "", "", story, "", "", duration or "", size_mb,
                                                    "processing (if this stays, the file crashed the archiver)"])
        self.seen.add(m.id)
        msgs = [to_msg(n, self.cfg.include_photos, self.transcripts) for n in window]
        linked = link_target(to_msg(m, self.cfg.include_photos, self.transcripts), msgs, self.cfg.story_window_minutes)
        ext = (m.file.ext if m.file else "") or ".ogg"
        local_path = os.path.join(TMP_DIR, f"{m.id}{ext}")
        try:
            await self.client.download_media(m, file=local_path)
            thumb = None
            if linked:  # the video's picture helps the AI read a short voice note
                vm = next((n for n in window if n.id == linked), None)
                if vm is not None:
                    thumb = await self.thumbnail(vm, "video", "")  # Telegram's own thumb, no full download
            # No transcript yet -> leave the row unclassified so reclassify() re-transcribes it later.
            c = await self.classifier.classify(story, "voice note (transcript below)", thumb) if story.strip() \
                else Classification()
            banned = c.banned or self.profile.is_banned(story)
            path = (await self._linked_folder(linked) if linked else None) \
                or self.profile.folder_path(c.material, c.pillar, c.confident)
            pillar_label = path[1] if path != [TO_SORT] else TO_SORT
            material = path[0] if path != [TO_SORT] else c.material
            name = (f"{local_date:%Y%m%d}_{self.profile.key}_{slug(material)}_{slug(c.product_type)}_"
                    f"{slug(pillar_label)}_voice_{m.id}{ext}")
            folder_id = self.cfg.voice_folder_id or await asyncio.to_thread(self.drive.folder_for, path)
            mime = (m.file.mime_type if m.file else None) or "audio/ogg"
            note = f"Voice note for video msg {linked}" if linked else "Standalone voice note"
            uploaded = await asyncio.to_thread(
                self.drive.upload, local_path, name, mime, folder_id,
                f"{story or '(no transcript yet)'}\n\n{c.summary_my}\n{note}. From {sender_name}, Telegram msg {m.id}")
            if banned:
                status, key = "excluded: banned product", "excluded"
            elif not story or path == [TO_SORT]:
                status, key = "to sort", "to_sort"
            else:
                status, key = "new", "archived"
            self.stats[key] += 1
            self.stats["voice"] = self.stats.get("voice", 0) + 1
            c.material = material
            await self._log(m, local_date, sender_name, "voice", name, uploaded.get("webViewLink", ""),
                            uploaded.get("id", ""), c, pillar_label, story, duration, size_mb, status, "n/a", "")
            if linked:
                await asyncio.to_thread(self.sheet.set_cell, m.id, "V", str(linked))
            log.info("Archived voice msg %s -> %s (%s, linked video %s, %d chars transcript)",
                     m.id, "/".join(path), status, linked, len(story))
        finally:
            if os.path.exists(local_path):
                os.remove(local_path)

    async def _hq_parts(self, msg_id: int, local_path: str, name: str, path: list[str], original: dict) -> None:
        """Column T = a link the video maker can download (<10 MB) at full quality; column U = all parts as JSON.
        Original under the limit -> T is the original itself. Over -> stream-copied parts (see app/hq.py)."""
        try:
            parts = await asyncio.to_thread(split_for_drive, local_path)
            if not parts:
                await asyncio.to_thread(self.sheet.set_cell, msg_id, "T", original.get("webViewLink", ""))
                await asyncio.to_thread(self.sheet.set_cell, msg_id, "U", json.dumps(
                    [{"from": 0, "to": None, "id": original.get("id", ""), "original": True}]))
                return
            pid = await asyncio.to_thread(self.drive.folder_for, [HQ_ROOT, *path])
            out, first_link = [], ""
            stem = name.rsplit(".", 1)[0]
            for k, (p, a, b) in enumerate(parts, start=1):
                try:
                    up = await asyncio.to_thread(self.drive.upload, p, f"{stem}_p{k:02d}_{a:.0f}-{b:.0f}s.mp4", "video/mp4",
                                                 pid, f"HQ part {k}/{len(parts)} ({a:.1f}-{b:.1f}s) of {name}")
                    out.append({"from": a, "to": b, "id": up.get("id", "")})
                    first_link = first_link or up.get("webViewLink", "")
                finally:
                    if os.path.exists(p):
                        os.remove(p)
            await asyncio.to_thread(self.sheet.set_cell, msg_id, "T", first_link)
            await asyncio.to_thread(self.sheet.set_cell, msg_id, "U", json.dumps(out))
            self.stats["hq_parts"] = self.stats.get("hq_parts", 0) + 1
        except Exception:
            log.exception("HQ parts failed for msg %s", msg_id)

    async def poll_recent(self, window: int = 400) -> int:
        """Safety net for missed live updates: scan the newest messages and archive any unseen media
        (older than the story wait, so the live handler still gets first go)."""
        todo = []
        cutoff = time.time() - self.cfg.story_wait_seconds - 60
        async for m in self.client.iter_messages(self.chat, limit=window):
            if m.id in self.seen or self.kind(m) is None:
                continue
            if m.date.timestamp() > cutoff:
                continue
            todo.append(m)
        for m in reversed(todo):  # oldest first
            await self.process(m)
        self.stats["last_poll"] = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
        self.stats["newest_seen_id"] = max(self.seen) if self.seen else 0
        if todo:
            log.info("Poll: archived %d missed media messages", len(todo))
        return len(todo)

    async def hq_backfill(self, days: int, per_run: int) -> int:
        """Make HQ parts for already-archived big videos (newest first) that only have the old 720p proxy."""
        if days <= 0 or per_run <= 0:
            return 0
        rows = await asyncio.to_thread(self.sheet.rows, "A2:U")
        since = time.strftime("%Y-%m-%d", time.gmtime(time.time() - days * 86400))
        cand = []
        for r in rows:
            r = r + [""] * (21 - len(r))
            try:
                size = float(r[14] or 0)
            except ValueError:
                continue
            if r[3] == "video" and r[6] and not r[20] and r[1][:10] >= since and size > 0 \
                    and (r[15].startswith("new") or r[15].startswith("to sort")) and r[17] != "reject":
                cand.append(r)
        cand.sort(key=lambda r: r[1], reverse=True)
        done = 0
        for r in cand[:per_run]:
            msg_id, file_id, name = int(r[0]), r[6], r[4]
            path = [p for p in (r[7], r[9]) if p] if r[9] and r[9] != "_TO SORT" else ["_TO SORT"]
            local = os.path.join(TMP_DIR, f"hq_{msg_id}.mp4")
            async with self.lock:
                try:
                    if float(r[14]) <= LIMIT_MB:  # fits: point the video maker at the original, no download needed
                        await asyncio.to_thread(self.sheet.set_cell, msg_id, "T", r[5])
                        await asyncio.to_thread(self.sheet.set_cell, msg_id, "U", json.dumps(
                            [{"from": 0, "to": None, "id": file_id, "original": True}]))
                    else:
                        await asyncio.to_thread(self.drive.download, file_id, local)
                        await self._hq_parts(msg_id, local, name, path, {"id": file_id, "webViewLink": r[5]})
                    done += 1
                except Exception:
                    log.exception("HQ backfill failed for msg %s", msg_id)
                finally:
                    if os.path.exists(local):
                        os.remove(local)
        if done:
            log.info("HQ backfill: %d videos done (%d left)", done, max(0, len(cand) - done))
        self.stats["hq_backfill_left"] = max(0, len(cand) - done)
        return done

    async def reclassify(self, per_run: int) -> int:
        """Re-run the AI classifier on rows filed under _TO SORT because the AI call failed earlier
        (no summary written). Confident results move the Drive file into its proper folder and update the row.
        Stops for this cycle as soon as the AI is still out of quota."""
        if per_run <= 0:
            return 0
        rows = await asyncio.to_thread(self.sheet.rows, "A2:P")
        todo = []
        for r in rows:
            r = r + [""] * (16 - len(r))
            if r[0].isdigit() and r[6] and r[15].startswith("to sort") and not r[11] and not r[12]:
                todo.append(r)
        todo.sort(key=lambda r: r[1], reverse=True)  # newest first: what the video maker needs soonest
        done = 0
        for r in todo[:per_run]:
            msg_id, kind, file_id, old_name, story = int(r[0]), r[3], r[6], r[4], r[10]
            async with self.lock:
                try:
                    if kind == "voice":
                        thumb = None
                        if not story.strip():  # transcription failed the first time: try again from Drive
                            local = os.path.join(TMP_DIR, f"re_{msg_id}{os.path.splitext(old_name)[1] or '.ogg'}")
                            try:
                                await asyncio.to_thread(self.drive.download, file_id, local)
                                text = await self.transcriber.transcribe(local)
                            finally:
                                if os.path.exists(local):
                                    os.remove(local)
                            if not text:
                                log.info("Reclassify paused: voice transcription still unavailable")
                                break
                            story = VOICE_MARK + text
                        c = await self.classifier.classify(story, "voice note (transcript below)", None)
                    else:
                        thumb = await asyncio.to_thread(self.drive.thumbnail, file_id)
                        c = await self.classifier.classify(story, kind, thumb)
                    if c.failed:
                        log.info("Reclassify paused: AI still unavailable")
                        break
                    banned = c.banned or self.profile.is_banned(story)
                    path = self.profile.folder_path(c.material, c.pillar, c.confident)
                    pillar_label = c.pillar if path != [TO_SORT] else TO_SORT
                    ext = os.path.splitext(old_name)[1] or {"video": ".mp4", "voice": ".ogg"}.get(kind, ".jpg")
                    tag = "voice_" if kind == "voice" else ""
                    pre = f"{self.profile.key}_" if kind == "voice" else ""
                    name = (f"{old_name[:8]}_{pre}{slug(c.material)}_{slug(c.product_type)}_{slug(pillar_label)}_"
                            f"{tag}{msg_id}{ext}")
                    if kind == "voice" and self.cfg.voice_folder_id:  # voice notes stay in the one voice folder
                        if path == [TO_SORT]:
                            name = old_name
                        moved = await asyncio.to_thread(self.drive.move_rename, file_id, name, self.cfg.voice_folder_id)
                        link = moved.get("webViewLink", r[5])
                    elif path != [TO_SORT]:
                        fid = await asyncio.to_thread(self.drive.folder_for, path)
                        moved = await asyncio.to_thread(self.drive.move_rename, file_id, name, fid)
                        link = moved.get("webViewLink", r[5])
                    else:
                        name, link = old_name, r[5]
                    status = "excluded: banned product" if banned else ("to sort" if path == [TO_SORT] else "new")
                    vals = [name, link, file_id, c.material, c.product_type, pillar_label, story,
                            c.summary_my or "-", c.hook_idea, r[13], r[14], status]
                    await asyncio.to_thread(self.sheet.set_range, msg_id, "E", vals)
                    done += 1
                except Exception:
                    log.exception("Reclassify failed for msg %s", msg_id)
        self.stats["reclassified"] = self.stats.get("reclassified", 0) + done
        self.stats["reclassify_left"] = max(0, len(todo) - done)
        if done:
            log.info("Reclassify: %d rows done (%d left)", done, max(0, len(todo) - done))
        return done

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
            if self.kind(m) and m.id not in self.seen:
                await self.process(m)
                count += 1
                if limit and count >= limit:
                    break
        log.info("Catch-up done: %d media messages processed", count)

    async def on_new_message(self, m: Message) -> None:
        if self.kind(m) is None:
            return
        await asyncio.sleep(self.cfg.story_wait_seconds)  # give the team time to type the story
        await self.process(m)
