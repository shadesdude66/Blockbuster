"""Curses TUI for Blockbuster: the main list screen and entry point."""

import argparse
import curses
import locale
import os
import subprocess
from datetime import date

from . import __version__, config
from .adding import add_manually, add_via_search, fill_missing_info, require_api_key
from .db import DB, SORT_FIELDS
from .metadata import missing_info_ids
from .detail import edit_movie_form, movie_detail
from .import_export import export_movies, import_movies
from .screens import (
    aging_watchlist_screen,
    collections_screen,
    dramatic_random_pick,
    poster_wall_screen,
    recommendations_screen,
    stats_screen,
    trash_screen,
    watch_heatmap_screen,
)
from .widgets import (
    PREVIEW_MIN_TERM_WIDTH,
    PREVIEW_PANEL_WIDTH_MAX,
    PREVIEW_PANEL_WIDTH_MIN,
    accent_attr,
    bar_attr,
    blue_attr,
    clear_kitty_images,
    confirm,
    dim_attr,
    draw_box,
    flash,
    in_kitty,
    input_pending,
    prompt,
    rating_attr,
    safe_addstr,
    selected_attr,
    setup_theme,
    show_help_screen,
    type_icon,
    type_icon_attr,
)


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
ROW_FORMATS_WIDTH = 13
ROW_TITLE_WIDTH_DEFAULT = 35
ROW_TITLE_WIDTH_SOFT_MIN = 15
ROW_TITLE_WIDTH_HARD_MIN = 8


