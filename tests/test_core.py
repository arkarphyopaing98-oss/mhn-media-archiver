from datetime import datetime, timedelta, timezone

from app.classifier import parse_reply
from app.story import Msg, build_story
from app.taxonomy import TO_SORT, folder_path, is_banned

T0 = datetime(2026, 10, 27, 9, 0, tzinfo=timezone.utc)


def m(id, mins, sender=1, text="", media=False, g=None, reply=None):
    return Msg(id, T0 + timedelta(minutes=mins), sender, text, media, g, reply)


def test_caption_and_followup_text():
    v = m(10, 0, text="W55 slide", media=True)
    nb = [v, m(11, 2, text="Yangon site, 3 panels"), m(12, 3, sender=2, text="other person")]
    assert build_story(v, nb) == "W55 slide\nYangon site, 3 panels"


def test_followup_stops_at_next_video():
    v1 = m(10, 0, media=True)
    nb = [v1, m(11, 1, text="story one"), m(12, 2, media=True), m(13, 3, text="story two")]
    assert build_story(v1, nb) == "story one"
    assert build_story(nb[2], nb) == "story two"


def test_album_shares_caption_and_reply():
    a, b = m(20, 0, media=True, g=7, text="album caption"), m(21, 0, media=True, g=7)
    nb = [a, b, m(30, 30, sender=3, text="customer happy", reply=21)]
    assert build_story(b, nb) == "album caption\ncustomer happy"


def test_text_before_video_used_as_fallback():
    v = m(10, 0, media=True)
    assert build_story(v, [m(9, -2, text="before text"), v]) == "before text"


def test_window_limit():
    v = m(10, 0, media=True)
    assert build_story(v, [v, m(11, 40, text="too late")]) == ""


def test_parse_reply_and_folders():
    c = parse_reply('ok {"material":"upvc","product_type":"Slide Door","pillar":"installation",'
                    '"banned":false,"confidence":0.9,"summary_my":"x","hook_idea":"y"}')
    assert (c.material, c.pillar, c.confident) == ("UPVC", "INSTALLATION", True)
    assert folder_path(c.material, c.pillar, c.confident) == ["UPVC", "INSTALLATION"]
    bad = parse_reply('{"material":"wood","pillar":"??","confidence":0.9}')
    assert folder_path(bad.material, bad.pillar, bad.confident) == [TO_SORT]
    assert parse_reply("not json").confident is False


def test_banned():
    assert is_banned("This is a Folding Door install")
    assert not is_banned("uPVC slide door")


# --- voice notes -----------------------------------------------------------
from types import SimpleNamespace

from app.story import link_target


def test_voice_after_video_links_back_and_joins_story():
    v = m(10, 0, media=True)
    voice = m(11, 2, text="🎙️ ဒါ UPVC slide door ပါ")  # voice = text for pairing (has_media False)
    nb = [v, voice, m(12, 4, media=True)]
    assert link_target(voice, nb) == 10
    assert build_story(v, nb) == "🎙️ ဒါ UPVC slide door ပါ"


def test_voice_reply_wins_and_standalone_is_none():
    a, b = m(10, 0, media=True), m(11, 1, media=True)
    voice = m(12, 3, sender=2, reply=10)
    assert link_target(voice, [a, b, voice]) == 10
    lone = m(30, 0, sender=5)
    assert link_target(lone, [m(1, -60, sender=5, media=True), lone]) is None


def test_voice_before_video_links_forward():
    voice, v = m(10, 0), m(11, 2, media=True)
    assert link_target(voice, [voice, v]) == 11


