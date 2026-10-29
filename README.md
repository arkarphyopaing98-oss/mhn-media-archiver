# MHN Media Archiver

Watches the **Product History** Telegram group, pairs each video/photo with the story the team writes,
lets Claude classify it, uploads it to Google Drive and logs it in a Google Sheet. The TikTok content
machine uses that sheet and those folders as its raw footage.

```
Telegram group ──► download ──► story pairing ──► Claude classify ──► Drive: TIKTOK RAW/<MATERIAL>/<PILLAR>/
                                                                   └► Sheet: MHN Raw Footage Log (1 row per file)
```

## Drive layout (created automatically)

`MHN MARKETING ASSETS / TIKTOK RAW /`
- `UPVC`, `ALUMINIUM`, `STEEL`, `IRON ROD`, `GENERAL`
  - `BEHIND THE SCENES`, `INSTALLATION`, `PRODUCT SHOWCASE`, `CUSTOMER & TESTIMONIAL`, `KNOWLEDGE & FAQ`, `TEAM & STORY`
- `_TO SORT` (low-confidence items; move them by hand and fix the sheet row)

File name: `20261027_upvc_slide-door_installation_4821.mp4` (date, material, product, pillar, Telegram msg id).

## How the story is matched to a video

1. The video's caption. 2. Captions in the same album. 3. Replies to the video.
4. Texts from the same person within 15 min after it (stops at their next video).
5. If none: their text up to 5 min before it.
New videos wait 10 minutes (`STORY_WAIT_SECONDS`) before processing so the team can finish typing.

## Voice notes 🎙️

Voice notes / audio files in the group are archived too (`INCLUDE_VOICE=true`, default):
- **Transcribed** — ElevenLabs Scribe if `ELEVENLABS_API_KEY` is set (best Burmese), else Gemini (`TRANSCRIBE_MODEL`, default `gemini-flash-latest`).
- **Linked to a video:** the video it replies to → else the same person's last video within 15 min before it → else their next video within 5 min.
- **Saved** as the original `.ogg` in that business's own voice folder (MHN → "Phone Call" `1yB3tMS7…`, RM → "Phone call" `1grPN4q9…`; override with `VOICE_FOLDERS` JSON), named `<date>_<mhn|rm>_<material>_<product>_<pillar>_voice_<msgid>.ogg`. An empty id for a business files its voice notes next to their video instead.
- No extra API key needed: transcription uses the existing `GEMINI_API_KEY` (ElevenLabs only if `ELEVENLABS_API_KEY` is added).
- **Sheet:** kind `voice`, transcript in the story column (K), linked video msg id in column **V**.
- The transcript is also added to the linked **video's story** (marked 🎙️), so the video is classified better.
- Transcription failed → row stays `to sort` and is re-transcribed by the reclassify job.

**Team rule:** send the video, then type the story (where, which product, what's happening, customer name if they agree).

## Setup (one time, ~30 min)

1. **Telegram login:** get `TG_API_ID`/`TG_API_HASH` at https://my.telegram.org, then on your computer run
   `pip install telethon==1.36.0 && python scripts/make_session.py`. Copy `TG_SESSION` and the group id → `TG_GROUP`.
2. **Google:** follow the steps at the top of `scripts/google_token.py` → `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REFRESH_TOKEN`.
   Publish the OAuth app (Testing mode tokens expire after 7 days).
3. **AI classifier:** `GEMINI_API_KEY` (Google AI Studio, free tier) — or `ANTHROPIC_API_KEY` for Claude.

Each good video gets an **edit link** for the TikTok video maker (Claude's Drive connector only downloads files < 10 MB):
- original under 9.3 MB → Log column T = the original itself.
- bigger → cut into full-quality **HQ parts** under `_HQ PARTS/` by stream copy (no re-encode); column T = part 1, column U = all parts as JSON `[{from, to, id}]`.
The old 4×-compressed `_PROXY 720p` copies are no longer made. A background job (`HQ_BACKFILL_DAYS`, default 45) adds HQ parts to older big videos, 4 per poll.
Every `POLL_MINUTES` (default 10) the archiver also re-scans the newest 400 messages, so posts missed by live updates are still archived.
Classifier: retries 429/503 with backoff; optional `CLASSIFIER_FALLBACK_MODEL`. Rows filed under `_TO SORT` because the AI call failed (no summary) are re-classified from Drive's thumbnail, `RECLASSIFY_PER_RUN` (default 15) per poll, newest first; confident results move the file to its proper folder.
4. Push this folder to a new private GitHub repo (e.g. `xandra-X/mhn-media-archiver`).
5. Render → New → Blueprint (uses `render.yaml`) → fill the secret env vars. Plan **Starter** (free instances sleep and miss messages).
6. First deploy runs with `BACKFILL=true` and archives the whole history oldest-first. Watch progress at `https://<service>.onrender.com/status`.
   Tip: test first with `BACKFILL_LIMIT=20`. After the backfill finishes, set `BACKFILL=false`.
   Restarts are safe: the sheet's `tg_msg_id` column prevents duplicates, and the service catches up from the last archived id.

## Notes
- `TG_SESSION` is a full login to your Telegram account. Keep it only in Render env vars.
- Messages mentioning Folding Door / French Door / Frosted Glass are archived with status `excluded: banned product`.
- Tests: `pip install pytest && pytest -q`.
