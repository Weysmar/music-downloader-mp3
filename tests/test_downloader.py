"""End-to-end download pipeline with yt-dlp and YouTube Music replaced by fakes (real MP3 files, real tags)."""
import json
import os
import shutil
import time

import pytest
from mutagen.id3 import ID3

import youtube_downloader
from common import build_track_path
from matcher import Candidate
from metadata_injector import MetadataInjector
from youtube_downloader import YouTubeDownloader

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64

TRACK = {
    "id": "mb-rec1", "title": "Da Funk", "artist": "Daft Punk", "artists": ["Daft Punk"], "album": "Homework",
    "album_artist": "Daft Punk", "track_number": 7, "track_total": 16, "disc_number": 1, "disc_total": 1,
    "original_date": "1997-01-20", "duration_ms": 330000, "mb_recording_id": "11111111-1111-1111-1111-111111111111",
}


def track(title, **extra):
    return {**TRACK, "id": f"mb-{title}", "title": title, "mb_recording_id": None, **extra}


class FakeMatcher:
    def __init__(self, results=None):
        self.results = results or {}

    def find(self, t):
        ids = self.results.get(t["title"], ["vid-ok"])
        return [Candidate(video_id=v, title=t["title"], artists=["Daft Punk"], duration=330, score=0.95) for v in ids]


@pytest.fixture
def ydl(monkeypatch, mp3_template):
    """Fake yt-dlp. script[video_id] = list of outcomes: "ok", "noconvert" or an error message."""
    script, calls = {}, []

    class FakeYDL:
        def __init__(self, opts):
            self.outtmpl = opts["outtmpl"]
            assert opts["postprocessors"][0]["key"] == "FFmpegExtractAudio"

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def download(self, urls):
            video_id = urls[0].split("v=")[1]
            calls.append(video_id)
            outcome = (script.get(video_id) or ["ok"]).pop(0) if script.get(video_id) else "ok"
            # the source stream is always left behind, like a real failed/finished conversion would
            with open(self.outtmpl.replace("%(ext)s", "webm"), "wb") as f:
                f.write(b"opus")
            if outcome == "ok":
                shutil.copy(mp3_template, self.outtmpl.replace("%(ext)s", "mp3"))
            elif outcome != "noconvert":
                raise youtube_downloader.yt_dlp.utils.DownloadError(f"ERROR: {outcome}")

    monkeypatch.setattr(youtube_downloader.yt_dlp, "YoutubeDL", FakeYDL)
    monkeypatch.setattr(youtube_downloader.time, "sleep", lambda s: None)
    monkeypatch.setattr(youtube_downloader, "fetch_lyrics", lambda t: {"plain": "la la", "synced": "[00:01.00] la la"})
    monkeypatch.setattr(MetadataInjector, "fetch_cover", classmethod(lambda cls, t: (JPEG, "image/jpeg")))
    return script, calls


@pytest.fixture
def dl(tmp_path, ydl):
    downloader = YouTubeDownloader(output_dir=str(tmp_path / "downloads"), max_workers=2, rate_limit_delay=0)
    downloader.matcher = FakeMatcher()
    return downloader


def tmp_files(downloader):
    return os.listdir(downloader.tmp_dir)


def test_full_pipeline_produces_a_tagged_and_filed_mp3(dl):
    status, message = dl.process_track(TRACK)
    assert status == "downloaded", message
    target = build_track_path(dl.download_dir, TRACK)
    assert target.endswith(os.path.join("Daft Punk", "1997 - Homework", "07 - Da Funk.mp3"))
    tags = ID3(target)
    assert str(tags["TIT2"]) == "Da Funk"
    assert str(tags["TRCK"]) == "7/16"
    assert tags["TXXX:YOUTUBE_ID"].text == ["vid-ok"]
    assert tags.getall("APIC")[0].data == JPEG
    assert tags.getall("USLT")[0].text == "la la"
    folder = os.path.dirname(target)
    assert sorted(os.listdir(folder)) == ["07 - Da Funk.lrc", "07 - Da Funk.mp3", "cover.jpg"]
    assert tmp_files(dl) == []  # no leftover (webm source removed)


