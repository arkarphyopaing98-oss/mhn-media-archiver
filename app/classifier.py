"""Classify a media item from the team's story text + a thumbnail, using Claude or Gemini (whichever key is set)."""
from __future__ import annotations

import base64
import json
import logging
import re
from dataclasses import dataclass

import aiohttp

from .taxonomy import MATERIALS, PILLARS, PRODUCT_TYPES

log = logging.getLogger(__name__)

SYSTEM = f"""You sort raw marketing footage for MHN Window & Door Center (Myanmar).
MHN manufactures and wholesales UPVC and aluminium windows/doors, plus steel and iron rod.
Staff post phone videos/photos in a Telegram group with a short Burmese or English note.

Return ONLY a JSON object with these keys:
- material: one of {MATERIALS}
- product_type: one of {PRODUCT_TYPES}
- pillar: one of {PILLARS}
- banned: true if the item shows or mentions a folding door, French door or frosted glass, else false
- confidence: number 0-1, how sure you are of material AND pillar
- summary_my: one Burmese sentence describing what the footage shows
- hook_idea: one short Burmese TikTok hook line (max 12 words) this footage could open with

Pillar guide: factory machines/cutting/welding/QC/loading = BEHIND THE SCENES; fitting on a building
site or a finished site = INSTALLATION; a finished product, close-ups, features, locks = PRODUCT SHOWCASE;
a customer speaking or a review = CUSTOMER & TESTIMONIAL; a staff member explaining, comparing or
giving tips/warranty info = KNOWLEDGE & FAQ; team members, events, company story = TEAM & STORY.
Use GENERAL material when it is not about one material. Never invent facts that are not in the note or image."""


@dataclass
class Classification:
    material: str = "GENERAL"
    product_type: str = "Other"
    pillar: str = ""
    banned: bool = False
    confidence: float = 0.0
    summary_my: str = ""
    hook_idea: str = ""

    @property
    def confident(self) -> bool:
        return self.confidence >= 0.55


def parse_reply(text: str) -> Classification:
    match = re.search(r"\{.*\}", text or "", re.S)
    if not match:
        return Classification()
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return Classification()
    c = Classification(
        material=str(data.get("material", "GENERAL")).upper(),
        product_type=str(data.get("product_type", "Other")),
        pillar=str(data.get("pillar", "")).upper(),
        banned=bool(data.get("banned", False)),
        confidence=float(data.get("confidence", 0) or 0),
        summary_my=str(data.get("summary_my", "")),
        hook_idea=str(data.get("hook_idea", "")),
    )
    if c.material not in MATERIALS:
        c.material, c.confidence = "GENERAL", min(c.confidence, 0.4)
    if c.pillar not in PILLARS:
        c.pillar, c.confidence = "", 0.0
    if c.product_type not in PRODUCT_TYPES:
        c.product_type = "Other"
    return c


class Classifier:
    """provider = "anthropic" (needs ANTHROPIC_API_KEY) or "gemini" (needs GEMINI_API_KEY)."""

    def __init__(self, api_key: str, model: str, provider: str = "anthropic") -> None:
        self.provider, self.model, self.key = provider, model, api_key
        if provider == "anthropic":
            from anthropic import AsyncAnthropic
            self.client = AsyncAnthropic(api_key=api_key)

    async def _anthropic(self, story: str, media_type: str, thumb: bytes | None) -> str:
        content: list[dict] = []
        if thumb:
            content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                        "data": base64.b64encode(thumb).decode()}})
        content.append({"type": "text", "text": f"Media type: {media_type}\nTeam note:\n{story or '(no note)'}"})
        resp = await self.client.messages.create(model=self.model, max_tokens=400, system=SYSTEM,
                                                 messages=[{"role": "user", "content": content}])
        return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")

    async def _gemini(self, story: str, media_type: str, thumb: bytes | None) -> str:
        parts: list[dict] = []
        if thumb:
            parts.append({"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(thumb).decode()}})
        parts.append({"text": f"Media type: {media_type}\nTeam note:\n{story or '(no note)'}"})
        body = {"system_instruction": {"parts": [{"text": SYSTEM}]}, "contents": [{"role": "user", "parts": parts}],
                "generationConfig": {"maxOutputTokens": 600, "responseMimeType": "application/json"}}
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=body, headers={"x-goog-api-key": self.key},
                              timeout=aiohttp.ClientTimeout(total=90)) as r:
                d = await r.json()
                if r.status != 200:
                    raise RuntimeError(f"Gemini {r.status}: {str(d)[:300]}")
        return "".join(p.get("text", "") for p in d["candidates"][0]["content"]["parts"])

    async def classify(self, story: str, media_type: str, thumb_jpeg: bytes | None) -> Classification:
        try:
            fn = self._gemini if self.provider == "gemini" else self._anthropic
            return parse_reply(await fn(story, media_type, thumb_jpeg))
        except Exception:  # network/API issues must never stop archiving
            log.exception("Classification failed; filing under _TO SORT")
            return Classification()
