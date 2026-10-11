"""Discography and metadata extraction from MusicBrainz (free, no API key).

MusicBrainz is the metadata reference of the project: track/disc numbers, dates, album artist,
label, catalog number, barcode, ISRC, genres and MusicBrainz IDs (the same tags as MusicBrainz Picard).
"""
import logging
import re
import threading
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from common import APP_NAME, APP_URL, APP_VERSION, Track, dedupe_tracks, http_session, library_output_path, normalize, sanitize_component, text_similarity, write_json  # type: ignore
from config import Config  # type: ignore

logger = logging.getLogger(__name__)

VARIOUS_ARTISTS_ID = "89ad4ac3-39f7-470e-963a-56509c546377"
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)

DEFAULT_TYPES = ("album", "ep", "single")
PRIMARY_TYPES = ("album", "ep", "single", "broadcast", "other")
# Secondary types excluded by default (they mostly duplicate studio tracks)
EXCLUDABLE_SECONDARY = (
    "compilation", "live", "remix", "dj-mix", "mixtape/street", "demo",
    "interview", "spokenword", "audiobook", "audio drama", "field recording",
)
TYPE_PRIORITY = {"album": 0, "ep": 1, "single": 2}
VIDEO_FORMATS = {"dvd", "dvd-video", "dvd-audio", "blu-ray", "hd-dvd", "vhs", "vcd", "svcd", "umd", "laserdisc", "videotape"}
FORMAT_PRIORITY = {"digital media": 0, "cd": 1, "enhanced cd": 1, "sacd": 2, "vinyl": 3, '12" vinyl': 3, "cassette": 4}