def test_existing_file_is_skipped_without_searching(dl, ydl):
    _, calls = ydl
    dl.process_track(TRACK)
    assert dl.process_track(TRACK)[0] == "skipped"
    assert calls == ["vid-ok"]


def test_no_match_is_a_clean_failure(dl):
    dl.matcher = FakeMatcher({"Da Funk": []})
    status, message = dl.process_track(TRACK)
    assert status == "failed" and "no reliable match" in message


def test_unavailable_video_falls_back_to_next_candidate(dl, ydl):
    script, calls = ydl
    dl.matcher = FakeMatcher({"Da Funk": ["vid-gone", "vid-ok"]})
    script["vid-gone"] = ["Video unavailable. This video has been removed"]
    assert dl.process_track(TRACK)[0] == "downloaded"
    assert calls == ["vid-gone", "vid-ok"]
    assert ID3(build_track_path(dl.download_dir, TRACK))["TXXX:YOUTUBE_ID"].text == ["vid-ok"]


def test_bot_check_pauses_then_retries_same_video(dl, ydl, monkeypatch):
    script, calls = ydl
    pauses = []
    monkeypatch.setattr(dl, "_trigger_cooldown", lambda: pauses.append(1))
    script["vid-ok"] = ["Sign in to confirm you're not a bot", "ok"]
    assert dl.process_track(TRACK)[0] == "downloaded"
    assert calls == ["vid-ok", "vid-ok"] and pauses == [1]


def test_transient_403_is_retried(dl, ydl):
    script, calls = ydl
    script["vid-ok"] = ["unable to download video data: HTTP Error 403: Forbidden", "ok"]
    assert dl.process_track(TRACK)[0] == "downloaded"
    assert calls == ["vid-ok", "vid-ok"]


def test_persistent_errors_end_as_failure_with_reason(dl, ydl):
    script, _ = ydl
    script["vid-ok"] = ["HTTP Error 403: Forbidden", "HTTP Error 403: Forbidden"]
    status, message = dl.process_track(TRACK)
    assert status == "failed" and "403" in message
    assert not os.path.exists(build_track_path(dl.download_dir, TRACK))
    assert tmp_files(dl) == []


def test_conversion_failure_is_reported(dl, ydl):
    script, _ = ydl
    script["vid-ok"] = ["noconvert"]
    status, message = dl.process_track(TRACK)
    assert status == "failed" and "FFmpeg" in message
    assert tmp_files(dl) == []


def test_tag_failure_never_leaves_an_untagged_file(dl, monkeypatch):
    monkeypatch.setattr(MetadataInjector, "inject_metadata", classmethod(lambda cls, *a, **k: False))
    status, message = dl.process_track(TRACK)
    assert status == "failed" and "tags" in message
    assert not os.path.exists(build_track_path(dl.download_dir, TRACK))
    assert tmp_files(dl) == []


def test_lyrics_and_cover_are_optional(dl, monkeypatch):
    monkeypatch.setattr(youtube_downloader, "fetch_lyrics", lambda t: None)
    monkeypatch.setattr(MetadataInjector, "fetch_cover", classmethod(lambda cls, t: None))
    assert dl.process_track(TRACK)[0] == "downloaded"
    target = build_track_path(dl.download_dir, TRACK)
    assert os.listdir(os.path.dirname(target)) == ["07 - Da Funk.mp3"]
    assert not ID3(target).getall("APIC")


def test_very_long_names_are_written(dl):
    long_track = track("L" * 200, album="A" * 200, album_artist="B" * 200)
    status, message = dl.process_track(long_track)
    assert status == "downloaded", message


def test_cancellation_stops_before_downloading(dl, ydl):
    _, calls = ydl
    dl._stop.set()
    assert dl.process_track(TRACK) == ("failed", "cancelled")
    assert calls == []


