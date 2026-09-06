"""Curses TUI for movie tracker."""

import csv
import curses
import json
import locale
import os
import random
import shutil
import subprocess
import textwrap
from collections import Counter
from datetime import date, datetime
from pathlib import Path

from . import config, omdb, theme
from .db import DB, Movie, SORT_FIELDS

# Help screen content, grouped by section for the column layout in
# show_help_screen() - keep each description to one short line, it has
# to fit a ~38-column card.
HELP_SECTIONS = [
    ("NAVIGATION", [
        ("Up/Down, j/k", "move selection"),
        ("Enter / l", "open movie details"),
        ("E", "edit every field directly"),
        ("/", "filter title/genre/director/cast"),
        ("s", "cycle sort field"),
        ("Tab", "cycle status filter"),
        ("y", "cycle type filter"),
        ("g", "cycle tag filter"),
        ("Space", "mark/unmark selected"),
        ("Esc", "clear all marks"),
        ("G", "tag marked movie(s)"),
    ]),
    ("MOVIES & TV SHOWS", [
        ("a", "add via OMDb search"),
        ("m", "add manually, no internet"),
        ("d", "delete marked (soft delete)"),
        ("u", "undo last delete"),
        ("w", "mark watched"),
        ("W", "mark watchlist"),
        ("R", "random watchlist pick"),
        ("P", "poster wall (kitty only)"),
        ("T", "trash: browse/restore/purge"),
        ("S", "stats screen"),
        ("C", "collections screen"),
        ("B", "back up database now"),
        ("X", "export to CSV/JSON"),
        ("I", "import from CSV/JSON"),
    ]),
    ("MOVIE DETAIL SCREEN", [
        ("r", "set your rating (0-10)"),
        ("n", "set your rank"),
        ("M", "set content rating"),
        ("w", "toggle watched/watchlist"),
        ("t", "set watched date to today"),
        ("c", "+1 rewatch count"),
        ("N", "advance season (series)"),
        ("e", "edit notes"),
        ("E", "edit all fields at once"),
        ("p", "view poster fullscreen"),
        ("x", "delete this movie"),
    ]),
    ("OTHER", [
        ("K", "set/update OMDb API key"),
        ("?", "this help screen"),
        ("q / Esc", "back / quit"),
    ]),
]

# Extra prose notes shown below the columns, full-width.
HELP_NOTES = [
    "My rating and IMDb rating are color-coded (green >= 7.5, yellow >= "
    "5, red below that) using your desktop theme's colors when available.",
    "In kitty, on a terminal at least 64 columns wide, the poster of the "
    "selected movie shows live in a panel on the right as you move the "
    "selection.",
]


def type_icon(media_type: str) -> str:
    """A small glyph for a movie vs. a series, used anywhere the old
    'Mv'/'Sr' text tag used to go."""
    return "📺" if media_type == "series" else "🎬"


def in_kitty() -> bool:
    return bool(os.environ.get("KITTY_WINDOW_ID")) or os.environ.get("TERM", "").startswith(
        "xterm-kitty"
    )


PREVIEW_MIN_TERM_WIDTH = 64
PREVIEW_PANEL_WIDTH_MIN = 20
PREVIEW_PANEL_WIDTH_MAX = 32
DETAILS_PANEL_TARGET_LINES = 11
DETAILS_PANEL_MIN_POSTER_H = 6

# Fixed width of the select-marker, #, Rk, Type, Year, Rated, and Status
# columns plus their separating spaces (everything in the list row
# except Title, Runtime, Mine, and IMDb, which shrink or drop to make
# room for the poster panel).
ROW_CORE_FIXED_WIDTH = 31
ROW_RUNTIME_WIDTH = 9
ROW_MINE_WIDTH = 6
ROW_IMDB_WIDTH = 6
ROW_TAGS_WIDTH = 21
ROW_TITLE_WIDTH_DEFAULT = 35
ROW_TITLE_WIDTH_SOFT_MIN = 15
ROW_TITLE_WIDTH_HARD_MIN = 8


def compute_row_layout(content_w: int):
    """Pick a title width and which optional columns fit in content_w.

    Shrinks the title column first, then drops Tags/Runtime/Mine/IMDb (in
    that order - Tags first, since it's the newest/most optional column)
    if it's still too tight, then shrinks the title further as a last
    resort so the poster panel can show even on narrow terminals.
    """
    title_w = ROW_TITLE_WIDTH_DEFAULT
    show_runtime = show_mine = show_imdb = show_tags = True

    def total():
        return (
            ROW_CORE_FIXED_WIDTH
            + title_w
            + (ROW_RUNTIME_WIDTH if show_runtime else 0)
            + (ROW_MINE_WIDTH if show_mine else 0)
            + (ROW_IMDB_WIDTH if show_imdb else 0)
            + (ROW_TAGS_WIDTH if show_tags else 0)
        )

    while total() > content_w and title_w > ROW_TITLE_WIDTH_SOFT_MIN:
        title_w -= 1
    while total() > content_w and show_tags:
        show_tags = False
    while total() > content_w and show_runtime:
        show_runtime = False
    while total() > content_w and show_mine:
        show_mine = False
    while total() > content_w and show_imdb:
        show_imdb = False
    while total() > content_w and title_w > ROW_TITLE_WIDTH_HARD_MIN:
        title_w -= 1
    return title_w, show_runtime, show_mine, show_imdb, show_tags


# ------------------------------------------------------------ theming --

PAIR_BAR = 1
PAIR_SELECTED = 2
PAIR_ACCENT = 3
PAIR_DIM = 4
PAIR_GOOD = 5
PAIR_MID = 6
PAIR_BAD = 7
PAIR_BLUE = 8

RATING_GOOD_THRESHOLD = 7.5
RATING_MID_THRESHOLD = 5.0

_theme_colors_enabled = False
_rating_colors_enabled = False
_blue_enabled = False


def setup_theme() -> None:
    """Pick up the active Omarchy desktop theme's colors, if any, and set
    up rating colors (green/yellow/red).

    Safe to call unconditionally: on a non-Omarchy system, or a terminal
    without enough color support, this leaves the app on its original
    plain curses attributes (reverse-video, dim, bold, underline) and no
    rating colors.
    """
    global _theme_colors_enabled, _rating_colors_enabled, _blue_enabled
    _theme_colors_enabled = False
    _rating_colors_enabled = False
    _blue_enabled = False
    if not curses.has_colors():
        return
    curses.start_color()

    colors = theme.load() if curses.COLORS >= 256 else None

    good = mid = bad = blue = None
    if colors:
        good = theme.hex_to_xterm256(colors.get("green"))
        mid = theme.hex_to_xterm256(colors.get("yellow"))
        bad = theme.hex_to_xterm256(colors.get("red"))
        blue = theme.hex_to_xterm256(colors.get("blue"))
    if good is None or mid is None or bad is None:
        good, mid, bad = curses.COLOR_GREEN, curses.COLOR_YELLOW, curses.COLOR_RED
    if blue is None:
        blue = curses.COLOR_BLUE
    try:
        curses.init_pair(PAIR_GOOD, good, -1)
        curses.init_pair(PAIR_MID, mid, -1)
        curses.init_pair(PAIR_BAD, bad, -1)
        _rating_colors_enabled = True
        curses.init_pair(PAIR_BLUE, blue, -1)
        _blue_enabled = True
    except curses.error:
        pass

    if not colors:
        return
    fg = theme.hex_to_xterm256(colors["foreground"])
    bg = theme.hex_to_xterm256(colors["background"])
    accent = theme.hex_to_xterm256(colors["accent"])
    selection = theme.hex_to_xterm256(colors["selection"])
    bright_fg = theme.hex_to_xterm256(colors["bright_foreground"])
    muted = theme.hex_to_xterm256(colors["muted"])
    if None in (fg, bg, accent, selection, bright_fg, muted):
        return
    try:
        curses.init_pair(PAIR_BAR, bg, accent)
        curses.init_pair(PAIR_SELECTED, bright_fg, selection)
        curses.init_pair(PAIR_ACCENT, accent, -1)
        curses.init_pair(PAIR_DIM, muted, -1)
    except curses.error:
        return
    _theme_colors_enabled = True


