"""YouTube Music matching with incomplete or unusual data, and the search fallback logic."""
import pytest

import matcher
from matcher import Candidate, YouTubeMatcher, score_candidate


def cand(title, artists, duration, kind="song", album=None, video_id=None):
    return Candidate(video_id=video_id or title, title=title, artists=artists, duration=duration, result_type=kind, album=album)


def test_translated_title_matches_original():
    track = {"title": "Nothin’ = ナッシン", "artists": ["Guns N’ Roses"], "duration_ms": 264000}
    assert score_candidate(track, cand("Nothin'", ["Guns N' Roses"], 265)) >= 0.85


def test_missing_duration_cannot_reach_full_confidence():
    track = {"title": "Song", "artists": ["Artist"]}
    score = score_candidate(track, cand("Song", ["Artist"], None))
    assert 0.72 <= score < 0.95


def test_other_artist_same_title_rejected():
    track = {"title": "Hurt", "artists": ["Nine Inch Nails"], "duration_ms": 373000}
    assert score_candidate(track, cand("Hurt", ["Johnny Cash"], 218)) == 0
    assert score_candidate(track, cand("Hurt", ["Johnny Cash"], 373)) == 0


def test_live_track_wanted_studio_rejected():
    track = {"title": "Running Free (live)", "artists": ["Iron Maiden"], "album": "Maiden Japan", "duration_ms": 189000}
    assert score_candidate(track, cand("Running Free", ["Iron Maiden"], 189)) < 0.72


def test_live_album_context_accepts_untagged_live_titles():
    track = {"title": "Aces High", "artists": ["Iron Maiden"], "album": "Live After Death", "duration_ms": 291000}
    assert score_candidate(track, cand("Aces High (Live)", ["Iron Maiden"], 292, album="Live After Death")) >= 0.85


def test_unicode_and_punctuation_heavy_titles():
    track = {"title": "Ça (c'est vraiment toi)", "artists": ["Téléphone"], "duration_ms": 280000}
    assert score_candidate(track, cand("Ca (C'est Vraiment Toi)", ["Telephone"], 281)) >= 0.85


def test_candidate_from_incomplete_results():
    assert Candidate.from_result({"title": "no id"}) is None
    c = Candidate.from_result({"videoId": "abc", "title": None, "artists": [{"name": None}, {}], "album": None})
    assert c.video_id == "abc" and c.title == "" and c.duration is None
    assert score_candidate({"title": "X", "artists": ["Y"]}, c) == 0


@pytest.fixture
def fake_searches(monkeypatch):
    calls = []
    answers = {}

    def search(self, query, search_filter):
        calls.append((query, search_filter))
        return [Candidate(**vars(c)) for c in answers.get((query, search_filter), [])]

    monkeypatch.setattr(YouTubeMatcher, "_search", search)
    return calls, answers


def test_find_falls_back_to_videos(fake_searches):
    calls, answers = fake_searches
    track = {"title": "Song", "artists": ["Artist"], "duration_ms": 200000}
    answers[("Artist Song", "videos")] = [cand("Artist - Song (Official Audio)", ["Artist"], 201, "video")]
    found = YouTubeMatcher(threshold=0.72).find(track)
    assert [c.title for c in found] == ["Artist - Song (Official Audio)"]
    assert calls == [("Artist Song", "songs"), ("Artist Song", "videos")]


def test_find_returns_nothing_rather_than_a_wrong_song(fake_searches):
    calls, answers = fake_searches
    track = {"title": "Possédé Framboise", "artists": ["Vald"], "duration_ms": 154000}
    answers[("Vald Possédé Framboise", "songs")] = [cand("Possédé", ["Vald"], 154), cand("Aulnay Sous Bois", ["Vald"], 154)]
    assert YouTubeMatcher(threshold=0.72).find(track) == []


def test_find_keeps_only_close_fallbacks(fake_searches):
    _, answers = fake_searches
    track = {"title": "Song", "artists": ["Artist"], "duration_ms": 200000}
    answers[("Artist Song", "songs")] = [
        cand("Song", ["Artist"], 200, video_id="best"),
        cand("Song", ["Artist"], 209, "video", video_id="close"),
        cand("Song (Live)", ["Artist"], 200, video_id="live"),
    ]
    found = YouTubeMatcher(threshold=0.72).find(track)
    assert [c.video_id for c in found] == ["best", "close"]


def test_search_errors_are_retried_then_ignored(monkeypatch):
    monkeypatch.setattr(matcher.time, "sleep", lambda s: None)
    attempts = []

    class FlakyYT:
        def search(self, query, filter, limit):
            attempts.append(query)
            raise ConnectionError("YouTube Music unreachable")

    m = YouTubeMatcher()
    monkeypatch.setattr(m, "_yt", lambda: FlakyYT())
    assert m.find({"title": "Song", "artists": ["Artist"]}) == []
    assert len(attempts) == 6  # 3 attempts x (songs, videos)
