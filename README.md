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

**Team rule:** send the video, then type the story (where, which product, what's happening, customer name if they agree).

## Setup (one time, ~30 min)

1. **Telegram login:** get `TG_API_ID`/`TG_API_HASH` at https://my.telegram.org, then on your computer run
   `pip install telethon==1.36.0 && python scripts/make_session.py`. Copy `TG_SESSION` and the group id → `TG_GROUP`.
2. **Google:** follow the steps at the top of `scripts/google_token.py` → `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REFRESH_TOKEN`.
   Publish the OAuth app (Testing mode tokens expire after 7 days).
3. **AI classifier:** `GEMINI_API_KEY` (Google AI Studio, free tier) — or `ANTHROPIC_API_KEY` for Claude.

Each good video also gets a small 720p proxy copy under `_PROXY 720p/` (for Claude to edit from).
4. Push this folder to a new private GitHub repo (e.g. `xandra-X/mhn-media-archiver`).
5. Render → New → Blueprint (uses `render.yaml`) → fill the secret env vars. Plan **Starter** (free instances sleep and miss messages).
6. First deploy runs with `BACKFILL=true` and archives the whole history oldest-first. Watch progress at `https://<service>.onrender.com/status`.
   Tip: test first with `BACKFILL_LIMIT=20`. After the backfill finishes, set `BACKFILL=false`.
   Restarts are safe: the sheet's `tg_msg_id` column prevents duplicates, and the service catches up from the last archived id.

## Notes
- `TG_SESSION` is a full login to your Telegram account. Keep it only in Render env vars.
- Messages mentioning Folding Door / French Door / Frosted Glass are archived with status `excluded: banned product`.
- Tests: `pip install pytest && pytest -q`.
