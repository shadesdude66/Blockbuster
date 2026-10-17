"""Shared curses helpers for Blockbuster: theme colors, drawing
primitives, prompts, and the help/poster screens."""

import curses
import os
import shutil
import subprocess
import textwrap

from . import config, theme


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
        ("f", "cycle format filter"),
        ("D", "cycle decade filter"),
        ("r", "set minimum rating filter"),
        ("t", "set maximum runtime filter"),
        ("Space", "mark/unmark selected"),
        ("Esc", "clear all marks"),
        ("G", "tag marked movie(s)"),
        ("O", "add format to marked movie(s)"),
    ]),
    ("MOVIES & TV SHOWS", [
        ("a", "add via OMDb search"),
        ("m", "add manually, no internet"),
        ("d", "delete marked (soft delete)"),
        ("u", "undo last delete"),
        ("w", "mark watched"),
        ("W", "mark watchlist"),
        ("R", "random watchlist pick"),
        ("F", "recommended for you"),
        ("A", "aging watchlist (oldest first)"),
        ("H", "watch activity heatmap"),
        ("P", "poster wall (kitty only)"),
        ("T", "trash: browse/restore/purge"),
        ("S", "stats screen"),
        ("C", "collections screen"),
        ("B", "back up database now"),
        ("X", "export to CSV/JSON/Obsidian vault"),
        ("I", "import from CSV/JSON/Letterboxd"),
        ("U", "fill in missing info from OMDb"),
    ]),
    ("MOVIE DETAIL SCREEN", [
        ("r", "set your rating (0-10)"),
        ("n", "set your rank"),
        ("M", "set content rating"),
        ("w", "toggle watched/watchlist"),
        ("t", "set watched date to today"),
        ("c", "+1 rewatch count"),
        ("v", "view rewatch history"),
        ("o", "where to watch + trailer (needs TMDb key)"),
        ("e", "edit notes"),
        ("E", "edit all fields at once"),
        ("p", "view poster fullscreen"),
        ("u", "refresh poster from OMDb"),
        ("f", "fill in missing info from OMDb"),
        ("x", "delete this movie"),
    ]),
    ("OTHER", [
        ("K", "set/update API keys (OMDb, TMDb)"),
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

    lines = ["BLOCKBUSTER - KEYS", ""]
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
