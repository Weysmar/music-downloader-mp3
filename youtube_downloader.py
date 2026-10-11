"""Downloads a JSON library from YouTube Music (no Google account / cookies), converts to MP3, tags."""
import hashlib
import glob
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple

import yt_dlp  # type: ignore
from rich.console import Console  # type: ignore
from rich.markup import escape  # type: ignore
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn, TimeElapsedColumn, TimeRemainingColumn  # type: ignore

from common import Track, build_track_path, data_dir, find_ffmpeg, load_library, long_path, resolve_library, write_json  # type: ignore
from config import Config  # type: ignore
from lyrics import fetch_lyrics  # type: ignore
from matcher import YouTubeMatcher  # type: ignore
from metadata_injector import MetadataInjector  # type: ignore

logger = logging.getLogger(__name__)
console = Console()

BOT_CHECK_MARKERS = ("confirm you", "not a bot", "HTTP Error 429", "Too Many Requests")
# Often transient on YouTube (expired stream URL, CDN hiccup): the same video is tried once more
TRANSIENT_MARKERS = ("HTTP Error 403", "HTTP Error 5", "timed out", "Connection reset", "IncompleteRead")


class _YdlLogger:
    """Sends yt-dlp output to the log file instead of the console."""

    def debug(self, msg: str) -> None:
        logger.debug(f"yt-dlp: {msg}")

    def info(self, msg: str) -> None:
        logger.debug(f"yt-dlp: {msg}")

    def warning(self, msg: str) -> None:
        logger.debug(f"yt-dlp warning: {msg}")

    def error(self, msg: str) -> None:
        logger.debug(f"yt-dlp error: {msg}")


