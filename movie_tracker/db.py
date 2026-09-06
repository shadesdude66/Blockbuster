"""SQLite storage layer for movie tracker."""

import os
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS movies (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    title           TEXT NOT NULL,
    year            TEXT,
    media_type      TEXT NOT NULL DEFAULT 'movie',
    total_seasons   INTEGER,
    runtime_minutes INTEGER,
    genre           TEXT,
    director        TEXT,
    actors          TEXT,
    plot            TEXT,
    rated           TEXT,
    imdb_rating     REAL,
    imdb_id         TEXT,
    poster_path     TEXT,
    my_rating       REAL,
    my_rank         INTEGER,
    status          TEXT NOT NULL DEFAULT 'watchlist',
    watched_date    TEXT,
    rewatch_count   INTEGER NOT NULL DEFAULT 0,
    notes           TEXT,
    added_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tags (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE
);

CREATE TABLE IF NOT EXISTS movie_tags (
    movie_id INTEGER NOT NULL REFERENCES movies(id) ON DELETE CASCADE,
    tag_id   INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (movie_id, tag_id)
);
"""


@dataclass
class Movie:
    id: int | None = None
    title: str = ""
    year: str = ""
    media_type: str = "movie"
    total_seasons: int | None = None
    current_season: int | None = None
    runtime_minutes: int | None = None
    genre: str = ""
    director: str = ""
    actors: str = ""
    plot: str = ""
    rated: str = ""
    imdb_rating: float | None = None
    imdb_id: str = ""
    poster_path: str | None = None
    my_rating: float | None = None
    my_rank: int | None = None
    status: str = "watchlist"
    watched_date: str | None = None
    rewatch_count: int = 0
    notes: str = ""
    added_at: str = field(default_factory=lambda: date.today().isoformat())
    deleted_at: str | None = None


SORT_FIELDS = {
    "rank": ("my_rank IS NULL, my_rank", "asc"),
    "rating": ("my_rating IS NULL, my_rating", "desc"),
    "imdb": ("imdb_rating IS NULL, imdb_rating", "desc"),
    "title": ("title COLLATE NOCASE", "asc"),
    "watched": ("watched_date IS NULL, watched_date", "desc"),
    "runtime": ("runtime_minutes IS NULL, runtime_minutes", "asc"),
    "added": ("added_at", "desc"),
    "year": ("year = '', year", "asc"),
}


class DB:
    def __init__(self, path=None):
        config.ensure_dirs()
        self.conn = sqlite3.connect(path or config.DB_PATH)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        self._migrate()

    def _migrate(self) -> None:
        cols = {row["name"] for row in self.conn.execute("PRAGMA table_info(movies)")}
        if "rated" not in cols:
            self.conn.execute("ALTER TABLE movies ADD COLUMN rated TEXT")
            self.conn.commit()
        if "deleted_at" not in cols:
            self.conn.execute("ALTER TABLE movies ADD COLUMN deleted_at TEXT")
            self.conn.commit()
        if "media_type" not in cols:
            self.conn.execute("ALTER TABLE movies ADD COLUMN media_type TEXT NOT NULL DEFAULT 'movie'")
            self.conn.commit()
        if "total_seasons" not in cols:
            self.conn.execute("ALTER TABLE movies ADD COLUMN total_seasons INTEGER")
            self.conn.commit()
        if "current_season" not in cols:
            self.conn.execute("ALTER TABLE movies ADD COLUMN current_season INTEGER")
            self.conn.commit()
        self._normalize_poster_paths()

    def _normalize_poster_paths(self) -> None:
        """Rewrite any absolute poster_path values to a bare filename.

        Older rows stored a full path baked in from whatever $HOME the
        poster was first downloaded under, which breaks after moving
        machines or restoring the DB elsewhere. New rows already store
        just the filename (see omdb.download_poster).
        """
        rows = self.conn.execute(
            "SELECT id, poster_path FROM movies WHERE poster_path LIKE '/%'"
        ).fetchall()
        for row in rows:
            filename = os.path.basename(row["poster_path"])
            self.conn.execute(
                "UPDATE movies SET poster_path = ? WHERE id = ?", (filename, row["id"])
            )
        if rows:
            self.conn.commit()

    def close(self):
        self.conn.close()

    def add(self, m: Movie) -> int:
        cur = self.conn.execute(
            """INSERT INTO movies
               (title, year, media_type, total_seasons, current_season, runtime_minutes,
                genre, director,
                actors, plot, rated, imdb_rating, imdb_id, poster_path, my_rating, my_rank,
                status, watched_date, rewatch_count, notes, added_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                m.title, m.year, m.media_type, m.total_seasons, m.current_season,
                m.runtime_minutes, m.genre,
                m.director, m.actors, m.plot, m.rated, m.imdb_rating, m.imdb_id, m.poster_path,
                m.my_rating, m.my_rank, m.status, m.watched_date, m.rewatch_count, m.notes,
                m.added_at,
            ),
        )
        self.conn.commit()
        return cur.lastrowid

    def update(self, movie_id: int, **fields) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k} = ?" for k in fields)
        self.conn.execute(
            f"UPDATE movies SET {cols} WHERE id = ?",
            (*fields.values(), movie_id),
        )
        self.conn.commit()

    def delete(self, movie_id: int) -> None:
        """Soft-delete: hide the movie but keep its row (and tags) around
        so it can be restored from the trash screen or via 'undo'."""
        self.delete_many([movie_id])

    def delete_many(self, movie_ids: list[int]) -> None:
        """Soft-delete several movies as one batch (same timestamp), so
        'undo' can restore the whole batch as a unit."""
        ts = datetime.now().isoformat(timespec="seconds")
        for movie_id in movie_ids:
            self.conn.execute(
                "UPDATE movies SET deleted_at = ? WHERE id = ?", (ts, movie_id)
            )
        self.conn.commit()

    def restore(self, movie_id: int) -> None:
        self.conn.execute("UPDATE movies SET deleted_at = NULL WHERE id = ?", (movie_id,))
        self.conn.commit()

    def undo_last_delete(self) -> list[sqlite3.Row]:
        """Restore every movie deleted in the most recent delete action -
        a single delete, or a whole delete_many() batch, since those share
        one timestamp. Returns the restored rows."""
        last = self.last_deleted()
        if last is None:
            return []
        ts = last["deleted_at"]
        rows = self.conn.execute(
            "SELECT * FROM movies WHERE deleted_at = ? ORDER BY id", (ts,)
        ).fetchall()
        self.conn.execute("UPDATE movies SET deleted_at = NULL WHERE deleted_at = ?", (ts,))
        self.conn.commit()
        return rows

    def purge(self, movie_id: int) -> None:
        """Permanently delete a movie (and its tag associations)."""
        self.conn.execute("DELETE FROM movies WHERE id = ?", (movie_id,))
        self.conn.commit()

    def get(self, movie_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM movies WHERE id = ?", (movie_id,)
        ).fetchone()

    def list(self, sort: str = "rank", query: str = "", status: str | None = None,
              tag: str | None = None, media_type: str | None = None) -> list[sqlite3.Row]:
        col, direction = SORT_FIELDS.get(sort, SORT_FIELDS["rank"])
        sql = "SELECT movies.* FROM movies"
        params: list = []
        if tag:
            sql += (
                " JOIN movie_tags ON movie_tags.movie_id = movies.id"
                " JOIN tags ON tags.id = movie_tags.tag_id AND tags.name = ?"
            )
            params.append(tag)
        sql += " WHERE movies.deleted_at IS NULL"
        if query:
            sql += (
                " AND (movies.title LIKE ? OR movies.genre LIKE ?"
                " OR movies.director LIKE ? OR movies.actors LIKE ?)"
            )
            like = f"%{query}%"
            params.extend([like, like, like, like])
        if status:
            sql += " AND movies.status = ?"
            params.append(status)
        if media_type:
            sql += " AND movies.media_type = ?"
            params.append(media_type)
        sql += f" ORDER BY {col} {direction}"
        return self.conn.execute(sql, params).fetchall()

    def list_deleted(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM movies WHERE deleted_at IS NOT NULL ORDER BY deleted_at DESC"
        ).fetchall()

    def last_deleted(self) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM movies WHERE deleted_at IS NOT NULL "
            "ORDER BY deleted_at DESC LIMIT 1"
        ).fetchone()

    # ------------------------------------------------------------- tags --

    def all_tags(self) -> list[str]:
        return [row["name"] for row in self.conn.execute("SELECT name FROM tags ORDER BY name COLLATE NOCASE")]

    def get_tags_for_movie(self, movie_id: int) -> list[str]:
        rows = self.conn.execute(
            "SELECT tags.name FROM tags "
            "JOIN movie_tags ON movie_tags.tag_id = tags.id "
            "WHERE movie_tags.movie_id = ? ORDER BY tags.name COLLATE NOCASE",
            (movie_id,),
        )
        return [row["name"] for row in rows]

    def set_tags_for_movie(self, movie_id: int, names: list[str]) -> None:
        """Replace the full set of tags on a movie with `names`."""
        self.conn.execute("DELETE FROM movie_tags WHERE movie_id = ?", (movie_id,))
        for name in names:
            name = name.strip()
            if not name:
                continue
            self.conn.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (name,))
            tag_id = self.conn.execute(
                "SELECT id FROM tags WHERE name = ? COLLATE NOCASE", (name,)
            ).fetchone()["id"]
            self.conn.execute(
                "INSERT OR IGNORE INTO movie_tags (movie_id, tag_id) VALUES (?, ?)",
                (movie_id, tag_id),
            )
        self.conn.commit()

    def add_tags_to_movies(self, movie_ids: list[int], names: list[str]) -> None:
        """Add each tag in `names` to each movie in `movie_ids`, on top of
        whatever tags they already have (unlike set_tags_for_movie, this
        doesn't remove anything) - for bulk-tagging from the list screen."""
        for name in names:
            name = name.strip()
            if not name:
                continue
            self.conn.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (name,))
            tag_id = self.conn.execute(
                "SELECT id FROM tags WHERE name = ? COLLATE NOCASE", (name,)
            ).fetchone()["id"]
            for movie_id in movie_ids:
                self.conn.execute(
                    "INSERT OR IGNORE INTO movie_tags (movie_id, tag_id) VALUES (?, ?)",
                    (movie_id, tag_id),
                )
        self.conn.commit()
