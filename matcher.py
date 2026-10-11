"""Finds the right YouTube Music audio for a track (no Google account needed).

Strategy: ISRC search, then "artist title" in YouTube Music *songs* (official audio, no clip
intro), then videos as a last resort. Each result is scored on title, artist and duration;
live/cover/remix/sped-up... versions are penalized unless the track itself is one.
"""
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional

from ytmusicapi import YTMusic  # type: ignore

from common import Track, clean_title, normalize, primary_artist, strip_translation, text_similarity, track_artists  # type: ignore
from config import Config  # type: ignore

logger = logging.getLogger(__name__)

# Words that mean "another version of the song" when present on one side only
VERSION_WORDS = (
    "live", "remix", "cover", "karaoke", "instrumental", "acoustic", "acoustique", "unplugged",
    "nightcore", "reverb", "slowed", "sped up", "8d", "bass boosted", "reaction", "tutorial",
    "piano", "demo", "rehearsal", "orchestral", "symphonic", "medley", "mashup", "parody",
    "1 hour", "10 hours", "loop", "extended", "a cappella", "acapella",
)
_VERSION_RES = [(w, re.compile(rf"\b{re.escape(w)}\b")) for w in VERSION_WORDS]


@dataclass
class Candidate:
    video_id: str
    title: str
    artists: List[str] = field(default_factory=list)
    album: Optional[str] = None
    duration: Optional[int] = None  # seconds
    result_type: str = "song"
    score: float = 0.0
    details: str = ""

    @classmethod
    def from_result(cls, r: Dict[str, Any]) -> Optional["Candidate"]:
        if not r.get("videoId"):
            return None
        return cls(
            video_id=r["videoId"],
            title=r.get("title") or "",
            artists=[a.get("name", "") for a in r.get("artists") or []],
            album=(r.get("album") or {}).get("name"),
            duration=r.get("duration_seconds"),
            result_type=r.get("resultType") or "song",
        )


def _version_words(text: str) -> set:
    norm = normalize(text, strip_feat=False)
    return {w for w, rx in _VERSION_RES if rx.search(norm)}


def _core_parts(title: str) -> List[str]:
    """'Artist - Song (Live in X) [Remaster]' -> ['Artist', 'Song']: the title without its qualifiers."""
    without_brackets = re.sub(r"[\(\[][^\)\]]*[\)\]]", " ", strip_translation(title))
    return [p for p in (normalize(part) for part in re.split(r"\s[-–|]\s", without_brackets)) if p]


def core_title_similarity(src_title: str, cand_title: str, artists: List[str]) -> float:
    """Strict similarity of the main title (no containment bonus): 'Plus rien' != 'Plus rien ne m'étonne',
    'Aces High (Live...)' != 'Run To The Hills (Live...)'."""
    src_parts = _core_parts(src_title)
    if not src_parts:
        return 1.0
    src = src_parts[0]
    artist_words = {w for a in artists for w in normalize(a).split()}
    best = 0.0
    for part in _core_parts(cand_title):
        variants = {part, " ".join(w for w in part.split() if w not in artist_words)}
        for v in filter(None, variants):
            seq = SequenceMatcher(None, src, v).ratio()
            ts, tv = set(src.split()), set(v.split())
            common = len(ts & tv)
            f1 = 2 * common / (len(ts) + len(tv)) if common else 0.0
            best = max(best, seq, f1)
    return best


def artist_similarity(src_artists: List[str], cand_artists: List[str], cand_title: str) -> float:
    if not src_artists:
        return 0.5
    primary = normalize(src_artists[0])
    others = {normalize(a) for a in src_artists[1:]}
    best = 0.0
    for name in (normalize(a) for a in cand_artists):
        if not name:
            continue
        if name == primary:
            return 1.0
        if primary.replace(" ", "") and primary.replace(" ", "") in name.replace(" ", ""):
            best = max(best, 0.95)  # "Metallica - Topic", "MetallicaVEVO"
        if name in others:
            best = max(best, 0.9)
        best = max(best, SequenceMatcher(None, primary, name).ratio())
    if primary and re.search(rf"\b{re.escape(primary)}\b", normalize(cand_title)):
        best = max(best, 0.9)  # "Artist - Title" uploaded by another channel
    return best


