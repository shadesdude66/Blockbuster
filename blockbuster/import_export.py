"""CSV/JSON/Obsidian export and CSV/JSON/Letterboxd import."""

import csv
import json
from datetime import datetime
from pathlib import Path

from . import config
from .db import DB, Movie
from .widgets import choose_from_list, flash, prompt


# -------------------------------------------------------------- export --

EXPORT_FIELDS = [
    "id", "title", "year", "media_type", "rated", "genre",
    "director", "actors", "plot", "runtime_minutes", "imdb_rating", "my_rating", "my_rank",
    "status", "watched_date", "rewatch_count", "notes", "tags", "formats", "added_at",
]


def export_movies(stdscr, db: DB) -> None:
    """Export the active (non-deleted) collection to a CSV or JSON file
    under config.EXPORT_DIR, or to an Obsidian vault directory."""
    fmt = choose_from_list(stdscr, "Export format:", ["CSV", "JSON", "Obsidian vault"], lambda x: x)
    if not fmt:
        return
    if fmt == "Obsidian vault":
        _export_obsidian(stdscr, db)
        return
    movies = db.list(sort="added")
    config.ensure_dirs()
    config.EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")

    rows = []
    for m in movies:
        row = {k: m[k] for k in EXPORT_FIELDS if k not in ("tags", "formats")}
        row["tags"] = ", ".join(db.get_tags_for_movie(m["id"]))
        row["formats"] = ", ".join(db.get_formats_for_movie(m["id"]))
        rows.append(row)

    if fmt == "CSV":
        dest = config.EXPORT_DIR / f"movies-{ts}.csv"
        with dest.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=EXPORT_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
    else:
        dest = config.EXPORT_DIR / f"movies-{ts}.json"
        dest.write_text(json.dumps(rows, indent=2))

    flash(stdscr, f"Exported {len(rows)} movies to {dest}")


def _export_obsidian(stdscr, db: DB) -> None:
    """In-app entry point for the same vault export as
    `blockbuster --export-obsidian DIR` (see obsidian_export.export_vault)."""
    default_dir = str(config.DATA_DIR / "obsidian-vault")
    path_str = prompt(stdscr, "Obsidian vault directory", default_dir)
    if not path_str:
        return
    dest = Path(path_str).expanduser()
    flash(stdscr, f"Exporting Obsidian vault to {dest}...")
    from . import obsidian_export

    try:
        counts = obsidian_export.export_vault(db, dest)
    except OSError as e:
        flash(stdscr, f"Failed to export vault: {e}")
        return
    flash(
        stdscr,
        f"Exported {counts['movies']} titles, {counts['people']} people, "
        f"{counts['genres']} genres, {counts['collections']} collections, {counts['formats']} formats to {dest}",
    )


def _read_import_rows(path: Path) -> list[dict]:
    """Parse a CSV or JSON file (matching the export schema) into row dicts."""
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text())
        return [data] if isinstance(data, dict) else data
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def _is_letterboxd_export(rows: list[dict]) -> bool:
    return bool(rows) and "Letterboxd URI" in rows[0]


def _normalize_letterboxd_rows(rows: list[dict], path: Path) -> list[dict]:
    """Map a Letterboxd data-export CSV (watched.csv, ratings.csv,
    diary.csv, or watchlist.csv - from https://letterboxd.com/settings/data/)
    onto this app's import row schema, so it flows through the same
    dedup/insert logic as a native export. Letterboxd's 0.5-5 star
    ratings are scaled to this app's 0-10 scale. A diary.csv row marked
    'Rewatch' logs a rewatch on the existing title instead of being
    skipped as a plain duplicate (see import_movies)."""
    is_watchlist = "watchlist" in path.stem.lower()
    out = []
    for row in rows:
        rating_raw = (row.get("Rating") or "").strip()
        my_rating = ""
        if rating_raw:
            try:
                my_rating = str(float(rating_raw) * 2)
            except ValueError:
                my_rating = ""
        watched_date = (row.get("Watched Date") or row.get("Date") or "").strip()
        out.append(
            {
                "title": row.get("Name", ""),
                "year": row.get("Year", ""),
                "media_type": "movie",
                "status": "watchlist" if is_watchlist else "watched",
                "watched_date": "" if is_watchlist else watched_date,
                "my_rating": my_rating,
                "tags": row.get("Tags", ""),
                "_rewatch": "1" if (row.get("Rewatch") or "").strip().lower() == "yes" else "",
            }
        )
    return out


def _row_int(row: dict, key: str) -> int | None:
    val = (row.get(key) or "").strip()
    return int(val) if val.lstrip("-").isdigit() else None


