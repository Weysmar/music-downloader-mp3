"""Shortcut kept from v1: python download_artist.py "Artist" [options of `main.py spotify-artist`]"""
import sys

from main import main  # type: ignore

if __name__ == "__main__":
    main(["spotify-artist", *sys.argv[1:]])
