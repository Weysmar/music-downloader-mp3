"""Shared helpers: text normalization, safe file paths, de-duplication, HTTP session, FFmpeg lookup, data folder."""
import json
import logging
import logging.handlers
import os
import re
import shutil
import sys
import unicodedata
from difflib import SequenceMatcher
from typing import Any, Dict, Iterable, List, Optional

import requests  # type: ignore
from requests.adapters import HTTPAdapter  # type: ignore
from urllib3.util.retry import Retry  # type: ignore

from config import CONFIG_WARNINGS, Config  # type: ignore

logger = logging.getLogger(__name__)

APP_NAME = "music-downloader-mp3"
APP_URL = "https://github.com/Weysmar/music-downloader-mp3"
APP_VERSION = "2.0"
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))

Track = Dict[str, Any]


# ---------------------------------------------------------------------------
# Text normalization
# ---------------------------------------------------------------------------

_FEAT_PAREN = re.compile(r"[\(\[]\s*(feat\.?|ft\.?|featuring|with|avec)\s[^\)\]]*[\)\]]", re.I)
_FEAT_TAIL = re.compile(r"\s(feat\.?|ft\.?|featuring)\s.*$", re.I)

# Qualifiers that do not change the song itself (used for de-duplication and search queries)
_NOISE_WORDS = r"(remaster(ed)?|radio edit|single version|album version|edit|mono|stereo|explicit|clean|version originale|original mix)"
_NOISE_PAREN = re.compile(r"[\(\[][^\)\]]*\b" + _NOISE_WORDS + r"\b[^\)\]]*[\)\]]", re.I)
_NOISE_DASH = re.compile(r"\s-\s[^-]*\b" + _NOISE_WORDS + r"\b.*$", re.I)
# Translated title appended by MusicBrainz on Asian/Russian releases: "Nothin’ = ナッシン" -> "Nothin’"
_TRANSLATION = re.compile(r"\s=\s(?=[^=]*[Ѐ-ӿ֐-ۿ฀-๿぀-ヿ㐀-鿿가-힯]).*$")


def strip_accents(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c))


def normalize(text: Optional[str], strip_feat: bool = True) -> str:
    """Lowercase, accent-free, punctuation-free version of a string, for comparisons."""
    if not text:
        return ""
    s = text
    if strip_feat:
        s = _FEAT_PAREN.sub(" ", s)
        s = _FEAT_TAIL.sub(" ", s)
    s = strip_accents(s).lower().replace("&", " and ").replace("$", "s")
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def strip_translation(title: Optional[str]) -> str:
    return _TRANSLATION.sub("", title or "")


def clean_title(title: Optional[str]) -> str:
    """Removes featurings and remaster/edit qualifiers: 'Aces High - 2015 Remaster' -> 'Aces High'."""
    if not title:
        return ""
    s = _FEAT_PAREN.sub(" ", strip_translation(title))
    s = _FEAT_TAIL.sub(" ", s)
    s = _NOISE_PAREN.sub(" ", s)
    s = _NOISE_DASH.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip(" -")


def text_similarity(a: Optional[str], b: Optional[str]) -> float:
    """0..1 similarity, tolerant to extra words in b (e.g. '(Official Video)')."""
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    seq = SequenceMatcher(None, na, nb).ratio()
    ta, tb = set(na.split()), set(nb.split())
    common = len(ta & tb)
    recall = common / len(ta)
    precision = common / len(tb)
    f1 = 2 * recall * precision / (recall + precision) if common else 0.0
    contained = 0.92 if recall == 1.0 else 0.0
    return max(seq, f1, contained)


# ---------------------------------------------------------------------------
# Track helpers
# ---------------------------------------------------------------------------

def track_artists(track: Track) -> List[str]:
    artists = track.get("artists")
    if isinstance(artists, list) and artists:
        return [a for a in artists if a]
    artist = track.get("artist") or ""
    return [a.strip() for a in artist.split(",") if a.strip()]


def primary_artist(track: Track) -> str:
    artists = track_artists(track)
    return artists[0] if artists else ""


def as_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    """7, "7", "07", "7/12" -> 7; anything else (None, "A1", "") -> default."""
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    match = re.match(r"^\s*(\d+)", str(value or ""))
    return int(match.group(1)) if match else default


def track_year(track: Track) -> Optional[str]:
    for key in ("original_date", "date"):
        value = track.get(key)
        if value and re.match(r"^\d{4}", str(value)):
            return str(value)[:4]
    return None


def dedupe_tracks(tracks: Iterable[Track], title_dedup: bool = True) -> List[Track]:
    """Removes duplicates while keeping the first occurrence (callers put albums first).

    - same recording / Spotify id / ISRC  -> duplicate
    - with title_dedup: a single, EP or compilation track whose title already appeared (same
      primary artist) is dropped, so the album version wins over the single version.
    """
    out: List[Track] = []
    seen_ids, seen_isrc, seen_titles = set(), set(), set()
    for t in tracks:
        tid = t.get("mb_recording_id") or t.get("id")
        if tid and tid in seen_ids:
            continue
        isrc = (t.get("isrc") or "").upper()
        if isrc and isrc in seen_isrc:
            continue
        key = (normalize(primary_artist(t)), normalize(clean_title(t.get("title"))))
        secondary_release = (t.get("release_type") or "").lower() in ("single", "ep", "compilation") or t.get("compilation")
        if title_dedup and secondary_release and key in seen_titles:
            continue
        out.append(t)
        if tid:
            seen_ids.add(tid)
        if isrc:
            seen_isrc.add(isrc)
        seen_titles.add(key)
    return out


