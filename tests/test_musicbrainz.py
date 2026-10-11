from main import _legacy_args
from musicbrainz_extractor import MusicBrainzExtractor

ARTIST = {"name": "Daft Punk", "joinphrase": "", "artist": {"id": "dp", "name": "Daft Punk"}}
GUEST = {"name": "Pharrell Williams", "joinphrase": "", "artist": {"id": "pw", "name": "Pharrell Williams"}}


def release(**extra):
    data = {
        "id": "rel1", "title": "Random Access Memories", "date": "2013-05-17", "country": "XW", "status": "Official",
        "barcode": "888837168612", "artist-credit": [ARTIST],
        "cover-art-archive": {"front": True},
        "label-info": [{"catalog-number": "88883716861", "label": {"name": "Columbia"}}],
        "release-group": {"id": "rg1", "primary-type": "Album", "secondary-types": [], "first-release-date": "2013-05-17",
                          "genres": [{"name": "disco", "count": 3}, {"name": "electronic", "count": 9}]},
        "media": [
            {"position": 1, "format": "Digital Media", "track-count": 2, "tracks": [
                {"id": "t1", "position": 1, "title": "Give Life Back to Music", "length": 274000,
                 "recording": {"id": "rec1", "title": "Give Life Back to Music", "isrcs": ["USQX91300101"]}},
                {"id": "t2", "position": 2, "title": "Get Lucky", "length": 369000,
                 "artist-credit": [dict(ARTIST, joinphrase=" feat. "), GUEST],
                 "recording": {"id": "rec2", "title": "Get Lucky", "isrcs": []}},
            ]},
        ],
    }
    data.update(extra)
    return data


def test_tracks_from_release():
    tracks = MusicBrainzExtractor().tracks_from_release(release())
    assert len(tracks) == 2
    lucky = tracks[1]
    assert lucky["artist"] == "Daft Punk feat. Pharrell Williams"
    assert lucky["artists"] == ["Daft Punk", "Pharrell Williams"]
    assert lucky["album_artist"] == "Daft Punk"
    assert (lucky["track_number"], lucky["track_total"], lucky["disc_number"], lucky["disc_total"]) == (2, 2, 1, 1)
    assert lucky["genres"] == ["Electronic", "Disco"]
    assert lucky["label"] == "Columbia"
    assert lucky["mb_artist_ids"] == ["dp", "pw"]
    assert tracks[0]["isrc"] == "USQX91300101"
    assert tracks[0]["cover_url"].endswith("/release/rel1/front-1200")
    assert tracks[0]["cover_fallback_url"].endswith("/release-group/rg1/front-1200")


def test_choose_release(monkeypatch):
    releases = [
        {"id": "deluxe", "date": "2023-05-12", "disambiguation": "10th anniversary", "media": [{"format": "CD", "track-count": 22}], "cover-art-archive": {"front": True}},
        {"id": "vinyl", "date": "2013-05-17", "country": "US", "media": [{"format": "Vinyl", "track-count": 13}], "cover-art-archive": {"front": True}},
        {"id": "digital", "date": "2013-05-17", "country": "XW", "media": [{"format": "Digital Media", "track-count": 13}], "cover-art-archive": {"front": True}},
        {"id": "nocover", "date": "2013-05-17", "country": "XW", "media": [{"format": "CD", "track-count": 13}], "cover-art-archive": {"front": False}},
    ]
    monkeypatch.setattr(MusicBrainzExtractor, "_browse", lambda self, e, k, p: releases)
    mb = MusicBrainzExtractor()
    assert mb.choose_release("rg")["id"] == "digital"
    assert mb.choose_release("rg", edition="largest")["id"] == "deluxe"


def test_bootlegs_are_never_chosen(monkeypatch):
    def browse(self, entity, key, params):
        if params.get("status") == "official":
            return []
        return [{"id": "boot", "status": "Bootleg", "date": "2001"}, {"id": "promo", "status": "Promotion", "date": "2001"}]

    monkeypatch.setattr(MusicBrainzExtractor, "_browse", browse)
    assert MusicBrainzExtractor().choose_release("rg")["id"] == "promo"
    monkeypatch.setattr(MusicBrainzExtractor, "_browse", lambda self, e, k, p: [] if p.get("status") else [{"id": "b", "status": "Bootleg"}])
    assert MusicBrainzExtractor().choose_release("rg") is None


def test_release_groups_filtering(monkeypatch):
    groups = [
        {"id": "s", "title": "Single", "primary-type": "Single", "secondary-types": [], "first-release-date": "2001"},
        {"id": "l", "title": "Alive", "primary-type": "Album", "secondary-types": ["Live"], "first-release-date": "2007"},
        {"id": "a2", "title": "Discovery", "primary-type": "Album", "secondary-types": [], "first-release-date": "2001"},
        {"id": "a1", "title": "Homework", "primary-type": "Album", "secondary-types": [], "first-release-date": "1997"},
    ]
    monkeypatch.setattr(MusicBrainzExtractor, "_browse", lambda self, e, k, p: groups)
    mb = MusicBrainzExtractor()
    assert [g["id"] for g in mb.get_release_groups("x")] == ["a1", "a2", "s"]
    assert [g["id"] for g in mb.get_release_groups("x", include_secondary=["live"])] == ["a1", "a2", "l", "s"]


def test_legacy_cli_flags():
    assert _legacy_args(["--extract"]) == ["spotify-library", "-o", "library.json", "--extract-only"]
    assert _legacy_args(["--download", "--library", "x.json"]) == ["download", "x.json"]
    assert _legacy_args(["--all"]) == ["spotify-library", "-o", "library.json"]
    assert _legacy_args(["artist", "X"]) == ["artist", "X"]
