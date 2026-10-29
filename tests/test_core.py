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