# ---------------------------------------------------------------------------
# File system
# ---------------------------------------------------------------------------

_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def sanitize_component(name: Optional[str], max_len: int = 120, fallback: str = "Unknown") -> str:
    """Makes a string safe as a single Windows/Linux path component."""
    if not name:
        return fallback
    s = str(name)
    s = re.sub(r"\s*:\s*", " - ", s)
    s = re.sub(r"[\\/|]", "-", s)
    s = s.replace('"', "'")
    s = re.sub(r"[*?<>\x00-\x1f]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) > max_len:
        s = s[:max_len]
    s = s.rstrip(". ").strip()
    if not s:
        return fallback
    if s.split(".")[0].upper() in _RESERVED_NAMES:
        s = "_" + s
    return s


def build_track_path(base_dir: str, track: Track) -> str:
    """downloads/<Album Artist>/<Year> - <Album>/<[disc-]NN> - <Title>.mp3"""
    album_artist = track.get("album_artist") or primary_artist(track) or "Unknown Artist"
    album = track.get("album") or "Unknown Album"
    year = track_year(track)
    album_dir = f"{year} - {album}" if year else album

    title = track.get("title") or "Unknown Title"
    number = as_int(track.get("track_number"))
    disc = as_int(track.get("disc_number")) or 1
    disc_total = as_int(track.get("disc_total")) or 1
    if number:
        prefix = f"{disc}-{number:02d}" if (disc_total > 1 or disc > 1) else f"{number:02d}"
        filename = f"{prefix} - {title}"
    else:
        filename = title

    return os.path.join(
        base_dir,
        sanitize_component(album_artist, 80),
        sanitize_component(album_dir, 100),
        sanitize_component(filename, 140) + ".mp3",
    )


def long_path(path: str) -> str:
    """Enables paths longer than 260 chars on Windows."""
    path = os.path.abspath(path)
    if sys.platform == "win32" and len(path) >= 240 and not path.startswith("\\\\?\\"):
        return "\\\\?\\" + path
    return path


def find_ffmpeg() -> Optional[str]:
    """Returns the directory containing ffmpeg AND ffprobe (project folder first, then PATH).
    yt-dlp needs both to convert to MP3."""
    ext = ".exe" if sys.platform == "win32" else ""
    for folder in (PROJECT_DIR, os.getcwd()):
        if all(os.path.exists(os.path.join(folder, tool + ext)) for tool in ("ffmpeg", "ffprobe")):
            return folder
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if ffmpeg and ffprobe and os.path.dirname(ffmpeg) == os.path.dirname(ffprobe):
        return os.path.dirname(ffmpeg)
    return None


# ---------------------------------------------------------------------------
# Data folder (library JSON files, failed_*.json, log)
# ---------------------------------------------------------------------------

def data_dir() -> str:
    """Folder holding everything the downloads produce besides the MP3 files (created if needed)."""
    folder = Config.DATA_DIR if os.path.isabs(Config.DATA_DIR) else os.path.join(PROJECT_DIR, Config.DATA_DIR)
    os.makedirs(folder, exist_ok=True)
    return folder


def library_output_path(name: str) -> str:
    """Where to write a library JSON: a bare file name goes to the data folder, a path is kept as is."""
    return name if os.path.dirname(name) else os.path.join(data_dir(), name)


def resolve_library(name: str) -> str:
    """Finds a library JSON given by the user: a bare file name is looked up in the data folder first."""
    if not os.path.dirname(name):
        candidate = os.path.join(data_dir(), name)
        if os.path.exists(candidate):
            return candidate
    return name


def write_json(path: str, data: Any) -> None:
    """Atomic write: an interruption (Ctrl+C, crash, full disk) never leaves a truncated library."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def load_library(path: str) -> List[Track]:
    """Reads a library JSON (list of tracks). Raises ValueError with a readable message if it is invalid;
    entries that are not tracks (no title) are skipped."""
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(f"{path} is not valid JSON (line {e.lineno}, column {e.colno}): {e.msg}") from None
    if not isinstance(data, list):
        raise ValueError(f"{path} must contain a list of tracks.")
    tracks = [t for t in data if isinstance(t, dict) and str(t.get("title") or "").strip()]
    if len(tracks) < len(data):
        logger.warning(f"{len(data) - len(tracks)} invalid entries (no title) ignored in {path}.")
    return tracks


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def http_session(user_agent: Optional[str] = None) -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=4,
        backoff_factor=1.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_maxsize=16)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers["User-Agent"] = user_agent or f"{APP_NAME}/{APP_VERSION}"
    return session


def setup_logging(log_file: str = "spotify_to_mp3.log", verbose: bool = False) -> None:
    """Console (INFO) + file (DEBUG) logging. Called once by the CLI entry points."""
    root = logging.getLogger()
    if getattr(root, "_stm_configured", False):
        return
    root.setLevel(logging.DEBUG)

    console = logging.StreamHandler()
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s", "%H:%M:%S"))
    root.addHandler(console)

    file_handler = logging.handlers.RotatingFileHandler(
        os.path.join(data_dir(), log_file), maxBytes=5_000_000, backupCount=2, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
    root.addHandler(file_handler)

    for noisy in ("urllib3", "spotipy", "ytmusicapi", "requests"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    root._stm_configured = True  # type: ignore[attr-defined]
    for message in CONFIG_WARNINGS:
        logger.warning(message)