def score_candidate(track: Track, cand: Candidate) -> float:
    """0..1 score of a YouTube result for the track. Also fills cand.details."""
    title = track.get("title") or ""
    t = max(text_similarity(title, cand.title), text_similarity(clean_title(title), clean_title(cand.title)) - 0.03)
    a = artist_similarity(track_artists(track), cand.artists, cand.title)

    # Hard gates: another song of the same artist, or the same song by someone else, is never accepted
    core = core_title_similarity(title, cand.title, track_artists(track) + cand.artists)
    if core < 0.75 or a < 0.6:
        cand.score, cand.details = 0.0, f"core title={core:.2f} artist={a:.2f}"
        return 0.0

    src_duration = (track.get("duration_ms") or 0) / 1000
    if src_duration and cand.duration:
        diff = abs(src_duration - cand.duration)
        if diff > max(30.0, 0.2 * src_duration):
            cand.score, cand.details = 0.0, f"duration {cand.duration}s vs {src_duration:.0f}s"
            return 0.0
        full, zero = 3.0, max(15.0, 0.08 * src_duration)
        d = 1.0 if diff <= full else max(0.0, 1 - (diff - full) / (zero - full))
        score = 0.45 * t + 0.25 * a + 0.30 * d
    else:
        d = None
        score = (0.45 * t + 0.25 * a) / 0.70 * 0.9  # no duration to check: cannot be fully trusted

    # Version mismatch (live vs studio, cover, remix, sped up...). The album name counts as
    # context: tracks of "Live After Death" may be titled without "live".
    src_words = _version_words(f"{title} {track.get('album') or ''}")
    cand_words = _version_words(f"{cand.title} {cand.album or ''}")
    mismatched = (cand_words - src_words) | (_version_words(title) - cand_words)
    penalty = min(0.6, 0.3 * len(mismatched))

    if cand.result_type == "song":
        score += 0.03
    if cand.album and track.get("album") and text_similarity(track["album"], cand.album) > 0.8:
        score += 0.03

    cand.score = max(0.0, min(1.0, score) - penalty)
    cand.details = (
        f"title={t:.2f} artist={a:.2f} duration={'n/a' if d is None else f'{d:.2f}'}"
        + (f" version≠{sorted(mismatched)}" if mismatched else "")
    )
    return cand.score


class YouTubeMatcher:
    def __init__(self, threshold: Optional[float] = None) -> None:
        self.threshold = Config.MATCH_THRESHOLD if threshold is None else threshold
        self._local = threading.local()

    def _yt(self) -> YTMusic:
        if not hasattr(self._local, "yt"):
            self._local.yt = YTMusic()  # anonymous: no account, no cookies
        return self._local.yt

    def _search(self, query: str, search_filter: str) -> List[Candidate]:
        for attempt in range(3):
            try:
                results = self._yt().search(query, filter=search_filter, limit=10)
                return [c for c in (Candidate.from_result(r) for r in results) if c]
            except Exception as e:
                logger.debug(f"YouTube Music search failed ({query!r}, {search_filter}): {e}")
                time.sleep(2 * (attempt + 1))
        return []

    def find(self, track: Track, max_candidates: int = 3) -> List[Candidate]:
        """Accepted candidates (score >= threshold), best first. Empty list = no reliable match."""
        artist = primary_artist(track)
        query = f"{artist} {clean_title(track.get('title'))}".strip()
        searches = []
        if track.get("isrc"):
            searches.append((track["isrc"], "songs"))
        searches += [(query, "songs"), (query, "videos")]

        seen: Dict[str, Candidate] = {}
        for search_query, search_filter in searches:
            for cand in self._search(search_query, search_filter):
                if cand.video_id in seen:
                    continue
                score_candidate(track, cand)
                seen[cand.video_id] = cand
            best = max(seen.values(), key=lambda c: c.score, default=None)
            if best and best.score >= 0.9:
                break  # confident enough, no need for more searches

        ranked = sorted(seen.values(), key=lambda c: c.score, reverse=True)
        for c in ranked[:3]:
            logger.debug(f"[{track.get('title')}] {c.score:.2f} {c.result_type} {c.video_id} '{c.title}' {c.artists} ({c.details})")
        if not ranked:
            return []
        floor = max(self.threshold, ranked[0].score - 0.15)  # fallbacks only if nearly as good as the best
        return [c for c in ranked if c.score >= floor][:max_candidates]