def _row_float(row: dict, key: str) -> float | None:
    val = (row.get(key) or "").strip()
    try:
        return float(val) if val else None
    except ValueError:
        return None


def import_movies(stdscr, db: DB) -> None:
    """Import movies/series from a CSV or JSON file matching this app's
    export schema (see EXPORT_FIELDS) - either a file previously produced
    by 'X', or a hand-built file following the same column names. Rows are
    matched against the existing collection by imdb_id (or by title+year
    when imdb_id is blank) and skipped if already present."""
    config.ensure_dirs()
    candidates = sorted(
        (*config.EXPORT_DIR.glob("*.csv"), *config.EXPORT_DIR.glob("*.json")),
        key=lambda p: p.stat().st_mtime, reverse=True,
    )
    items: list[dict] = [{"path": p} for p in candidates]
    items.append({"custom": True})
    chosen = choose_from_list(
        stdscr, "Import from:", items,
        lambda it: "-- Enter a file path --" if it.get("custom") else it["path"].name,
    )
    if not chosen:
        return
    if chosen.get("custom"):
        path_str = prompt(stdscr, "File path to import (CSV or JSON)")
        if not path_str:
            return
        path = Path(path_str).expanduser()
    else:
        path = chosen["path"]
    if not path.exists():
        flash(stdscr, f"File not found: {path}")
        return

    flash(stdscr, f"Reading {path.name}...")
    try:
        rows = _read_import_rows(path)
    except (OSError, json.JSONDecodeError, csv.Error) as e:
        flash(stdscr, f"Failed to read file: {e}")
        return
    if not rows:
        flash(stdscr, "No rows found to import.")
        return
    if _is_letterboxd_export(rows):
        rows = _normalize_letterboxd_rows(rows, path)
        flash(stdscr, f"Detected a Letterboxd export ({len(rows)} rows).")

    existing_imdb_ids = {
        r["imdb_id"] for r in db.conn.execute(
            "SELECT imdb_id FROM movies WHERE imdb_id IS NOT NULL AND imdb_id != ''"
        )
    }
    existing_title_years = {
        (r["title"].strip().lower(), (r["year"] or "").strip())
        for r in db.conn.execute("SELECT title, year FROM movies")
    }

    imported = skipped = 0
    for row in rows:
        title = (row.get("title") or "").strip()
        if not title:
            skipped += 1
            continue
        imdb_id = (row.get("imdb_id") or "").strip()
        year = (row.get("year") or "").strip()
        if imdb_id and imdb_id in existing_imdb_ids:
            skipped += 1
            continue
        if not imdb_id and (title.lower(), year) in existing_title_years:
            if (row.get("_rewatch") or "").strip() == "1":
                existing_row = db.conn.execute(
                    "SELECT id FROM movies WHERE title = ? COLLATE NOCASE AND year = ? AND deleted_at IS NULL",
                    (title, year),
                ).fetchone()
                if existing_row:
                    db.log_rewatch(existing_row["id"], (row.get("watched_date") or "").strip() or None)
            skipped += 1
            continue

        media_type = (row.get("media_type") or "movie").strip().lower()
        if media_type not in ("movie", "series"):
            media_type = "movie"
        status = (row.get("status") or "watchlist").strip().lower()
        if status not in ("watched", "watchlist"):
            status = "watchlist"

        m = Movie(
            title=title,
            year=year,
            media_type=media_type,
            runtime_minutes=_row_int(row, "runtime_minutes"),
            genre=(row.get("genre") or ""),
            director=(row.get("director") or ""),
            actors=(row.get("actors") or ""),
            plot=(row.get("plot") or ""),
            rated=(row.get("rated") or ""),
            imdb_rating=_row_float(row, "imdb_rating"),
            imdb_id=imdb_id,
            my_rating=_row_float(row, "my_rating"),
            my_rank=_row_int(row, "my_rank"),
            status=status,
            watched_date=(row.get("watched_date") or "").strip() or None,
            rewatch_count=_row_int(row, "rewatch_count") or 0,
            notes=(row.get("notes") or ""),
        )
        movie_id = db.add(m)
        tags = [t.strip() for t in (row.get("tags") or "").split(",") if t.strip()]
        if tags:
            db.set_tags_for_movie(movie_id, tags)
        formats = [f.strip() for f in (row.get("formats") or "").split(",") if f.strip()]
        if formats:
            db.set_formats_for_movie(movie_id, formats)
        if imdb_id:
            existing_imdb_ids.add(imdb_id)
        existing_title_years.add((title.lower(), year))
        imported += 1

    flash(stdscr, f"Imported {imported} title(s), skipped {skipped} duplicate/invalid row(s).")
