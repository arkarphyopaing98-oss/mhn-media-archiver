"""Central configuration, read once from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _req(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


@dataclass(frozen=True)
class Config:
    # Telegram (user account via Telethon)
    tg_api_id: int
    tg_api_hash: str
    tg_session: str
    tg_group: str  # numeric chat id (e.g. -1001234567890) or @username / invite link

    # Google (OAuth user credentials so files count against YOUR Drive quota)
    google_client_id: str
    google_client_secret: str
    google_refresh_token: str
    drive_root_folder_id: str  # "TIKTOK RAW" folder
    sheet_id: str              # "MHN Raw Footage Log"
    sheet_tab: str = "Log"

    # Classification
    anthropic_api_key: str = ""
    classifier_model: str = "claude-haiku-4-5"
    gemini_api_key: str = ""
    elevenlabs_api_key: str = ""   # optional: best Burmese speech-to-text for voice notes

    @property
    def classifier_provider(self) -> str:
        return "anthropic" if self.anthropic_api_key else "gemini"

    @property
    def classifier_key(self) -> str:
        return self.anthropic_api_key or self.gemini_api_key

    # Behaviour
    story_wait_seconds: int = 600      # live: wait this long for the team's text after a video
    story_window_minutes: int = 15     # texts from the same sender within this window = the story
    max_file_mb: int = 2000
    backfill: bool = False
    backfill_limit: int = 0            # 0 = whole history
    port: int = 10000
    include_photos: bool = True
    include_voice: bool = True         # archive + transcribe voice notes / audio files
    voice_folder_id: str = ""          # all voice notes go into this one Drive folder ("" = next to their video)
    timezone: str = "Asia/Yangon"
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            tg_api_id=int(_req("TG_API_ID")),
            tg_api_hash=_req("TG_API_HASH"),
            tg_session=_req("TG_SESSION"),
            tg_group=_req("TG_GROUP"),
            google_client_id=_req("GOOGLE_CLIENT_ID"),
            google_client_secret=_req("GOOGLE_CLIENT_SECRET"),
            google_refresh_token=_req("GOOGLE_REFRESH_TOKEN"),
            drive_root_folder_id=_req("DRIVE_ROOT_FOLDER_ID"),
            sheet_id=_req("SHEET_ID"),
            sheet_tab=os.environ.get("SHEET_TAB", "Log"),
            anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
            gemini_api_key=os.environ.get("GEMINI_API_KEY", ""),
            elevenlabs_api_key=os.environ.get("ELEVENLABS_API_KEY", ""),
            classifier_model=os.environ.get("CLASSIFIER_MODEL") or ("claude-haiku-4-5" if os.environ.get("ANTHROPIC_API_KEY") else "gemini-flash-lite-latest"),
            story_wait_seconds=_int("STORY_WAIT_SECONDS", 600),
            story_window_minutes=_int("STORY_WINDOW_MINUTES", 15),
            max_file_mb=_int("MAX_FILE_MB", 2000),
            backfill=os.environ.get("BACKFILL", "false").lower() in {"1", "true", "yes"},
            backfill_limit=_int("BACKFILL_LIMIT", 0),
            port=_int("PORT", 10000),
            include_photos=os.environ.get("INCLUDE_PHOTOS", "true").lower() in {"1", "true", "yes"},
            include_voice=os.environ.get("INCLUDE_VOICE", "true").lower() in {"1", "true", "yes"},
            voice_folder_id=os.environ.get("VOICE_FOLDER_ID", "1yB3tMS7Y8AXBOphYVzCGMY18MlZltWTy").strip(),
            timezone=os.environ.get("TZ_NAME", "Asia/Yangon"),
        )
