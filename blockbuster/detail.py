"""The movie/series detail screen and its edit-all-fields form."""

import curses
import subprocess
import textwrap
from datetime import date

from . import config, omdb, tmdb
from .adding import fill_info_for_title
from .db import DB
from .widgets import (
    PREVIEW_MIN_TERM_WIDTH,
    PREVIEW_PANEL_WIDTH_MAX,
    PREVIEW_PANEL_WIDTH_MIN,
    accent_attr,
    clear_kitty_images,
    confirm,
    dim_attr,
    draw_box,
    flash,
    in_kitty,
    prompt,
    rating_attr,
    safe_addstr,
    selected_attr,
    show_text_screen,
    type_icon,
    view_poster,
)


# ----------------------------------------------------------- detail view --

# (db column, field label, kind) for the full-record edit form.
# kind controls both input validation and how the value is saved:
#   text    - stored as-is
#   int     - whole number, blank saves as NULL
#   int0    - whole number, blank saves as 0 (rewatch_count is NOT NULL)
#   rating  - a number from 0-10, blank saves as NULL
#   status  - must be "watched" or "watchlist"
#   media_type - must be "movie" or "series"
#   date    - blank, or a YYYY-MM-DD date
#   tags    - comma-separated tag names; not a movies column, saved via
#             db.set_tags_for_movie() instead of db.update()
#   formats - comma-separated format names; likewise saved via
#             db.set_formats_for_movie()
# poster_path, imdb_id, and added_at are left out: they're managed by the
# app itself (poster downloads, OMDb lookups, add timestamp), not
# free-text fields a user would hand-edit.
EDIT_FORM_FIELDS = [
    ("title", "Title", "text"),
    ("year", "Year", "text"),
    ("media_type", "Type (movie/series)", "media_type"),
    ("status", "Status (watched/watchlist)", "status"),
    ("my_rank", "My rank", "int"),
    ("my_rating", "My rating (0-10)", "rating"),
    ("imdb_rating", "IMDb rating (0-10)", "rating"),
    ("watched_date", "Watched date (YYYY-MM-DD)", "date"),
    ("rewatch_count", "Rewatch count", "int0"),
    ("runtime_minutes", "Runtime (min)", "int"),
    ("rated", "Rated", "text"),
    ("genre", "Genre", "text"),
    ("director", "Director", "text"),
    ("actors", "Actors", "text"),
    ("tags", "Tags (comma-separated)", "tags"),
    ("formats", "Formats (comma-separated)", "formats"),
    ("plot", "Plot", "text"),
    ("notes", "Notes", "text"),
]

# Section headers inserted before the field they key on, purely for
# scannability - not selectable, don't consume a field index.
EDIT_FORM_SECTIONS = {
    "title": "TITLE",
    "status": "STATUS & RATINGS",
    "runtime_minutes": "DETAILS",
    "plot": "TEXT",
}


def _field_display_label(key: str, label: str, values: dict) -> str:
    """Field label, adjusted for context - e.g. runtime_minutes holds the
    per-episode runtime for a series (same as the detail screen and the
    manual-add form), so call it that here too instead of just "Runtime"."""
    if key == "runtime_minutes" and values.get("media_type") == "series":
        return "Episode runtime (min)"
    return label


def _edit_form_display_lines() -> list[tuple[str, object]]:
    """('header', text) / ('field', field_index) rows in display order."""
    lines: list[tuple[str, object]] = []
    for field_i, (key, _, _) in enumerate(EDIT_FORM_FIELDS):
        if key in EDIT_FORM_SECTIONS:
            lines.append(("header", EDIT_FORM_SECTIONS[key]))
        lines.append(("field", field_i))
    return lines


def _validate_field(stdscr, kind: str, new_val: str) -> str | None:
    """Check new_val against kind's rules; flash and return None if invalid."""
    stripped = new_val.strip()
    if kind in ("int", "int0") and stripped and not stripped.isdigit():
        flash(stdscr, "Enter a whole number.")
        return None
    if kind == "rating" and stripped:
        try:
            if not (0.0 <= float(stripped) <= 10.0):
                raise ValueError
        except ValueError:
            flash(stdscr, "Enter a number between 0 and 10.")
            return None
    if kind == "status":
        if stripped.lower() not in ("watched", "watchlist"):
            flash(stdscr, "Enter 'watched' or 'watchlist'.")
            return None
        return stripped.lower()
    if kind == "media_type":
        if stripped.lower() not in ("movie", "series"):
            flash(stdscr, "Enter 'movie' or 'series'.")
            return None
        return stripped.lower()
    if kind == "date":
        if stripped:
            try:
                date.fromisoformat(stripped)
            except ValueError:
                flash(stdscr, "Enter a date as YYYY-MM-DD.")
                return None
        return stripped
    return new_val


