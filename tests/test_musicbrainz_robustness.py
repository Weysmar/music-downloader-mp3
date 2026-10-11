"""MusicBrainz extraction under adverse conditions: split singles, DVD media, flaky server."""
import json

import pytest
import requests

import musicbrainz_extractor
from musicbrainz_extractor import MusicBrainzExtractor


def credit(artist_id, name, join=""):
    return {"name": name, "joinphrase": join, "artist": {"id": artist_id, "name": name}}


MAIDEN = credit("maiden", "Iron Maiden")
KISS = credit("kiss", "KISS")


def split_single():
    """Split single 'Detroit Rock City / Iron Maiden' (KISS song + Iron Maiden song) with a DVD."""
    return {
        "id": "split", "title": "Detroit Rock City / Running Free", "date": "2005", "status": "Official",
        "artist-credit": [credit("kiss", "KISS", " / "), MAIDEN],
        "release-group": {"id": "rg", "primary-type": "Single", "secondary-types": [], "first-release-date": "2005"},
        "media": [
            {"position": 1, "format": "CD", "track-count": 2, "tracks": [
                {"id": "t1", "position": 1, "title": "Detroit Rock City", "artist-credit": [KISS],
                 "recording": {"id": "r-kiss", "title": "Detroit Rock City"}},
                {"id": "t2", "position": 2, "title": "Running Free", "artist-credit": [MAIDEN],
                 "recording": {"id": "r-maiden", "title": "Running Free"}},
                {"id": "t3", "position": 3, "title": "Running Free (video)", "artist-credit": [MAIDEN],
                 "recording": {"id": "r-clip", "title": "Running Free", "video": True}},
            ]},
            {"position": 2, "format": "DVD-Video", "track-count": 1, "tracks": [
                {"id": "t4", "position": 1, "title": "Live concert", "artist-credit": [MAIDEN],
                 "recording": {"id": "r-dvd", "title": "Live concert"}},
            ]},
        ],
    }


@pytest.fixture
def mb(monkeypatch):
    monkeypatch.setattr(musicbrainz_extractor.time, "sleep", lambda s: None)
    return MusicBrainzExtractor()


def test_split_single_keeps_only_the_artist_audio_tracks(mb, monkeypatch):
    monkeypatch.setattr(mb, "get_release_groups", lambda *a, **k: [{"id": "rg", "title": "split"}])
    monkeypatch.setattr(mb, "choose_release", lambda *a, **k: {"id": "split"})
    monkeypatch.setattr(mb, "get_release", lambda rid: split_single())
    tracks = mb.get_artist_tracks("maiden", "Iron Maiden")
    assert [t["mb_recording_id"] for t in tracks] == ["r-maiden"]  # no KISS song, no clip, no DVD


def test_track_without_own_credit_inherits_album_artist(mb):
    release = split_single()
    release["artist-credit"] = [MAIDEN]
    del release["media"][0]["tracks"][1]["artist-credit"]
    track = next(t for t in mb.tracks_from_release(release) if t["mb_recording_id"] == "r-maiden")
    assert track["mb_artist_ids"] == ["maiden"]


class FakeResponse:
    def __init__(self, status=200, payload=None, text=None):
        self.status_code = status
        self.ok = 200 <= status < 400
        self._payload = payload
        self.text = text if text is not None else json.dumps(payload)

    def json(self):
        if self._payload is None:
            raise ValueError("invalid JSON")
        return self._payload


def scripted_session(mb, monkeypatch, answers):
    calls = []

    def get(url, params=None, timeout=None):
        calls.append(url)
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(mb.session, "get", get)
    monkeypatch.setattr(mb, "MIN_INTERVAL", 0)
    return calls


def test_get_survives_network_errors_busy_server_and_garbage(mb, monkeypatch):
    calls = scripted_session(mb, monkeypatch, [
        requests.ConnectionError("reset"),
        FakeResponse(503, text="busy"),
        FakeResponse(200, payload=None, text="<html>proxy error</html>"),
        FakeResponse(200, payload={"id": "ok"}),
    ])
    assert mb._get("artist/x") == {"id": "ok"}
    assert len(calls) == 4


def test_get_gives_up_cleanly(mb, monkeypatch):
    scripted_session(mb, monkeypatch, [FakeResponse(503, text="busy")] * 6)
    assert mb._get("artist/x") is None
    scripted_session(mb, monkeypatch, [FakeResponse(404, payload={"error": "not found"})])
    assert mb._get("artist/x") is None
    scripted_session(mb, monkeypatch, [FakeResponse(400, payload={"error": "bad query"})])
    assert mb._get("artist/x") is None


