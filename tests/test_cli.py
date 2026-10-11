"""Command line: argument validation, error exits, data folder wiring."""
import json

import pytest

import main
from main import build_parser


@pytest.fixture(autouse=True)
def quiet_logging(monkeypatch):
    monkeypatch.setattr(main, "setup_logging", lambda **k: None)


@pytest.mark.parametrize("argv", [
    ["artist", "X", "--limit", "-1"],
    ["artist", "X", "--limit", "abc"],
    ["artist", "X", "--types", "albums"],
    ["artist", "X", "--include", "lives"],
    ["artist", "X", "--edition", "deluxe"],
    ["download", "x.json", "--workers", "0"],
    ["download"],
    ["unknown-command"],
])
def test_invalid_arguments_are_rejected(argv, capsys):
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(argv)
    assert exc.value.code == 2


def test_valid_arguments_are_normalized():
    args = build_parser().parse_args(["artist", "X", "--types", " Album, EP ", "--include", "Live", "--limit", "0"])
    assert args.types == "album,ep" and args.include == "live" and args.limit == 0


@pytest.mark.parametrize("command", ["download", "enrich", "retag"])
def test_missing_library_exits_with_error(command):
    with pytest.raises(SystemExit) as exc:
        main.main([command, "nope.json"])
    assert exc.value.code == 1


def test_artist_without_name_exits_with_error():
    with pytest.raises(SystemExit) as exc:
        main.main(["artist"])
    assert exc.value.code == 1


def test_unexpected_error_exits_cleanly(monkeypatch, isolated_data_dir):
    isolated_data_dir.mkdir(exist_ok=True)
    (isolated_data_dir / "broken.json").write_text("[{", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        main.main(["enrich", "broken.json"])
    assert exc.value.code == 1


def test_missing_ffmpeg_is_reported(monkeypatch, isolated_data_dir):
    isolated_data_dir.mkdir(exist_ok=True)
    (isolated_data_dir / "lib.json").write_text(json.dumps([{"title": "A"}]), encoding="utf-8")
    monkeypatch.setattr(main, "find_ffmpeg", lambda: None)
    with pytest.raises(SystemExit) as exc:
        main.main(["download", "lib.json"])
    assert exc.value.code == 1


def test_artist_extract_only_writes_into_data_folder(monkeypatch, isolated_data_dir):
    import musicbrainz_extractor

    monkeypatch.setattr(musicbrainz_extractor.MusicBrainzExtractor, "resolve_artist", lambda self, a: {"id": "x", "name": "Daft Punk"})
    monkeypatch.setattr(musicbrainz_extractor.MusicBrainzExtractor, "get_artist_tracks", lambda self, *a: [{"title": "Da Funk"}])
    main.main(["artist", "Daft Punk", "--extract-only"])
    assert json.loads((isolated_data_dir / "Daft Punk_mb_library.json").read_text(encoding="utf-8")) == [{"title": "Da Funk"}]


def test_legacy_shortcut_scripts_still_work(monkeypatch):
    called = []
    monkeypatch.setattr(main, "main", lambda argv: called.append(argv))
    import runpy

    monkeypatch.setattr("sys.argv", ["download_discography_free.py", "Daft Punk", "--extract-only"])
    runpy.run_module("download_discography_free", run_name="__main__")
    assert called == [["artist", "Daft Punk", "--extract-only"]]
