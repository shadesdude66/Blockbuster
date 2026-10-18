"""Work through a plain list of titles (CSV or Excel .xlsx): add every
title not already in the library from OMDb, or add a tag or format to
every listed title.

The list only needs a column of titles. A header row is optional; when
there is one, a "Title"/"Name"/"Movie"/"Film" column is used for titles
and an optional "Year" column narrows the match. Without a header, the
first column is the title and a second column holding a 4-digit year is
used as the year. A title written as "Heat (1995)" also counts as having
a year.
"""

import csv
import curses
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from . import config, metadata, omdb
from .adding import esc_pressed, require_api_key
from .db import DB, Movie
from .widgets import accent_attr, choose_from_list, confirm, dim_attr, flash, prompt, show_text_screen

TITLE_HEADERS = ("title", "name", "movie", "film", "series", "show", "movie title", "film title")
YEAR_HEADERS = ("year", "release year", "released")
SUMMARY_MAX_LISTED = 25

_TITLE_YEAR_RE = re.compile(r"^(.*?)\s*\((\d{4})\)\s*$")
_YEAR_RE = re.compile(r"^\d{4}$")


# --------------------------------------------------------------- reading --

def _read_csv(path: Path) -> list[list[str]]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        sample = f.read(4096)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        return [row for row in csv.reader(f, dialect)]


_XLSX_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def _col_index(ref: str) -> int:
    """0-based column index from a cell reference like "C12"."""
    n = 0
    for ch in ref:
        if not ch.isalpha():
            break
        n = n * 26 + (ord(ch.upper()) - ord("A") + 1)
    return n - 1


def _read_xlsx(path: Path) -> list[list[str]]:
    """Rows of the first worksheet of an .xlsx file, using only the
    standard library (an .xlsx is a zip of XML parts)."""
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        shared: list[str] = []
        if "xl/sharedStrings.xml" in names:
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in root.findall("m:si", _XLSX_NS):
                shared.append("".join(t.text or "" for t in si.iter(f"{{{_XLSX_NS['m']}}}t")))
        sheets = sorted(
            (n for n in names if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n)),
            key=lambda n: int(re.search(r"(\d+)\.xml$", n).group(1)),
        )
        if not sheets:
            return []
        root = ET.fromstring(z.read(sheets[0]))

    rows: list[list[str]] = []
    for row_el in root.iter(f"{{{_XLSX_NS['m']}}}row"):
        cells: dict[int, str] = {}
        for i, c in enumerate(row_el.findall("m:c", _XLSX_NS)):
            col = _col_index(c.get("r", "")) if c.get("r") else i
            kind = c.get("t", "")
            if kind == "inlineStr":
                value = "".join(t.text or "" for t in c.iter(f"{{{_XLSX_NS['m']}}}t"))
            else:
                v = c.find("m:v", _XLSX_NS)
                value = v.text if v is not None and v.text else ""
                if kind == "s" and value.isdigit():
                    value = shared[int(value)] if int(value) < len(shared) else ""
                elif kind in ("", "n") and value.endswith(".0"):
                    value = value[:-2]  # whole numbers like a year come back as "1995.0"
            cells[col] = value
        if cells:
            rows.append([cells.get(i, "") for i in range(max(cells) + 1)])
    return rows


def read_title_list(path: Path) -> list[tuple[str, str]]:
    """(title, year) pairs from a CSV/TSV/TXT or .xlsx file, in file order,
    without duplicates. Year is "" when the list doesn't give one."""
    suffix = path.suffix.lower()
    if suffix in (".xlsx", ".xlsm"):
        rows = _read_xlsx(path)
    elif suffix == ".xls":
        raise ValueError("Old .xls files aren't supported - save it as .xlsx or .csv first.")
    else:
        rows = _read_csv(path)
    rows = [[cell.strip() for cell in row] for row in rows if any(cell.strip() for cell in row)]
    if not rows:
        return []

    header = [cell.casefold() for cell in rows[0]]
    title_col = next((i for i, h in enumerate(header) if h in TITLE_HEADERS), None)
    if title_col is not None:
        year_col = next((i for i, h in enumerate(header) if h in YEAR_HEADERS), None)
        rows = rows[1:]
    else:
        title_col = 0
        year_col = 1 if all(len(r) > 1 and (not r[1] or _YEAR_RE.match(r[1])) for r in rows) else None

    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        title = row[title_col] if title_col < len(row) else ""
        year = row[year_col] if year_col is not None and year_col < len(row) else ""
        m = _TITLE_YEAR_RE.match(title)
        if m and not year:
            title, year = m.group(1), m.group(2)
        year = year[:4] if _YEAR_RE.match(year[:4]) else ""
        if not title:
            continue
        key = (metadata.norm_title(title), year)
        if key in seen:
            continue
        seen.add(key)
        out.append((title, year))
    return out


