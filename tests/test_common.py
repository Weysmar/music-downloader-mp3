import os

from common import build_track_path, clean_title, dedupe_tracks, normalize, sanitize_component, text_similarity


def test_sanitize_component():
    assert sanitize_component("AC/DC") == "AC-DC"
    assert sanitize_component("Re: Stacks") == "Re - Stacks"
    assert sanitize_component("CON") == "_CON"
    assert sanitize_component("Album...") == "Album"
    assert sanitize_component('Say "Hi"?') == "Say 'Hi'"
    assert sanitize_component("") == "Unknown"
    assert sanitize_component("???") == "Unknown"
    assert len(sanitize_component("x" * 300, 50)) == 50


def test_normalize():
    assert normalize("Beyoncé") == "beyonce"
    assert normalize("Song (feat. Angèle)") == "song"
    assert normalize("With or Without You") == "with or without you"
    assert normalize("bbno$") == "bbnos"


def test_clean_title():
    assert clean_title("Aces High - 2015 Remaster") == "Aces High"
    assert clean_title("Layla (Radio Edit)") == "Layla"
    assert clean_title("Dirty Harry (feat. Bootie Brown)") == "Dirty Harry"
    assert clean_title("Démons - live orchestral") == "Démons - live orchestral"


def test_text_similarity():
    assert text_similarity("Blackened", "Blackened") == 1.0
    assert text_similarity("Blackened", "Metallica - Blackened (Official Audio)") >= 0.9
    assert text_similarity("Blackened", "Enter Sandman") < 0.5


def test_build_track_path_single_disc():
    track = {"title": "Da Funk", "artists": ["Daft Punk"], "album_artist": "Daft Punk", "album": "Homework",
             "original_date": "1997-01-20", "track_number": 7, "disc_number": 1, "disc_total": 1}
    assert build_track_path("downloads", track) == os.path.join("downloads", "Daft Punk", "1997 - Homework", "07 - Da Funk.mp3")


def test_build_track_path_multi_disc_and_missing_fields():
    track = {"title": "Track", "artist": "A, B", "album": "Live: Tour", "date": "2001", "track_number": 3,
             "disc_number": 2, "disc_total": 2}
    assert build_track_path("d", track) == os.path.join("d", "A", "2001 - Live - Tour", "2-03 - Track.mp3")
    legacy = {"title": "Song", "artist": "X"}  # v1 JSON without album info
    assert build_track_path("d", legacy) == os.path.join("d", "X", "Unknown Album", "Song.mp3")


def test_dedupe_tracks():
    album = {"id": "1", "title": "Hit", "artists": ["A"], "release_type": "album"}
    single = {"id": "2", "title": "Hit - Radio Edit", "artists": ["A"], "release_type": "single"}
    intro1 = {"id": "3", "title": "Intro", "artists": ["A"], "release_type": "album"}
    intro2 = {"id": "4", "title": "Intro", "artists": ["A"], "release_type": "album"}
    same_isrc = {"id": "5", "title": "Other", "artists": ["A"], "isrc": "FR123", "release_type": "album"}
    same_isrc2 = {"id": "6", "title": "Other (Remaster)", "artists": ["A"], "isrc": "fr123", "release_type": "album"}
    result = dedupe_tracks([album, single, intro1, intro2, same_isrc, same_isrc2, album])
    assert [t["id"] for t in result] == ["1", "3", "4", "5"]
    assert len(dedupe_tracks([album, single], title_dedup=False)) == 2


# --------------------------------------------------------------- robustness

import json  # noqa: E402

import pytest  # noqa: E402

import common  # noqa: E402
from common import as_int, library_output_path, load_library, resolve_library, write_json  # noqa: E402


def test_as_int():
    assert as_int(7) == 7
    assert as_int("07") == 7
    assert as_int("3/12") == 3
    assert as_int("A1") is None
    assert as_int(None, 1) == 1
    assert as_int(True) is None


