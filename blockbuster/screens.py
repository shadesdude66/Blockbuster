"""Secondary screens reached from the main list: trash, collections,
poster wall, recommendations, aging watchlist, random pick, watch
heatmap, and stats."""

import curses
import random
import subprocess
from collections import Counter
from datetime import date, timedelta

from . import config
from .db import DB
from .detail import movie_detail
from .recommend import recommend_watchlist
from .widgets import (
    PREVIEW_MIN_TERM_WIDTH,
    PREVIEW_PANEL_WIDTH_MAX,
    PREVIEW_PANEL_WIDTH_MIN,
    accent_attr,
    ascii_bar,
    bar_attr,
    blue_attr,
    choose_from_list,
    clear_kitty_images,
    confirm_by_typing,
    dim_attr,
    draw_box,
    flash,
    in_kitty,
    input_pending,
    rating_attr,
    safe_addstr,
    selected_attr,
    show_boxed_text_screen,
    type_icon,
    type_icon_attr,
)


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


# --------------------------------------------------------- recommended --


def recommendations_screen(stdscr, db: DB) -> None:
    """Watchlist titles most similar (by genre/director/cast overlap) to
    your highly-rated watched movies. Enter opens a pick's details."""
    scored = recommend_watchlist(db)
    if not scored:
        flash(
            stdscr,
            "No recommendations yet - rate some watched movies (press 'r') "
            "to build a taste profile.",
        )
        return

    def formatter(item):
        m, score, reasons = item
        why = "; ".join(reasons)
        return f"{m['title']} ({m['year'] or '?'})  [{score:.1f}]  {why}"

    picked = choose_from_list(
        stdscr, "RECOMMENDED FOR YOU (from your watchlist)", scored, formatter
    )
    if picked:
        movie_detail(stdscr, db, picked[0]["id"])


# ---------------------------------------------------- aging watchlist ui --

AGING_WATCHLIST_DAYS = 90


def aging_watchlist_screen(stdscr, db: DB) -> None:
    """Watchlist titles that have been sitting unwatched for a while,
    oldest first - a nudge to dig out the backlog rather than just
    adding more to it."""
    rows = db.aging_watchlist(AGING_WATCHLIST_DAYS)
    if not rows:
        flash(stdscr, f"Nothing's been sitting on your watchlist {AGING_WATCHLIST_DAYS}+ days.")
        return

    today = date.today()

    def formatter(m):
        try:
            added = date.fromisoformat((m["added_at"] or "")[:10])
            days = (today - added).days
        except ValueError:
            days = "?"
        return f"{m['title']} ({m['year'] or '?'})  added {days}d ago"

    picked = choose_from_list(stdscr, "AGING WATCHLIST (oldest first)", rows, formatter)
    if picked:
        movie_detail(stdscr, db, picked["id"])


# ------------------------------------------------------- dramatic random --

RANDOM_SPIN_STEPS = 16
RANDOM_SPIN_START_DELAY = 40
RANDOM_SPIN_SLOWDOWN = 1.22

# Fraction of RANDOM_SPIN_STEPS at which each of the three reels stops
# spinning and locks onto the final pick - left first, then middle, then
# right, like a real slot machine.
REEL_STOP_FRACTIONS = (0.5, 0.75, 0.95)
REEL_FLASH_STEPS = 6
REEL_FLASH_DELAY_MS = 140


def _spin_attr(step: int) -> int:
    """Cycle through the theme's accent/blue/rating colors while a reel
    is still spinning, so the blur of titles reads as colorful motion."""
    cycle = (accent_attr(), blue_attr(), rating_attr(9.0), rating_attr(6.0), rating_attr(2.0))
    return cycle[step % len(cycle)] | curses.A_BOLD