def test_stale_temporary_files_are_purged(tmp_path, ydl):
    tmp_dir = tmp_path / "downloads" / ".tmp"
    tmp_dir.mkdir(parents=True)
    old, recent = tmp_dir / "old.webm", tmp_dir / "recent.webm"
    old.write_bytes(b"x")
    recent.write_bytes(b"x")
    two_days_ago = time.time() - 2 * 86400
    os.utime(old, (two_days_ago, two_days_ago))
    YouTubeDownloader(output_dir=str(tmp_path / "downloads"))
    assert not old.exists() and recent.exists()


# ------------------------------------------------------------- Library runs

def write_library(folder, name, tracks):
    path = folder / name
    path.write_text(json.dumps(tracks, ensure_ascii=False), encoding="utf-8")
    return str(path)


def test_library_run_writes_then_clears_the_failed_list(dl, ydl, isolated_data_dir):
    script, _ = ydl
    isolated_data_dir.mkdir(exist_ok=True)
    tracks = [track("One"), track("Two", track_number=8), track("Three", track_number=9),
              track("One"),  # listed twice: downloaded once
              {"artist": "no title"}]  # invalid entry: ignored
    library = write_library(isolated_data_dir, "Daft Punk_mb_library.json", tracks)
    dl.matcher = FakeMatcher({"Two": ["vid-two"], "Three": []})
    script["vid-two"] = ["Video unavailable"]

    counts = dl.download_library(library)
    assert counts == {"downloaded": 1, "skipped": 1, "failed": 2}
    failed_path = isolated_data_dir / "failed_Daft Punk_mb_library.json"
    failed = json.loads(failed_path.read_text(encoding="utf-8"))
    assert sorted(t["title"] for t in failed) == ["Three", "Two"]
    assert all(t["_failure_reason"] for t in failed)

    # retry of the failed list: everything succeeds -> the failed list disappears
    dl.matcher = FakeMatcher()
    counts = dl.download_library("failed_Daft Punk_mb_library.json")  # bare name: found in the data folder
    assert counts == {"downloaded": 2, "skipped": 0, "failed": 0}
    assert not failed_path.exists()

    # a full rerun downloads nothing again
    assert dl.download_library(library) == {"downloaded": 0, "skipped": 4, "failed": 0}


def test_library_run_with_missing_or_broken_file(dl, isolated_data_dir):
    assert dl.download_library("does_not_exist.json") == {}
    isolated_data_dir.mkdir(exist_ok=True)
    broken = write_library(isolated_data_dir, "broken.json", [])
    with open(broken, "w") as f:
        f.write("[{")
    with pytest.raises(ValueError, match="not valid JSON"):
        dl.download_library(broken)


def test_unexpected_exception_in_a_track_does_not_stop_the_run(dl, isolated_data_dir, monkeypatch):
    isolated_data_dir.mkdir(exist_ok=True)
    library = write_library(isolated_data_dir, "lib.json", [track("Boom"), track("Fine", track_number=8)])
    original = dl.process_track

    def process(t):
        if t["title"] == "Boom":
            raise OSError("disk full")
        return original(t)

    monkeypatch.setattr(dl, "process_track", process)
    assert dl.download_library(library) == {"downloaded": 1, "skipped": 0, "failed": 1}
    failed = json.loads((isolated_data_dir / "failed_lib.json").read_text(encoding="utf-8"))
    assert "disk full" in failed[0]["_failure_reason"]


def test_retag_rewrites_existing_files_only(dl, isolated_data_dir):
    isolated_data_dir.mkdir(exist_ok=True)
    dl.process_track(TRACK)
    fixed = {**TRACK, "album": "Homework", "label": "Virgin"}
    library = write_library(isolated_data_dir, "lib.json", [fixed, track("Not downloaded", track_number=9)])
    assert dl.retag_library(library) == {"retagged": 1, "missing": 1}
    tags = ID3(build_track_path(dl.download_dir, TRACK))
    assert str(tags["TPUB"]) == "Virgin"
    assert tags["TXXX:YOUTUBE_ID"].text == ["vid-ok"]  # kept
