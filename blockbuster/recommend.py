"""Local "similar to what you've liked" scoring - no network access.

Builds a taste profile from watched movies (weighted by your rating) and
scores every watchlist title against it by genre/director/cast overlap.
Pure stdlib, works entirely off the local database.
"""

from collections import Counter

from .db import DB

GENRE_WEIGHT = 1.0
DIRECTOR_WEIGHT = 3.0
ACTOR_WEIGHT = 1.5


def _split_list(s: str | None) -> list[str]:
    return [x.strip() for x in (s or "").split(",") if x.strip()]


def _taste_profile(watched_rows) -> tuple[Counter, Counter, Counter]:
    genre_c: Counter = Counter()
    director_c: Counter = Counter()
    actor_c: Counter = Counter()
    for m in watched_rows:
        weight = max((m["my_rating"] or 5.0) - 5.0, 0.5)
        for g in _split_list(m["genre"]):
            genre_c[g.lower()] += weight
        for d in _split_list(m["director"]):
            director_c[d.lower()] += weight
        for a in _split_list(m["actors"]):
            actor_c[a.lower()] += weight
    return genre_c, director_c, actor_c


def _score(m, genre_c: Counter, director_c: Counter, actor_c: Counter) -> tuple[float, list[str]]:
    score = 0.0
    reasons = []

    matched_genres = [g for g in _split_list(m["genre"]) if genre_c.get(g.lower())]
    if matched_genres:
        score += sum(genre_c[g.lower()] for g in matched_genres) * GENRE_WEIGHT
        reasons.append("genre: " + ", ".join(matched_genres))

    matched_directors = [d for d in _split_list(m["director"]) if director_c.get(d.lower())]
    if matched_directors:
        score += sum(director_c[d.lower()] for d in matched_directors) * DIRECTOR_WEIGHT
        reasons.append("director: " + ", ".join(matched_directors))

    matched_actors = [a for a in _split_list(m["actors"]) if actor_c.get(a.lower())]
    if matched_actors:
        score += sum(actor_c[a.lower()] for a in matched_actors) * ACTOR_WEIGHT
        reasons.append("cast: " + ", ".join(matched_actors[:3]))

    return score, reasons


def recommend_watchlist(db: DB, limit: int = 10) -> list[tuple]:
    """Return up to `limit` (row, score, reasons) tuples for watchlist
    titles most similar to your rated watched movies, highest score first.
    Empty if you have no rated watched movies or no scoring watchlist
    matches."""
    watched = [m for m in db.list(status="watched") if m["my_rating"] is not None]
    if not watched:
        return []
    genre_c, director_c, actor_c = _taste_profile(watched)

    scored = []
    for m in db.list(status="watchlist"):
        score, reasons = _score(m, genre_c, director_c, actor_c)
        if score > 0:
            scored.append((m, score, reasons))
    scored.sort(key=lambda t: t[1], reverse=True)
    return scored[:limit]