def edit_movie_form(stdscr, db: DB, movie_id: int) -> None:
    """A form-style screen for editing every field on a movie at once.

    Up/Down moves between fields, Enter edits the highlighted one (each
    field is still a single-line prompt, same as everywhere else in the
    app). Fields you've changed since opening the form are marked with
    '*'. 'q' saves and exits; Esc discards any changes and exits (with a
    confirmation if there are unsaved changes to lose).
    """
    row = db.get(movie_id)
    if row is None:
        return
    values = {}
    for key, _, kind in EDIT_FORM_FIELDS:
        if kind == "tags":
            values[key] = ", ".join(db.get_tags_for_movie(movie_id))
            continue
        if kind == "formats":
            values[key] = ", ".join(db.get_formats_for_movie(movie_id))
            continue
        raw = row[key]
        values[key] = str(raw) if raw is not None else ""
    original_values = dict(values)

    display_lines = _edit_form_display_lines()
    idx = 0
    top = 0
    label_w = max(len(label) for _, label, _ in EDIT_FORM_FIELDS) + 2
    while True:
        stdscr.clear()
        h, w = stdscr.getmaxyx()
        dirty = values != original_values
        title_line = f"Edit: {values['title'] or '(untitled)'}{'*' if dirty else ''} ({values['year']})"
        stdscr.addstr(0, 0, title_line[: w - 1], accent_attr() | curses.A_BOLD)
        draw_box(stdscr, 1, 0, h - 2, w, dim_attr())

        visible_rows = max(1, h - 4)
        cursor_pos = next(i for i, (kind, val) in enumerate(display_lines) if kind == "field" and val == idx)
        if cursor_pos < top:
            top = cursor_pos
        if cursor_pos >= top + visible_rows:
            top = cursor_pos - visible_rows + 1

        y = 2
        for i in range(top, min(len(display_lines), top + visible_rows)):
            line_kind, val = display_lines[i]
            if line_kind == "header":
                try:
                    stdscr.addstr(y, 2, str(val)[: w - 3], accent_attr() | curses.A_UNDERLINE)
                except curses.error:
                    pass
                y += 1
                continue

            field_i = val
            key, label, kind = EDIT_FORM_FIELDS[field_i]
            label = _field_display_label(key, label, values)
            is_current = field_i == idx
            changed = values[key] != original_values[key]
            base_attr = selected_attr() if is_current else curses.A_NORMAL
            marker = "*" if changed else " "
            label_str = f"{marker}{label:<{label_w}}"
            display = values[key] if values[key] else "-"
            x = 2
            try:
                stdscr.addstr(y, x, label_str[: max(w - x - 1, 0)], base_attr)
            except curses.error:
                pass
            x += len(label_str)
            value_attr = base_attr
            if kind == "rating" and not is_current and values[key]:
                try:
                    value_attr = rating_attr(float(values[key]))
                except ValueError:
                    pass
            try:
                stdscr.addstr(y, x, display[: max(w - x - 1, 0)], value_attr)
            except curses.error:
                pass
            y += 1

        footer = "Up/Down move  Enter edit field  q save & exit  Esc discard & exit"
        stdscr.addstr(h - 1, 0, footer[: w - 1], dim_attr())
        stdscr.refresh()

        ch = stdscr.getch()
        if ch == ord("q"):
            break
        elif ch == 27:
            if values == original_values or confirm(stdscr, "Discard unsaved changes?"):
                return
        elif ch in (curses.KEY_UP, ord("k")):
            idx = max(0, idx - 1)
        elif ch in (curses.KEY_DOWN, ord("j")):
            idx = min(len(EDIT_FORM_FIELDS) - 1, idx + 1)
        elif ch in (10, 13, curses.KEY_ENTER):
            key, label, kind = EDIT_FORM_FIELDS[idx]
            label = _field_display_label(key, label, values)
            new_val = prompt(stdscr, label, values[key])
            if new_val is None:
                continue
            validated = _validate_field(stdscr, kind, new_val)
            if validated is None:
                continue
            values[key] = validated

    updates = {}
    for key, _, kind in EDIT_FORM_FIELDS:
        val = values[key].strip()
        if kind == "tags":
            db.set_tags_for_movie(movie_id, val.split(","))
        elif kind == "formats":
            db.set_formats_for_movie(movie_id, val.split(","))
        elif kind == "int":
            updates[key] = int(val) if val else None
        elif kind == "int0":
            updates[key] = int(val) if val else 0
        elif kind == "rating":
            updates[key] = float(val) if val else None
        else:
            updates[key] = values[key]
    db.update(movie_id, **updates)


