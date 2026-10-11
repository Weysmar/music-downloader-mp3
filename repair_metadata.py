"""Shortcut kept from v1: re-writes the tags of downloaded files from a library JSON.
Usage: python repair_metadata.py "Daft Punk_mb_library.json"
"""
import sys

from main import main  # type: ignore

if __name__ == "__main__":
    main(["retag", *sys.argv[1:]])
