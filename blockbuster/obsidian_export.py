"""Export the Blockbuster library to an Obsidian-compatible markdown vault.

Produces one note per movie/series (with YAML frontmatter for rating,
genre, director, tags, etc.), plus stub notes per director/actor, genre,
and collection/tag so Obsidian's backlinks and graph view have something
to connect, and a Stats.md mirroring the app's stats screen. This is a
one-shot snapshot, not a live sync - re-run it to refresh.
"""

import re
import shutil
from collections import Counter, defaultdict
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


def _link(folder: str, name: str) -> str:
    """A wikilink qualified by folder, so it resolves unambiguously even
    when a name collides across folders (e.g. a tag named the same as a
    genre) - Obsidian still renders it as just the note's name."""
    return f"[[{folder}/{_safe_name(name)}]]"


def export_vault(db: DB, dest: Path) -> dict:
    """Write the vault to `dest`, creating it if needed. Returns counts."""
    dest = Path(dest).expanduser()
    movies_dir = dest / "Movies"
    people_dir = dest / "People"
    genres_dir = dest / "Genres"
    collections_dir = dest / "Collections"
    posters_dir = dest / "Posters"
    movies_dir.mkdir(parents=True, exist_ok=True)
    people_dir.mkdir(parents=True, exist_ok=True)
    genres_dir.mkdir(parents=True, exist_ok=True)
    collections_dir.mkdir(parents=True, exist_ok=True)
    posters_dir.mkdir(parents=True, exist_ok=True)

    rows = db.list(sort="title")
    used_names: dict[str, int] = {}
    # name -> list of (role, movie note name)
    people: dict[str, list[tuple[str, str]]] = defaultdict(list)
    genres: dict[str, list[str]] = defaultdict(list)
    collections_map: dict[str, list[str]] = defaultdict(list)
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
        poster_link = None
        poster_src = config.poster_full_path(row["poster_path"])
        if poster_src and poster_src.exists():
            dest_poster = posters_dir / poster_src.name
            if not dest_poster.exists():
                shutil.copy2(poster_src, dest_poster)
            poster_link = f"Posters/{poster_src.name}"
            poster_embed = f"![[{poster_link}]]\n"

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
            "poster": f"[[{poster_link}]]" if poster_link else None,
            "genres": genre_list,
            "directors": directors,
            "actors": actors,
            "collections": tags,
            "tags": fm_tags,
        }

        body = [_frontmatter(fields), ""]
        if poster_embed:
            body.append(poster_embed)
        if row["plot"]:
            body.append(f"## Plot\n\n{row['plot']}\n")
        if directors:
            body.append("## Director\n\n" + ", ".join(_link("People", d) for d in directors) + "\n")
        if actors:
            body.append("## Cast\n\n" + ", ".join(_link("People", a) for a in actors) + "\n")
        if genre_list:
            body.append("## Genre\n\n" + ", ".join(_link("Genres", g) for g in genre_list) + "\n")
        if tags:
            body.append("## Collections\n\n" + ", ".join(_link("Collections", t) for t in tags) + "\n")
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
        for t in tags:
            collections_map[t].append(note_name)
        index_by_status[row["status"]].append((note_name, row["my_rating"]))

    for name, credits in people.items():
        note_name = _safe_name(name)
        lines = [_frontmatter({"tags": ["blockbuster/person"]}), "", "## Appears in", ""]
        lines += [f"- {_link('Movies', movie)} ({role})" for role, movie in credits]
        (people_dir / f"{note_name}.md").write_text("\n".join(lines).rstrip() + "\n")

    for name, movies in genres.items():
        note_name = _safe_name(name)
        lines = [_frontmatter({"tags": ["blockbuster/genre"]}), "", "## Titles", ""]
        lines += [f"- {_link('Movies', movie)}" for movie in movies]
        (genres_dir / f"{note_name}.md").write_text("\n".join(lines).rstrip() + "\n")

    for name, movies in collections_map.items():
        note_name = _safe_name(name)
        lines = [_frontmatter({"tags": ["blockbuster/collection"]}), "", "## Titles", ""]
        lines += [f"- {_link('Movies', movie)}" for movie in movies]
        (collections_dir / f"{note_name}.md").write_text("\n".join(lines).rstrip() + "\n")

    index_lines = ["# Blockbuster", ""]
    for status in ("watched", "watching", "watchlist"):
        items = index_by_status.get(status, [])
        if not items:
            continue
        items.sort(key=lambda t: (t[1] is None, -(t[1] or 0)))
        index_lines.append(f"## {status.capitalize()} ({len(items)})\n")
        index_lines += [f"- {_link('Movies', name)}" for name, _ in items]
        index_lines.append("")
    (dest / "Blockbuster.md").write_text("\n".join(index_lines).rstrip() + "\n")

    (dest / "Stats.md").write_text(_stats_note(rows, db))
    (dest / "Movies.base").write_text(_MOVIES_BASE)

    return {
        "movies": len(rows),
        "people": len(people),
        "genres": len(genres),
        "collections": len(collections_map),
    }


