"""Writes complete ID3 tags (same frame names as MusicBrainz Picard) and cover art into MP3 files."""
import logging
import re
import threading
from collections import OrderedDict
from typing import Any, Dict, Optional, Tuple, Union

from mutagen.id3 import (  # type: ignore
    APIC, ID3, TALB, TCMP, TCON, TDOR, TDRC, TIT2, TMED, TPE1, TPE2, TPOS, TPUB, TRCK, TSRC, TXXX, UFID, USLT,
    ID3NoHeaderError,
)

from common import APP_NAME, APP_VERSION, Track, http_session  # type: ignore
from config import Config  # type: ignore

logger = logging.getLogger(__name__)

Cover = Tuple[bytes, str]  # (image data, mime type)
Lyrics = Union[str, Dict[str, Optional[str]], None]

_LRC_TIMESTAMP = re.compile(r"^\s*\[\d+:\d+(?:[.:]\d+)?\]\s?", re.M)


def image_mime(data: bytes) -> Optional[str]:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    return None


def lyrics_text(lyrics: Lyrics) -> Optional[str]:
    if not lyrics:
        return None
    if isinstance(lyrics, str):
        return lyrics
    if lyrics.get("plain"):
        return lyrics["plain"]
    if lyrics.get("synced"):
        return _LRC_TIMESTAMP.sub("", lyrics["synced"]).strip()
    return None


