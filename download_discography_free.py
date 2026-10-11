"""Shortcut kept from v1: python download_discography_free.py "Artist" [options of `main.py artist`]"""
import sys

from main import main  # type: ignore

if __name__ == "__main__":
    main(["artist", *sys.argv[1:]])