def movie_detail(stdscr, db: DB, movie_id: int) -> None:
    last_poster_key = "unset"
    try:
        while True:
            row = db.get(movie_id)
            if row is None:
                return
            stdscr.clear()
            h, w = stdscr.getmaxyx()

            box_attr = dim_attr()
            draw_box(stdscr, 0, 0, h - 1, w, box_attr)
            interior_left, interior_right = 1, w - 2

            full_path = config.poster_full_path(row["poster_path"])
            preview_on = bool(in_kitty() and w >= PREVIEW_MIN_TERM_WIDTH and full_path and full_path.exists())
            if preview_on:
                preview_w = max(PREVIEW_PANEL_WIDTH_MIN, min(PREVIEW_PANEL_WIDTH_MAX, w // 3))
                divider_x = interior_right - preview_w
                content_w = divider_x
                for row_y in range(1, h - 2):
                    safe_addstr(stdscr, row_y, divider_x, "│", box_attr)
                safe_addstr(stdscr, 0, divider_x, "┬", box_attr)
                safe_addstr(stdscr, h - 2, divider_x, "┴", box_attr)
            else:
                preview_w = 0
                content_w = interior_right + 1

            y = 1
            title_line = f"{row['title']} ({row['year']})"
            stdscr.addstr(y, interior_left, title_line[: content_w - 1], accent_attr() | curses.A_BOLD)
            y += 2

            my_rating = f"{row['my_rating']:.1f}/10" if row["my_rating"] is not None else "-"
            imdb_rating = f"{row['imdb_rating']:.1f}/10" if row["imdb_rating"] is not None else "-"
            rank = row["my_rank"] if row["my_rank"] is not None else "-"
            watched = row["watched_date"] or "-"
            tags = db.get_tags_for_movie(movie_id)
            formats = db.get_formats_for_movie(movie_id)

            is_series = row["media_type"] == "series"
            runtime_label = "Episode runtime:" if is_series else "Runtime:"

            # (label, value, rating value to color-code the line by, or None)
            lines = [
                ("Type:", f"{type_icon(row['media_type'])} {'Series' if is_series else 'Movie'}", None),
                ("Status:", row["status"], None),
                ("My rank:", rank, None),
                ("My rating:", my_rating, row["my_rating"]),
                ("IMDb rating:", imdb_rating, row["imdb_rating"]),
            ]
            lines += [
                (runtime_label, f"{row['runtime_minutes']} min" if row["runtime_minutes"] else "-", None),
                ("Rated:", row["rated"] or "-", None),
                ("Genre:", row["genre"] or "-", None),
                ("Director:", row["director"] or "-", None),
                ("Actors:", row["actors"] or "-", None),
                ("Tags:", ", ".join(tags) if tags else "-", None),
                ("Formats:", ", ".join(formats) if formats else "-", None),
                ("Watched date:", watched, None),
                ("Rewatch count:", row["rewatch_count"], None),
                ("Poster cached:", "yes" if row["poster_path"] else "no", None),
            ]
            for label, value, rating_val in lines:
                if y >= h - 4:
                    break
                label_str = f"{label:<14} "
                try:
                    stdscr.addstr(y, 2, label_str, curses.A_NORMAL)
                    stdscr.addstr(y, 2 + len(label_str), str(value)[: max(content_w - 5 - len(label_str), 0)], rating_attr(rating_val))
                except curses.error:
                    pass
                y += 1

            y += 1
            if row["plot"] and y < h - 7:
                stdscr.addstr(y, interior_left, "Plot:"[: content_w - 1], accent_attr() | curses.A_UNDERLINE)
                y += 1
                for wline in textwrap.wrap(row["plot"], max(content_w - 4, 20)):
                    if y >= h - 5:
                        break
                    stdscr.addstr(y, 2, wline[: content_w - 3])
                    y += 1

            if row["notes"]:
                y += 1
                if y < h - 4:
                    stdscr.addstr(y, interior_left, "Notes:"[: content_w - 1], accent_attr() | curses.A_UNDERLINE)
                    y += 1
                    for wline in textwrap.wrap(row["notes"], max(content_w - 4, 20)):
                        if y >= h - 3:
                            break
                        stdscr.addstr(y, 2, wline[: content_w - 3])
                        y += 1

            footer = "r rating  n rank  M rated  w watched  t today  c +rewatch  v history  o watch  "
            footer += "e notes  E edit all  p poster  u refresh poster  f fill info  x delete  q back"
            stdscr.addstr(h - 1, 0, footer[: w - 1], dim_attr())
            stdscr.refresh()

            if preview_on:
                preview_left = divider_x + 1
                preview_top = 1
                preview_height = max(4, (h - 2) - preview_top)
                key = (row["id"], row["poster_path"], (preview_left, preview_top, preview_w - 1, preview_height))
                if key != last_poster_key:
                    last_poster_key = key
                    clear_kitty_images()
                    try:
                        subprocess.run(
                            [
                                "kitty", "+kitten", "icat",
                                "--place", f"{preview_w - 1}x{preview_height}@{preview_left}x{preview_top}",
                                "--scale-up", str(full_path),
                            ],
                            stderr=subprocess.DEVNULL,
                        )
                    except OSError:
                        pass
            elif last_poster_key != "unset":
                clear_kitty_images()
                last_poster_key = "unset"

            ch = stdscr.getch()
            if ch in (ord("q"), 27):
                return
            elif ch == ord("E"):
                clear_kitty_images()
                last_poster_key = "unset"
                edit_movie_form(stdscr, db, movie_id)
            elif ch == ord("r"):
                val = prompt(stdscr, "Your rating (0-10)", str(row["my_rating"] or ""))
                if val is not None:
                    try:
                        rating = max(0.0, min(10.0, float(val))) if val.strip() else None
                        db.update(movie_id, my_rating=rating)
                    except ValueError:
                        flash(stdscr, "Enter a number between 0 and 10.")
            elif ch == ord("n"):
                val = prompt(stdscr, "Your rank", str(row["my_rank"] or ""))
                if val is not None:
                    try:
                        rank = int(val) if val.strip() else None
                        db.update(movie_id, my_rank=rank)
                    except ValueError:
                        flash(stdscr, "Enter a whole number.")
            elif ch == ord("M"):
                val = prompt(stdscr, "Rating e.g. R, PG-13, TV-14", row["rated"] or "")
                if val is not None:
                    db.update(movie_id, rated=val)
            elif ch == ord("w"):
                new_status = "watched" if row["status"] == "watchlist" else "watchlist"
                updates = {"status": new_status}
                if new_status == "watched" and not row["watched_date"]:
                    updates["watched_date"] = date.today().isoformat()
                db.update(movie_id, **updates)
            elif ch == ord("t"):
                db.update(movie_id, watched_date=date.today().isoformat(), status="watched")
            elif ch == ord("c"):
                db.log_rewatch(movie_id)
            elif ch == ord("v"):
                history = db.get_rewatch_history(movie_id)
                if not history:
                    flash(stdscr, "No rewatches logged yet - press 'c' to log one.")
                else:
                    lines = [f"REWATCH HISTORY: {row['title']}", ""]
                    lines.extend(history)
                    lines.append("")
                    lines.append("Press any key to close.")
                    show_text_screen(stdscr, "\n".join(lines))
            elif ch == ord("o"):
                try:
                    providers = tmdb.get_watch_providers(row["imdb_id"], row["media_type"])
                except tmdb.TmdbError as e:
                    flash(stdscr, str(e))
                else:
                    try:
                        trailer_url = tmdb.get_trailer_url(row["imdb_id"], row["media_type"])
                    except tmdb.TmdbError:
                        trailer_url = None
                    lines = [f"WHERE TO WATCH: {row['title']}", ""]
                    if providers:
                        for label, names in providers:
                            lines.append(f"{label}: {', '.join(names)}")
                    else:
                        lines.append("No streaming/rent/buy sources found for this title/region.")
                    lines.append("")
                    lines.append(f"Trailer: {trailer_url}" if trailer_url else "Trailer: not found")
                    lines.append("")
                    lines.append("Press any key to close.")
                    show_text_screen(stdscr, "\n".join(lines))
            elif ch == ord("e"):
                val = prompt(stdscr, "Notes", row["notes"] or "")
                if val is not None:
                    db.update(movie_id, notes=val)
            elif ch == ord("p"):
                clear_kitty_images()
                last_poster_key = "unset"
                view_poster(stdscr, row["poster_path"], title_line)
            elif ch == ord("u"):
                if not row["imdb_id"]:
                    flash(stdscr, "No IMDb ID on this title - can't auto-fetch a poster.")
                else:
                    flash(stdscr, "Fetching latest poster from OMDb...")
                    try:
                        details = omdb.get_by_id(row["imdb_id"])
                    except omdb.OmdbError as e:
                        flash(stdscr, f"OMDb error: {e}")
                    else:
                        new_path = omdb.download_poster(details.get("poster_url", ""), row["imdb_id"])
                        if new_path:
                            db.update(movie_id, poster_path=new_path)
                            clear_kitty_images()
                            last_poster_key = "unset"
                            flash(stdscr, "Poster updated from OMDb.")
                        else:
                            flash(stdscr, "OMDb has no poster for this title.")
            elif ch == ord("f"):
                clear_kitty_images()
                last_poster_key = "unset"
                fill_info_for_title(stdscr, db, movie_id)
            elif ch == ord("x"):
                if confirm(stdscr, f"Delete '{row['title']}'?"):
                    db.delete(movie_id)
                    flash(stdscr, f"Deleted '{row['title']}'. Press 'u' from the list to undo.")
                    return
    finally:
        clear_kitty_images()
