"""Speech-to-text for voice notes the team sends in the Product History group.

Order: ElevenLabs Scribe (best Burmese, needs ELEVENLABS_API_KEY) -> Gemini (GEMINI_API_KEY),
with the same 429/500/503 backoff as the classifier. Returns "" when every provider fails,
so a voice note is still archived (and re-tried later by Archiver.reclassify).
"""
from __future__ import annotations

import asyncio
import base64
import logging
import os

import aiohttp

log = logging.getLogger(__name__)

GEMINI_PROMPT = ("Transcribe this voice note word for word. It is usually Burmese, sometimes mixed with English "
                 "product words (UPVC, aluminium, slide door...). Keep Burmese in Burmese script. "
                 "Return only the transcript text, no notes or translation. If there is no speech, return nothing.")
GEMINI_INLINE_LIMIT = 18 * 1_048_576  # inline audio must stay under Gemini's 20 MB request limit
RETRY_CODES = ("429", "500", "502", "503", "overloaded", "RESOURCE_EXHAUSTED", "UNAVAILABLE")


class Transcriber:
    def __init__(self, elevenlabs_key: str = "", gemini_key: str = "", gemini_model: str = "") -> None:
        self.eleven = elevenlabs_key
        self.gemini = gemini_key
        self.models = [m.strip() for m in (gemini_model or os.environ.get("TRANSCRIBE_MODEL", "")
                                           or "gemini-flash-latest").split(",") if m.strip()]
        self.models += [m.strip() for m in os.environ.get("CLASSIFIER_FALLBACK_MODEL", "").split(",")
                        if m.strip() and m.strip() not in self.models]

    @property
    def enabled(self) -> bool:
        return bool(self.eleven or self.gemini)

    async def _elevenlabs(self, path: str, mime: str) -> str:
        form = aiohttp.FormData()
        form.add_field("model_id", os.environ.get("ELEVENLABS_STT_MODEL", "scribe_v1"))
        form.add_field("language_code", os.environ.get("TRANSCRIBE_LANGUAGE", "mya"))
        form.add_field("tag_audio_events", "false")
        with open(path, "rb") as fh:
            form.add_field("file", fh.read(), filename=os.path.basename(path), content_type=mime)
        async with aiohttp.ClientSession() as s:
            async with s.post("https://api.elevenlabs.io/v1/speech-to-text", data=form,
                              headers={"xi-api-key": self.eleven}, timeout=aiohttp.ClientTimeout(total=300)) as r:
                d = await r.json(content_type=None)
                if r.status != 200:
                    raise RuntimeError(f"ElevenLabs {r.status}: {str(d)[:300]}")
        return str(d.get("text", "")).strip()

    async def _gemini(self, model: str, path: str, mime: str) -> str:
        if os.path.getsize(path) > GEMINI_INLINE_LIMIT:
            raise RuntimeError("audio too big for Gemini inline upload")
        with open(path, "rb") as fh:
            data = base64.b64encode(fh.read()).decode()
        body = {"contents": [{"role": "user", "parts": [{"inline_data": {"mime_type": mime, "data": data}},
                                                        {"text": GEMINI_PROMPT}]}],
                "generationConfig": {"maxOutputTokens": 4000, "temperature": 0}}
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=body, headers={"x-goog-api-key": self.gemini},
                              timeout=aiohttp.ClientTimeout(total=180)) as r:
                d = await r.json(content_type=None)
                if r.status != 200:
                    raise RuntimeError(f"Gemini {r.status}: {str(d)[:300]}")
        parts = d.get("candidates", [{}])[0].get("content", {}).get("parts", [])
        return "".join(p.get("text", "") for p in parts).strip()

    async def transcribe(self, path: str, mime: str = "audio/ogg") -> str:
        """Never raises: network/API problems must not stop archiving."""
        mime = (mime or "audio/ogg").split(";")[0]
        if self.eleven:
            try:
                return await self._elevenlabs(path, mime)
            except Exception as e:
                log.warning("ElevenLabs transcription failed, trying Gemini: %s", str(e)[:160])
        if self.gemini:
            for model in self.models:
                for wait in (0, 15, 45):
                    if wait:
                        await asyncio.sleep(wait)
                    try:
                        return await self._gemini(model, path, mime)
                    except Exception as e:
                        retry = any(c in str(e) for c in RETRY_CODES)
                        log.warning("Transcription attempt failed (%s, %s): %s", model,
                                    "retry" if retry else "next model", str(e)[:160])
                        if not retry:
                            break
        log.error("Transcription failed for %s", os.path.basename(path))
        return ""