def bar_attr() -> int:
    """Solid accent-colored bar, e.g. the app header."""
    if _theme_colors_enabled:
        return curses.color_pair(PAIR_BAR) | curses.A_BOLD
    return curses.A_BOLD | curses.A_REVERSE


def selected_attr() -> int:
    """Highlight for the selected row in a list."""
    return curses.color_pair(PAIR_SELECTED) if _theme_colors_enabled else curses.A_REVERSE


def accent_attr() -> int:
    """Accent-colored text with no background fill, e.g. section headers."""
    return curses.color_pair(PAIR_ACCENT) if _theme_colors_enabled else 0


def dim_attr() -> int:
    """Muted text, e.g. footers and hints."""
    return curses.color_pair(PAIR_DIM) if _theme_colors_enabled else curses.A_DIM


def blue_attr() -> int:
    """Blue text, e.g. the watched checkmark."""
    return curses.color_pair(PAIR_BLUE) if _blue_enabled else 0


def type_icon_attr(media_type: str) -> int:
    """Color for the movie/series type icon - blue for series (paired
    with the same blue as the watched checkmark), theme accent for
    movies, so the two read as distinct at a glance."""
    return blue_attr() if media_type == "series" else accent_attr()


def safe_addstr(stdscr, y: int, x: int, text: str, attr: int = 0) -> None:
    """addstr that swallows curses.error - writing to the last screen
    column/row is a well-known ncurses edge case, and panels get drawn
    against terminal sizes we can't fully control."""
    try:
        stdscr.addstr(y, x, text, attr)
    except curses.error:
        pass


def draw_box(stdscr, y: int, x: int, h: int, w: int, attr: int = 0) -> None:
    """Draw a box-drawing-character rectangle. (y, x) is the outer
    top-left corner; h/w are the box's outer height/width (border
    included), so the interior is (h - 2) x (w - 2).

    Each corner is written separately from the straight run beside it -
    writing all the way to the window's last column/row in one addstr
    call is a well-known ncurses trip-up, and this way a failure there
    only drops that one corner glyph instead of the whole border line.
    """
    if h < 2 or w < 2:
        return
    safe_addstr(stdscr, y, x, "┌" + "─" * (w - 2), attr)
    safe_addstr(stdscr, y, x + w - 1, "┐", attr)
    for row in range(y + 1, y + h - 1):
        safe_addstr(stdscr, row, x, "│", attr)
        safe_addstr(stdscr, row, x + w - 1, "│", attr)
    safe_addstr(stdscr, y + h - 1, x, "└" + "─" * (w - 2), attr)
    safe_addstr(stdscr, y + h - 1, x + w - 1, "┘", attr)


def ascii_bar(value: float, max_value: float, width: int = 20) -> str:
    """A block-character bar for stats/progress displays, e.g.
    '███████████░░░░░░░░░' - `value`/`max_value` filled out of `width`."""
    filled = round(width * value / max_value) if max_value > 0 else 0
    filled = max(0, min(width, filled))
    return "█" * filled + "░" * (width - filled)


def rating_attr(value: float | None) -> int:
    """Green/yellow/red for a 0-10 rating value; 0 (no color) if unset."""
    if value is None or not _rating_colors_enabled:
        return 0
    if value >= RATING_GOOD_THRESHOLD:
        return curses.color_pair(PAIR_GOOD)
    if value >= RATING_MID_THRESHOLD:
        return curses.color_pair(PAIR_MID)
    return curses.color_pair(PAIR_BAD)


def clear_kitty_images() -> None:
    if not in_kitty():
        return
    try:
        subprocess.run(["kitty", "+kitten", "icat", "--clear"], stderr=subprocess.DEVNULL)
    except OSError:
        pass


def input_pending(stdscr) -> bool:
    """True if there's already another keypress waiting to be read.

    Used to skip a slow redraw (e.g. shelling out to kitty to render a
    poster) while the user is holding a key down to scroll/page fast -
    otherwise every row/page passed through blocks on that redraw before
    the next keypress is even read, turning a fast scroll into a stutter.
    """
    stdscr.nodelay(True)
    ch = stdscr.getch()
    stdscr.nodelay(False)
    if ch == -1:
        return False
    curses.ungetch(ch)
    return True


# ---------------------------------------------------------------- widgets --

def flash(stdscr, msg: str) -> None:
    h, w = stdscr.getmaxyx()
    stdscr.addstr(h - 1, 0, " " * (w - 1))
    stdscr.addstr(h - 1, 0, msg[: w - 1])
    stdscr.refresh()
    curses.napms(1100)


def edit_line(stdscr, y: int, x: int, width: int, initial: str = "") -> str | None:
    curses.curs_set(1)
    buf = list(initial)
    pos = len(buf)
    try:
        while True:
            s = "".join(buf)
            stdscr.addstr(y, x, " " * max(width, 1))
            stdscr.addstr(y, x, s[-width:] if len(s) > width else s)
            shown_pos = min(pos, width - 1) if width > 0 else 0
            stdscr.move(y, x + shown_pos)
            stdscr.refresh()
            ch = stdscr.getch()
            if ch in (10, 13, curses.KEY_ENTER):
                return s
            if ch == 27:
                return None
            if ch in (curses.KEY_BACKSPACE, 127, 8):
                if pos > 0:
                    buf.pop(pos - 1)
                    pos -= 1
            elif ch == curses.KEY_LEFT:
                pos = max(0, pos - 1)
            elif ch == curses.KEY_RIGHT:
                pos = min(len(buf), pos + 1)
            elif ch == curses.KEY_DC:
                if pos < len(buf):
                    buf.pop(pos)
            elif 32 <= ch <= 126:
                buf.insert(pos, chr(ch))
                pos += 1
    finally:
        curses.curs_set(0)


def prompt(stdscr, label: str, initial: str = "") -> str | None:
    h, w = stdscr.getmaxyx()
    y = h - 1
    stdscr.addstr(y, 0, " " * (w - 1))
    text = f"{label}: "
    stdscr.addstr(y, 0, text[: w - 1])
    return edit_line(stdscr, y, min(len(text), w - 1), max(w - len(text) - 1, 1), initial)


def confirm(stdscr, question: str) -> bool:
    ans = prompt(stdscr, f"{question} (y/N)")
    return (ans or "").strip().lower() == "y"


def confirm_by_typing(stdscr, question: str, expected: str) -> bool:
    """A stricter confirm for irreversible actions: the user must type the
    exact expected text (e.g. the movie's title) rather than just 'y'."""
    typed = prompt(stdscr, f"{question} (type '{expected}' to confirm)")
    return typed is not None and typed.strip() == expected


