"""Lyrics from LRCLIB (https://lrclib.net): free, no account, plain and synced (LRC) lyrics."""
import logging
from difflib import SequenceMatcher
from typing import Dict, Optional

from common import APP_NAME, APP_URL, APP_VERSION, Track, clean_title, http_session, normalize, primary_artist  # type: ignore

logger = logging.getLogger(__name__)

API_URL = "https://lrclib.net/api"
_session = http_session(f"{APP_NAME}/{APP_VERSION} ( {APP_URL} )")


def same_title(a: Optional[str], b: Optional[str]) -> bool:
    """Strict comparison: 'Plus rien' != 'Plus rien ne m'étonne', 'Song' != 'Song (Remix)'."""
    na, nb = normalize(clean_title(a)), normalize(clean_title(b))
    return bool(na and nb) and SequenceMatcher(None, na, nb).ratio() >= 0.9


def fetch_lyrics(track: Track) -> Optional[Dict[str, Optional[str]]]:
    """Returns {"plain": ..., "synced": ...} or None when nothing is found."""
    title, artist = track.get("title"), primary_artist(track)
    if not title or not artist:
        return None
    duration = round((track.get("duration_ms") or 0) / 1000)

    try:
        params = {"track_name": title, "artist_name": artist}
        if track.get("album"):
            params["album_name"] = track["album"]
        if duration:
            params["duration"] = duration
        response = _session.get(f"{API_URL}/get", params=params, timeout=15)
        data = response.json() if response.status_code == 200 else None

        if not data:
            response = _session.get(f"{API_URL}/search", params={"track_name": title, "artist_name": artist}, timeout=15)
            results = response.json() if response.ok else []
            candidates = [
                r for r in results
                if same_title(title, r.get("trackName"))
                and (not duration or abs((r.get("duration") or 0) - duration) <= 3)
            ]
            candidates.sort(key=lambda r: abs((r.get("duration") or 0) - duration))
            data = candidates[0] if candidates else None
    except Exception as e:
        logger.debug(f"LRCLIB error for {artist} - {title}: {e}")
        return None

    if not data or data.get("instrumental"):
        return None
    plain, synced = data.get("plainLyrics"), data.get("syncedLyrics")
    if not plain and not synced:
        return None
    return {"plain": plain, "synced": synced}
