import os
from typing import Callable, List, Optional, TypeVar

from dotenv import load_dotenv

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))

# Load environment variables from the .env file next to this script (whatever the current folder)
load_dotenv(os.path.join(PROJECT_DIR, ".env"))

T = TypeVar("T")

# Invalid .env values are replaced by their default; the CLI logs these messages once logging is set up
CONFIG_WARNINGS: List[str] = []


def _env(name: str, default: T, parse: Callable[[str], T], valid: Optional[Callable[[T], bool]] = None, expected: str = "") -> T:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = parse(raw.strip())
        if valid is None or valid(value):
            return value
    except ValueError:
        pass
    CONFIG_WARNINGS.append(f"Invalid {name}={raw!r} in .env{f' (expected {expected})' if expected else ''}: using {default!r}.")
    return default


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() in ("1", "true", "yes", "on", "oui")


def _path(value: str) -> str:
    path = os.path.expanduser(os.path.expandvars(value.strip()))
    return path if os.path.isabs(path) else os.path.join(PROJECT_DIR, path)


def _mp3_quality(value: str) -> str:
    """'0'-'9' = LAME VBR quality, or a CBR bitrate in kbps ('320', '256k'...)."""
    number = int(value.lower().rstrip("k"))
    if 0 <= number <= 9 or 32 <= number <= 320:
        return str(number)
    raise ValueError(value)


class Config:
    # Spotify Configuration (optional: only needed for the Spotify commands)
    SPOTIPY_CLIENT_ID = os.getenv("SPOTIPY_CLIENT_ID")
    SPOTIPY_CLIENT_SECRET = os.getenv("SPOTIPY_CLIENT_SECRET")
    SPOTIPY_REDIRECT_URI = os.getenv("SPOTIPY_REDIRECT_URI", "http://127.0.0.1:8080/callback")

    # Storage Configuration
    # MP3 files and, below, library JSON files / failed_*.json / log ("~" allowed; relative = inside the project folder)
    DOWNLOAD_DIR = _path(os.getenv("DOWNLOAD_DIR") or "downloads")
    DATA_DIR = _path(os.getenv("DATA_DIR") or "data")

    # Rate Limiting & Concurrency
    RATE_LIMIT_DELAY = _env("RATE_LIMIT_DELAY", 2.0, float, lambda v: 0 <= v <= 600, "seconds >= 0")
    CONCURRENT_DOWNLOADS = _env("CONCURRENT_DOWNLOADS", 3, int, lambda v: 1 <= v <= 32, "1 to 32")

    # Audio: "0" = LAME V0 VBR (~245 kbps, transparent), or a bitrate such as "320" for CBR.
    # The YouTube source is ~130-160 kbps Opus, so V0 keeps everything it contains.
    MP3_QUALITY = _env("MP3_QUALITY", "0", _mp3_quality, expected="0-9 (VBR) or a bitrate such as 320")

    # Matching: minimal score (0-1) for a YouTube Music result to be accepted
    MATCH_THRESHOLD = _env("MATCH_THRESHOLD", 0.72, float, lambda v: 0 < v <= 1, "a number between 0 and 1")

    # Metadata
    ID3_VERSION = _env("ID3_VERSION", 3, int, lambda v: v in (3, 4), "3 or 4")  # 3 = best compatibility (Windows Explorer, car stereos)
    COVER_SIZE = _env("COVER_SIZE", "1200", str.lower, lambda v: v in ("250", "500", "1200", "full"), "250, 500, 1200 or full")
    SAVE_FOLDER_COVER = _bool("SAVE_FOLDER_COVER", True)  # writes cover.jpg in each album folder
    FETCH_LYRICS = _bool("FETCH_LYRICS", True)  # lyrics from LRCLIB (free, no account)
    SAVE_LRC = _bool("SAVE_LRC", True)  # synced lyrics as a .lrc file next to the .mp3

    # MusicBrainz asks for a contact (URL or e-mail) in the User-Agent
    MUSICBRAINZ_CONTACT = os.getenv("MUSICBRAINZ_CONTACT", "")
    # Preferred release countries when choosing the canonical edition of an album
    PREFERRED_COUNTRIES = [
        c.strip().upper() for c in os.getenv("PREFERRED_COUNTRIES", "XW,FR,XE,US,GB").split(",") if c.strip()
    ]

    @classmethod
    def validate_spotify(cls):
        missing = []
        for name in ("SPOTIPY_CLIENT_ID", "SPOTIPY_CLIENT_SECRET"):
            value = getattr(cls, name)
            if not value or value.startswith("your_"):  # empty or still the placeholder of .env.example
                missing.append(name)

        if missing:
            raise ValueError(f"Missing required environment variables: {', '.join(missing)}\nPlease check your .env file.")

    # Backwards compatibility
    validate = validate_spotify