# ------------------------------------------------------------------ sync --

ACTION_ADD = "Add missing titles from OMDb"
ACTION_TAG = "Add tag(s) to every listed title"
ACTION_FORMAT = "Add format(s) to every listed title"


def sync_title_list(stdscr, db: DB) -> None:
    """Ask for a CSV/.xlsx file of titles, then either add every title
    that isn't already in the library (filled in from OMDb), or add a tag
    or format to every listed title."""
    path_str = prompt(stdscr, "Title list (CSV or .xlsx)")
    if not path_str:
        return
    path = Path(path_str.strip().strip("'\"")).expanduser()
    if not path.is_file():
        flash(stdscr, f"File not found: {path}")
        return
    try:
        titles = read_title_list(path)
    except (OSError, ValueError, csv.Error, zipfile.BadZipFile, ET.ParseError, UnicodeDecodeError) as e:
        flash(stdscr, f"Failed to read file: {e}")
        return
    if not titles:
        flash(stdscr, "No titles found in that file.")
        return
    action = choose_from_list(
        stdscr, f"{len(titles)} title(s) in {path.name} - what to do?",
        [ACTION_ADD, ACTION_TAG, ACTION_FORMAT], lambda x: x,
    )
    if action == ACTION_ADD:
        _add_missing_titles(stdscr, db, path, titles)
    elif action == ACTION_TAG:
        _label_titles(stdscr, db, path, titles, "tag")
    elif action == ACTION_FORMAT:
        _label_titles(stdscr, db, path, titles, "format")


def _ask_status(stdscr, title: str) -> str | None:
    status = choose_from_list(stdscr, title, ["Watchlist", "Watched"], lambda x: x)
    return status.lower() if status else None


def _add_missing_titles(stdscr, db: DB, path: Path, titles: list[tuple[str, str]]) -> None:
    if not require_api_key(stdscr):
        return
    library = metadata.library_index(db)
    missing = [(t, y) for t, y in titles if not metadata.find_in_library(library, t, y)]
    if not missing:
        flash(stdscr, f"All {len(titles)} title(s) are already in your library.")
        return
    status = _ask_status(
        stdscr,
        f"{len(titles) - len(missing)} of {len(titles)} already in your library. "
        f"Add the other {len(missing)} as:",
    )
    if not status:
        return
    result = _fetch_and_add(stdscr, db, path, missing, status)
    lines = [
        f"Checked {len(titles)} title(s) from {path.name}:",
        f"  {len(titles) - len(missing)} already in your library",
        f"  {len(result.added)} added from OMDb",
    ]
    if result.restored:
        lines.append(f"  {len(result.restored)} restored from trash")
    lines += _result_details(result, "Run it again later to pick up the rest - titles added so far are skipped.")
    lines += ["", "Press any key."]
    show_text_screen(stdscr, "\n".join(lines))


