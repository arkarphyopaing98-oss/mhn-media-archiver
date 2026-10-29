"""Pair each media message with the team's story text.

Rules (same for live messages and history backfill):
1. The media's own caption.
2. Captions of other items in the same album (grouped_id).
3. Text messages that reply to the media.
4. Text messages from the same sender AFTER the media, within the window,
   stopping at that sender's next media message (that text belongs to the next video).
5. If 1-4 found nothing: text from the same sender up to 5 minutes BEFORE the media.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable, Optional


@dataclass
class Msg:
    """Minimal message view so the pairing logic is testable without Telegram."""
    id: int
    date: datetime
    sender_id: Optional[int]
    text: str
    has_media: bool
    grouped_id: Optional[int] = None
    reply_to: Optional[int] = None


def build_story(target: Msg, neighbours: Iterable[Msg], window_minutes: int = 15) -> str:
    nb = sorted((m for m in neighbours if m.id != target.id), key=lambda m: m.id)
    parts: list[str] = []

    def add(text: str) -> None:
        t = (text or "").strip()
        if t and t not in parts:
            parts.append(t)

    add(target.text)

    if target.grouped_id is not None:
        for m in nb:
            if m.grouped_id == target.grouped_id:
                add(m.text)

    for m in nb:
        if m.reply_to == target.id and not m.has_media:
            add(m.text)

    end = target.date + timedelta(minutes=window_minutes)
    for m in nb:
        if m.id <= target.id or m.sender_id != target.sender_id:
            continue
        if m.date > end:
            break
        if m.has_media and (target.grouped_id is None or m.grouped_id != target.grouped_id):
            break  # next video from this sender: its story starts here
        if not m.has_media and m.reply_to in (None, target.id):
            add(m.text)

    if not parts:
        start = target.date - timedelta(minutes=5)
        for m in reversed(nb):
            if m.id >= target.id or m.sender_id != target.sender_id:
                continue
            if m.date < start or m.has_media:
                break
            add(m.text)

    return "\n".join(parts)


def link_target(voice: Msg, neighbours: Iterable[Msg], window_minutes: int = 15) -> Optional[int]:
    """Which video/photo a voice note talks about (same idea as the story rules, in reverse):
    1. The media it replies to.
    2. The same sender's latest media before it, within the window (voice sent after the video = its story).
    3. The same sender's next media within 5 minutes (voice sent first, video right after).
    None = a standalone voice note."""
    nb = sorted((m for m in neighbours if m.id != voice.id), key=lambda m: m.id)
    by_id = {m.id: m for m in nb}
    if voice.reply_to in by_id and by_id[voice.reply_to].has_media:
        return voice.reply_to
    start = voice.date - timedelta(minutes=window_minutes)
    for m in reversed(nb):
        if m.id >= voice.id or m.sender_id != voice.sender_id:
            continue
        if m.date < start:
            break
        if m.has_media:
            return m.id
    end = voice.date + timedelta(minutes=5)
    for m in nb:
        if m.id <= voice.id or m.sender_id != voice.sender_id:
            continue
        if m.date > end:
            break
        if m.has_media:
            return m.id
    return None