class MetadataInjector:
    _covers: "OrderedDict[str, Optional[Cover]]" = OrderedDict()
    _lock = threading.Lock()
    _session = http_session(f"{APP_NAME}/{APP_VERSION}")

    # ----------------------------------------------------------------- Cover

    @classmethod
    def fetch_cover(cls, track: Track) -> Optional[Cover]:
        """Downloads the cover (cached per URL, so an album's cover is fetched once)."""
        for url in (track.get("cover_url"), track.get("cover_fallback_url")):
            if not url:
                continue
            with cls._lock:
                if url in cls._covers:
                    cls._covers.move_to_end(url)
                    cached = cls._covers[url]
                    if cached:
                        return cached
                    continue
            result: Optional[Cover] = None
            try:
                response = cls._session.get(url, timeout=30)
                if response.ok:
                    mime = image_mime(response.content)
                    if mime:
                        result = (response.content, mime)
                    else:
                        logger.debug(f"Unsupported image format for {url}")
            except Exception as e:
                logger.debug(f"Cover download failed ({url}): {e}")
            with cls._lock:
                cls._covers[url] = result
                while len(cls._covers) > 64:
                    cls._covers.popitem(last=False)
            if result:
                return result
        return None

    # ------------------------------------------------------------------ Tags

    @staticmethod
    def _add_text(tags: ID3, frame_cls, value: Any) -> None:
        if value in (None, "", []):
            return
        text = [str(v) for v in value] if isinstance(value, list) else [str(value)]
        tags.add(frame_cls(encoding=3, text=text))

    @staticmethod
    def _add_txxx(tags: ID3, desc: str, value: Any) -> None:
        if value in (None, "", []):
            return
        text = [str(v) for v in value] if isinstance(value, list) else [str(value)]
        tags.add(TXXX(encoding=3, desc=desc, text=text))

    @classmethod
    def build_tags(
        cls,
        track: Track,
        cover: Optional[Cover] = None,
        lyrics: Lyrics = None,
        source_id: Optional[str] = None,
    ) -> ID3:
        tags = ID3()
        cls._add_text(tags, TIT2, track.get("title"))
        cls._add_text(tags, TPE1, track.get("artist"))
        cls._add_text(tags, TPE2, track.get("album_artist") or track.get("artist"))
        cls._add_text(tags, TALB, track.get("album"))

        number, total = track.get("track_number"), track.get("track_total")
        if number:
            cls._add_text(tags, TRCK, f"{number}/{total}" if total else number)
        disc, disc_total = track.get("disc_number"), track.get("disc_total")
        if disc and (disc_total or 1) > 1:
            cls._add_text(tags, TPOS, f"{disc}/{disc_total}")

        # TDRC = original year (what players display), the edition's own date goes to RELEASEDATE
        cls._add_text(tags, TDRC, track.get("original_date") or track.get("date"))
        cls._add_text(tags, TDOR, track.get("original_date"))
        cls._add_txxx(tags, "RELEASEDATE", track.get("date"))
        cls._add_text(tags, TCON, track.get("genres") or [])
        cls._add_text(tags, TPUB, track.get("label"))
        cls._add_text(tags, TSRC, track.get("isrc"))
        cls._add_text(tags, TMED, track.get("media_format"))
        if track.get("compilation"):
            cls._add_text(tags, TCMP, "1")

        artists = track.get("artists") or []
        if len(artists) > 1:
            cls._add_txxx(tags, "ARTISTS", artists)
        cls._add_txxx(tags, "CATALOGNUMBER", track.get("catalog_number"))
        cls._add_txxx(tags, "BARCODE", track.get("barcode"))

        release_type = [track.get("release_type")] + (track.get("release_secondary_types") or [])
        cls._add_txxx(tags, "MusicBrainz Album Type", [t for t in release_type if t])
        cls._add_txxx(tags, "MusicBrainz Album Status", track.get("release_status"))
        cls._add_txxx(tags, "MusicBrainz Album Release Country", track.get("release_country"))
        cls._add_txxx(tags, "MusicBrainz Album Id", track.get("mb_release_id"))
        cls._add_txxx(tags, "MusicBrainz Release Group Id", track.get("mb_release_group_id"))
        cls._add_txxx(tags, "MusicBrainz Release Track Id", track.get("mb_track_id"))
        cls._add_txxx(tags, "MusicBrainz Artist Id", track.get("mb_artist_ids"))
        cls._add_txxx(tags, "MusicBrainz Album Artist Id", track.get("mb_album_artist_ids"))
        if track.get("mb_recording_id"):
            tags.add(UFID(owner="http://musicbrainz.org", data=track["mb_recording_id"].encode("ascii")))

        cls._add_txxx(tags, "SPOTIFY_ID", track.get("spotify_id"))
        cls._add_txxx(tags, "YOUTUBE_ID", source_id)

        if cover:
            data, mime = cover
            tags.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=data))

        text = lyrics_text(lyrics)
        if text:
            tags.add(USLT(encoding=3, lang="XXX", desc="", text=text))
        return tags

    @classmethod
    def inject_metadata(
        cls,
        file_path: str,
        track: Track,
        source_id: Optional[str] = None,
        lyrics: Lyrics = None,
        cover: Optional[Cover] = None,
    ) -> bool:
        """Replaces all tags of the file. Keeps the existing cover, lyrics and YouTube ID when
        no new value is given (useful when re-tagging)."""
        try:
            try:
                existing: Optional[ID3] = ID3(file_path)
            except ID3NoHeaderError:
                existing = None

            if cover is None:
                cover = cls.fetch_cover(track)
            if existing is not None:
                if cover is None:
                    old = existing.getall("APIC")
                    cover = (old[0].data, old[0].mime) if old else None
                if lyrics is None:
                    old_lyrics = existing.getall("USLT")
                    lyrics = old_lyrics[0].text if old_lyrics else None
                if source_id is None:
                    old_ids = existing.getall("TXXX:YOUTUBE_ID")
                    source_id = old_ids[0].text[0] if old_ids else None

            tags = cls.build_tags(track, cover, lyrics, source_id)
            tags.save(file_path, v2_version=Config.ID3_VERSION)
            logger.debug(f"Metadata OK: {track.get('artist')} - {track.get('title')}")
            return True
        except Exception as e:
            logger.error(f"Failed to write metadata for {file_path}: {e}")
            return False
