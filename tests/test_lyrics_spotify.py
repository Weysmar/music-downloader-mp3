"""LRCLIB lyrics and Spotify conversion, with the APIs replaced by fakes."""
import pytest
import spotipy

import lyrics
import spotify_extractor
from spotify_extractor import SpotifyExtractor


class Resp:
    def __init__(self, status, payload=None, raise_json=False):
        self.status_code, self.ok = status, 200 <= status < 400
        self._payload, self._raise = payload, raise_json

    def json(self):
        if self._raise:
            raise ValueError("not JSON")
        return self._payload


def fake_lrclib(monkeypatch, get_answer, search_answer=None):
    def get(url, params=None, timeout=None):
        answer = get_answer if url.endswith("/get") else search_answer
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(lyrics._session, "get", get)


TRACK = {"title": "Alors on danse", "artists": ["Stromae"], "album": "Cheese", "duration_ms": 206000}


def test_lyrics_direct_hit(monkeypatch):
    fake_lrclib(monkeypatch, Resp(200, {"plainLyrics": "Alors on danse", "syncedLyrics": "[00:01.00] Alors on danse"}))
    assert lyrics.fetch_lyrics(TRACK) == {"plain": "Alors on danse", "synced": "[00:01.00] Alors on danse"}


def test_lyrics_search_fallback_checks_title_and_duration(monkeypatch):
    results = [
        {"trackName": "Alors on danse (Remix)", "duration": 206, "plainLyrics": "wrong"},
        {"trackName": "Alors on danse", "duration": 260, "plainLyrics": "wrong duration"},
        {"trackName": "Alors on danse", "duration": 207, "plainLyrics": "right"},
    ]
    fake_lrclib(monkeypatch, Resp(404), Resp(200, results))
    assert lyrics.fetch_lyrics(TRACK)["plain"] == "right"


def test_lyrics_of_a_longer_title_are_never_used(monkeypatch):
    track = {"title": "Plus rien", "artists": ["Orelsan"], "duration_ms": 209000}
    fake_lrclib(monkeypatch, Resp(404), Resp(200, [{"trackName": "Plus rien ne m'étonne", "duration": 209, "plainLyrics": "x"}]))
    assert lyrics.fetch_lyrics(track) is None
    assert lyrics.same_title("Aces High - 2015 Remaster", "Aces High")


@pytest.mark.parametrize("get_answer, search_answer", [
    (Resp(200, {"instrumental": True, "plainLyrics": None}), None),
    (Resp(404), Resp(200, [])),
    (Resp(200, raise_json=True), None),
    (ConnectionError("offline"), None),
    (Resp(404), Resp(500, raise_json=True)),
    (Resp(404), Resp(200, {"unexpected": "dict"})),
])
def test_lyrics_never_raise(monkeypatch, get_answer, search_answer):
    fake_lrclib(monkeypatch, get_answer, search_answer)
    assert lyrics.fetch_lyrics(TRACK) is None


def test_lyrics_skipped_without_title_or_artist():
    assert lyrics.fetch_lyrics({"title": "", "artists": ["X"]}) is None
    assert lyrics.fetch_lyrics({"title": "X"}) is None


# ------------------------------------------------------------------ Spotify

@pytest.fixture
def sp():
    return object.__new__(SpotifyExtractor)  # no OAuth: only the conversion logic is tested


def test_track_info_ignores_local_files_and_podcasts(sp):
    assert sp._track_info(None) is None
    assert sp._track_info({"id": "x", "is_local": True}) is None
    assert sp._track_info({"id": "ep", "type": "episode"}) is None
    assert sp._track_info({"name": "no id"}) is None


def test_track_info_conversion(sp):
    track = {
        "id": "sp1", "name": "Get Lucky", "track_number": 8, "disc_number": 1, "duration_ms": 369000,
        "artists": [{"name": "Daft Punk"}, {"name": "Pharrell Williams"}, {"name": None}],
        "external_ids": {"isrc": "USQX91300108"},
        "album": {"name": "Random Access Memories", "album_type": "album", "release_date": "2013-05-17", "total_tracks": 13,
                  "artists": [{"name": "Daft Punk"}],
                  "images": [{"url": "small", "width": 64}, {"url": "big", "width": 640}, {"url": "nowidth"}]},
    }
    info = sp._track_info(track)
    assert info["artist"] == "Daft Punk, Pharrell Williams"
    assert info["album_artist"] == "Daft Punk"
    assert info["cover_url"] == "big"
    assert info["isrc"] == "USQX91300108"


def test_choose_editions(sp):
    albums = [
        {"id": "orig", "name": "Master of Puppets", "album_type": "album", "release_date": "1986-03-03", "total_tracks": 8},
        {"id": "remaster", "name": "Master of Puppets (Remastered)", "album_type": "album", "release_date": "2017-11-10", "total_tracks": 8},
        {"id": "deluxe", "name": "Master of Puppets (Deluxe Box Set / Remastered)", "album_type": "album", "release_date": "2017-11-10", "total_tracks": 120},
        {"id": "single", "name": "Battery", "album_type": "single", "release_date": "1986-01-01", "total_tracks": 1},
    ]
    assert [a["id"] for a in sp._choose_editions(albums, "original")] == ["orig", "single"]
    assert [a["id"] for a in sp._choose_editions(albums, "largest")] == ["deluxe", "single"]


def test_call_waits_on_rate_limit_and_retries_server_errors(sp, monkeypatch):
    sleeps = []
    monkeypatch.setattr(spotify_extractor.time, "sleep", sleeps.append)
    errors = [
        spotipy.exceptions.SpotifyException(429, -1, "rate limited", headers={"Retry-After": "2"}),
        spotipy.exceptions.SpotifyException(502, -1, "bad gateway"),
    ]

    def flaky():
        if errors:
            raise errors.pop(0)
        return "ok"

    assert sp._call(flaky) == "ok"
    assert sleeps == [3, 2]


def test_call_does_not_retry_client_errors(sp):
    def forbidden():
        raise spotipy.exceptions.SpotifyException(403, -1, "Premium required")

    with pytest.raises(spotipy.exceptions.SpotifyException):
        sp._call(forbidden)
