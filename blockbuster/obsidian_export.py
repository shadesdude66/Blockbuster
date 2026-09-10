"""Export the Blockbuster library to an Obsidian-compatible markdown vault.

Produces one note per movie/series (with YAML frontmatter for rating,
genre, director, tags, etc.), plus stub notes per director/actor and per
genre so Obsidian's backlinks and graph view have something to connect.
This is a one-shot snapshot, not a live sync - re-run it to refresh.
"""

import re
import shutil
from collections import defaultdict
from pathlib import Path

from . import config
from .db import DB

_INVALID_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|#^\[\]]')


def _safe_name(name: str) -> str:
    name = _INVALID_FILENAME_CHARS.sub("", name).strip()
    return name or "Untitled"


def _split_list(value: str | None) -> list[str]:
    if not value or value == "N/A":
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def _yaml_str(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _yaml_value(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return _yaml_str(str(value))


def _frontmatter(fields: dict) -> str:
    lines = ["---"]
    for key, value in fields.items():
        if isinstance(value, list):
            if not value:
                lines.append(f"{key}: []")
            else:
                lines.append(f"{key}:")
                lines.extend(f"  - {_yaml_str(v)}" for v in value)
        else:
            lines.append(f"{key}: {_yaml_value(value)}")
    lines.append("---")
    return "\n".join(lines)


def _unique_name(base: str, used: dict[str, int]) -> str:
    n = used.get(base, 0)
    used[base] = n + 1
    return base if n == 0 else f"{base} ({n + 1})"


def export_vault(db: DB, dest: Path) -> dict:
    """Write the vault to `dest`, creating it if needed. Returns counts."""
    dest = Path(dest).expanduser()
    movies_dir = dest / "Movies"
    people_dir = dest / "People"
    genres_dir = dest / "Genres"
    posters_dir = dest / "Posters"
    movies_dir.mkdir(parents=True, exist_ok=True)
    people_dir.mkdir(parents=True, exist_ok=True)
    genres_dir.mkdir(parents=True, exist_ok=True)
    posters_dir.mkdir(parents=True, exist_ok=True)

    rows = db.list(sort="title")
    used_names: dict[str, int] = {}
    # name -> list of (role, movie note name)
    people: dict[str, list[tuple[str, str]]] = defaultdict(list)
    genres: dict[str, list[str]] = defaultdict(list)
    index_by_status: dict[str, list[tuple[str, str]]] = defaultdict(list)

    for row in rows:
        title = row["title"] or "Untitled"
        year = row["year"] or ""
        base = _safe_name(f"{title} ({year})" if year else title)
        note_name = _unique_name(base, used_names)

        directors = _split_list(row["director"])
        actors = _split_list(row["actors"])
        genre_list = _split_list(row["genre"])
        tags = db.get_tags_for_movie(row["id"])
        rewatches = db.get_rewatch_history(row["id"])

        poster_embed = ""
        poster_src = config.poster_full_path(row["poster_path"])
        if poster_src and poster_src.exists():
            dest_poster = posters_dir / poster_src.name
            if not dest_poster.exists():
                shutil.copy2(poster_src, dest_poster)
            poster_embed = f"![[Posters/{poster_src.name}]]\n"

        fm_tags = [
            "blockbuster/series" if row["media_type"] == "series" else "blockbuster/movie",
            f"blockbuster/{row['status']}",
        ] + [f"blockbuster/{t.replace(' ', '-')}" for t in tags]

        fields = {
            "title": title,
            "year": year,
            "type": row["media_type"],
            "status": row["status"],
            "my_rating": row["my_rating"],
            "my_rank": row["my_rank"],
            "imdb_rating": row["imdb_rating"],
            "rated": row["rated"] or None,
            "runtime_minutes": row["runtime_minutes"],
            "total_seasons": row["total_seasons"],
            "current_season": row["current_season"],
            "watched_date": row["watched_date"],
            "rewatch_count": row["rewatch_count"],
            "genres": genre_list,
            "directors": directors,
            "actors": actors,
            "tags": fm_tags,
        }

        body = [_frontmatter(fields), ""]
        if poster_embed:
            body.append(poster_embed)
        if row["plot"]:
            body.append(f"## Plot\n\n{row['plot']}\n")
        if directors:
            body.append("## Director\n\n" + ", ".join(f"[[{d}]]" for d in directors) + "\n")
        if actors:
            body.append("## Cast\n\n" + ", ".join(f"[[{a}]]" for a in actors) + "\n")
        if genre_list:
            body.append("## Genre\n\n" + ", ".join(f"[[{g}]]" for g in genre_list) + "\n")
        if row["notes"]:
            body.append(f"## Notes\n\n{row['notes']}\n")
        if rewatches:
            body.append("## Rewatch history\n\n" + "\n".join(f"- {d}" for d in rewatches) + "\n")

        (movies_dir / f"{note_name}.md").write_text("\n".join(body).rstrip() + "\n")

        for d in directors:
            people[d].append(("Director", note_name))
        for a in actors:
            people[a].append(("Actor", note_name))
        for g in genre_list:
            genres[g].append(note_name)
        index_by_status[row["status"]].append((note_name, row["my_rating"]))

    for name, credits in people.items():
        note_name = _safe_name(name)
        lines = [_frontmatter({"tags": ["blockbuster/person"]}), "", "## Appears in", ""]
        lines += [f"- [[{movie}]] ({role})" for role, movie in credits]
        (people_dir / f"{note_name}.md").write_text("\n".join(lines).rstrip() + "\n")

    for name, movies in genres.items():
        note_name = _safe_name(name)
        lines = [_frontmatter({"tags": ["blockbuster/genre"]}), "", "## Titles", ""]
        lines += [f"- [[{movie}]]" for movie in movies]
        (genres_dir / f"{note_name}.md").write_text("\n".join(lines).rstrip() + "\n")

    index_lines = ["# Blockbuster", ""]
    for status in ("watched", "watching", "watchlist"):
        items = index_by_status.get(status, [])
        if not items:
            continue
        items.sort(key=lambda t: (t[1] is None, -(t[1] or 0)))
        index_lines.append(f"## {status.capitalize()} ({len(items)})\n")
        index_lines += [f"- [[{name}]]" for name, _ in items]
        index_lines.append("")
    (dest / "Blockbuster.md").write_text("\n".join(index_lines).rstrip() + "\n")

    (dest / "Movies.base").write_text(_MOVIES_BASE)

    return {"movies": len(rows), "people": len(people), "genres": len(genres)}


# A Base scoped to the Movies/ folder, with a few pre-built table views
# (All, Watched, Watchlist, Top Rated, TV Shows, Movies Only) over the
# frontmatter fields every movie note carries - lets Obsidian's Bases
# core plugin render/sort/filter the library as a spreadsheet instead
# of clicking through individual notes. Requires Obsidian 1.9+.
_MOVIES_BASE = """\
filters:
  and:
    - file.inFolder("Movies")

properties:
  type:
    displayName: Type
  status:
    displayName: Status
  my_rating:
    displayName: My Rating
  my_rank:
    displayName: Rank
  imdb_rating:
    displayName: IMDb
  rated:
    displayName: Rated
  runtime_minutes:
    displayName: Runtime (min)
  total_seasons:
    displayName: Seasons
  watched_date:
    displayName: Watched
  rewatch_count:
    displayName: Rewatches
  genres:
    displayName: Genre
  directors:
    displayName: Director
  actors:
    displayName: Cast

views:
  - type: table
    name: All
    order:
      - file.name
      - note.type
      - note.status
      - note.my_rating
      - note.imdb_rating
      - note.my_rank
      - note.genres
      - note.watched_date
      - note.runtime_minutes
    sort:
      - property: note.my_rank
        direction: ASC

  - type: table
    name: Watched
    filters:
      and:
        - status == "watched"
    order:
      - file.name
      - note.my_rating
      - note.imdb_rating
      - note.watched_date
      - note.rewatch_count
      - note.genres
    sort:
      - property: note.watched_date
        direction: DESC

  - type: table
    name: Watchlist
    filters:
      and:
        - status == "watchlist"
    order:
      - file.name
      - note.imdb_rating
      - note.genres
      - note.runtime_minutes
      - note.total_seasons
    sort:
      - property: note.imdb_rating
        direction: DESC

  - type: table
    name: Top Rated
    filters:
      and:
        - my_rating != null
    order:
      - file.name
      - note.my_rating
      - note.my_rank
      - note.genres
      - note.watched_date
    sort:
      - property: note.my_rating
        direction: DESC

  - type: table
    name: TV Shows
    filters:
      and:
        - type == "series"
    order:
      - file.name
      - note.status
      - note.my_rating
      - note.total_seasons
      - note.watched_date
    sort:
      - property: file.name
        direction: ASC

  - type: table
    name: Movies Only
    filters:
      and:
        - type == "movie"
    order:
      - file.name
      - note.status
      - note.my_rating
      - note.imdb_rating
      - note.runtime_minutes
      - note.watched_date
    sort:
      - property: note.my_rank
        direction: ASC
"""