def _tg(**kw):
    base = dict(voice=None, audio=None, document=None, video=None, video_note=None, gif=None, photo=None, file=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_media_kind_voice():
    from app.archiver import media_kind
    ogg = SimpleNamespace(mime_type="audio/ogg")
    assert media_kind(_tg(voice=True, document=True, file=ogg)) == "voice"
    assert media_kind(_tg(document=True, file=SimpleNamespace(mime_type="audio/mpeg"))) == "voice"
    assert media_kind(_tg(voice=True, file=ogg), include_voice=False) is None
    assert media_kind(_tg(video=True, file=SimpleNamespace(mime_type="video/mp4"))) == "video"


# --- Drive import (owner 2026-11-04: files dropped straight into TIKTOK RAW) ---
from app.drive_import import candidates, folder_guess, import_id, is_archiver_file, posted_at
from app.taxonomy import PROFILES


def test_import_id_stable_negative():
    a = import_id("1AbC")
    assert a < 0 and a == import_id("1AbC") and a != import_id("1AbD")


def test_archiver_files_are_skipped():
    assert is_archiver_file({"description": "story\nFrom Arkar, Telegram msg 1506"})
    assert is_archiver_file({"description": "HQ part 1/2 (0-20s) of x.mp4"})
    assert not is_archiver_file({"name": "IMG_0454.MOV", "description": ""})


def test_candidates_new_media_only_oldest_first_no_copies():
    files = [
        ({"id": "a", "name": "b.MOV", "mimeType": "video/quicktime", "size": "5", "modifiedTime": "2026-09-02T00:00:00Z"}, ("UPVC",)),
        ({"id": "b", "name": "a.jpg", "mimeType": "image/jpeg", "size": "7", "modifiedTime": "2025-12-01T00:00:00Z"}, ()),
        ({"id": "c", "name": "a.jpg", "mimeType": "image/jpeg", "size": "7", "modifiedTime": "2025-12-01T00:00:00Z"}, ()),  # copy
        ({"id": "d", "name": "doc.pdf", "mimeType": "application/pdf"}, ()),
        ({"id": "e", "name": "x.mp4", "mimeType": "video/mp4", "description": "Telegram msg 5"}, ()),
        ({"id": "known", "name": "k.jpg", "mimeType": "image/jpeg"}, ()),
    ]
    got = [f["id"] for f, _ in candidates(files, {"known"})]
    assert got == ["b", "a"]


def test_folder_guess_and_posted_at():
    p = PROFILES["mhn"]
    assert folder_guess(("UPVC", "INSTALLATION", "2025 jobs"), p) == ("UPVC", "INSTALLATION")
    assert folder_guess(("random",), p) == ("", "")
    assert posted_at({"imageMediaMetadata": {"time": "2025:12:26 10:17:01"}}, "Asia/Yangon") == "2025-12-26 10:17"
    assert posted_at({"modifiedTime": "2026-09-28T07:59:25.516Z"}, "Asia/Yangon") == "2026-09-28 14:29"


# --- Pick tab (owner 2026-11-04: footage must not repeat) ---
from datetime import date
from app.pick import build, job_id, usable


def _row(mid, posted, sender="Ko Aung", kind="video", mat="UPVC", prod="Window", used="", status="new", qc="good",
         size="5.0", dur="20", edit="https://e"):
    r = [""] * 21
    r[0], r[1], r[2], r[3], r[6], r[7], r[8], r[9] = str(mid), posted, sender, kind, f"f{mid}", mat, prod, "INSTALLATION"
    r[13], r[14], r[15], r[16], r[17], r[19] = dur, size, status, used, qc, edit
    return r


def test_pick_skips_bad_rows():
    assert usable(_row(1, "2026-11-01 10:00"))
    assert not usable(_row(2, "2026-11-01 10:00", status="duplicate: stale crash-guard row"))
    assert not usable(_row(3, "2026-11-01 10:00", qc="reject"))
    assert not usable(_row(4, "2026-11-01 10:00", size="40", edit=""))
    assert usable(_row(-5, "2026-11-01 10:00", size="4", edit=""))  # Drive upload, small original


def test_pick_job_rest_clip_rest_and_dedupe():
    today = date(2026, 11, 4)
    rows = [
        _row(10, "2026-11-03 09:00", size="5.1", dur="21"),  # job A
        _row(11, "2026-11-03 09:05", size="6.0", dur="30", used="2026-11-02"),  # job A used 2 days ago -> whole job rests
        _row(20, "2026-11-02 09:00", sender="Ko Min", size="5.1", dur="21"),  # job B, same size+dur as 10 but 10 skipped
        _row(21, "2026-11-02 09:00", sender="Ko Min", size="5.1", dur="21"),  # identical copy of 20 -> dropped
        _row(30, "2026-09-01 09:00", sender="Old", used="2026-10-01", size="7.0"),  # archive, clip rest over (>14d)
        _row(31, "2026-08-01 09:00", sender="Older", size="8.0"),  # archive, never used
    ]
    out = build(rows, today)
    ids = [r[3] for r in out]
    assert "10" not in ids and "11" not in ids and "21" not in ids
    assert ids == ["20", "31", "30"]  # new, archive(never used), archive(least recent)
    assert [r[1] for r in out] == ["new", "archive", "archive"]
    assert [r[0] for r in out] == [1, 2, 3]


def test_pick_alternates_new_and_archive_and_groups_jobs():
    today = date(2026, 11, 4)
    rows = [_row(i, p, sender=s, dur=d, size=f"{i}.5") for i, p, s, d in [
        (1, "2026-11-03 10:00", "A", "20"), (2, "2026-11-03 10:02", "A", "40"),  # same job, 2 clips
        (3, "2026-11-02 10:00", "B", "20"), (4, "2026-11-01 10:00", "C", "20"),
        (5, "2026-06-01 10:00", "D", "20"), (6, "2026-05-01 10:00", "E", "20")]]
    rows.append(_row(7, "2026-05-01 10:00", sender="E", kind="photo", size="0.2", dur=""))
    rows.append(_row(8, "2026-05-01 11:00", sender="F", kind="photo", size="0.2", dur=""))  # photos never deduped
    out = build(rows, today)
    assert [(r[0], r[1]) for r in out][:3] == [(1, "new"), (1, "new"), (2, "archive")]
    assert out[0][3] == "2"  # longest clip of the job first
    assert [r[1] for r in out if r[0] in (3, 4)] == ["new", "archive"]
    assert job_id(rows[0]) == job_id(rows[1])
    assert {"7", "8"} <= {r[3] for r in out}


def test_clip_rest_days_override():
    from datetime import date
    from app.pick import build
    row = [""] * 22
    row[0], row[1], row[2], row[3], row[6], row[7], row[8], row[14], row[15], row[16] = \
        "500", "2026-10-20 10:00", "Staff", "video", "fid", "RM", "BRICK", "5", "new", "2026-11-01"
    assert build([row], date(2026, 11, 7)) == []          # 6 days < 14-day rest
    assert len(build([row], date(2026, 11, 9), 7)) == 1    # 8 days > 7-day rest (RM)