def test_build_track_path_tolerates_odd_values():
    # string numbers (hand-edited JSON), vinyl sides, missing disc info: never a crash
    track = {"title": "Song", "artist": "X", "album": "LP", "track_number": "05", "disc_number": "2", "disc_total": None}
    assert build_track_path("d", track).endswith(os.path.join("LP", "2-05 - Song.mp3"))
    vinyl = {"title": "Side A", "artist": "X", "album": "LP", "track_number": "A1"}
    assert build_track_path("d", vinyl).endswith(os.path.join("LP", "Side A.mp3"))


def test_build_track_path_windows_unsafe_names():
    track = {"title": "What? / Why: * <now>", "artist": "AUX", "album": "CON.", "track_number": 1}
    path = build_track_path("d", track)
    parts = path.split(os.sep)
    assert parts[1] == "_AUX"
    assert parts[2] == "_CON"
    assert not any(c in parts[3] for c in r'<>:"/\|?*')


def test_build_track_path_very_long_names_stay_bounded():
    track = {"title": "T" * 500, "artist": "A" * 500, "album": "B" * 500, "track_number": 1, "date": "2000"}
    artist, album, filename = build_track_path("d", track).split(os.sep)[1:]
    assert len(artist) <= 80 and len(album) <= 100 and len(filename) <= 144


def test_clean_title_strips_translations_only():
    assert clean_title("Nothin’ = ナッシン") == "Nothin’"
    assert clean_title("Кино = Kino") == "Кино = Kino"  # the original title is never removed
    assert clean_title("E = MC²") == "E = MC²"


def test_write_json_is_atomic(tmp_path):
    path = tmp_path / "lib.json"
    write_json(str(path), [{"title": "ok"}])
    with pytest.raises(TypeError):
        write_json(str(path), [{"title": object()}])  # crash while writing
    assert json.loads(path.read_text(encoding="utf-8")) == [{"title": "ok"}]  # previous content intact


def test_load_library_validation(tmp_path):
    good = tmp_path / "good.json"
    good.write_text('\ufeff[{"title": "A"}, {"artist": "no title"}, "junk", {"title": "  "}, {"title": "B"}]', encoding="utf-8")
    assert [t["title"] for t in load_library(str(good))] == ["A", "B"]  # BOM + invalid entries tolerated

    broken = tmp_path / "broken.json"
    broken.write_text('[{"title": "A"},', encoding="utf-8")
    with pytest.raises(ValueError, match="not valid JSON"):
        load_library(str(broken))

    not_a_list = tmp_path / "dict.json"
    not_a_list.write_text('{"title": "A"}', encoding="utf-8")
    with pytest.raises(ValueError, match="list of tracks"):
        load_library(str(not_a_list))


def test_library_paths_use_data_folder(isolated_data_dir, tmp_path, monkeypatch):
    assert library_output_path("X_mb_library.json") == os.path.join(str(isolated_data_dir), "X_mb_library.json")
    assert library_output_path(os.path.join("elsewhere", "x.json")) == os.path.join("elsewhere", "x.json")

    monkeypatch.chdir(tmp_path)
    (tmp_path / "only_here.json").write_text("[]", encoding="utf-8")
    assert resolve_library("only_here.json") == "only_here.json"  # current folder as fallback
    (isolated_data_dir / "both.json").write_text("[]", encoding="utf-8")
    (tmp_path / "both.json").write_text("[]", encoding="utf-8")
    assert resolve_library("both.json") == os.path.join(str(isolated_data_dir), "both.json")  # data folder first


def test_find_ffmpeg_requires_ffprobe(tmp_path, monkeypatch):
    ext = ".exe" if os.name == "nt" else ""
    monkeypatch.setattr(common, "PROJECT_DIR", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(common.shutil, "which", lambda name: None)
    (tmp_path / f"ffmpeg{ext}").write_bytes(b"")
    assert common.find_ffmpeg() is None  # ffmpeg alone is not enough for yt-dlp
    (tmp_path / f"ffprobe{ext}").write_bytes(b"")
    assert common.find_ffmpeg() == str(tmp_path)