def compute_row_layout(content_w: int):
    """Pick a title width and which optional columns fit in content_w.

    Shrinks the title column first, then drops Formats/Tags/Runtime/Mine/
    IMDb (in that order - Formats first, since it's the newest/most
    optional column)
    if it's still too tight, then shrinks the title further as a last
    resort so the poster panel can show even on narrow terminals.
    """
    title_w = ROW_TITLE_WIDTH_DEFAULT
    show_runtime = show_mine = show_imdb = show_tags = show_formats = True

    def total():
        return (
            ROW_CORE_FIXED_WIDTH
            + title_w
            + (ROW_RUNTIME_WIDTH if show_runtime else 0)
            + (ROW_MINE_WIDTH if show_mine else 0)
            + (ROW_IMDB_WIDTH if show_imdb else 0)
            + (ROW_TAGS_WIDTH if show_tags else 0)
            + (ROW_FORMATS_WIDTH if show_formats else 0)
        )

    while total() > content_w and title_w > ROW_TITLE_WIDTH_SOFT_MIN:
        title_w -= 1
    while total() > content_w and show_formats:
        show_formats = False
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
    return title_w, show_runtime, show_mine, show_imdb, show_tags, show_formats


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
        self.format_filter: str | None = None
        self.decade_filter: int | None = None
        self.rating_min: float | None = None
        self.runtime_max: int | None = None
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
            tag=self.tag_filter, format=self.format_filter,
            media_type=self.type_filter,
            decade=self.decade_filter, rating_min=self.rating_min,
            runtime_max=self.runtime_max,
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

    def cycle_format_filter(self) -> None:
        formats = self.db.all_formats()
        options = [None] + formats
        try:
            i = options.index(self.format_filter)
        except ValueError:
            i = 0
        self.format_filter = options[(i + 1) % len(options)]
        self.idx = 0

    def cycle_decade_filter(self) -> None:
        decades = self.db.all_decades()
        options = [None] + decades
        try:
            i = options.index(self.decade_filter)
        except ValueError:
            i = 0
        self.decade_filter = options[(i + 1) % len(options)]
        self.idx = 0

    def bulk_targets(self, movies) -> list[int]:
        """Marked movie ids, or just the currently selected one if
        nothing's marked - the target set for G/O/d/w/W actions."""
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

        header = " BLOCKBUSTER "
        _, sort_dir = SORT_FIELDS.get(self.sort, SORT_FIELDS["rank"])
        sort_arrow = "▲" if sort_dir == "asc" else "▼"
        sub = f"sort:{self.sort}{sort_arrow}  status:{self.status_filter or 'all'}"
        if self.type_filter:
            sub += f"  type:{self.type_filter}"
        if self.tag_filter:
            sub += f"  tag:'{self.tag_filter}'"
        if self.format_filter:
            sub += f"  format:'{self.format_filter}'"
        if self.decade_filter is not None:
            sub += f"  decade:{self.decade_filter}s"
        if self.rating_min is not None:
            sub += f"  rating>={self.rating_min:g}"
        if self.runtime_max is not None:
            sub += f"  runtime<={self.runtime_max}m"
        if self.query:
            sub += f"  filter:'{self.query}'"
        if self.selected_ids:
            sub += f"  selected:{len(self.selected_ids)}"
        if movies:
            sub += f"  {self.idx + 1}/{len(movies)}"
        stdscr.addstr(0, 0, header[: w - 1], bar_attr())
        stdscr.addstr(0, max(len(header) + 1, w - len(sub) - 1), sub[: max(w - len(header) - 2, 0)])

        title_w, show_runtime, show_mine, show_imdb, show_tags, show_formats = compute_row_layout(content_w - interior_left)

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
        if show_formats:
            header_parts.append(f"{'Format':<12}")
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
            if show_formats:
                formats_str = ", ".join(self.db.get_formats_for_movie(m["id"])) or "-"
                put(f"{formats_str[:12]:<12}", base_attr)
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
            "a add  m manual  Enter details  E edit  / filter  s sort  Tab status  y type  g tag  f format  "
            "Space mark  G add tag(s)  O add format(s)  d delete  u undo  w watched  W watchlist  "
            "R random  P poster wall  T trash  S stats  C collections  B backup  X export  I import  U fill info  K api-key  ? help  q quit"
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
        formats = self.db.get_formats_for_movie(movie["id"])
        if formats:
            fields.append(("Formats:", ", ".join(formats), None))

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
            elif ch == ord("U"):
                clear_kitty_images()
                self.last_preview_key = "unset"
                if not require_api_key(stdscr):
                    target_ids = []
                elif self.selected_ids:
                    target_ids = list(self.selected_ids)
                else:
                    target_ids = missing_info_ids(self.db)
                    if not target_ids:
                        flash(stdscr, "Every title already has its OMDb info.")
                    elif not confirm(stdscr, f"Fill in missing info from OMDb for {len(target_ids)} title(s)?"):
                        target_ids = []
                if target_ids:
                    fill_missing_info(stdscr, self.db, target_ids)
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
            elif ch == ord("f"):
                self.cycle_format_filter()
            elif ch == ord("D"):
                self.cycle_decade_filter()
            elif ch == ord("F"):
                clear_kitty_images()
                self.last_preview_key = "unset"
                recommendations_screen(stdscr, self.db)
            elif ch == ord("A"):
                clear_kitty_images()
                self.last_preview_key = "unset"
                aging_watchlist_screen(stdscr, self.db)
            elif ch == ord("H"):
                clear_kitty_images()
                self.last_preview_key = "unset"
                watch_heatmap_screen(stdscr, self.db)
            elif ch == ord("r"):
                val = prompt(stdscr, "Minimum rating filter, 0-10 (blank = clear)", "" if self.rating_min is None else str(self.rating_min))
                if val is not None:
                    try:
                        self.rating_min = float(val) if val.strip() else None
                    except ValueError:
                        flash(stdscr, "Enter a number between 0 and 10.")
                    else:
                        self.idx = 0
            elif ch == ord("t"):
                val = prompt(stdscr, "Maximum runtime filter, minutes (blank = clear)", "" if self.runtime_max is None else str(self.runtime_max))
                if val is not None:
                    try:
                        self.runtime_max = int(val) if val.strip() else None
                    except ValueError:
                        flash(stdscr, "Enter a whole number of minutes.")
                    else:
                        self.idx = 0
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
            elif ch == ord("O"):
                target_ids = self.bulk_targets(movies)
                if len(target_ids) > 1:
                    label = f"Add format(s) to {len(target_ids)} selected movies (comma-separated)"
                elif target_ids:
                    title = next((m["title"] for m in movies if m["id"] == target_ids[0]), "this movie")
                    label = f"Add format(s) to '{title}' (comma-separated)"
                else:
                    label = ""
                if not target_ids:
                    flash(stdscr, "No movies to add a format to.")
                else:
                    val = prompt(stdscr, label)
                    if val:
                        names = [n.strip() for n in val.split(",") if n.strip()]
                        if names:
                            self.db.add_formats_to_movies(target_ids, names)
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
                changed = False
                key = prompt(stdscr, "OMDb API key", config.get_api_key() or "")
                if key:
                    config.set_api_key(key.strip())
                    changed = True
                tkey = prompt(
                    stdscr, "TMDb API key (optional, for 'o' in detail view)",
                    config.get_tmdb_api_key() or "",
                )
                if tkey:
                    config.set_tmdb_api_key(tkey.strip())
                    changed = True
                if changed:
                    flash(stdscr, "API key(s) saved.")
            elif ch == ord("?"):
                clear_kitty_images()
                self.last_preview_key = "unset"
                show_help_screen(stdscr)


def _run(stdscr, db: DB):
    App(db).run(stdscr)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="blockbuster",
        description="A keyboard-driven terminal app for tracking movies and TV series you've watched.",
    )
    parser.add_argument("--version", action="version", version=f"blockbuster {__version__}")
    parser.add_argument(
        "--export-obsidian",
        metavar="DIR",
        help="export the library as an Obsidian vault (markdown notes) to DIR and exit",
    )
    args = parser.parse_args()

    if args.export_obsidian:
        from . import obsidian_export

        config.ensure_dirs()
        db = DB()
        try:
            counts = obsidian_export.export_vault(db, args.export_obsidian)
        finally:
            db.close()
        print(
            f"Exported {counts['movies']} titles, {counts['people']} people, "
            f"{counts['genres']} genres, {counts['collections']} collections, "
            f"and {counts['formats']} formats "
            f"to {args.export_obsidian}"
        )
        return

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
