from matcher import Candidate, YouTubeMatcher, score_candidate

BLACKENED = {"title": "Blackened", "artists": ["Metallica"], "album": "...And Justice for All", "duration_ms": 402_000}


def cand(title, artists, duration, result_type="song", album=None):
    return Candidate(video_id=title[:11], title=title, artists=artists, duration=duration, result_type=result_type, album=album)


def test_official_audio_wins():
    assert score_candidate(BLACKENED, cand("Blackened (Remastered)", ["Metallica"], 403, album="...And Justice for All")) >= 0.9


def test_rejects_wrong_duration_cover_and_live():
    assert score_candidate(BLACKENED, cand("Metallica Blackened Intro | 1987 Demo version", ["Apollon"], 69, "video")) == 0
    assert score_candidate(BLACKENED, cand('Metallica - "Blackened" (Bass Cover)', ["Kello Gonzalez"], 401, "video")) < 0.72
    assert score_candidate(BLACKENED, cand("Blackened (Live)", ["Metallica"], 410)) < 0.72


def test_music_video_with_long_intro_is_rejected():
    track = {"title": "Song", "artists": ["Artist"], "duration_ms": 200_000}
    assert score_candidate(track, cand("Artist - Song (Official Video)", ["ArtistVEVO"], 230, "video")) < 0.72


def test_live_album_context():
    track = {"title": "2 Minutes to Midnight", "artists": ["Iron Maiden"], "album": "Live After Death", "duration_ms": 360_000}
    assert score_candidate(track, cand("2 Minutes to Midnight (Live)", ["Iron Maiden"], 361)) >= 0.72


def test_source_version_must_match():
    track = {"title": "Démons - live orchestral", "artists": ["Angèle", "Damso"], "duration_ms": 250_000}
    assert score_candidate(track, cand("Démons", ["Angèle", "Damso"], 251)) < 0.72
    assert score_candidate(track, cand("Démons (live orchestral)", ["Angèle", "Damso"], 251)) >= 0.72


def test_other_song_of_same_artist_is_rejected():
    aces = {"title": "Aces High - Live in Mexico City, Palacio de los Deportes, Mexico, September 2019",
            "artists": ["Iron Maiden"], "duration_ms": 298_789}
    assert score_candidate(aces, cand("Run To The Hills (Live in Mexico City, Palacio de los Deportes, Mexico, September 2019)", ["Iron Maiden"], 290)) == 0
    assert score_candidate(aces, cand("Aces High (Live in Mexico City, Palacio de los Deportes, Mexico, September 2019)", ["Iron Maiden"], 299)) >= 0.9
    plus_rien = {"title": "Plus rien (feat. 幾田りら)", "artists": ["Orelsan", "Lilas"], "duration_ms": 209_490}
    assert score_candidate(plus_rien, cand("Plus rien ne m'étonne", ["Orelsan"], 220)) == 0
    assert score_candidate(plus_rien, cand("Plus rien (feat. Lilas)", ["Orelsan"], 209)) >= 0.9
    vald = {"title": "Blauwburgwal", "artists": ["Vald"], "duration_ms": 147_554}
    assert score_candidate(vald, cand("Toujours pareil", ["Vald"], 148)) == 0


def test_video_title_with_artist_prefix():
    assert score_candidate(BLACKENED, cand("Metallica - Blackened (Remastered)", ["Metallica"], 403, "video")) >= 0.85
    assert score_candidate(BLACKENED, cand("Metallica Blackened", ["SomeChannel"], 403, "video")) >= 0.72


def test_without_duration_score_is_capped():
    track = {"title": "Blackened", "artists": ["Metallica"]}
    assert score_candidate(track, cand("Blackened", ["Metallica"], 402)) <= 0.95


def test_find_uses_isrc_then_text(monkeypatch):
    calls = []
    good = cand("Blackened", ["Metallica"], 402)
    bad = cand("Enter Sandman", ["Metallica"], 331)

    def fake_search(self, query, search_filter):
        calls.append((query, search_filter))
        return [bad] if query == "USXXX" else [bad, good]

    monkeypatch.setattr(YouTubeMatcher, "_search", fake_search)
    found = YouTubeMatcher(threshold=0.72).find({**BLACKENED, "isrc": "USXXX"})
    assert found and found[0].title == "Blackened"
    assert calls == [("USXXX", "songs"), ("Metallica Blackened", "songs")]
