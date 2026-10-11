"""Spotify to MP3 — discographies and libraries to tagged MP3 files (YouTube Music, no Google account).

Examples:
  python main.py artist "Daft Punk"                       # full discography (MusicBrainz, no API key)
  python main.py artist "Iron Maiden" --include live      # also live albums
  python main.py artist --mbid 056e4f3e-d505-4dad-8ec1-d04f521cbb56
  python main.py spotify-library --enrich                 # liked songs + own playlists
  python main.py spotify-artist "Gazo"
  python main.py download "Daft Punk_mb_library.json"     # (re)download a JSON library
  python main.py download "failed_Daft Punk_mb_library.json"
  python main.py enrich library.json && python main.py retag library.json
"""
import argparse
import logging
import os
import sys
from typing import Callable, List, Optional, Sequence

from common import find_ffmpeg, library_output_path, load_library, resolve_library, setup_logging, write_json  # type: ignore

logger = logging.getLogger(__name__)


def check_ffmpeg() -> bool:
    """Kept for backwards compatibility."""
    return find_ffmpeg() is not None


def _download(library_file: str, args: argparse.Namespace) -> None:
    from youtube_downloader import YouTubeDownloader  # type: ignore
    if not check_ffmpeg():
        logger.error("FFmpeg not found: put ffmpeg.exe and ffprobe.exe in the project folder or install FFmpeg in the PATH.")
        sys.exit(1)
    downloader = YouTubeDownloader(
        output_dir=args.output_dir,
        max_workers=args.workers,
        fetch_lyrics=False if args.no_lyrics else None,
    )
    downloader.download_library(library_file)


def _enrich_file(library_file: str) -> None:
    from musicbrainz_extractor import MusicBrainzExtractor  # type: ignore
    library_file = resolve_library(library_file)
    tracks = MusicBrainzExtractor().enrich_tracks(load_library(library_file))
    write_json(library_file, tracks)
    logger.info(f"Enriched library saved to {library_file}")


def _require_library(name: str) -> str:
    library = resolve_library(name)
    if not os.path.exists(library):
        logger.error(f"Library file {name} not found (current folder or data folder).")
        sys.exit(1)
    return library


def cmd_artist(args: argparse.Namespace) -> None:
    from musicbrainz_extractor import MusicBrainzExtractor  # type: ignore
    artist = args.mbid or args.artist
    if not artist:
        logger.error("Give an artist name or --mbid.")
        sys.exit(1)
    library_file = MusicBrainzExtractor().extract_discography(
        artist,
        output_file=args.output,
        types=[t.strip().lower() for t in args.types.split(",") if t.strip()],
        include_secondary=[t.strip().lower() for t in args.include.split(",") if t.strip()],
        edition=args.edition,
        limit=args.limit,
        keep_duplicates=args.keep_duplicates,
    )
    if not library_file:
        sys.exit(1)
    if not args.extract_only:
        _download(library_file, args)


def cmd_spotify_artist(args: argparse.Namespace) -> None:
    from spotify_extractor import SpotifyExtractor  # type: ignore
    library_file = SpotifyExtractor().extract_artist_discography(
        args.artist,
        include_compilations=args.include_compilations,
        edition=args.edition,
        keep_duplicates=args.keep_duplicates,
    )
    if not library_file:
        sys.exit(1)
    if args.enrich:
        _enrich_file(library_file)
    if not args.extract_only:
        _download(library_file, args)


def cmd_spotify_library(args: argparse.Namespace) -> None:
    from spotify_extractor import SpotifyExtractor  # type: ignore
    output = library_output_path(args.output)
    SpotifyExtractor().extract_library(output)
    if args.enrich:
        _enrich_file(output)
    if not args.extract_only:
        _download(output, args)


def cmd_download(args: argparse.Namespace) -> None:
    _download(_require_library(args.library), args)


def cmd_enrich(args: argparse.Namespace) -> None:
    _enrich_file(_require_library(args.library))


def cmd_retag(args: argparse.Namespace) -> None:
    from youtube_downloader import YouTubeDownloader  # type: ignore
    YouTubeDownloader(output_dir=args.output_dir, fetch_lyrics=False if args.no_lyrics else None).retag_library(
        _require_library(args.library))


def _int_at_least(minimum: int) -> Callable[[str], int]:
    def parse(value: str) -> int:
        try:
            number = int(value)
        except ValueError:
            raise argparse.ArgumentTypeError(f"nombre entier attendu : {value!r}") from None
        if number < minimum:
            raise argparse.ArgumentTypeError(f"doit être >= {minimum} : {number}")
        return number
    return parse


def _choices_list(allowed: Sequence[str]) -> Callable[[str], str]:
    def parse(value: str) -> str:
        items = [v.strip().lower() for v in value.split(",") if v.strip()]
        unknown = [v for v in items if v not in allowed]
        if unknown:
            raise argparse.ArgumentTypeError(f"valeur(s) inconnue(s) {', '.join(unknown)} ; possibles : {', '.join(allowed)}")
        return ",".join(items)
    return parse