class YouTubeDownloader:
    def __init__(
        self,
        output_dir: Optional[str] = None,
        max_workers: Optional[int] = None,
        rate_limit_delay: Optional[float] = None,
        fetch_lyrics: Optional[bool] = None,
    ) -> None:
        self.download_dir = output_dir or Config.DOWNLOAD_DIR
        self.concurrent_downloads = max(1, max_workers or Config.CONCURRENT_DOWNLOADS)
        self.rate_limit_delay = Config.RATE_LIMIT_DELAY if rate_limit_delay is None else rate_limit_delay
        self.fetch_lyrics = Config.FETCH_LYRICS if fetch_lyrics is None else fetch_lyrics
        self.tmp_dir = os.path.join(self.download_dir, ".tmp")
        self.matcher = YouTubeMatcher()
        self.ffmpeg_dir = find_ffmpeg()
        self.failed_tracks: List[Track] = []

        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._cooldown_until = 0.0
        self._cooldown_count = 0
        os.makedirs(self.tmp_dir, exist_ok=True)
        self._purge_stale_tmp()

    def _purge_stale_tmp(self, max_age: float = 86400) -> None:
        """Removes what a killed run left in .tmp (files older than a day, so a parallel run is not disturbed)."""
        now = time.time()
        for leftover in glob.glob(os.path.join(glob.escape(self.tmp_dir), "*")):
            try:
                if now - os.path.getmtime(leftover) > max_age:
                    os.remove(leftover)
            except OSError:
                pass

    # --------------------------------------------------------------- yt-dlp

    def _ydl_options(self, output_template: str) -> Dict[str, Any]:
        opts: Dict[str, Any] = {
            "format": "bestaudio/best",  # Opus ~130-160 kbps (format 251) when available
            "outtmpl": output_template,
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "retries": 5,
            "fragment_retries": 5,
            # JavaScript runtime used by yt-dlp to solve YouTube's challenges (Deno or Node.js)
            "js_runtimes": {"deno": {}, "node": {}},
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": Config.MP3_QUALITY,
            }],
            "logger": _YdlLogger(),
        }
        if self.ffmpeg_dir:
            opts["ffmpeg_location"] = self.ffmpeg_dir
        return opts

    def _wait_cooldown(self) -> None:
        while not self._stop.is_set():
            remaining = self._cooldown_until - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(remaining, 1.0))

    def _trigger_cooldown(self) -> None:
        """YouTube asks to prove we are not a bot: every worker pauses (longer each time)."""
        with self._lock:
            if self._cooldown_until > time.monotonic():
                return
            self._cooldown_count += 1
            pause = min(60 * 2 ** (self._cooldown_count - 1), 900)
            self._cooldown_until = time.monotonic() + pause
        console.print(f"[yellow]⏸️  YouTube is throttling the downloads, pausing {pause}s...[/yellow]")

    def _cleanup_tmp(self, key: str) -> None:
        for leftover in glob.glob(os.path.join(glob.escape(self.tmp_dir), f"{key}.*")):
            try:
                os.remove(leftover)
            except OSError:
                pass

    # ----------------------------------------------------------- One track

    def _write_extras(self, target: str, lyrics: Optional[Dict[str, Optional[str]]], cover) -> None:
        if Config.SAVE_LRC and lyrics and lyrics.get("synced"):
            with open(long_path(os.path.splitext(target)[0] + ".lrc"), "w", encoding="utf-8") as f:
                f.write(lyrics["synced"])
        if Config.SAVE_FOLDER_COVER and cover:
            data, mime = cover
            folder_cover = os.path.join(os.path.dirname(target), "cover.png" if mime == "image/png" else "cover.jpg")
            with self._lock:
                if not os.path.exists(long_path(folder_cover)):
                    with open(long_path(folder_cover), "wb") as f:
                        f.write(data)

    def process_track(self, track: Track) -> Tuple[str, str]:
        """Returns (status, message) with status in: skipped, downloaded, failed."""
        target = build_track_path(self.download_dir, track)
        if os.path.exists(long_path(target)):
            return "skipped", target

        candidates = self.matcher.find(track)
        if not candidates:
            return "failed", "no reliable match on YouTube Music (title/artist/duration)"

        # One temporary name per destination: the same recording kept twice (--keep-duplicates) never collides
        key = hashlib.sha1(os.path.abspath(target).encode("utf-8")).hexdigest()[:16]
        tmp_mp3 = os.path.join(self.tmp_dir, f"{key}.mp3")
        last_error = ""
        for cand in candidates:
            for _attempt in range(2):
                self._wait_cooldown()
                if self._stop.is_set():
                    return "failed", "cancelled"
                try:
                    with yt_dlp.YoutubeDL(self._ydl_options(os.path.join(self.tmp_dir, f"{key}.%(ext)s"))) as ydl:
                        ydl.download([f"https://www.youtube.com/watch?v={cand.video_id}"])
                    if not os.path.exists(tmp_mp3):
                        last_error = "MP3 conversion failed (FFmpeg)"
                        break

                    lyrics = fetch_lyrics(track) if self.fetch_lyrics else None
                    cover = MetadataInjector.fetch_cover(track)
                    if not MetadataInjector.inject_metadata(tmp_mp3, track, source_id=cand.video_id, lyrics=lyrics, cover=cover):
                        return "failed", "could not write the tags (see the log)"

                    os.makedirs(long_path(os.path.dirname(target)), exist_ok=True)
                    os.replace(tmp_mp3, long_path(target))
                    self._write_extras(target, lyrics, cover)
                    return "downloaded", f"{target}  [score {cand.score:.2f}{', lyrics' if lyrics else ''}]"
                except yt_dlp.utils.DownloadError as e:
                    last_error = str(e).replace("ERROR: ", "")
                    if any(m in last_error for m in BOT_CHECK_MARKERS):
                        self._trigger_cooldown()
                        continue  # retry the same video after the pause
                    if any(m in last_error for m in TRANSIENT_MARKERS):
                        time.sleep(3)
                        continue
                    break  # video unavailable, region locked...: try the next candidate
                finally:
                    self._cleanup_tmp(key)
        return "failed", last_error or "download failed"

    # ------------------------------------------------------------- Library

    def _failed_log_path(self, library_json_path: str) -> str:
        name = os.path.basename(library_json_path)
        return library_json_path if name.startswith("failed_") else os.path.join(data_dir(), f"failed_{name}")

    def download_library(self, library_json_path: str = "library.json") -> Dict[str, int]:
        """Downloads every track of a JSON library with a thread pool. Returns counters per status."""
        library_json_path = resolve_library(library_json_path)
        if not os.path.exists(library_json_path):
            console.print(f"[bold red]Library file '{escape(library_json_path)}' not found.[/bold red]")
            return {}
        tracks = load_library(library_json_path)
        if not self.ffmpeg_dir:
            console.print("[bold red]FFmpeg/FFprobe not found (project folder or PATH).[/bold red]")
            return {}

        # Two entries with the same destination (same song listed twice) would download it twice in parallel
        unique: Dict[str, Track] = {}
        for track in tracks:
            unique.setdefault(os.path.normcase(os.path.abspath(build_track_path(self.download_dir, track))), track)
        duplicates = len(tracks) - len(unique)
        tracks = list(unique.values())

        console.print(f"[bold green]{len(tracks)} tracks in {escape(library_json_path)}[/bold green] — "
                      f"{self.concurrent_downloads} workers, MP3 quality {Config.MP3_QUALITY}, no cookies")
        counts = {"downloaded": 0, "skipped": duplicates, "failed": 0}
        self.failed_tracks = []

        def worker(track: Track) -> Tuple[Track, str, str]:
            if self._stop.is_set():
                return track, "failed", "cancelled"
            try:
                status, message = self.process_track(track)
            except Exception as e:
                logger.debug("Unexpected error", exc_info=True)
                status, message = "failed", f"unexpected error: {e}"
            if status == "downloaded" and self.rate_limit_delay:
                time.sleep(self.rate_limit_delay)
            return track, status, message

        with Progress(
            SpinnerColumn(), TextColumn("[progress.description]{task.description}"), BarColumn(),
            TaskProgressColumn(), TimeElapsedColumn(), TimeRemainingColumn(), console=console,
        ) as progress:
            main_task = progress.add_task("[bold green]Downloading", total=len(tracks))
            executor = ThreadPoolExecutor(max_workers=self.concurrent_downloads)
            try:
                futures = [executor.submit(worker, t) for t in tracks]
                for future in as_completed(futures):
                    track, status, message = future.result()
                    counts[status] += 1
                    label = f"{track.get('artist')} - {track.get('title')}"
                    if status == "downloaded":
                        progress.console.print(f"[green]✅ {escape(message)}[/green]")
                    elif status == "failed":
                        progress.console.print(f"[red]❌ {escape(label)}: {escape(message)}[/red]")
                        logger.debug(f"FAILED {label}: {message}")
                        self.failed_tracks.append({**track, "_failure_reason": message})
                    progress.advance(main_task)
            except KeyboardInterrupt:
                self._stop.set()
                executor.shutdown(wait=False, cancel_futures=True)
                console.print("[yellow]Interrupted: run the same command again to resume.[/yellow]")
                raise
            finally:
                executor.shutdown(wait=True)

        failed_log = self._failed_log_path(library_json_path)
        if self.failed_tracks:
            write_json(failed_log, self.failed_tracks)
            console.print(f"[bold yellow]⚠️ {len(self.failed_tracks)} failed tracks saved to {escape(failed_log)} "
                          f"(retry with: python main.py download \"{escape(failed_log)}\")[/bold yellow]")
        elif os.path.exists(failed_log):
            os.remove(failed_log)  # everything succeeded: the previous list of failures is obsolete

        console.print(f"[bold green]Done: {counts['downloaded']} downloaded, {counts['skipped']} already present, "
                      f"{counts['failed']} failed.[/bold green] Files in '{self.download_dir}'.")
        return counts

    def retag_library(self, library_json_path: str) -> Dict[str, int]:
        """Rewrites the tags of already downloaded files (e.g. after `enrich`)."""
        library_json_path = resolve_library(library_json_path)
        tracks = load_library(library_json_path)
        counts = {"retagged": 0, "missing": 0}
        from tqdm import tqdm  # type: ignore
        from mutagen.id3 import ID3, ID3NoHeaderError  # type: ignore
        for track in tqdm(tracks, desc="Tagging", unit="track"):
            target = long_path(build_track_path(self.download_dir, track))
            if not os.path.exists(target):
                counts["missing"] += 1
                continue
            lyrics = None
            if self.fetch_lyrics:
                try:
                    has_lyrics = bool(ID3(target).getall("USLT"))
                except ID3NoHeaderError:
                    has_lyrics = False
                if not has_lyrics:
                    lyrics = fetch_lyrics(track)
            if MetadataInjector.inject_metadata(target, track, lyrics=lyrics):
                counts["retagged"] += 1
        console.print(f"[bold green]{counts['retagged']} files re-tagged, {counts['missing']} not downloaded yet.[/bold green]")
        return counts


if __name__ == "__main__":
    from common import setup_logging  # type: ignore
    setup_logging()
    YouTubeDownloader().download_library()
