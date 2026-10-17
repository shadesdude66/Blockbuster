"""Adding titles: OMDb search and manual entry."""

import curses

from . import omdb
from .db import DB, Movie
from .widgets import (
    accent_attr,
    choose_from_list,
    confirm,
    dim_attr,
    draw_box,
    flash,
    prompt,
    selected_attr,
)


# ------------------------------------------------------------- add movie --

def add_via_search(stdscr, db: DB) -> list[int]:
    """Search OMDb by title. Space marks multiple results to add them all
    at once; Enter with nothing marked adds just the highlighted one
    (same as before). Returns the ids of every movie actually added."""
    title = prompt(stdscr, "Search title")
    if not title:
        return []

    all_results: list[dict] = []
    total = 0
    page = 0
    marked: set[int] = set()
    idx = 0
    top = 0

    def fetch_page() -> bool:
        nonlocal page, total
        page += 1
        flash(stdscr, f"Searching OMDb (page {page})...")
        try:
            results, total = omdb.search(title, page=page)
        except omdb.OmdbError as e:
            flash(stdscr, f"Error: {e}")
            return False
        all_results.extend(results)
        return True

    if not fetch_page():
        return []
    if not all_results:
        flash(stdscr, "No results found.")
        return []

    chosen: list[dict] = []
    while True:
        has_more = len(all_results) < total
        rows: list = list(all_results) + (["more"] if has_more else [])
        idx = min(idx, len(rows) - 1)

        stdscr.clear()
        h, w = stdscr.getmaxyx()
        header = f"Results for '{title}' ({len(all_results)} of {total} loaded)"
        stdscr.addstr(0, 0, header[: w - 1], accent_attr() | curses.A_BOLD)
        draw_box(stdscr, 1, 0, h - 2, w, dim_attr())
        visible_rows = h - 4
        if idx < top:
            top = idx
        if idx >= top + visible_rows:
            top = idx - visible_rows + 1
        for row_num, i in enumerate(range(top, min(len(rows), top + visible_rows))):
            item = rows[i]
            if item == "more":
                line = "-- Load more results --"
            else:
                mark = "[x]" if i in marked else "[ ]"
                line = f"{mark} {item['title']} ({item['year']}) [{item['media_type'].capitalize()}]"
            attr = selected_attr() if i == idx else curses.A_NORMAL
            try:
                stdscr.addstr(row_num + 2, 2, line[: w - 3], attr)
            except curses.error:
                pass
        add_hint = f"Enter add {len(marked)} marked" if marked else "Enter add selected"
        footer = f"Up/Down move  Space mark  {add_hint}  Esc cancel"
        try:
            stdscr.addstr(h - 1, 0, footer[: w - 1], dim_attr())
        except curses.error:
            pass
        stdscr.refresh()

        ch = stdscr.getch()
        if ch in (curses.KEY_UP, ord("k")):
            idx = max(0, idx - 1)
        elif ch in (curses.KEY_DOWN, ord("j")):
            idx = min(len(rows) - 1, idx + 1)
        elif ch == ord(" "):
            if rows[idx] != "more":
                marked.symmetric_difference_update({idx})
                idx = min(idx + 1, len(rows) - 1)
        elif ch in (10, 13, curses.KEY_ENTER):
            if rows[idx] == "more":
                fetch_page()
                continue
            chosen = [all_results[i] for i in sorted(marked)] if marked else [rows[idx]]
            break
        elif ch == 27:
            return []

    added: list[tuple[dict, int]] = []
    for item in chosen:
        existing = db.conn.execute(
            "SELECT id FROM movies WHERE imdb_id = ?", (item["imdb_id"],)
        ).fetchone()
        if existing:
            flash(stdscr, f"'{item['title']}' is already in your list, skipping.")
            continue
        flash(stdscr, f"Fetching details for '{item['title']}'...")
        try:
            details = omdb.get_by_id(item["imdb_id"])
        except omdb.OmdbError as e:
            flash(stdscr, f"Error adding '{item['title']}': {e}")
            continue
        poster_path = omdb.download_poster(details.pop("poster_url", ""), details["imdb_id"])
        m = Movie(
            title=details["title"] or item["title"],
            year=details["year"],
            media_type=details["media_type"],
            runtime_minutes=details["runtime_minutes"],
            genre=details["genre"],
            director=details["director"],
            actors=details["actors"],
            plot=details["plot"],
            rated=details["rated"],
            imdb_rating=details["imdb_rating"],
            imdb_id=details["imdb_id"],
            poster_path=poster_path,
            status="watchlist",
        )
        added.append((item, db.add(m)))

    if not added:
        return []
    if len(added) == 1:
        flash(stdscr, f"Added '{added[0][0]['title']}'.")
    else:
        flash(stdscr, f"Added {len(added)} titles.")
    return [movie_id for _, movie_id in added]


def add_manually(stdscr, db: DB) -> int | None:
    type_choice = choose_from_list(stdscr, "Add as:", ["Movie", "Series"], lambda x: x)
    if not type_choice:
        return None
    media_type = type_choice.lower()
    title = prompt(stdscr, "Title")
    if not title:
        return None
    year = prompt(stdscr, "Year (optional)") or ""
    existing = db.conn.execute(
        "SELECT id FROM movies WHERE deleted_at IS NULL AND title = ? COLLATE NOCASE AND year = ?",
        (title, year),
    ).fetchone()
    if existing and not confirm(stdscr, f"'{title}' ({year or '?'}) is already in your library - add anyway?"):
        return None
    runtime_label = "Episode runtime in minutes (optional)" if media_type == "series" else "Runtime in minutes (optional)"
    runtime_str = prompt(stdscr, runtime_label) or ""
    runtime_minutes = int(runtime_str) if runtime_str.strip().isdigit() else None
    genre = prompt(stdscr, "Genre (optional)") or ""
    director = prompt(stdscr, "Director (optional)") or ""
    rated = prompt(stdscr, "Rating e.g. R, PG-13, TV-14 (optional)") or ""
    m = Movie(
        title=title,
        year=year,
        media_type=media_type,
        runtime_minutes=runtime_minutes,
        genre=genre,
        director=director,
        rated=rated,
        status="watchlist",
    )
    movie_id = db.add(m)
    flash(stdscr, f"Added '{title}'.")
    return movie_id
