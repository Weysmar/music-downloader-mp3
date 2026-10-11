import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common import find_ffmpeg  # noqa: E402
from config import Config  # noqa: E402

LIVE = os.getenv("RUN_LIVE_TESTS") == "1"


def pytest_configure(config):
    config.addinivalue_line("markers", "live: hits the real MusicBrainz / YouTube Music (RUN_LIVE_TESTS=1)")


def pytest_collection_modifyitems(config, items):
    if LIVE:
        return
    skip = pytest.mark.skip(reason="live test: set RUN_LIVE_TESTS=1 to run it")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    """Library JSON / failed files / log of the tests never touch the real data folder."""
    monkeypatch.setattr(Config, "DATA_DIR", str(tmp_path / "data"))
    return tmp_path / "data"


@pytest.fixture(autouse=True)
def no_network(request, monkeypatch):
    """Unit tests must not depend on the Internet: any real HTTP request fails loudly."""
    if "live" in request.keywords:
        return

    def blocked(*args, **kwargs):
        raise RuntimeError("network access in a unit test")

    monkeypatch.setattr("requests.adapters.HTTPAdapter.send", blocked)


@pytest.fixture(scope="session")
def mp3_template(tmp_path_factory):
    """A real 1-second MP3 encoded by ffmpeg."""
    folder = find_ffmpeg()
    if not folder:
        pytest.skip("ffmpeg/ffprobe not available")
    exe = os.path.join(folder, "ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    path = tmp_path_factory.mktemp("audio") / "template.mp3"
    subprocess.run([exe, "-v", "error", "-f", "lavfi", "-i", "sine=d=1", "-q:a", "0", str(path)], check=True)
    return str(path)


@pytest.fixture
def mp3(tmp_path, mp3_template):
    path = tmp_path / "test.mp3"
    shutil.copy(mp3_template, path)
    return str(path)
