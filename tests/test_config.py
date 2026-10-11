import os

import pytest

import config
from config import Config, _env, _mp3_quality


@pytest.fixture(autouse=True)
def clear_warnings(monkeypatch):
    monkeypatch.setattr(config, "CONFIG_WARNINGS", [])


def test_invalid_values_fall_back_to_defaults(monkeypatch):
    monkeypatch.setenv("X_FLOAT", "abc")
    assert _env("X_FLOAT", 2.0, float) == 2.0
    monkeypatch.setenv("X_INT", "0")
    assert _env("X_INT", 3, int, lambda v: v >= 1, "1 or more") == 3
    assert len(config.CONFIG_WARNINGS) == 2
    assert "X_INT" in config.CONFIG_WARNINGS[1] and "1 or more" in config.CONFIG_WARNINGS[1]


def test_valid_and_missing_values(monkeypatch):
    monkeypatch.setenv("X_FLOAT", " 0.5 ")
    assert _env("X_FLOAT", 2.0, float) == 0.5
    monkeypatch.delenv("X_MISSING", raising=False)
    assert _env("X_MISSING", 7, int) == 7
    monkeypatch.setenv("X_EMPTY", "")
    assert _env("X_EMPTY", 7, int) == 7
    assert config.CONFIG_WARNINGS == []


@pytest.mark.parametrize("raw, expected", [("0", "0"), ("9", "9"), ("320", "320"), ("256k", "256"), ("192K", "192")])
def test_mp3_quality_accepted(raw, expected):
    assert _mp3_quality(raw) == expected


@pytest.mark.parametrize("raw", ["10", "500", "-1", "best", ""])
def test_mp3_quality_rejected(raw):
    with pytest.raises(ValueError):
        _mp3_quality(raw)


def test_spotify_placeholder_keys_are_rejected(monkeypatch):
    monkeypatch.setattr(Config, "SPOTIPY_CLIENT_ID", "your_spotify_client_id")
    monkeypatch.setattr(Config, "SPOTIPY_CLIENT_SECRET", "real-secret")
    with pytest.raises(ValueError, match="SPOTIPY_CLIENT_ID"):
        Config.validate_spotify()
    monkeypatch.setattr(Config, "SPOTIPY_CLIENT_ID", "abc123")
    Config.validate_spotify()


def test_current_config_is_valid():
    assert Config.ID3_VERSION in (3, 4)
    assert Config.COVER_SIZE in ("250", "500", "1200", "full")
    assert Config.CONCURRENT_DOWNLOADS >= 1
    assert 0 < Config.MATCH_THRESHOLD <= 1
    _mp3_quality(Config.MP3_QUALITY)


def test_paths_expand_home_and_are_anchored_to_the_project(monkeypatch):
    monkeypatch.setenv("HOME", "/home/me")
    monkeypatch.setenv("USERPROFILE", "/home/me")
    assert config._path("~/Music").replace("\\", "/").endswith("/home/me/Music")
    assert config._path("downloads") == os.path.join(config.PROJECT_DIR, "downloads")
    absolute = os.path.abspath("/somewhere/music")
    assert config._path(absolute) == absolute