def build_parser() -> argparse.ArgumentParser:
    from musicbrainz_extractor import EXCLUDABLE_SECONDARY, PRIMARY_TYPES  # type: ignore
    parser = argparse.ArgumentParser(
        description="Télécharge des discographies / bibliothèques en MP3 taggués (YouTube Music, sans compte Google).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("\n", 2)[2],
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="affiche les détails (scores de correspondance...)")

    dl = argparse.ArgumentParser(add_help=False)
    dl.add_argument("--output-dir", default=None, help="dossier de destination (défaut : DOWNLOAD_DIR ou downloads)")
    dl.add_argument("--workers", type=_int_at_least(1), default=None, help="téléchargements en parallèle (défaut : CONCURRENT_DOWNLOADS)")
    dl.add_argument("--no-lyrics", action="store_true", help="ne pas chercher les paroles")

    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("artist", parents=[dl], help="discographie complète via MusicBrainz (sans clé API)")
    p.add_argument("artist", nargs="?", help="nom de l'artiste (ou MBID)")
    p.add_argument("--mbid", help="identifiant MusicBrainz de l'artiste (en cas d'homonymes)")
    p.add_argument("--types", type=_choices_list(PRIMARY_TYPES), default="album,ep,single", help="types de sorties (défaut : album,ep,single)")
    p.add_argument("--include", type=_choices_list(EXCLUDABLE_SECONDARY), default="", help="types secondaires à inclure : live,compilation,remix,demo,dj-mix,mixtape/street...")
    p.add_argument("--edition", choices=["original", "largest"], default="original",
                   help="édition retenue par album : originale (défaut) ou la plus complète (deluxe, bonus)")
    p.add_argument("--limit", type=_int_at_least(0), default=0, help="nombre max de morceaux (0 = illimité)")
    p.add_argument("--keep-duplicates", action="store_true", help="garder les doublons (single + album...)")
    p.add_argument("--extract-only", action="store_true", help="créer le JSON sans télécharger")
    p.add_argument("-o", "--output", help="fichier JSON de sortie")
    p.set_defaults(func=cmd_artist)

    p = sub.add_parser("spotify-artist", parents=[dl], help="discographie via l'API Spotify (clés dans .env)")
    p.add_argument("artist")
    p.add_argument("--include-compilations", action="store_true")
    p.add_argument("--edition", choices=["original", "largest"], default="original")
    p.add_argument("--keep-duplicates", action="store_true")
    p.add_argument("--enrich", action="store_true", help="compléter les métadonnées via MusicBrainz")
    p.add_argument("--extract-only", action="store_true")
    p.set_defaults(func=cmd_spotify_artist)

    p = sub.add_parser("spotify-library", parents=[dl], help="titres likés + playlists Spotify")
    p.add_argument("-o", "--output", default="library.json")
    p.add_argument("--enrich", action="store_true", help="compléter les métadonnées via MusicBrainz (par ISRC)")
    p.add_argument("--extract-only", action="store_true")
    p.set_defaults(func=cmd_spotify_library)

    p = sub.add_parser("download", parents=[dl], help="télécharger un fichier JSON (reprend là où il s'était arrêté)")
    p.add_argument("library")
    p.set_defaults(func=cmd_download)

    p = sub.add_parser("enrich", help="compléter un JSON avec MusicBrainz (par ISRC)")
    p.add_argument("library")
    p.set_defaults(func=cmd_enrich)

    p = sub.add_parser("retag", parents=[dl], help="réécrire les tags des fichiers déjà téléchargés")
    p.add_argument("library")
    p.set_defaults(func=cmd_retag)
    return parser


def _legacy_args(argv: List[str]) -> List[str]:
    """Translates the v1 flags (--extract / --download / --all / --library)."""
    if not argv or argv[0] not in ("--extract", "--download", "--all", "--library"):
        return argv
    library = "library.json"
    if "--library" in argv and argv.index("--library") + 1 < len(argv):
        library = argv[argv.index("--library") + 1]
    if "--all" in argv or ("--extract" in argv and "--download" in argv):
        return ["spotify-library", "-o", library]
    if "--extract" in argv:
        return ["spotify-library", "-o", library, "--extract-only"]
    return ["download", library]


def main(argv: Optional[List[str]] = None) -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")  # accents/emojis when redirected on Windows
    argv = _legacy_args(list(sys.argv[1:] if argv is None else argv))
    args = build_parser().parse_args(argv)
    setup_logging(verbose=args.verbose)
    try:
        args.func(args)
    except KeyboardInterrupt:
        logger.info("Interrupted by user.")
        sys.exit(130)
    except Exception as e:
        logger.error(f"An error occurred: {e}")
        logger.debug("Traceback", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
