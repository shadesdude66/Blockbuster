"""Fill in missing OMDb metadata (genre, director, cast, plot, poster,
...) on titles that were imported or added without it - e.g. from a
Letterboxd export, which carries none of those fields.

Only empty fields are ever filled: ratings, dates, notes, and anything
already set (by hand or by an earlier fetch) are left alone.
"""

import sqlite3

from . import omdb
from .db import DB

# Fields copied from OMDb's details when the title's own value is empty.
FILL_FIELDS = (
    "year", "runtime_minutes", "genre", "director", "actors", "plot",
    "rated", "imdb_rating", "imdb_id",
)


def missing_info_ids(db: DB) -> list[int]:
    """Ids of live titles that look like they never got OMDb metadata:
    no IMDb id (so never matched), no genre, or no cached poster."""
    rows = db.conn.execute(
        "SELECT id FROM movies WHERE deleted_at IS NULL AND ("
        " imdb_id IS NULL OR imdb_id = ''"
        " OR genre IS NULL OR genre = ''"
        " OR poster_path IS NULL OR poster_path = '')"
        " ORDER BY title COLLATE NOCASE"
    ).fetchall()
    return [r["id"] for r in rows]


def norm_title(title: str) -> str:
    """Title folded for loose comparison: case and punctuation ignored."""
    return "".join(ch for ch in title.casefold() if ch.isalnum())


def _year(value: str | None) -> str:
    # Series years look like "2019–2022"; compare on the start year.
    return (value or "").strip()[:4]


def library_index(db: DB) -> dict[str, list[tuple[str, int]]]:
    """Live titles keyed by norm_title, each with its (start year, id)
    entries - year "" for a title with no year."""
    index: dict[str, list[tuple[str, int]]] = {}
    for r in db.conn.execute("SELECT id, title, year FROM movies WHERE deleted_at IS NULL"):
        index.setdefault(norm_title(r["title"]), []).append((_year(r["year"]), r["id"]))
    return index


def find_in_library(index: dict[str, list[tuple[str, int]]], title: str, year: str = "") -> list[int]:
    """Ids of the library_index entries matching a title (and year, if
    given). A year only rules out entries that have a different year."""
    entries = index.get(norm_title(title), [])
    return [movie_id for y, movie_id in entries if not year or not y or y == year]


def find_match(row: sqlite3.Row | dict) -> dict | None:
    """Full OMDb details for this title, or None if there's no confident
    automatic match (left for the user to pick by hand).

    A title with an IMDb id is fetched by id. Otherwise OMDb's exact-title
    lookup is accepted when its year agrees (or, with no year on our side,
    when the title matches exactly); failing that, a search restricted to
    the year is accepted only if it turns up exactly one title.
    """
    if row["imdb_id"]:
        return omdb.get_by_id(row["imdb_id"])
    title = row["title"]
    year = _year(row["year"])
    details = omdb.lookup(title, year or None)
    if details:
        if year and _year(details["year"]) == year:
            return details
        if not year and norm_title(details["title"]) == norm_title(title):
            return details
    if not year:
        return None
    try:
        results, _ = omdb.search(title, year=year)
    except omdb.OmdbError as e:
        if omdb.is_not_found(e):
            return None
        raise
    hits = [r for r in results if _year(r["year"]) == year]
    if len(hits) != 1:
        return None
    return omdb.get_by_id(hits[0]["imdb_id"])


def apply_details(db: DB, row: sqlite3.Row, details: dict) -> tuple[list[str], str | None]:
    """Copy OMDb details into this title's empty fields (and download its
    poster if it has none). Returns (names of the fields filled, title of
    another library entry that already has this IMDb id, if any - in which
    case the IMDb id itself is left unset rather than duplicated)."""
    updates = {
        f: details[f] for f in FILL_FIELDS
        if row[f] in (None, "") and details.get(f) not in (None, "")
    }
    clash_title = None
    if "imdb_id" in updates:
        clash = db.conn.execute(
            "SELECT title FROM movies WHERE imdb_id = ? AND id != ? AND deleted_at IS NULL",
            (updates["imdb_id"], row["id"]),
        ).fetchone()
        if clash:
            clash_title = clash["title"]
            del updates["imdb_id"]
    if not row["poster_path"] and details.get("imdb_id"):
        poster = omdb.download_poster(details.get("poster_url", ""), details["imdb_id"])
        if poster:
            updates["poster_path"] = poster
    db.update(row["id"], **updates)
    return list(updates), clash_title
