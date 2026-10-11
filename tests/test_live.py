"""Real-world checks against MusicBrainz, YouTube Music, LRCLIB and YouTube (no account, no cookies).

Skipped by default. Run them when something stops working (YouTube changes often):
    RUN_LIVE_TESTS=1 python -m pytest tests/test_live.py -v      (PowerShell: $env:RUN_LIVE_TESTS=1)
"""
import os

import pytest
from mutagen.id3 import ID3

from common import build_track_path
from lyrics import fetch_lyrics
from matcher import YouTubeMatcher
from musicbrainz_extractor import MusicBrainzExtractor
from youtube_downloader import YouTubeDownloader

pytestmark = pytest.mark.live

DAFT_PUNK = "056e4f3e-d505-4dad-8ec1-d04f521cbb56"


@pytest.fixture(scope="module")
def da_funk():
    """Da Funk (Homework), straight from MusicBrainz."""
    mb = MusicBrainzExtractor()
    groups = [g for g in mb.get_release_groups(DAFT_PUNK, types=["album"]) if g["title"] == "Homework"]
    assert groups, "Homework not found on MusicBrainz"
    release = mb.get_release(mb.choose_release(groups[0]["id"])["id"])
    tracks = [t for t in mb.tracks_from_release(release) if t["title"] == "Da Funk"]
    assert tracks, "Da Funk not found in Homework"
    return tracks[0]


def test_musicbrainz_artist_and_metadata(da_funk):
    assert MusicBrainzExtractor().resolve_artist("Daft Punk")["id"] == DAFT_PUNK
    assert da_funk["album_artist"] == "Daft Punk"
    assert da_funk["original_date"].startswith("1997")
    assert da_funk["duration_ms"] and da_funk["mb_recording_id"]


def test_youtube_music_finds_the_right_audio(da_funk):
    found = YouTubeMatcher().find(da_funk)
    assert found, "no YouTube Music match for Da Funk"
    assert found[0].score >= 0.85


def test_lrclib_answers(da_funk):
    fetch_lyrics(da_funk)  # instrumental: None is a valid answer, the point is that it does not raise


def test_real_download_conversion_and_tags(da_funk, tmp_path):
    downloader = YouTubeDownloader(output_dir=str(tmp_path), max_workers=1, rate_limit_delay=0)
    status, message = downloader.process_track(da_funk)
    assert status == "downloaded", message
    target = build_track_path(str(tmp_path), da_funk)
    assert os.path.getsize(target) > 1_000_000
    tags = ID3(target)
    assert str(tags["TIT2"]) == "Da Funk"
    assert tags.getall("APIC"), "no cover embedded"