def _label_titles(stdscr, db: DB, path: Path, titles: list[tuple[str, str]], kind: str) -> None:
    """Add a tag or format (kind) to every listed title in the library,
    optionally adding any missing ones from OMDb first so they get it too."""
    val = prompt(stdscr, f"Add {kind}(s) to the {len(titles)} listed title(s) (comma-separated)")
    names = [n.strip() for n in (val or "").split(",") if n.strip()]
    if not names:
        return

    library = metadata.library_index(db)
    target_ids: set[int] = set()
    ambiguous: list[str] = []
    missing: list[tuple[str, str]] = []
    for title, year in titles:
        ids = metadata.find_in_library(library, title, year)
        if len(ids) == 1:
            target_ids.add(ids[0])
        elif ids:
            ambiguous.append(f"{title} - {len(ids)} titles in your library by that name")
        else:
            missing.append((title, year))

    result = None
    if missing and config.get_api_key() and confirm(
        stdscr, f"{len(missing)} listed title(s) aren't in your library - add them from OMDb first?"
    ):
        status = _ask_status(stdscr, f"Add the {len(missing)} missing title(s) as:")
        if status:
            result = _fetch_and_add(stdscr, db, path, missing, status)
            target_ids.update(result.ids)

    if target_ids:
        if kind == "tag":
            db.add_tags_to_movies(sorted(target_ids), names)
        else:
            db.add_formats_to_movies(sorted(target_ids), names)

    lines = [f"Added {', '.join(names)} to {len(target_ids)} title(s) from {path.name}."]
    if result:
        lines.append(f"  ({len(result.added)} of them newly added from OMDb, {len(result.restored)} restored from trash)")
        lines += _result_details(result, f"Titles not reached didn't get the {kind} - run it again to finish.")
    elif missing:
        lines += ["", f"Not in your library, so not given the {kind} (choose '{ACTION_ADD}' to add them):"]
        lines += _capped([f"{t} ({y})" if y else t for t, y in missing])
    if ambiguous:
        lines += ["", f"Skipped - add a Year column to the list, or use '{'G' if kind == 'tag' else 'O'}' on the right one:"]
        lines += _capped(ambiguous)
    lines += ["", "Press any key."]
    show_text_screen(stdscr, "\n".join(lines))


@dataclass
class _AddResult:
    ids: list[int] = field(default_factory=list)  # every title now in the library
    added: list[str] = field(default_factory=list)
    restored: list[str] = field(default_factory=list)
    already: list[str] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)
    stopped: str = ""


def _fetch_and_add(stdscr, db: DB, path: Path, missing: list[tuple[str, str]], status: str) -> _AddResult:
    """Look up each (title, year) on OMDb and add it with the given status.
    Esc stops early; unmatched titles are noted rather than guessed."""
    result = _AddResult()
    total = len(missing)
    for n, (title, year) in enumerate(missing, 1):
        if esc_pressed(stdscr):
            result.stopped = "Stopped early (Esc)."
            break
        label = f"{title} ({year})" if year else title
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        stdscr.addstr(0, 0, f"Adding titles from {path.name}: {n} of {total}"[: w - 1], accent_attr() | curses.A_BOLD)
        stdscr.addstr(2, 2, label[: w - 3])
        stdscr.addstr(h - 1, 0, "Esc stop"[: w - 1], dim_attr())
        stdscr.refresh()
        try:
            details = metadata.find_match({"title": title, "year": year, "imdb_id": ""})
        except omdb.OmdbError as e:
            result.stopped = f"Stopped early - OMDb error: {e}"
            break
        if not details or not details.get("imdb_id"):
            result.unmatched.append(label)
            continue

        existing = db.conn.execute(
            "SELECT id, title, deleted_at FROM movies WHERE imdb_id = ?", (details["imdb_id"],)
        ).fetchone()
        if existing and existing["deleted_at"] is None:
            # Listed under a different spelling than the library's title.
            result.already.append(f"{label} - in your library as '{existing['title']}'")
            result.ids.append(existing["id"])
            continue
        if existing:
            db.restore(existing["id"])
            result.restored.append(existing["title"])
            result.ids.append(existing["id"])
            continue

        poster_path = omdb.download_poster(details.pop("poster_url", ""), details["imdb_id"])
        result.ids.append(db.add(Movie(
            title=details["title"] or title,
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
            status=status,
        )))
        result.added.append(f"{details['title'] or title} ({details['year']})")
    return result


def _capped(items: list[str]) -> list[str]:
    lines = [f"  {item}" for item in items[:SUMMARY_MAX_LISTED]]
    if len(items) > SUMMARY_MAX_LISTED:
        lines.append(f"  ...and {len(items) - SUMMARY_MAX_LISTED} more")
    return lines


def _result_details(result: _AddResult, stopped_hint: str) -> list[str]:
    lines: list[str] = []
    if result.stopped:
        lines += ["", result.stopped, stopped_hint]
    for heading, items in (
        ("Added:", result.added),
        ("Restored from trash:", result.restored),
        ("Already in your library under another title:", result.already),
        ("No confident OMDb match - add these with 'a' to pick the right one:", result.unmatched),
    ):
        if items:
            lines += ["", heading] + _capped(items)
    return lines