def choose_from_list(stdscr, title: str, items: list, formatter):
    if not items:
        return None
    idx = 0
    top = 0
    while True:
        stdscr.clear()
        h, w = stdscr.getmaxyx()
        stdscr.addstr(0, 0, title[: w - 1], accent_attr() | curses.A_BOLD)
        visible_rows = h - 4
        if idx < top:
            top = idx
        if idx >= top + visible_rows:
            top = idx - visible_rows + 1
        for row, i in enumerate(range(top, min(len(items), top + visible_rows))):
            item = items[i]
            line = formatter(item)
            attr = selected_attr() if i == idx else curses.A_NORMAL
            stdscr.addstr(row + 2, 2, line[: w - 3], attr)
        stdscr.addstr(h - 1, 0, "Up/Down move   Enter select   Esc cancel"[: w - 1])
        stdscr.refresh()
        ch = stdscr.getch()
        if ch in (curses.KEY_UP, ord("k")):
            idx = max(0, idx - 1)
        elif ch in (curses.KEY_DOWN, ord("j")):
            idx = min(len(items) - 1, idx + 1)
        elif ch in (10, 13, curses.KEY_ENTER):
            return items[idx]
        elif ch == 27:
            return None


def show_text_screen(stdscr, text: str) -> None:
    stdscr.clear()
    h, w = stdscr.getmaxyx()
    lines = text.strip("\n").split("\n")
    for i, line in enumerate(lines[: h - 1]):
        try:
            stdscr.addstr(i, 0, line[: w - 1])
        except curses.error:
            pass
    stdscr.refresh()
    stdscr.getch()


def show_boxed_text_screen(stdscr, text: str) -> None:
    """Like show_text_screen, but frames the content in a box-drawn
    border - for screens meant to read as a single self-contained panel
    (e.g. stats) rather than a plain scroll of text."""
    stdscr.clear()
    h, w = stdscr.getmaxyx()
    draw_box(stdscr, 0, 0, h, w, dim_attr())
    lines = text.strip("\n").split("\n")
    max_w = max(w - 4, 1)
    for i, line in enumerate(lines[: max(h - 2, 0)]):
        try:
            stdscr.addstr(i + 1, 2, line[:max_w])
        except curses.error:
            pass
    stdscr.refresh()
    stdscr.getch()


def show_help_screen(stdscr) -> None:
    """Lay HELP_SECTIONS out as side-by-side columns (falling back to one
    stacked column on a narrow terminal), then a couple of full-width
    prose notes below."""
    h, w = stdscr.getmaxyx()

    def block_lines(section) -> list[str]:
        title, entries = section
        key_w = max((len(k) for k, _ in entries), default=0)
        lines = [title, "-" * len(title)]
        lines.extend(f"{k:<{key_w}}  {d}" for k, d in entries)
        return lines

    blocks = [block_lines(s) for s in HELP_SECTIONS]
    left_col = blocks[0] + [""] + blocks[2]
    right_col = blocks[1] + [""] + blocks[3]
    left_w = max(len(l) for l in left_col)
    right_w = max(len(l) for l in right_col)
    gap = "   "

    if w >= left_w + len(gap) + right_w + 4:
        rows = max(len(left_col), len(right_col))
        left_col = left_col + [""] * (rows - len(left_col))
        right_col = right_col + [""] * (rows - len(right_col))
        body_lines = [f"{l:<{left_w}}{gap}{r}".rstrip() for l, r in zip(left_col, right_col)]
    else:
        body_lines = left_col + [""] + right_col

    lines = ["MOVIE TRACKER - KEYS", ""]
    lines.extend(body_lines)
    lines.append("")
    wrap_w = max(w - 4, 20)
    for note in HELP_NOTES:
        lines.extend(textwrap.wrap(note, wrap_w))
        lines.append("")
    lines.append("Press any key to close this help.")
    show_text_screen(stdscr, "\n".join(lines))


