import os
import subprocess

import pytest
from mutagen.id3 import ID3

from common import find_ffmpeg
from metadata_injector import MetadataInjector

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32

TRACK = {
    "title": "Da Funk", "artist": "Daft Punk", "artists": ["Daft Punk"], "album": "Homework",
    "album_artist": "Daft Punk", "track_number": 7, "track_total": 16, "disc_number": 1, "disc_total": 2,
    "date": "1997-01-20", "original_date": "1997-01-20", "genres": ["French House", "Electronic"],
    "label": "Virgin", "isrc": "GBDUW9600006", "catalog_number": "7243 8 42609 4 1", "barcode": "724384260941",
    "release_type": "album", "release_status": "official", "release_country": "FR",
    "mb_recording_id": "11111111-1111-1111-1111-111111111111", "mb_release_id": "22222222-2222-2222-2222-222222222222",
    "mb_release_group_id": "33333333-3333-3333-3333-333333333333", "mb_artist_ids": ["056e4f3e-d505-4dad-8ec1-d04f521cbb56"],
    "mb_album_artist_ids": ["056e4f3e-d505-4dad-8ec1-d04f521cbb56"],
}


@pytest.fixture
def mp3(tmp_path):
    folder = find_ffmpeg()
    if not folder:
        pytest.skip("ffmpeg not available")
    exe = os.path.join(folder, "ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    path = tmp_path / "test.mp3"
    subprocess.run([exe, "-v", "error", "-f", "lavfi", "-i", "sine=d=1", "-q:a", "0", str(path)], check=True)
    return str(path)


def test_full_tags_written(mp3, monkeypatch):
    monkeypatch.setattr(MetadataInjector, "fetch_cover", classmethod(lambda cls, t: None))
    assert MetadataInjector.inject_metadata(mp3, TRACK, source_id="abc123", lyrics={"plain": "la la", "synced": None}, cover=(PNG, "image/png"))
    tags = ID3(mp3)
    assert tags.version[:2] == (2, 3)
    assert str(tags["TIT2"]) == "Da Funk"
    assert str(tags["TPE2"]) == "Daft Punk"
    assert str(tags["TRCK"]) == "7/16"
    assert str(tags["TPOS"]) == "1/2"
    assert str(tags["TDRC"]).startswith("1997")
    assert str(tags["TPUB"]) == "Virgin"
    assert str(tags["TSRC"]) == "GBDUW9600006"
    assert tags["TCON"].text == ["French House/Electronic"]  # ID3v2.3 has no multi-value: joined like Picard
    assert tags["TXXX:MusicBrainz Album Id"].text == ["22222222-2222-2222-2222-222222222222"]
    assert tags["TXXX:YOUTUBE_ID"].text == ["abc123"]
    assert tags["UFID:http://musicbrainz.org"].data == b"11111111-1111-1111-1111-111111111111"
    assert tags.getall("APIC")[0].mime == "image/png"
    assert tags.getall("USLT")[0].text == "la la"


def test_retag_keeps_cover_lyrics_and_source(mp3, monkeypatch):
    monkeypatch.setattr(MetadataInjector, "fetch_cover", classmethod(lambda cls, t: None))
    MetadataInjector.inject_metadata(mp3, TRACK, source_id="abc123", lyrics="words", cover=(PNG, "image/png"))
    MetadataInjector.inject_metadata(mp3, {**TRACK, "title": "Da Funk (fixed)"})
    tags = ID3(mp3)
    assert str(tags["TIT2"]) == "Da Funk (fixed)"
    assert tags["TXXX:YOUTUBE_ID"].text == ["abc123"]
    assert tags.getall("USLT")[0].text == "words"
    assert tags.getall("APIC")[0].data == PNG