def dramatic_random_pick(stdscr, db: DB) -> int | None:
    """Spin three slot-machine reels through the watchlist, stopping them
    one at a time on the actual pick, flash the winner, then (in kitty)
    pop up its poster. Returns the picked movie's id (or None if the
    watchlist is empty)."""
    watchlist = db.list(status="watchlist")
    if not watchlist:
        flash(stdscr, "Your watchlist is empty.")
        return None
    final = random.choice(watchlist)
    final_line = f"{final['title']} ({final['year'] or '?'})"

    clear_kitty_images()
    curses.curs_set(0)
    h, w = stdscr.getmaxyx()

    box_w = max(34, min(max(len(final_line), 26) + 10, w - 2))
    box_h = min(8, max(6, h - 1))
    box_y = max(0, h // 2 - box_h // 2)
    box_x = max(0, (w - box_w) // 2)
    interior_w = max(1, box_w - 4)

    def center_text(y: int, text: str, attr: int) -> None:
        text = text[:interior_w]
        x = box_x + 2 + max(0, (interior_w - len(text)) // 2)
        safe_addstr(stdscr, y, x, text, attr)

    reel_stop_steps = [int(RANDOM_SPIN_STEPS * f) for f in REEL_STOP_FRACTIONS]

    def draw_frame(box_attr: int, reel_lines: list[tuple[str, int]]) -> None:
        stdscr.clear()
        draw_box(stdscr, box_y, box_x, box_h, box_w, box_attr)
        center_text(box_y + 1, "🎬 SPINNING THE WATCHLIST 🎬", box_attr)
        for i, (text, attr) in enumerate(reel_lines):
            center_text(box_y + 3 + i, text, attr)
        stdscr.refresh()

    delay = RANDOM_SPIN_START_DELAY
    for step in range(RANDOM_SPIN_STEPS):
        reel_lines = []
        for i, stop_step in enumerate(reel_stop_steps):
            if step < stop_step:
                candidate = random.choice(watchlist)
                text = f"{candidate['title']} ({candidate['year'] or '?'})"
                attr = _spin_attr(step + i)
            else:
                text = final_line
                attr = rating_attr(10.0) | curses.A_BOLD | curses.A_REVERSE
            reel_lines.append((text, attr))
        draw_frame(accent_attr() | curses.A_BOLD, reel_lines)
        curses.napms(int(delay))
        delay *= RANDOM_SPIN_SLOWDOWN

    landed = [(final_line, rating_attr(10.0) | curses.A_BOLD | curses.A_REVERSE)] * 3
    for blink in range(REEL_FLASH_STEPS):
        flash_attr = rating_attr(10.0) | curses.A_BOLD
        if blink % 2 == 0:
            flash_attr |= curses.A_REVERSE
        draw_frame(flash_attr, landed)
        curses.napms(REEL_FLASH_DELAY_MS)
    curses.beep()

    stdscr.clear()
    draw_box(stdscr, box_y, box_x, box_h, box_w, rating_attr(10.0) | curses.A_BOLD)
    center_text(box_y + 1, "🍿 tonight's pick 🍿", rating_attr(10.0) | curses.A_BOLD)
    center_text(box_y + 3, final_line, rating_attr(10.0) | curses.A_BOLD | curses.A_REVERSE)
    stdscr.refresh()

    full_path = config.poster_full_path(final["poster_path"])
    if in_kitty() and full_path and full_path.exists() and w >= PREVIEW_MIN_TERM_WIDTH:
        preview_w = max(PREVIEW_PANEL_WIDTH_MIN, min(PREVIEW_PANEL_WIDTH_MAX, w // 3))
        preview_left = max(0, (w - preview_w) // 2)
        preview_top = box_y + box_h + 1
        preview_height = min(14, h - preview_top - 1)
        if preview_height > 3:
            try:
                subprocess.run(
                    [
                        "kitty", "+kitten", "icat",
                        "--place", f"{preview_w}x{preview_height}@{preview_left}x{preview_top}",
                        "--scale-up", str(full_path),
                    ],
                    stderr=subprocess.DEVNULL,
                )
            except OSError:
                pass
            curses.napms(1600)
        else:
            curses.napms(900)
    else:
        curses.napms(900)
    return final["id"]


# ------------------------------------------------------------ heatmap ui --

HEATMAP_WEEKS = 26


def _heatmap_attr(count: int, max_count: int) -> int:
    """Intensity color for a heatmap cell, reusing the existing rating
    color thresholds (green/yellow/red) as a ready-made 3-step gradient
    since the theme doesn't define a dedicated one."""
    if count <= 0:
        return dim_attr()
    if max_count <= 1:
        return rating_attr(8.0)
    ratio = count / max_count
    if ratio >= 0.66:
        return rating_attr(8.0) | curses.A_BOLD
    if ratio >= 0.33:
        return rating_attr(6.0)
    return rating_attr(3.0)


def watch_heatmap_screen(stdscr, db: DB) -> None:
    """A GitHub-style contribution grid of watch activity (first watches
    plus logged rewatches) over the last several months, one column per
    week and one row per weekday."""
    h, w = stdscr.getmaxyx()
    weeks = min(HEATMAP_WEEKS, max(4, (w - 10) // 2))
    today = date.today()
    start_of_week = today - timedelta(days=today.weekday())  # Monday
    grid_start = start_of_week - timedelta(weeks=weeks - 1)
    counts = db.watch_activity_by_date(since=grid_start.isoformat())
    max_count = max(counts.values(), default=0)

    stdscr.clear()
    draw_box(stdscr, 0, 0, h, w, dim_attr())
    safe_addstr(stdscr, 1, 2, "WATCH ACTIVITY", accent_attr() | curses.A_BOLD)

    grid_x, grid_y = 6, 4
    for row_i, label in enumerate(["Mon", "", "Wed", "", "Fri", "", ""]):
        if grid_y + row_i < h - 3:
            safe_addstr(stdscr, grid_y + row_i, 2, f"{label:<3}", dim_attr())

    total_events = active_days = 0
    last_month = None
    for col in range(weeks):
        week_start = grid_start + timedelta(weeks=col)
        if week_start.month != last_month:
            last_month = week_start.month
            safe_addstr(stdscr, grid_y - 1, grid_x + col * 2, week_start.strftime("%b"), dim_attr())
        for row in range(7):
            day = week_start + timedelta(days=row)
            if day > today:
                continue
            count = counts.get(day.isoformat(), 0)
            total_events += count
            active_days += 1 if count else 0
            safe_addstr(stdscr, grid_y + row, grid_x + col * 2, "██", _heatmap_attr(count, max_count))

    summary_y = min(grid_y + 9, h - 2)
    safe_addstr(
        stdscr, summary_y, 2,
        f"{active_days} active day(s), {total_events} watch event(s) in the last {weeks} weeks.",
        curses.A_NORMAL,
    )
    safe_addstr(stdscr, h - 1, 0, "Press any key to close.", dim_attr())
    stdscr.refresh()
    stdscr.getch()


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