def test_browse_paginates(mb, monkeypatch):
    pages = [
        {"release-groups": [{"id": str(i)} for i in range(100)], "release-group-count": 150},
        {"release-groups": [{"id": str(i)} for i in range(100, 150)], "release-group-count": 150},
    ]
    seen_offsets = []

    def fake_get(endpoint, params=None):
        seen_offsets.append(params["offset"])
        return pages.pop(0)

    monkeypatch.setattr(mb, "_get", fake_get)
    items = mb._browse("release-group", "release-groups", {"artist": "x"})
    assert len(items) == 150 and seen_offsets == [0, 100]


def test_resolve_artist_prefers_exact_name_or_alias(mb, monkeypatch):
    results = {"artists": [
        {"id": "1", "name": "Nirvana (UK)", "score": 100},
        {"id": "2", "name": "Nirvana", "score": 95, "disambiguation": "US grunge band"},
        {"id": "3", "name": "Oasis", "score": 90, "aliases": [{"name": "Rain"}]},
    ]}
    monkeypatch.setattr(mb, "_get", lambda e, p=None: results)
    assert mb.resolve_artist("nirvana")["id"] == "2"
    assert mb.resolve_artist("Rain")["id"] == "3"


def test_resolve_artist_by_mbid(mb, monkeypatch):
    mbid = "5b11f4ce-a62d-471e-81fc-a69a8278c7da"
    monkeypatch.setattr(mb, "_get", lambda e, p=None: {"id": mbid, "name": "Nirvana"} if e == f"artist/{mbid}" else None)
    assert mb.resolve_artist(f" {mbid} ")["name"] == "Nirvana"


def test_resolve_artist_with_quotes_in_name(mb, monkeypatch):
    queries = []
    monkeypatch.setattr(mb, "_get", lambda e, p=None: queries.append(p["query"]) or {"artists": []})
    assert mb.resolve_artist('Guns N\' Roses "Live"') is None
    assert queries == ['artist:"Guns N\' Roses \\"Live\\""']


def test_extract_discography_writes_json_in_data_folder(mb, monkeypatch, isolated_data_dir):
    monkeypatch.setattr(mb, "resolve_artist", lambda a: {"id": "maiden", "name": "Iron Maiden"})
    monkeypatch.setattr(mb, "get_artist_tracks", lambda *a, **k: [{"title": "Running Free"}])
    path = mb.extract_discography("Iron Maiden")
    assert path == str(isolated_data_dir / "Iron Maiden_mb_library.json")
    with open(path, encoding="utf-8") as f:
        assert json.load(f) == [{"title": "Running Free"}]


def test_extract_discography_unknown_artist(mb, monkeypatch):
    monkeypatch.setattr(mb, "resolve_artist", lambda a: None)
    assert mb.extract_discography("zzzz") is None


def test_enrich_track_from_isrc(mb, monkeypatch):
    release = split_single()
    release["artist-credit"] = [MAIDEN]
    answers = {
        "isrc/GBAYE0000001": {"recordings": [{"id": "r-maiden", "title": "Running Free", "length": 200000}]},
        "recording/r-maiden": {"releases": [{"id": "split", "title": "Running Free", "status": "Official", "date": "2005"}]},
    }
    monkeypatch.setattr(mb, "_get", lambda e, p=None: answers.get(e))
    monkeypatch.setattr(mb, "get_release", lambda rid: release)
    spotify_track = {"id": "sp1", "source": "spotify", "title": "Running Free", "artist": "Iron Maiden",
                     "isrc": "GBAYE0000001", "duration_ms": 200500, "cover_url": "https://i.scdn.co/x.jpg"}
    enriched = mb.enrich_track(spotify_track)
    assert enriched["mb_recording_id"] == "r-maiden"
    assert enriched["id"] == "sp1" and enriched["source"] == "spotify"  # identity kept
    assert enriched["cover_fallback_url"] == "https://i.scdn.co/x.jpg"  # Spotify cover kept as fallback


def test_enrich_tracks_never_crashes(mb, monkeypatch):
    def boom(track):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(mb, "enrich_track", boom)
    tracks = [{"title": "A", "isrc": "X"}]
    assert mb.enrich_tracks(tracks) == tracks
