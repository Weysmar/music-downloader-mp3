"""Track lists from Spotify: liked songs, own playlists, artist discography.

Adapted to the February 2026 Web API restrictions for Development Mode apps:
no batch endpoints, playlist contents via /playlists/{id}/items and only for playlists the
user owns or collaborates on, no album label. Use `--enrich` (MusicBrainz) to complete metadata.
"""
import logging
import os
import re
import time
from collections import defaultdict
from typing import Any, Dict, Iterator, List, Optional

import spotipy  # type: ignore
from spotipy.oauth2 import SpotifyOAuth  # type: ignore

from common import PROJECT_DIR, Track, dedupe_tracks, library_output_path, normalize, sanitize_component, write_json  # type: ignore
from config import Config  # type: ignore

logger = logging.getLogger(__name__)

_EDITION_RE = re.compile(
    r"\s*[\(\[][^\)\]]*\b(remaster(ed)?|deluxe|edition|expanded|anniversary|bonus|reissue|version)\b[^\)\]]*[\)\]]"
    r"|\s-\s.*\b(remaster(ed)?|deluxe|edition|expanded|anniversary)\b.*$",
    re.I,
)
ALBUM_TYPE_PRIORITY = {"album": 0, "single": 1, "compilation": 2}


class SpotifyExtractor:
    def __init__(self) -> None:
        Config.validate_spotify()
        self.sp = spotipy.Spotify(
            auth_manager=SpotifyOAuth(
                client_id=Config.SPOTIPY_CLIENT_ID,
                client_secret=Config.SPOTIPY_CLIENT_SECRET,
                redirect_uri=Config.SPOTIPY_REDIRECT_URI,
                scope="user-library-read playlist-read-private playlist-read-collaborative",
                open_browser=True,
                cache_path=os.path.join(PROJECT_DIR, ".cache"),
            ),
            requests_timeout=20,
            retries=0,
            status_retries=0,
        )

    # ------------------------------------------------------------------ HTTP

    def _call(self, func, *args, **kwargs):
        """Calls the API, waiting on 429 (rate limit) and retrying transient 5xx errors."""
        server_errors = 0
        while True:
            try:
                return func(*args, **kwargs)
            except spotipy.exceptions.SpotifyException as e:
                if e.http_status == 429:
                    retry_after = int((e.headers or {}).get("Retry-After", 15))
                    logger.warning(f"Spotify rate limit: waiting {retry_after}s...")
                    time.sleep(retry_after + 1)
                    continue
                if e.http_status and e.http_status >= 500 and server_errors < 3:
                    server_errors += 1
                    time.sleep(2 ** server_errors)
                    continue
                raise

    def _paginate(self, page: Optional[Dict[str, Any]]) -> Iterator[Dict[str, Any]]:
        while page:
            for item in page.get("items") or []:
                if item:
                    yield item
            page = self._call(self.sp.next, page) if page.get("next") else None

    # ------------------------------------------------------------ Conversion

    @staticmethod
    def _cover(images: List[Dict[str, Any]]) -> Optional[str]:
        if not images:
            return None
        return max(images, key=lambda i: i.get("width") or 0).get("url")

    def _track_info(self, track: Dict[str, Any], album: Optional[Dict[str, Any]] = None) -> Optional[Track]:
        """Spotify track object (+ its album) -> track dict."""
        if not track or track.get("is_local") or not track.get("id") or track.get("type", "track") != "track":
            return None
        album = album or track.get("album") or {}
        artists = [a["name"] for a in track.get("artists") or [] if a.get("name")]
        album_artists = [a["name"] for a in album.get("artists") or [] if a.get("name")]
        album_type = (album.get("album_type") or "").lower()
        return {
            "id": track["id"],
            "spotify_id": track["id"],
            "source": "spotify",
            "title": track.get("name"),
            "artist": ", ".join(artists),
            "artists": artists,
            "album": album.get("name"),
            "album_artist": ", ".join(album_artists) or None,
            "track_number": track.get("track_number"),
            "track_total": album.get("total_tracks"),
            "disc_number": track.get("disc_number") or 1,
            "date": album.get("release_date"),
            "isrc": (track.get("external_ids") or {}).get("isrc"),
            "duration_ms": track.get("duration_ms"),
            "release_type": album_type,
            "compilation": album_type == "compilation",
            "cover_url": self._cover(album.get("images") or []),
            "mb_artist_ids": [],
        }

    # --------------------------------------------------------------- Library

    def fetch_saved_tracks(self) -> List[Track]:
        logger.info("Fetching liked songs...")
        page = self._call(self.sp.current_user_saved_tracks, limit=50)
        tracks = [t for t in (self._track_info(item.get("track")) for item in self._paginate(page)) if t]
        logger.info(f"{len(tracks)} liked songs.")
        return tracks

    def _playlist_entries(self, playlist_id: str) -> Iterator[Dict[str, Any]]:
        try:
            page = self._call(self.sp._get, f"playlists/{playlist_id}/items", limit=50, additional_types="track")
        except spotipy.exceptions.SpotifyException as e:
            if e.http_status != 404:
                raise
            page = self._call(self.sp.playlist_items, playlist_id, limit=100, additional_types=("track",))
        for entry in self._paginate(page):
            yield entry.get("item") or entry.get("track") or {}

    def fetch_user_playlists(self) -> List[Track]:
        logger.info("Fetching playlists...")
        me = self._call(self.sp.me)["id"]
        tracks: List[Track] = []
        for playlist in self._paginate(self._call(self.sp.current_user_playlists, limit=50)):
            name = playlist.get("name")
            owner = (playlist.get("owner") or {}).get("id")
            if owner != me and not playlist.get("collaborative"):
                logger.info(f"Skipping '{name}': Spotify no longer exposes playlists you don't own (add its songs to a playlist of yours).")
                continue
            try:
                found = [t for t in (self._track_info(item) for item in self._playlist_entries(playlist["id"])) if t]
                logger.info(f"Playlist '{name}': {len(found)} tracks")
                tracks.extend(found)
            except spotipy.exceptions.SpotifyException as e:
                logger.warning(f"Skipping playlist '{name}' (API error {e.http_status}).")
        logger.info(f"{len(tracks)} tracks in playlists.")
        return tracks

    def extract_library(self, output_file: str = "library.json") -> List[Track]:
        """Liked songs + own playlists, de-duplicated, saved to JSON."""
        output_file = library_output_path(output_file)
        tracks = dedupe_tracks(self.fetch_saved_tracks() + self.fetch_user_playlists(), title_dedup=False)
        logger.info(f"Total unique tracks: {len(tracks)}")
        write_json(output_file, tracks)
        logger.info(f"Library exported to {output_file}")
        return tracks

    # ----------------------------------------------------------- Discography

    def _find_artist(self, artist_name: str) -> Optional[Dict[str, Any]]:
        results = self._call(self.sp.search, q=artist_name, type="artist", limit=10)
        items = (results or {}).get("artists", {}).get("items", [])
        if not items:
            return None
        target = normalize(artist_name)
        return next((a for a in items if normalize(a["name"]) == target), items[0])

    def _artist_albums(self, artist_id: str, include_groups: str) -> List[Dict[str, Any]]:
        for limit in (50, 10):
            try:
                page = self._call(self.sp._get, f"artists/{artist_id}/albums", include_groups=include_groups, limit=limit)
                return list(self._paginate(page))
            except spotipy.exceptions.SpotifyException as e:
                if e.http_status != 400 or limit == 10:
                    raise
        return []

    @staticmethod
    def _choose_editions(albums: List[Dict[str, Any]], edition: str) -> List[Dict[str, Any]]:
        """Keeps one edition per album (Remastered / Deluxe / Anniversary... are the same album)."""
        groups: Dict[Any, List[Dict[str, Any]]] = defaultdict(list)
        for album in albums:
            key = (normalize(_EDITION_RE.sub("", album["name"])), album.get("album_type"))
            groups[key].append(album)

        chosen = []
        for editions in groups.values():
            if edition == "largest":
                chosen.append(max(editions, key=lambda a: (a.get("total_tracks") or 0, -int((a.get("release_date") or "9999")[:4]))))
            else:
                chosen.append(min(editions, key=lambda a: (a.get("release_date") or "9999", bool(_EDITION_RE.search(a["name"])))))
        chosen.sort(key=lambda a: (ALBUM_TYPE_PRIORITY.get(a.get("album_type"), 9), a.get("release_date") or "9999"))
        return chosen

    def extract_artist_discography(
        self,
        artist_name: str,
        include_compilations: bool = False,
        edition: str = "original",
        keep_duplicates: bool = False,
    ) -> Optional[str]:
        """Every album and single of an artist, saved to <Artist>_library.json. Returns the file path."""
        artist = self._find_artist(artist_name)
        if not artist:
            logger.error(f"Artist '{artist_name}' not found on Spotify.")
            return None
        artist_id, found_name = artist["id"], artist["name"]
        logger.info(f"Artist: {found_name} (Spotify ID {artist_id})")

        groups = "album,single" + (",compilation" if include_compilations else "")
        albums = self._artist_albums(artist_id, groups)
        chosen = self._choose_editions(albums, edition)
        logger.info(f"{len(albums)} releases found, {len(chosen)} after merging editions.")

        all_tracks: List[Track] = []
        from tqdm import tqdm  # type: ignore
        for album_ref in tqdm(chosen, desc="Albums", unit="album"):
            try:
                album = self._call(self.sp.album, album_ref["id"])
                album_tracks = list(self._paginate(album.get("tracks")))
            except spotipy.exceptions.SpotifyException as e:
                logger.warning(f"Failed to fetch '{album_ref['name']}': {e}")
                continue

            disc_sizes: Dict[int, int] = defaultdict(int)
            for t in album_tracks:
                disc_sizes[t.get("disc_number") or 1] += 1

            for t in album_tracks:
                if artist_id not in [a.get("id") for a in t.get("artists") or []]:
                    continue  # track of another artist on a split / various artists release
                info = self._track_info(t, album)
                if info:
                    info["track_total"] = disc_sizes[info["disc_number"]]
                    info["disc_total"] = len(disc_sizes)
                    all_tracks.append(info)

        tracks = all_tracks if keep_duplicates else dedupe_tracks(all_tracks)
        logger.info(f"{len(tracks)} tracks for {found_name} ({len(all_tracks) - len(tracks)} duplicates removed).")
        if not tracks:
            return None

        output_file = library_output_path(f"{sanitize_component(found_name)}_library.json")
        write_json(output_file, tracks)
        logger.info(f"Discography exported to {output_file}")
        return output_file


if __name__ == "__main__":
    from common import setup_logging  # type: ignore
    setup_logging()
    SpotifyExtractor().extract_library()