def _stats_note(rows, db: DB) -> str:
    """Mirror the app's `S` stats screen as a markdown note."""
    total = len(rows)
    series_count = sum(1 for r in rows if r["media_type"] == "series")
    movie_count = total - series_count
    watched = [r for r in rows if r["status"] == "watched"]
    watchlist_count = total - len(watched)
    total_minutes = sum(r["runtime_minutes"] or 0 for r in watched)
    hours, minutes = divmod(total_minutes, 60)

    my_ratings = [r["my_rating"] for r in rows if r["my_rating"] is not None]
    imdb_ratings = [r["imdb_rating"] for r in rows if r["imdb_rating"] is not None]
    avg_my = sum(my_ratings) / len(my_ratings) if my_ratings else None
    avg_imdb = sum(imdb_ratings) / len(imdb_ratings) if imdb_ratings else None
    total_rewatches = sum(r["rewatch_count"] or 0 for r in rows)
    trash_count = len(db.list_deleted())

    genre_counter: Counter = Counter()
    for r in rows:
        for g in _split_list(r["genre"]):
            genre_counter[g] += 1

    decade_counter: Counter = Counter()
    for r in rows:
        year_str = (r["year"] or "").strip()[:4]
        if year_str.isdigit():
            decade_counter[(int(year_str) // 10) * 10] += 1

    lines = [
        "# Stats",
        "",
        f"- **Total titles:** {total} ({len(watched)} watched, {watchlist_count} watchlist)",
        f"- **Movies / Series:** {movie_count} / {series_count}",
        f"- **Total watch time:** {hours}h {minutes}m",
        f"- **Avg my rating:** {f'{avg_my:.1f} / 10' if avg_my is not None else '-'}",
        f"- **Avg IMDb rating:** {f'{avg_imdb:.1f} / 10' if avg_imdb is not None else '-'}",
        f"- **Total rewatches:** {total_rewatches}",
        f"- **In trash:** {trash_count}",
        "",
    ]
    if genre_counter:
        lines.append("## Top genres\n")
        lines += [f"- {_link('Genres', g)}: {c}" for g, c in genre_counter.most_common(5)]
        lines.append("")
    if decade_counter:
        lines.append("## By decade\n")
        lines += [f"- {d}s: {c}" for d, c in sorted(decade_counter.items(), reverse=True)]
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# A Base scoped to the Movies/ folder, with a few pre-built table views
# (All, Watched, Watchlist, Top Rated, TV Shows, Movies Only) plus a
# Poster Wall cards gallery, over the frontmatter fields every movie
# note carries - lets Obsidian's Bases core plugin render/sort/filter
# the library as a spreadsheet instead of clicking through individual
# notes. Requires Obsidian 1.9+.
#
# Note: the `note.` prefix on every key below (rather than the bare
# property name shown in Obsidian's own docs example) is required for
# `displayName` to actually apply to table column headers - confirmed
# empirically against Obsidian 1.13.7, where the bare form is silently
# ignored.
_MOVIES_BASE = """\
filters:
  and:
    - file.inFolder("Movies")

properties:
  note.type:
    displayName: Type
  note.status:
    displayName: Status
  note.my_rating:
    displayName: My Rating
  note.my_rank:
    displayName: Rank
  note.imdb_rating:
    displayName: IMDb
  note.rated:
    displayName: Rated
  note.runtime_minutes:
    displayName: Runtime (min)
  note.total_seasons:
    displayName: Seasons
  note.watched_date:
    displayName: Watched
  note.rewatch_count:
    displayName: Rewatches
  note.genres:
    displayName: Genre
  note.directors:
    displayName: Director
  note.actors:
    displayName: Cast
  note.collections:
    displayName: Collections

views:
  - type: cards
    name: Poster Wall
    filters:
      and:
        - poster != null
    image: note.poster
    order:
      - file.name
      - note.status
      - note.my_rating

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