def view_poster(stdscr, poster_path: str | None, label: str) -> None:
    full_path = config.poster_full_path(poster_path)
    if not full_path or not full_path.exists():
        flash(stdscr, "No poster image cached for this movie.")
        return
    full_path = str(full_path)
    curses.def_prog_mode()
    curses.endwin()
    os.system("clear")
    print(f"{label}\n")
    if in_kitty():
        subprocess.run(["kitty", "+kitten", "icat", "--align", "left", full_path])
    elif shutil.which("chafa"):
        subprocess.run(["chafa", full_path])
    elif shutil.which("xdg-open"):
        subprocess.Popen(
            ["xdg-open", full_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        print("Opened poster in your default image viewer.")
    else:
        print(f"Poster saved at: {full_path}")
    input("\nPress Enter to return...")
    stdscr.refresh()
    curses.reset_prog_mode()
    curses.curs_set(0)


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
            total_seasons=details["total_seasons"],
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
    total_seasons = None
    if media_type == "series":
        seasons_str = prompt(stdscr, "Total seasons (optional)") or ""
        total_seasons = int(seasons_str) if seasons_str.strip().isdigit() else None
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
        total_seasons=total_seasons,
        runtime_minutes=runtime_minutes,
        genre=genre,
        director=director,
        rated=rated,
        status="watchlist",
    )
    movie_id = db.add(m)
    flash(stdscr, f"Added '{title}'.")
    return movie_id


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
    ("total_seasons", "Total seasons", "int"),
    ("current_season", "Currently on season", "int"),
    ("rated", "Rated", "text"),
    ("genre", "Genre", "text"),
    ("director", "Director", "text"),
    ("actors", "Actors", "text"),
    ("tags", "Tags (comma-separated)", "tags"),
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
            if is_series:
                lines.append(("Seasons:", row["total_seasons"] if row["total_seasons"] else "-", None))
                if row["current_season"]:
                    progress = f"Season {row['current_season']}"
                    if row["total_seasons"]:
                        progress += f" of {row['total_seasons']}"
                else:
                    progress = "not started"
                lines.append(("Watching:", progress, None))
            lines += [
                (runtime_label, f"{row['runtime_minutes']} min" if row["runtime_minutes"] else "-", None),
                ("Rated:", row["rated"] or "-", None),
                ("Genre:", row["genre"] or "-", None),
                ("Director:", row["director"] or "-", None),
                ("Actors:", row["actors"] or "-", None),
                ("Tags:", ", ".join(tags) if tags else "-", None),
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

            footer = "r rating  n rank  M rated  w watched  t today  c +rewatch  "
            if is_series:
                footer += "N +season  "
            footer += "e notes  E edit all  p poster  x delete  q back"
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
                db.update(movie_id, rewatch_count=row["rewatch_count"] + 1)
            elif ch == ord("N") and is_series:
                season = (row["current_season"] or 0) + 1
                if row["total_seasons"] and season > row["total_seasons"]:
                    flash(stdscr, f"Already on the last season ({row['total_seasons']}).")
                else:
                    db.update(movie_id, current_season=season)
            elif ch == ord("e"):
                val = prompt(stdscr, "Notes", row["notes"] or "")
                if val is not None:
                    db.update(movie_id, notes=val)
            elif ch == ord("p"):
                clear_kitty_images()
                last_poster_key = "unset"
                view_poster(stdscr, row["poster_path"], title_line)
            elif ch == ord("x"):
                if confirm(stdscr, f"Delete '{row['title']}'?"):
                    db.delete(movie_id)
                    flash(stdscr, f"Deleted '{row['title']}'. Press 'u' from the list to undo.")
                    return
    finally:
        clear_kitty_images()


# --------------------------------------------------------------- trash ui --

def trash_screen(stdscr, db: DB) -> None:
    """Browse soft-deleted movies: restore them, or purge them for good."""
    idx = 0
    top = 0
    while True:
        deleted = db.list_deleted()
        if not deleted:
            flash(stdscr, "Trash is empty.")
            return
        idx = min(idx, len(deleted) - 1)

        stdscr.clear()
        h, w = stdscr.getmaxyx()
        stdscr.addstr(0, 0, " TRASH "[: w - 1], bar_attr())
        draw_box(stdscr, 1, 0, h - 2, w, dim_attr())

        visible_rows = max(1, h - 4)
        if idx < top:
            top = idx
        if idx >= top + visible_rows:
            top = idx - visible_rows + 1

        for row_num, i in enumerate(range(top, min(len(deleted), top + visible_rows))):
            m = deleted[i]
            line = f"{m['title'][:40]:<40} ({m['year'] or '?'})  deleted {m['deleted_at']}"
            attr = selected_attr() if i == idx else curses.A_NORMAL
            try:
                stdscr.addstr(row_num + 2, 2, line[: w - 3], attr)
            except curses.error:
                pass

        footer = "Up/Down move  Enter/r restore  x purge forever  q back"
        stdscr.addstr(h - 1, 0, footer[: w - 1], dim_attr())
        stdscr.refresh()

        ch = stdscr.getch()
        if ch in (ord("q"), 27):
            return
        elif ch in (curses.KEY_UP, ord("k")):
            idx = max(0, idx - 1)
        elif ch in (curses.KEY_DOWN, ord("j")):
            idx = min(len(deleted) - 1, idx + 1)
        elif ch in (10, 13, curses.KEY_ENTER, ord("r")):
            db.restore(deleted[idx]["id"])
            flash(stdscr, f"Restored '{deleted[idx]['title']}'.")
        elif ch == ord("x"):
            target = deleted[idx]
            if confirm_by_typing(
                stdscr, f"Permanently delete '{target['title']}'? This can't be undone.", target["title"]
            ):
                db.purge(target["id"])
                flash(stdscr, f"Permanently deleted '{target['title']}'.")
            else:
                flash(stdscr, "Cancelled.")


# --------------------------------------------------------- collections ui --

def collections_screen(stdscr, db: DB) -> None:
    """Browse your tags as franchises/collections, one row per tag with a
    watched/total count and average rating. Enter drills into that tag's
    titles, sorted by year (release order); Enter there opens the normal
    movie detail screen."""
    idx = top = 0
    while True:
        tags = db.all_tags()
        if not tags:
            flash(stdscr, "No tags yet - use 'G' from the list to group movies into collections.")
            return
        summaries = []
        for t in tags:
            movies = db.list(tag=t, sort="year")
            watched = sum(1 for m in movies if m["status"] == "watched")
            ratings = [m["my_rating"] for m in movies if m["my_rating"] is not None]
            avg = sum(ratings) / len(ratings) if ratings else None
            summaries.append({"tag": t, "count": len(movies), "watched": watched, "avg": avg})
        idx = min(idx, len(summaries) - 1)

        stdscr.clear()
        h, w = stdscr.getmaxyx()
        stdscr.addstr(0, 0, " COLLECTIONS "[: w - 1], bar_attr())
        draw_box(stdscr, 1, 0, h - 2, w, dim_attr())

        visible_rows = max(1, h - 4)
        if idx < top:
            top = idx
        if idx >= top + visible_rows:
            top = idx - visible_rows + 1

        for row_num, i in enumerate(range(top, min(len(summaries), top + visible_rows))):
            s = summaries[i]
            is_current = i == idx
            base_attr = selected_attr() if is_current else curses.A_NORMAL
            done = s["count"] > 0 and s["watched"] == s["count"]
            avg_str = f"  avg {s['avg']:.1f}" if s["avg"] is not None else ""
            bar = ascii_bar(s["watched"], s["count"], width=14)
            line = f"{s['tag']:<20} {bar} {s['watched']}/{s['count']} watched{avg_str}"
            y = row_num + 2
            try:
                stdscr.addstr(y, 2, line[: w - 3], base_attr)
            except curses.error:
                pass
            if done:
                badge = " ★ COMPLETE"
                badge_attr = base_attr if is_current else (rating_attr(10.0) | curses.A_BOLD)
                bx = 2 + len(line)
                try:
                    stdscr.addstr(y, bx, badge[: max(w - bx - 1, 0)], badge_attr)
                except curses.error:
                    pass

        footer = "Up/Down move  Enter open  q back"
        stdscr.addstr(h - 1, 0, footer[: w - 1], dim_attr())
        stdscr.refresh()

        ch = stdscr.getch()
        if ch in (ord("q"), 27):
            return
        elif ch in (curses.KEY_UP, ord("k")):
            idx = max(0, idx - 1)
        elif ch in (curses.KEY_DOWN, ord("j")):
            idx = min(len(summaries) - 1, idx + 1)
        elif ch in (10, 13, curses.KEY_ENTER, ord("l")):
            _collection_detail_screen(stdscr, db, summaries[idx]["tag"])


def _collection_detail_screen(stdscr, db: DB, tag: str) -> None:
    idx = top = 0
    while True:
        movies = db.list(tag=tag, sort="year")
        if not movies:
            flash(stdscr, f"No movies left tagged '{tag}'.")
            return
        idx = min(idx, len(movies) - 1)

        stdscr.clear()
        h, w = stdscr.getmaxyx()
        header = f" {tag} "
        stdscr.addstr(0, 0, header[: w - 1], bar_attr())
        if all(m["status"] == "watched" for m in movies):
            badge = "★ COMPLETE"
            try:
                stdscr.addstr(0, max(len(header) + 1, w - len(badge) - 1), badge[: max(w - len(header) - 2, 0)],
                              rating_attr(10.0) | curses.A_BOLD)
            except curses.error:
                pass
        draw_box(stdscr, 1, 0, h - 2, w, dim_attr())

        visible_rows = max(1, h - 4)
        if idx < top:
            top = idx
        if idx >= top + visible_rows:
            top = idx - visible_rows + 1

        for row_num, i in enumerate(range(top, min(len(movies), top + visible_rows))):
            m = movies[i]
            kind = type_icon(m["media_type"])
            rating = f"{m['my_rating']:.1f}" if m["my_rating"] is not None else "-"
            year_str = f"{(m['year'] or '?'):<10} "
            rest_str = f"{m['title'][:40]:<40} {m['status']:<9} {rating}"
            is_current = i == idx
            attr = selected_attr() if is_current else curses.A_NORMAL
            icon_attr = attr if is_current else type_icon_attr(m["media_type"])
            y = row_num + 2
            x = 2
            safe_addstr(stdscr, y, x, year_str[: max(w - 3 - (x - 2), 0)], attr)
            x += len(year_str)
            safe_addstr(stdscr, y, x, kind[: max(w - 3 - (x - 2), 0)], icon_attr)
            x += len(kind) + 1
            safe_addstr(stdscr, y, x, rest_str[: max(w - 3 - (x - 2), 0)], attr)

        footer = "Up/Down move  Enter details  q back"
        stdscr.addstr(h - 1, 0, footer[: w - 1], dim_attr())
        stdscr.refresh()

        ch = stdscr.getch()
        if ch in (ord("q"), 27):
            return
        elif ch in (curses.KEY_UP, ord("k")):
            idx = max(0, idx - 1)
        elif ch in (curses.KEY_DOWN, ord("j")):
            idx = min(len(movies) - 1, idx + 1)
        elif ch in (10, 13, curses.KEY_ENTER, ord("l")):
            clear_kitty_images()
            movie_detail(stdscr, db, movies[idx]["id"])


# ---------------------------------------------------------- poster wall ui --

POSTER_CELL_W = 18
POSTER_CELL_H = 9
POSTER_GRID_GAP = 2
POSTER_GRID_TOP = 2


def poster_wall_screen(stdscr, db: DB) -> None:
    """A browsable kitty-image grid of every cached poster, movies and
    series alike. Arrow keys move a selection between posters (paging
    automatically at the edges); Enter opens that title's detail screen."""
    if not in_kitty():
        flash(stdscr, "Poster wall needs a kitty terminal.")
        return
    movies = [
        m for m in db.list(sort="rank")
        if (p := config.poster_full_path(m["poster_path"])) and p.exists()
    ]
    if not movies:
        flash(stdscr, "No cached posters yet.")
        return

    idx = 0
    last_page = -1
    try:
        while True:
            h, w = stdscr.getmaxyx()
            cell_w = POSTER_CELL_W + POSTER_GRID_GAP
            cell_h = POSTER_CELL_H + 1 + POSTER_GRID_GAP
            cols = max(1, (w - 2 - POSTER_GRID_GAP) // cell_w)
            rows = max(1, (h - POSTER_GRID_TOP - 2) // cell_h)
            page_size = cols * rows
            idx = max(0, min(idx, len(movies) - 1))
            page = idx // page_size
            page_start = page * page_size
            page_movies = movies[page_start : page_start + page_size]

            if page != last_page:
                clear_kitty_images()
                last_page = page

            stdscr.clear()
            total_pages = (len(movies) - 1) // page_size + 1
            header = f" POSTER WALL - page {page + 1}/{total_pages} "
            stdscr.addstr(0, 0, header[: w - 1], bar_attr())
            draw_box(stdscr, 1, 0, h - 2, w, dim_attr())

            cell_positions = []
            for i, m in enumerate(page_movies):
                col, row = i % cols, i // cols
                x = 1 + POSTER_GRID_GAP + col * cell_w
                y = POSTER_GRID_TOP + row * cell_h
                cell_positions.append((x, y, m))
                is_current = page_start + i == idx
                label = f"{m['title'][:POSTER_CELL_W]:<{POSTER_CELL_W}}"
                label_attr = selected_attr() if is_current else curses.A_NORMAL
                try:
                    stdscr.addstr(y + POSTER_CELL_H, x, label[: max(w - x - 1, 0)], label_attr)
                except curses.error:
                    pass

            footer = "Arrows move  Enter details  q back"
            try:
                stdscr.addstr(h - 1, 0, footer[: w - 1], dim_attr())
            except curses.error:
                pass
            # Text must hit the real screen (refresh()) before the kitty
            # images are placed below - kitty writes graphics escape codes
            # straight to the terminal, bypassing curses entirely, so a
            # refresh() that runs afterwards would just paint blank cells
            # right back over them.
            stdscr.refresh()

            # Skip the (slow - it shells out to kitty once per poster)
            # redraw while more keystrokes are already queued, e.g. someone
            # holding an arrow key down to move fast across the grid -
            # otherwise every cell passed through blocks on a full-page
            # reload before the next keypress is even read. It'll draw once
            # the queue drains and the selection settles.
            if not input_pending(stdscr):
                for x, y, m in cell_positions:
                    full_path = config.poster_full_path(m["poster_path"])
                    try:
                        subprocess.run(
                            [
                                "kitty", "+kitten", "icat",
                                "--place", f"{POSTER_CELL_W}x{POSTER_CELL_H}@{x}x{y}",
                                "--scale-up", str(full_path),
                            ],
                            stderr=subprocess.DEVNULL,
                        )
                    except OSError:
                        pass

            ch = stdscr.getch()
            if ch in (ord("q"), 27):
                return
            elif ch in (curses.KEY_RIGHT, ord("l")):
                idx = min(len(movies) - 1, idx + 1)
            elif ch in (curses.KEY_LEFT, ord("h")):
                idx = max(0, idx - 1)
            elif ch in (curses.KEY_DOWN, ord("j")):
                idx = min(len(movies) - 1, idx + cols)
            elif ch in (curses.KEY_UP, ord("k")):
                idx = max(0, idx - cols)
            elif ch in (10, 13, curses.KEY_ENTER):
                clear_kitty_images()
                movie_detail(stdscr, db, page_movies[idx - page_start]["id"])
                last_page = -1
    finally:
        clear_kitty_images()


# ------------------------------------------------------- dramatic random --

RANDOM_SPIN_STEPS = 16
RANDOM_SPIN_START_DELAY = 40
RANDOM_SPIN_SLOWDOWN = 1.22


def dramatic_random_pick(stdscr, db: DB) -> int | None:
    """Spin through the watchlist slot-machine style, slowing down before
    landing on the actual pick, then flash the result. Returns the picked
    movie's id (or None if the watchlist is empty)."""
    watchlist = db.list(status="watchlist")
    if not watchlist:
        flash(stdscr, "Your watchlist is empty.")
        return None
    final = random.choice(watchlist)

    clear_kitty_images()
    curses.curs_set(0)
    h, w = stdscr.getmaxyx()
    delay = RANDOM_SPIN_START_DELAY
    caption = "spinning the watchlist..."
    for _ in range(RANDOM_SPIN_STEPS):
        candidate = random.choice(watchlist)
        title_line = f"{candidate['title']} ({candidate['year'] or '?'})"
        stdscr.clear()
        try:
            stdscr.addstr(h // 2 - 1, max(0, (w - len(caption)) // 2), caption[: w - 1],
                          dim_attr() | curses.A_BOLD)
            stdscr.addstr(h // 2 + 1, max(0, (w - len(title_line)) // 2), title_line[: w - 1],
                          curses.A_DIM)
        except curses.error:
            pass
        stdscr.refresh()
        curses.napms(int(delay))
        delay *= RANDOM_SPIN_SLOWDOWN

    reveal = f"*** tonight's pick: {final['title']} ({final['year'] or '?'}) ***"
    stdscr.clear()
    try:
        stdscr.addstr(h // 2, max(0, (w - len(reveal)) // 2), reveal[: w - 1],
                      rating_attr(10.0) | curses.A_BOLD)
    except curses.error:
        pass
    stdscr.refresh()
    curses.napms(900)
    return final["id"]


# --------------------------------------------------------------- stats ui --

def stats_screen(stdscr, db: DB) -> None:
    """A read-only summary of the whole collection: counts, watch time,
    average ratings, and breakdowns by genre and decade."""
    movies = db.list(sort="added")
    total = len(movies)
    series_count = sum(1 for m in movies if m["media_type"] == "series")
    movie_count = total - series_count
    watched = [m for m in movies if m["status"] == "watched"]
    watchlist_count = total - len(watched)
    total_minutes = sum(m["runtime_minutes"] or 0 for m in watched)
    hours, minutes = divmod(total_minutes, 60)

    my_ratings = [m["my_rating"] for m in movies if m["my_rating"] is not None]
    imdb_ratings = [m["imdb_rating"] for m in movies if m["imdb_rating"] is not None]
    avg_my = sum(my_ratings) / len(my_ratings) if my_ratings else None
    avg_imdb = sum(imdb_ratings) / len(imdb_ratings) if imdb_ratings else None
    total_rewatches = sum(m["rewatch_count"] or 0 for m in movies)
    trash_count = len(db.list_deleted())

    genre_counter: Counter = Counter()
    for m in movies:
        for g in (m["genre"] or "").split(","):
            g = g.strip()
            if g:
                genre_counter[g] += 1

    decade_counter: Counter = Counter()
    for m in movies:
        year_str = (m["year"] or "").strip()[:4]
        if year_str.isdigit():
            decade_counter[(int(year_str) // 10) * 10] += 1

    lines = [
        "STATISTICS",
        "",
        f"Total titles:      {total} ({len(watched)} watched, {watchlist_count} watchlist)",
        f"  {ascii_bar(len(watched), total)}" if total else "",
        f"Movies / Series:   {movie_count} / {series_count}",
        f"Total watch time:  {hours}h {minutes}m",
        f"Avg my rating:     {f'{avg_my:.1f} / 10' if avg_my is not None else '-'}",
        f"Avg IMDb rating:   {f'{avg_imdb:.1f} / 10' if avg_imdb is not None else '-'}",
        f"Total rewatches:   {total_rewatches}",
        f"In trash:          {trash_count}",
        "",
    ]
    if genre_counter:
        top_genres = genre_counter.most_common(5)
        max_genre = top_genres[0][1]
        lines.append("Top genres:")
        lines.extend(
            f"  {genre:<14} {ascii_bar(count, max_genre)} {count}"
            for genre, count in top_genres
        )
        lines.append("")
    if decade_counter:
        decade_items = sorted(decade_counter.items(), reverse=True)
        max_decade = max(count for _, count in decade_items)
        lines.append("By decade:")
        lines.extend(
            f"  {f'{decade}s':<14} {ascii_bar(count, max_decade)} {count}"
            for decade, count in decade_items
        )
        lines.append("")
    lines.append("Press any key to close.")
    show_boxed_text_screen(stdscr, "\n".join(lines))


# -------------------------------------------------------------- export --

EXPORT_FIELDS = [
    "id", "title", "year", "media_type", "total_seasons", "current_season", "rated", "genre",
    "director", "actors", "plot", "runtime_minutes", "imdb_rating", "my_rating", "my_rank",
    "status", "watched_date", "rewatch_count", "notes", "tags", "added_at",
]


def export_movies(stdscr, db: DB) -> None:
    """Export the active (non-deleted) collection to a CSV or JSON file
    under config.EXPORT_DIR."""
    fmt = choose_from_list(stdscr, "Export format:", ["CSV", "JSON"], lambda x: x)
    if not fmt:
        return
    movies = db.list(sort="added")
    config.ensure_dirs()
    config.EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")

    rows = []
    for m in movies:
        row = {k: m[k] for k in EXPORT_FIELDS if k != "tags"}
        row["tags"] = ", ".join(db.get_tags_for_movie(m["id"]))
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


def _read_import_rows(path: Path) -> list[dict]:
    """Parse a CSV or JSON file (matching the export schema) into row dicts."""
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text())
        return [data] if isinstance(data, dict) else data
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


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
            total_seasons=_row_int(row, "total_seasons"),
            current_season=_row_int(row, "current_season"),
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
        if imdb_id:
            existing_imdb_ids.add(imdb_id)
        existing_title_years.add((title.lower(), year))
        imported += 1

    flash(stdscr, f"Imported {imported} title(s), skipped {skipped} duplicate/invalid row(s).")


# --------------------------------------------------------------- list ui --

STATUS_CYCLE = [None, "watchlist", "watched"]
TYPE_CYCLE = [None, "movie", "series"]


class App:
    def __init__(self, db: DB):
        self.db = db
        self.sort = "title"
        self.query = ""
        self.status_idx = 0
        self.type_idx = 0
        self.tag_filter: str | None = None
        self.idx = 0
        self.top = 0
        self.last_preview_key = "unset"
        self.selected_ids: set[int] = set()

    @property
    def status_filter(self):
        return STATUS_CYCLE[self.status_idx]

    @property
    def type_filter(self):
        return TYPE_CYCLE[self.type_idx]

    def movies(self):
        return self.db.list(
            sort=self.sort, query=self.query, status=self.status_filter,
            tag=self.tag_filter, media_type=self.type_filter,
        )

    def cycle_tag_filter(self) -> None:
        tags = self.db.all_tags()
        options = [None] + tags
        try:
            i = options.index(self.tag_filter)
        except ValueError:
            i = 0
        self.tag_filter = options[(i + 1) % len(options)]
        self.idx = 0

    def bulk_targets(self, movies) -> list[int]:
        """Marked movie ids, or just the currently selected one if
        nothing's marked - the target set for G/d/w/W actions."""
        if self.selected_ids:
            return list(self.selected_ids)
        if movies:
            return [movies[self.idx]["id"]]
        return []

    def draw(self, stdscr):
        # clear() (not erase()) also marks the whole window dirty, forcing a
        # full repaint on the next refresh() instead of curses's usual
        # diff-based update. Needed because the kitty poster preview writes
        # straight to the terminal via a subprocess (see update_preview()),
        # which curses doesn't know about - without this, its cached idea of
        # what's already on screen goes stale and rows can render in the
        # wrong place (e.g. the footer legend landing over a movie row).
        stdscr.clear()
        h, w = stdscr.getmaxyx()
        movies = self.movies()
        if self.idx >= len(movies):
            self.idx = max(0, len(movies) - 1)

        box_attr = dim_attr()
        draw_box(stdscr, 1, 0, h - 2, w, box_attr)
        interior_left, interior_right = 1, w - 2

        # The side column (poster preview + live details panel) needs a
        # kitty terminal for the poster, but the details panel is plain
        # text - show it whenever there's room, poster or not.
        side_panel_on = w >= PREVIEW_MIN_TERM_WIDTH and bool(movies)
        poster_on = side_panel_on and in_kitty()
        if side_panel_on:
            preview_w = max(PREVIEW_PANEL_WIDTH_MIN, min(PREVIEW_PANEL_WIDTH_MAX, w // 3))
            divider_x = interior_right - preview_w
            content_w = divider_x
            for row_y in range(2, h - 2):
                safe_addstr(stdscr, row_y, divider_x, "│", box_attr)
            safe_addstr(stdscr, 1, divider_x, "┬", box_attr)
            safe_addstr(stdscr, h - 2, divider_x, "┴", box_attr)
        else:
            preview_w = 0
            divider_x = None
            content_w = interior_right + 1

        header = " MOVIE TRACKER "
        _, sort_dir = SORT_FIELDS.get(self.sort, SORT_FIELDS["rank"])
        sort_arrow = "▲" if sort_dir == "asc" else "▼"
        sub = f"sort:{self.sort}{sort_arrow}  status:{self.status_filter or 'all'}"
        if self.type_filter:
            sub += f"  type:{self.type_filter}"
        if self.tag_filter:
            sub += f"  tag:'{self.tag_filter}'"
        if self.query:
            sub += f"  filter:'{self.query}'"
        if self.selected_ids:
            sub += f"  selected:{len(self.selected_ids)}"
        if movies:
            sub += f"  {self.idx + 1}/{len(movies)}"
        stdscr.addstr(0, 0, header[: w - 1], bar_attr())
        stdscr.addstr(0, max(len(header) + 1, w - len(sub) - 1), sub[: max(w - len(header) - 2, 0)])

        title_w, show_runtime, show_mine, show_imdb, show_tags = compute_row_layout(content_w - interior_left)

        col_y = 2
        header_parts = [" ", f"{'#':>3}", f"{'Rk':>4}", f"{'Ty':<2}", f"{'Title':<{title_w}}", f"{'Rated':<6}", f"{'Year':<6}"]
        if show_runtime:
            header_parts.append(f"{'Runtime':>8}")
        if show_mine:
            header_parts.append(f"{'Mine':>5}")
        if show_imdb:
            header_parts.append(f"{'IMDb':>5}")
        if show_tags:
            header_parts.append(f"{'Tags':<20}")
        header_parts.append("Status")
        cols = " ".join(header_parts)
        stdscr.addstr(col_y, interior_left, cols[: content_w - interior_left], accent_attr() | curses.A_UNDERLINE)

        visible_rows = h - col_y - 4
        if visible_rows < 1:
            visible_rows = 1
        if self.idx < self.top:
            self.top = self.idx
        if self.idx >= self.top + visible_rows:
            self.top = self.idx - visible_rows + 1

        for row_num, i in enumerate(range(self.top, min(len(movies), self.top + visible_rows))):
            m = movies[i]
            rank = m["my_rank"] if m["my_rank"] is not None else "-"
            is_current = i == self.idx
            is_marked = m["id"] in self.selected_ids
            base_attr = selected_attr() if is_current else curses.A_NORMAL
            y = col_y + 1 + row_num
            x = interior_left

            def put(text: str, attr: int) -> None:
                nonlocal x
                try:
                    stdscr.addstr(y, x, text[: max(content_w - x, 0)], attr)
                except curses.error:
                    pass
                x += len(text) + 1

            marker = "*" if is_marked else " "
            put(marker, base_attr if is_current else (accent_attr() if is_marked else base_attr))
            put(f"{i + 1:>3}", base_attr)
            put(f"{str(rank):>4}", base_attr)
            put(f"{type_icon(m['media_type']):<2}", base_attr if is_current else type_icon_attr(m["media_type"]))
            put(f"{m['title'][:title_w]:<{title_w}}", base_attr)
            put(f"{(m['rated'] or '-')[:6]:<6}", base_attr)
            put(f"{(m['year'] or '')[:6]:<6}", base_attr)
            if show_runtime:
                runtime = f"{m['runtime_minutes']}m" if m["runtime_minutes"] else "-"
                put(f"{runtime:>8}", base_attr)
            if show_mine:
                mine = f"{m['my_rating']:.1f}" if m["my_rating"] is not None else "-"
                put(f"{mine:>5}", base_attr if is_current else rating_attr(m["my_rating"]))
            if show_imdb:
                imdb_r = f"{m['imdb_rating']:.1f}" if m["imdb_rating"] is not None else "-"
                put(f"{imdb_r:>5}", base_attr if is_current else rating_attr(m["imdb_rating"]))
            if show_tags:
                tags_str = ", ".join(self.db.get_tags_for_movie(m["id"])) or "-"
                put(f"{tags_str[:20]:<20}", base_attr)
            watched = m["status"] == "watched"
            status_symbol = "✓" if watched else "-"
            status_attr = base_attr if (is_current or not watched) else blue_attr()
            put(f"{status_symbol:<2}", status_attr)

        if not movies:
            msg = "No movies yet. Press 'a' to search & add, or 'm' to add manually."
            safe_addstr(stdscr, col_y + 2, interior_left + 1, msg[: max(content_w - interior_left - 2, 0)])
        else:
            more_above = self.top
            more_below = max(0, len(movies) - (self.top + visible_rows))
            if more_above or more_below:
                parts = []
                if more_above:
                    parts.append(f"^ {more_above} more above")
                if more_below:
                    parts.append(f"v {more_below} more below")
                hint = "   ".join(parts)
                safe_addstr(stdscr, h - 3, interior_left + 1, hint[: max(content_w - interior_left - 2, 0)], dim_attr())

        footer = (
            "a add  m manual  Enter details  E edit  / filter  s sort  Tab status  y type  g tag  "
            "Space mark  G add tag(s)  d delete  u undo  w watched  W watchlist  "
            "R random  P poster wall  T trash  S stats  C collections  B backup  X export  I import  K api-key  ? help  q quit"
        )
        stdscr.addstr(h - 1, 0, footer[: w - 1], dim_attr())

        selected = movies[self.idx] if movies else None
        if side_panel_on:
            side_left = divider_x + 1
            side_w = interior_right - side_left + 1
            interior_h = (h - 3) - col_y + 1
            if poster_on:
                details_h = min(
                    DETAILS_PANEL_TARGET_LINES,
                    max(0, interior_h - 1 - DETAILS_PANEL_MIN_POSTER_H),
                )
                poster_h = interior_h - 1 - details_h
            else:
                poster_h = 0
                details_h = interior_h
            if poster_on and poster_h > 0 and details_h > 0:
                divider_y = col_y + poster_h
                for col in range(side_left, interior_right + 1):
                    safe_addstr(stdscr, divider_y, col, "─", box_attr)
                safe_addstr(stdscr, divider_y, divider_x, "├", box_attr)
                safe_addstr(stdscr, divider_y, w - 1, "┤", box_attr)
                details_top = divider_y + 1
            else:
                details_top = col_y
            self.draw_details_panel(stdscr, selected, side_left, details_top, side_w, details_h)
        stdscr.refresh()

        if poster_on and input_pending(stdscr):
            pass
        elif poster_on:
            preview_top = col_y
            preview_height = max(1, poster_h)
            preview_left = divider_x + 1
            self.update_preview(selected, (preview_left, preview_top, preview_w - 1, preview_height))
        else:
            self.update_preview(None, None)

    def draw_details_panel(self, stdscr, movie, x: int, y: int, w: int, h: int) -> None:
        """A compact, always-visible summary of the selected movie, shown
        in the side column (below its poster preview, or filling the
        whole column when there's no kitty poster) - so key details are
        visible while just scrolling the list, no need to open each one."""
        if not movie or h <= 0 or w <= 0:
            return
        row = y
        title = movie["title"]
        year = f" ({movie['year']})" if movie["year"] else ""
        safe_addstr(stdscr, row, x, f"{title}{year}"[:w], accent_attr() | curses.A_BOLD)
        row += 1
        if row >= y + h:
            return
        is_series = movie["media_type"] == "series"
        safe_addstr(stdscr, row, x, type_icon(movie["media_type"]), type_icon_attr(movie["media_type"]))
        safe_addstr(stdscr, row, x + 2, ("Series" if is_series else "Movie")[: max(w - 2, 0)])
        row += 1
        if row >= y + h:
            return
        watched = movie["status"] == "watched"
        safe_addstr(stdscr, row, x, ("✓ Watched" if watched else "- Watchlist")[:w], blue_attr() if watched else 0)
        row += 2

        fields = []
        if movie["my_rating"] is not None:
            fields.append(("My rating:", f"{movie['my_rating']:.1f}/10", movie["my_rating"]))
        if movie["imdb_rating"] is not None:
            fields.append(("IMDb:", f"{movie['imdb_rating']:.1f}/10", movie["imdb_rating"]))
        if movie["my_rank"] is not None:
            fields.append(("Rank:", f"#{movie['my_rank']}", None))
        if movie["rated"]:
            fields.append(("Rated:", movie["rated"], None))
        if movie["runtime_minutes"]:
            fields.append(("Runtime:", f"{movie['runtime_minutes']} min", None))
        if movie["genre"]:
            fields.append(("Genre:", movie["genre"], None))
        if movie["director"]:
            fields.append(("Director:", movie["director"], None))
        tags = self.db.get_tags_for_movie(movie["id"])
        if tags:
            fields.append(("Tags:", ", ".join(tags), None))

        for label, value, rating_val in fields:
            if row >= y + h:
                break
            safe_addstr(stdscr, row, x, label[:w], dim_attr())
            val_x = x + len(label) + 1
            if val_x < x + w:
                safe_addstr(stdscr, row, val_x, str(value)[: max(x + w - val_x, 0)], rating_attr(rating_val))
            row += 1

    def update_preview(self, movie, rect) -> None:
        if not in_kitty():
            return
        key = (movie["id"], movie["poster_path"], rect) if movie else None
        if key == self.last_preview_key:
            return
        self.last_preview_key = key
        clear_kitty_images()
        if not movie or not rect:
            return
        full_path = config.poster_full_path(movie["poster_path"])
        if not full_path or not full_path.exists():
            return
        left, top, width, height = rect
        try:
            subprocess.run(
                [
                    "kitty", "+kitten", "icat",
                    "--place", f"{width}x{height}@{left}x{top}",
                    "--scale-up",
                    str(full_path),
                ],
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            pass

    def run(self, stdscr):
        curses.curs_set(0)
        try:
            curses.start_color()
            curses.use_default_colors()
        except curses.error:
            pass
        setup_theme()
        while True:
            self.draw(stdscr)
            movies = self.movies()
            ch = stdscr.getch()
            if ch in (ord("q"),):
                clear_kitty_images()
                return
            elif ch in (curses.KEY_UP, ord("k")):
                self.idx = max(0, self.idx - 1)
            elif ch in (curses.KEY_DOWN, ord("j")):
                self.idx = min(max(len(movies) - 1, 0), self.idx + 1)
            elif ch in (10, 13, curses.KEY_ENTER, ord("l")):
                if movies:
                    clear_kitty_images()
                    self.last_preview_key = "unset"
                    movie_detail(stdscr, self.db, movies[self.idx]["id"])
            elif ch == ord("E"):
                if movies:
                    clear_kitty_images()
                    self.last_preview_key = "unset"
                    edit_movie_form(stdscr, self.db, movies[self.idx]["id"])
            elif ch == ord("a"):
                clear_kitty_images()
                self.last_preview_key = "unset"
                new_ids = add_via_search(stdscr, self.db)
                if len(new_ids) == 1:
                    movie_detail(stdscr, self.db, new_ids[0])
            elif ch == ord("m"):
                clear_kitty_images()
                self.last_preview_key = "unset"
                new_id = add_manually(stdscr, self.db)
                if new_id:
                    movie_detail(stdscr, self.db, new_id)
            elif ch == ord("d"):
                target_ids = self.bulk_targets(movies)
                if target_ids:
                    if len(target_ids) == 1:
                        title = next((m["title"] for m in movies if m["id"] == target_ids[0]), "this movie")
                        question = f"Delete '{title}'?"
                    else:
                        question = f"Delete {len(target_ids)} selected movies?"
                    if confirm(stdscr, question):
                        self.db.delete_many(target_ids)
                        flash(stdscr, f"Deleted {len(target_ids)} movie(s). Press 'u' to undo.")
                        self.selected_ids.clear()
            elif ch == ord("u"):
                restored = self.db.undo_last_delete()
                if not restored:
                    flash(stdscr, "Nothing to undo.")
                elif len(restored) == 1:
                    flash(stdscr, f"Restored '{restored[0]['title']}'.")
                else:
                    flash(stdscr, f"Restored {len(restored)} movie(s).")
            elif ch == ord("w"):
                target_ids = self.bulk_targets(movies)
                if target_ids:
                    for mid in target_ids:
                        row = self.db.get(mid)
                        updates = {"status": "watched"}
                        if not row["watched_date"]:
                            updates["watched_date"] = date.today().isoformat()
                        self.db.update(mid, **updates)
                    flash(stdscr, f"Marked {len(target_ids)} movie(s) as watched.")
                    self.selected_ids.clear()
            elif ch == ord("W"):
                target_ids = self.bulk_targets(movies)
                if target_ids:
                    for mid in target_ids:
                        self.db.update(mid, status="watchlist")
                    flash(stdscr, f"Marked {len(target_ids)} movie(s) as watchlist.")
                    self.selected_ids.clear()
            elif ch == ord("B"):
                dest = config.backup_now()
                flash(stdscr, f"Backed up to {dest}")
            elif ch == ord("X"):
                clear_kitty_images()
                self.last_preview_key = "unset"
                export_movies(stdscr, self.db)
            elif ch == ord("I"):
                clear_kitty_images()
                self.last_preview_key = "unset"
                import_movies(stdscr, self.db)
            elif ch == ord("R"):
                clear_kitty_images()
                self.last_preview_key = "unset"
                movie_id = dramatic_random_pick(stdscr, self.db)
                if movie_id:
                    movie_detail(stdscr, self.db, movie_id)
            elif ch == ord("T"):
                clear_kitty_images()
                self.last_preview_key = "unset"
                trash_screen(stdscr, self.db)
            elif ch == ord("S"):
                clear_kitty_images()
                self.last_preview_key = "unset"
                stats_screen(stdscr, self.db)
            elif ch == ord("C"):
                clear_kitty_images()
                self.last_preview_key = "unset"
                collections_screen(stdscr, self.db)
            elif ch == ord("P"):
                clear_kitty_images()
                self.last_preview_key = "unset"
                poster_wall_screen(stdscr, self.db)
            elif ch == ord("y"):
                self.type_idx = (self.type_idx + 1) % len(TYPE_CYCLE)
                self.idx = 0
            elif ch == ord("g"):
                self.cycle_tag_filter()
            elif ch == ord(" "):
                if movies:
                    mid = movies[self.idx]["id"]
                    if mid in self.selected_ids:
                        self.selected_ids.discard(mid)
                    else:
                        self.selected_ids.add(mid)
                    self.idx = min(max(len(movies) - 1, 0), self.idx + 1)
            elif ch == 27:
                self.selected_ids.clear()
            elif ch == ord("G"):
                target_ids = self.bulk_targets(movies)
                if len(target_ids) > 1:
                    label = f"Add tag(s) to {len(target_ids)} selected movies (comma-separated)"
                elif target_ids:
                    title = next((m["title"] for m in movies if m["id"] == target_ids[0]), "this movie")
                    label = f"Add tag(s) to '{title}' (comma-separated)"
                else:
                    label = ""
                if not target_ids:
                    flash(stdscr, "No movies to tag.")
                else:
                    val = prompt(stdscr, label)
                    if val:
                        names = [n.strip() for n in val.split(",") if n.strip()]
                        if names:
                            self.db.add_tags_to_movies(target_ids, names)
                            flash(stdscr, f"Added {', '.join(names)} to {len(target_ids)} movie(s).")
                            self.selected_ids.clear()
            elif ch == ord("/"):
                q = prompt(stdscr, "Filter by title (empty to clear)", self.query)
                if q is not None:
                    self.query = q
                    self.idx = 0
            elif ch == ord("s"):
                keys = list(SORT_FIELDS.keys())
                self.sort = keys[(keys.index(self.sort) + 1) % len(keys)]
            elif ch == ord("\t"):
                self.status_idx = (self.status_idx + 1) % len(STATUS_CYCLE)
                self.idx = 0
            elif ch == ord("K"):
                key = prompt(stdscr, "OMDb API key", config.get_api_key() or "")
                if key:
                    config.set_api_key(key.strip())
                    flash(stdscr, "API key saved.")
            elif ch == ord("?"):
                clear_kitty_images()
                self.last_preview_key = "unset"
                show_help_screen(stdscr)


def _run(stdscr, db: DB):
    App(db).run(stdscr)


def main() -> None:
    # Needed for ncurses to render non-ASCII text (the movie/series icons,
    # the watched checkmark) correctly instead of mangling or dropping it -
    # must be set before initscr() runs.
    locale.setlocale(locale.LC_ALL, "")
    # ncurses waits ~1s after a bare Esc to see if more bytes are coming
    # (part of an arrow-key/function-key sequence) before delivering it.
    # This app doesn't use any Esc-prefixed sequences itself, so a short
    # delay keeps every Esc-to-cancel/discard/back feeling instant. Must
    # be set before initscr() runs.
    os.environ.setdefault("ESCDELAY", "25")
    config.ensure_dirs()
    db = DB()
    try:
        curses.wrapper(_run, db)
    finally:
        db.close()
