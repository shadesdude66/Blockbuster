"""Adding titles (OMDb search, manual entry) and filling in missing OMDb
info on titles already in the library."""

import curses

from . import config, metadata, omdb
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
    show_text_screen,
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


# --------------------------------------------------------- fill in info --

FIELD_LABELS = {
    "year": "year", "runtime_minutes": "runtime", "genre": "genre",
    "director": "director", "actors": "cast", "plot": "plot",
    "rated": "content rating", "imdb_rating": "IMDb rating",
    "imdb_id": "IMDb id", "poster_path": "poster",
}
FILL_SUMMARY_MAX_LISTED = 25


def require_api_key(stdscr) -> bool:
    if config.get_api_key():
        return True
    flash(stdscr, "Needs an OMDb API key - press 'K' in the list screen to set one.")
    return False


def _fill_result_message(filled: list[str], clash_title: str | None) -> str:
    if clash_title:
        msg = f"Already in your library as '{clash_title}' - IMDb id not linked."
        if filled:
            msg += " Filled in: " + ", ".join(FIELD_LABELS.get(f, f) for f in filled) + "."
        return msg
    if not filled:
        return "Nothing missing - every field OMDb has is already filled in."
    return "Filled in " + ", ".join(FIELD_LABELS.get(f, f) for f in filled) + "."


def fill_info_for_title(stdscr, db: DB, movie_id: int) -> None:
    """Fill in one title's empty fields from OMDb. A title already linked
    to an IMDb id is fetched directly; otherwise the user picks the right
    match from an OMDb search (same-year results listed first), refining
    the search text if nothing turns up."""
    if not require_api_key(stdscr):
        return
    row = db.get(movie_id)
    try:
        if row["imdb_id"]:
            flash(stdscr, "Fetching info from OMDb...")
            details = omdb.get_by_id(row["imdb_id"])
        else:
            year = (row["year"] or "").strip()[:4]
            query = row["title"]
            while True:
                flash(stdscr, f"Searching OMDb for '{query}'...")
                try:
                    results, _ = omdb.search(query)
                except omdb.OmdbError as e:
                    if not omdb.is_not_found(e):
                        raise
                    results = []
                if results:
                    break
                query = prompt(stdscr, "No OMDb results - search for", query)
                if not query:
                    return
            results.sort(key=lambda r: r["year"][:4] != year)
            choice = choose_from_list(
                stdscr, f"Pick the OMDb match for '{row['title']}' ({year or '?'}):", results,
                lambda r: f"{r['title']} ({r['year']}) [{r['media_type'].capitalize()}]",
            )
            if not choice:
                return
            flash(stdscr, f"Fetching details for '{choice['title']}'...")
            details = omdb.get_by_id(choice["imdb_id"])
    except omdb.OmdbError as e:
        flash(stdscr, f"OMDb error: {e}")
        return
    filled, clash_title = metadata.apply_details(db, row, details)
    flash(stdscr, _fill_result_message(filled, clash_title))


def _esc_pressed(stdscr) -> bool:
    """Non-blocking check for Esc; any other pending key is put back."""
    stdscr.nodelay(True)
    try:
        ch = stdscr.getch()
    finally:
        stdscr.nodelay(False)
    if ch not in (-1, 27):
        curses.ungetch(ch)
    return ch == 27


def fill_missing_info(stdscr, db: DB, movie_ids: list[int]) -> None:
    """Fill in empty fields from OMDb for many titles at once, matching
    each automatically by title and year (see metadata.find_match). Titles
    without a confident match are skipped and listed at the end, to fix
    one at a time with 'f' on their detail screen. Esc stops early; a
    later run picks up where this one left off, since filled-in titles no
    longer count as missing info."""
    if not movie_ids or not require_api_key(stdscr):
        return
    total = len(movie_ids)
    updated = 0
    unmatched: list[str] = []
    clashes: list[str] = []
    stopped = ""
    for n, movie_id in enumerate(movie_ids, 1):
        if _esc_pressed(stdscr):
            stopped = "Stopped early (Esc)."
            break
        row = db.get(movie_id)
        label = f"{row['title']} ({row['year'] or '?'})"
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        stdscr.addstr(0, 0, f"Filling in info from OMDb: {n} of {total}"[: w - 1], accent_attr() | curses.A_BOLD)
        stdscr.addstr(2, 2, label[: w - 3])
        stdscr.addstr(h - 1, 0, "Esc stop"[: w - 1], dim_attr())
        stdscr.refresh()
        try:
            details = metadata.find_match(row)
        except omdb.OmdbError as e:
            stopped = f"Stopped early - OMDb error: {e}"
            break
        if not details:
            unmatched.append(label)
            continue
        filled, clash_title = metadata.apply_details(db, row, details)
        if clash_title:
            clashes.append(f"{label} - already in your library as '{clash_title}'")
        if filled:
            updated += 1

    lines = [f"Filled in info for {updated} of {total} title(s)."]
    if stopped:
        lines += ["", stopped, "Run it again later to pick up the rest."]
    for heading, items in (
        ("No confident match - open each one and press 'f' to pick the right title:", unmatched),
        ("Duplicates (IMDb id not linked):", clashes),
    ):
        if items:
            lines += ["", heading]
            lines += [f"  {item}" for item in items[:FILL_SUMMARY_MAX_LISTED]]
            if len(items) > FILL_SUMMARY_MAX_LISTED:
                lines.append(f"  ...and {len(items) - FILL_SUMMARY_MAX_LISTED} more")
    lines += ["", "Press any key."]
    show_text_screen(stdscr, "\n".join(lines))