class MusicBrainzExtractor:
    BASE_URL = "https://musicbrainz.org/ws/2"
    MIN_INTERVAL = 1.1  # MusicBrainz policy: max 1 request per second

    def __init__(self) -> None:
        contact = Config.MUSICBRAINZ_CONTACT or APP_URL
        self.session = http_session(f"{APP_NAME}/{APP_VERSION} ( {contact} )")
        self._lock = threading.Lock()
        self._last_request = 0.0
        self._release_cache: Dict[str, Dict[str, Any]] = {}

    # ------------------------------------------------------------------ HTTP

    def _get(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """Rate-limited GET with retries on 503 (server busy) and network errors."""
        params = dict(params or {})
        params["fmt"] = "json"
        url = f"{self.BASE_URL}/{endpoint}"
        for attempt in range(6):
            with self._lock:
                wait = self.MIN_INTERVAL - (time.monotonic() - self._last_request)
                if wait > 0:
                    time.sleep(wait)
                self._last_request = time.monotonic()
            try:
                response = self.session.get(url, params=params, timeout=30)
            except Exception as e:
                logger.debug(f"MusicBrainz network error ({endpoint}): {e}")
                time.sleep(2 ** attempt)
                continue
            if response.status_code == 404:
                return None
            if response.status_code in (429, 503):
                time.sleep(2 ** attempt)
                continue
            if not response.ok:
                logger.error(f"MusicBrainz API error {response.status_code} on {endpoint}: {response.text[:200]}")
                return None
            try:
                return response.json()
            except ValueError:  # truncated answer or HTML error page from a proxy
                logger.debug(f"MusicBrainz returned invalid JSON ({endpoint}), retrying")
                time.sleep(2 ** attempt)
        logger.error(f"MusicBrainz API unavailable for {endpoint} after several retries.")
        return None

    def _browse(self, entity: str, key: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Fetches every page of a browse request."""
        items: List[Dict[str, Any]] = []
        offset = 0
        while True:
            data = self._get(entity, {**params, "limit": 100, "offset": offset})
            if not data:
                if offset:
                    logger.warning(f"MusicBrainz: only the first {offset} {key} could be fetched, the list may be incomplete.")
                break
            page = data.get(key) or []
            items.extend(page)
            offset += len(page)
            if not page or offset >= data.get(f"{entity}-count", 0):
                break
        return items

    # ---------------------------------------------------------------- Artist

    def resolve_artist(self, name_or_mbid: str) -> Optional[Dict[str, Any]]:
        """Returns the artist matching a name (best exact match) or a MusicBrainz ID."""
        if UUID_RE.match(name_or_mbid.strip()):
            artist = self._get(f"artist/{name_or_mbid.strip()}")
            if artist:
                logger.info(f"Artist: {artist['name']} ({artist.get('disambiguation') or 'no description'})")
            return artist

        escaped = name_or_mbid.replace("\\", "\\\\").replace('"', '\\"')
        data = self._get("artist", {"query": f'artist:"{escaped}"', "limit": 10})
        artists = (data or {}).get("artists") or []
        if not artists:
            return None

        target = normalize(name_or_mbid)
        exact = [a for a in artists if normalize(a.get("name")) == target
                 or any(normalize(al.get("name")) == target for al in a.get("aliases", []))]
        candidates = exact or artists
        chosen = max(candidates, key=lambda a: int(a.get("score", 0)))

        logger.info(f"Artist: {chosen['name']} ({chosen.get('disambiguation') or chosen.get('country') or 'no description'}) — MBID {chosen['id']}")
        others = [a for a in candidates if a["id"] != chosen["id"]]
        if others:
            logger.warning("Other artists with a similar name (use --mbid <ID> to pick one):")
            for a in others[:5]:
                logger.warning(f"   {a['id']}  {a['name']} ({a.get('disambiguation') or a.get('country') or '?'})")
        return chosen

    # Backwards compatibility
    def find_artist_id(self, artist_name: str) -> Optional[str]:
        artist = self.resolve_artist(artist_name)
        return artist["id"] if artist else None

    # --------------------------------------------------------- Release groups

    def get_release_groups(
        self,
        artist_id: str,
        types: Sequence[str] = DEFAULT_TYPES,
        include_secondary: Iterable[str] = (),
    ) -> List[Dict[str, Any]]:
        """Albums / EPs / singles of the artist, albums first then chronological."""
        include = {s.lower() for s in include_secondary}
        excluded = {s for s in EXCLUDABLE_SECONDARY if s not in include}

        groups = self._browse(
            "release-group", "release-groups",
            {"artist": artist_id, "type": "|".join(types), "inc": "genres"},
        )
        kept, skipped = [], 0
        for rg in groups:
            secondary = {s.lower() for s in rg.get("secondary-types") or []}
            if secondary & excluded:
                skipped += 1
                continue
            kept.append(rg)

        kept.sort(key=lambda rg: (
            TYPE_PRIORITY.get((rg.get("primary-type") or "").lower(), 9),
            rg.get("first-release-date") or "9999",
            rg.get("title", ""),
        ))
        logger.info(f"{len(kept)} release groups kept ({skipped} live/compilation/remix/... excluded).")
        return kept

    def choose_release(self, release_group_id: str, edition: str = "original") -> Optional[Dict[str, Any]]:
        """Picks the canonical edition of a release group.

        edition="original": earliest official release (prefers one with cover art, CD/digital, preferred country)
        edition="largest":  the official release with the most tracks (deluxe editions, bonus tracks)
        """
        releases = self._browse("release", "releases", {"release-group": release_group_id, "status": "official", "inc": "media"})
        if not releases:
            # e.g. promo-only singles; bootlegs and pseudo-releases (translations) are never used
            releases = [
                r for r in self._browse("release", "releases", {"release-group": release_group_id, "inc": "media"})
                if (r.get("status") or "").lower() not in ("bootleg", "pseudo-release")
            ]
        if not releases:
            return None

        countries = Config.PREFERRED_COUNTRIES

        def quality_key(r: Dict[str, Any]) -> Tuple:
            media = r.get("media") or []
            formats = [(m.get("format") or "").lower() for m in media]
            format_rank = min((FORMAT_PRIORITY.get(f, 5) for f in formats), default=5)
            country = (r.get("country") or "").upper()
            country_rank = countries.index(country) if country in countries else len(countries)
            no_cover = not (r.get("cover-art-archive") or {}).get("front")
            return (no_cover, bool(r.get("disambiguation")), format_rank, country_rank, r.get("date") or "9999")

        def track_count(r: Dict[str, Any]) -> int:
            return sum(m.get("track-count", 0) for m in r.get("media") or [])

        if edition == "largest":
            return min(releases, key=lambda r: (-track_count(r), *quality_key(r)))
        return min(releases, key=lambda r: ((r.get("date") or "9999")[:4], *quality_key(r)))

    def get_release(self, release_id: str) -> Optional[Dict[str, Any]]:
        if release_id not in self._release_cache:
            data = self._get(
                f"release/{release_id}",
                {"inc": "recordings+artist-credits+labels+isrcs+genres+release-groups+media"},
            )
            if not data:
                return None
            self._release_cache[release_id] = data
        return self._release_cache[release_id]

    # ------------------------------------------------------------ Conversion

    @staticmethod
    def _credit(credits: List[Dict[str, Any]]) -> Tuple[str, List[str], List[str]]:
        """Artist credit -> ('A feat. B', ['A', 'B'], [mbid_a, mbid_b])."""
        display, names, ids = "", [], []
        for c in credits or []:
            name = c.get("name") or (c.get("artist") or {}).get("name") or ""
            display += name + (c.get("joinphrase") or "")
            names.append(name)
            if (c.get("artist") or {}).get("id"):
                ids.append(c["artist"]["id"])
        return display.strip(), names, ids

    @staticmethod
    def _genres(*sources: Optional[List[Dict[str, Any]]], max_count: int = 3) -> List[str]:
        for source in sources:
            if source:
                ranked = sorted(source, key=lambda g: -int(g.get("count", 0)))
                return [g["name"].title() for g in ranked[:max_count]]
        return []

    def _cover_urls(self, release: Dict[str, Any], rg_id: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
        size = str(Config.COVER_SIZE).lower()
        suffix = "front" if size == "full" else f"front-{size}"
        release_url = f"https://coverartarchive.org/release/{release['id']}/{suffix}"
        rg_url = f"https://coverartarchive.org/release-group/{rg_id}/{suffix}" if rg_id else None
        if (release.get("cover-art-archive") or {}).get("front"):
            return release_url, rg_url
        return rg_url, None

    def tracks_from_release(self, release: Dict[str, Any]) -> List[Track]:
        """Converts a full MusicBrainz release into track dicts with every available tag."""
        rg = release.get("release-group") or {}
        album_artist, _, album_artist_ids = self._credit(release.get("artist-credit") or [])
        label_info = release.get("label-info") or []
        labels = list(dict.fromkeys((li.get("label") or {}).get("name") for li in label_info if li.get("label")))
        catalog_numbers = list(dict.fromkeys(li.get("catalog-number") for li in label_info if li.get("catalog-number")))
        primary_type = (rg.get("primary-type") or "").lower()
        secondary = [s.lower() for s in rg.get("secondary-types") or []]
        album_genres = self._genres(rg.get("genres"), release.get("genres"))
        cover_url, cover_fallback = self._cover_urls(release, rg.get("id"))
        media = release.get("media") or []

        tracks: List[Track] = []
        for medium in media:
            if (medium.get("format") or "").lower() in VIDEO_FORMATS:
                continue  # DVD / Blu-ray of a deluxe edition: concert films, clips
            for t in medium.get("tracks") or []:
                rec = t.get("recording") or {}
                if rec.get("video"):
                    continue
                artist, artists, artist_ids = self._credit(t.get("artist-credit") or rec.get("artist-credit") or [])
                isrcs = rec.get("isrcs") or []
                tracks.append({
                    "id": f"mb-{rec.get('id')}",
                    "source": "musicbrainz",
                    "title": t.get("title") or rec.get("title"),
                    "artist": artist or album_artist,
                    "artists": artists or [album_artist],
                    "album": release.get("title"),
                    "album_artist": album_artist,
                    "track_number": t.get("position"),
                    "track_total": medium.get("track-count"),
                    "disc_number": medium.get("position", 1),
                    "disc_total": len(media),
                    "date": release.get("date"),
                    "original_date": rg.get("first-release-date") or release.get("date"),
                    "genres": self._genres(rec.get("genres")) if not album_genres else album_genres,
                    "label": "; ".join(labels) or None,
                    "catalog_number": "; ".join(catalog_numbers) or None,
                    "barcode": release.get("barcode") or None,
                    "isrc": isrcs[0] if isrcs else None,
                    "duration_ms": t.get("length") or rec.get("length"),
                    "release_type": primary_type,
                    "release_secondary_types": secondary,
                    "release_status": (release.get("status") or "").lower() or None,
                    "release_country": release.get("country"),
                    "media_format": medium.get("format"),
                    "compilation": "compilation" in secondary or VARIOUS_ARTISTS_ID in album_artist_ids,
                    "cover_url": cover_url,
                    "cover_fallback_url": cover_fallback,
                    "mb_recording_id": rec.get("id"),
                    "mb_track_id": t.get("id"),
                    "mb_release_id": release.get("id"),
                    "mb_release_group_id": rg.get("id"),
                    "mb_artist_ids": artist_ids or album_artist_ids,
                    "mb_album_artist_ids": album_artist_ids,
                })
        return tracks

    # ---------------------------------------------------------- Discography

    def get_artist_tracks(
        self,
        artist_id: str,
        artist_name: str = "",
        types: Sequence[str] = DEFAULT_TYPES,
        include_secondary: Iterable[str] = (),
        edition: str = "original",
        limit: int = 0,
        keep_duplicates: bool = False,
    ) -> List[Track]:
        release_groups = self.get_release_groups(artist_id, types, include_secondary)
        all_tracks: List[Track] = []

        from tqdm import tqdm  # type: ignore
        for rg in tqdm(release_groups, desc="Releases", unit="release"):
            release_ref = self.choose_release(rg["id"], edition)
            if not release_ref:
                logger.warning(f"No release found for '{rg.get('title')}', skipped.")
                continue
            release = self.get_release(release_ref["id"])
            if not release:
                continue
            # Split singles / various artists releases also hold songs of other artists: keep the artist's own
            all_tracks.extend(t for t in self.tracks_from_release(release) if artist_id in t["mb_artist_ids"])
            if limit and len(dedupe_tracks(all_tracks, not keep_duplicates)) >= limit:
                logger.warning(f"Limit of {limit} tracks reached, stopping extraction.")
                break

        tracks = all_tracks if keep_duplicates else dedupe_tracks(all_tracks)
        if limit:
            tracks = tracks[:limit]
        logger.info(f"{len(tracks)} tracks for {artist_name or artist_id} ({len(all_tracks) - len(tracks)} duplicates removed).")
        return tracks

    def extract_discography(
        self,
        artist: str,
        output_file: Optional[str] = None,
        types: Sequence[str] = DEFAULT_TYPES,
        include_secondary: Iterable[str] = (),
        edition: str = "original",
        limit: int = 0,
        keep_duplicates: bool = False,
    ) -> Optional[str]:
        """Extracts an artist's discography (name or MBID) to a JSON library file."""
        artist_data = self.resolve_artist(artist)
        if not artist_data:
            logger.error(f"Artist '{artist}' not found on MusicBrainz.")
            return None

        tracks = self.get_artist_tracks(
            artist_data["id"], artist_data["name"], types, include_secondary, edition, limit, keep_duplicates
        )
        if not tracks:
            logger.error("No tracks found.")
            return None

        output_file = library_output_path(output_file or f"{sanitize_component(artist_data['name'])}_mb_library.json")
        write_json(output_file, tracks)
        logger.info(f"Discography (MusicBrainz) exported to {output_file}")
        return output_file

    # ------------------------------------------------------------ Enrichment

    def enrich_track(self, track: Track) -> Track:
        """Completes a track (e.g. from Spotify) with MusicBrainz metadata, using its ISRC."""
        if track.get("mb_recording_id") or not track.get("isrc"):
            return track
        data = self._get(f"isrc/{track['isrc']}")
        recordings = (data or {}).get("recordings") or []
        if not recordings:
            return track

        duration = track.get("duration_ms") or 0
        recording = max(recordings, key=lambda r: (
            text_similarity(track.get("title"), r.get("title")),
            -abs((r.get("length") or 0) - duration),
        ))
        rec = self._get(f"recording/{recording['id']}", {"inc": "releases+release-groups+media"})
        releases = (rec or {}).get("releases") or []
        if not releases:
            return track

        album_year = str(track.get("date") or "")[:4]

        def release_key(r: Dict[str, Any]) -> Tuple:
            return (
                -text_similarity(track.get("album"), r.get("title")),
                (r.get("status") or "").lower() != "official",
                (r.get("date") or "")[:4] != album_year,
                not (r.get("cover-art-archive") or {}).get("front"),
                r.get("date") or "9999",
            )

        best = min(releases, key=release_key)
        release = self.get_release(best["id"])
        if not release:
            return track
        match = next((t for t in self.tracks_from_release(release) if t["mb_recording_id"] == recording["id"]), None)
        if not match:
            return track

        enriched = dict(track)
        for key, value in match.items():
            if key in ("id", "source"):
                continue
            if key in ("cover_url", "cover_fallback_url") and track.get("cover_url"):
                # MusicBrainz cover first, the original one (e.g. Spotify 640px) as fallback
                if key == "cover_url" and value:
                    enriched["cover_url"], enriched["cover_fallback_url"] = value, track["cover_url"]
                continue
            if value not in (None, "", []):
                enriched[key] = value
        return enriched

    def enrich_tracks(self, tracks: List[Track]) -> List[Track]:
        from tqdm import tqdm  # type: ignore
        todo = sum(1 for t in tracks if t.get("isrc") and not t.get("mb_recording_id"))
        logger.info(f"Enriching {todo} tracks with MusicBrainz (~{todo * 3 // 60 + 1} min)...")
        result, enriched_count = [], 0
        for track in tqdm(tracks, desc="MusicBrainz", unit="track"):
            try:
                new = self.enrich_track(track)
            except Exception as e:
                logger.debug(f"Enrichment failed for {track.get('title')}: {e}")
                new = track
            enriched_count += bool(new.get("mb_recording_id") and not track.get("mb_recording_id"))
            result.append(new)
        logger.info(f"{enriched_count}/{todo} tracks enriched with MusicBrainz metadata.")
        return result


if __name__ == "__main__":
    from common import setup_logging  # type: ignore
    setup_logging()
    MusicBrainzExtractor().extract_discography("Orelsan")
