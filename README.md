# Blockbuster

![List view](screenshots/list-view.png)

<details>
<summary>More screenshots (detail view, stats)</summary>

![Detail view](screenshots/detail-view.png)
![Stats view](screenshots/stats-view.png)
![Full-window list view with poster and tags](screenshots/list-fullscreen.png)
![Poster wall view](screenshots/Posterwall.png)

</details>

A keyboard-driven terminal app for tracking movies and TV series you've
watched: rating, personal rank, runtime (episode runtime for series),
content rating, poster, genre, director, cast, watch status, watched
date, rewatch count, notes, and your own tags/collections.

No third-party dependencies — pure Python 3 standard library (`curses`,
`sqlite3`, `urllib`). On terminals at least 64 columns wide, a side
panel shows a live summary of the selected movie (rating, genre,
director, tags, and more) — plus a live poster in kitty, above the
summary. Narrower windows drop the poster and shrink less essential
columns; press `p` for a fullscreen poster instead, falling back to
`chafa` or your default image viewer outside kitty.

## Install

Requires only Python 3.10+ — no third-party dependencies (`curses`
and `sqlite3` are in the standard library). Works on macOS and Linux;
`curses` isn't available on Windows.

Install it as a `blockbuster` command with [pipx](https://pipx.pypa.io)
(or `uv tool install` in place of `pipx install`):

```
pipx install git+https://github.com/shadesdude66/Blockbuster.git
```

Update later with `pipx upgrade blockbuster`. Make sure `~/.local/bin`
is on your `PATH` (`pipx ensurepath` sets that up).

To try it without installing, run it straight from a clone (this only
works from inside the `Blockbuster` directory):

```
git clone https://github.com/shadesdude66/Blockbuster.git
cd Blockbuster
python3 -m blockbuster
```

Optionally, add it to your app launcher with a `.desktop` file (Linux):

```
cat > ~/.local/share/applications/blockbuster.desktop <<'EOF'
[Desktop Entry]
Type=Application
Name=Blockbuster
Comment=Track movies you've watched: ratings, rank, runtime, posters
Exec=kitty --title blockbuster -e blockbuster
Terminal=false
Icon=video-x-generic
Categories=AudioVideo;Video;Database;
EOF
```

(Swap `kitty` for your own terminal if you don't have it — kitty just
gets you live poster images, per the note above.)

### Shell completion

Tab-completion for `blockbuster`'s flags (`--help`, `--version`, and
`--export-obsidian`, which completes a directory):

```
# bash - add to ~/.bashrc
source /path/to/Blockbuster/completions/blockbuster.bash

# zsh - put completions/_blockbuster on your $fpath (before compinit), e.g.
mkdir -p ~/.zsh/completions
cp completions/_blockbuster ~/.zsh/completions/
# then in ~/.zshrc, before compinit: fpath=(~/.zsh/completions $fpath)
```

## Run it

```
blockbuster
```

## Adding movies & TV series

- `a` — search OMDb by title (results cover movies and series, labeled
  `[Movie]`/`[Series]`). `Space` marks results, `Enter` adds all
  marked ones, or `Enter` with nothing marked adds just the one you're
  on. Each addition auto-fills year, runtime (or episode runtime for
  series), genre, director, cast, plot, IMDb rating, and poster.
  "Load more results" fetches the next page past 10 matches, keeping
  your marks.
- `m` — add a movie or series manually (type, title, year,
  runtime, genre, director), no internet needed.

Online search needs a free OMDb API key: get one at
https://www.omdbapi.com/apikey.aspx (activate via email), then press
`K` in the app to save it. It's stored in
`~/.config/blockbuster/config.json`, or set the `OMDB_API_KEY`
environment variable instead. Press `K` again any time to update it or
add a second key for "where to watch" lookups (see below).

## Everyday keys

`Space` marks movies for bulk actions; `G`, `O`, `d`, `w`, and `W` below
apply to every marked movie, or just the selected one if none are
marked.

Status (`Tab`), type (`y`), tag (`g`), format (`f`), decade (`D`), and the free-text
filter (`/`, which matches title/genre/director/actors) all combine —
e.g. `Tab` to watchlist, `g` to a tag, and `D` to a decade narrows to
just that combination.

| Key | Action |
|---|---|
| `Up`/`Down`, `j`/`k` | move selection |
| `Enter` / `l` | open a movie's details |
| `E` | edit every field of the selected movie directly |
| `/` | filter by title, genre, director, or actors |
| `s` | cycle sort (rank, my rating, IMDb rating, title, watched date, runtime, date added, year) |
| `Tab` | cycle status filter (all / watchlist / watched) |
| `y` | cycle type filter (all / movie / series) |
| `g` | cycle tag filter (none, then each tag you've used) |
| `f` | cycle format filter (none, then each format you've used) |
| `D` | cycle decade filter (none, then each decade you have titles from) |
| `r` | set/clear a minimum rating filter (your rating, or IMDb's if unwatched) |
| `t` | set/clear a maximum runtime filter, in minutes |
| `Space` | mark/unmark selection |
| `Esc` | clear all marks |
| `G` | add tag(s) |
| `O` | add format(s), e.g. Blu-ray, 4K, DVD, Digital |
| `d` | delete (soft delete, see `u`) |
| `u` | undo: restore the most recently deleted movie(s) |
| `w` | mark as watched |
| `W` | mark as watchlist |
| `R` | random pick from your watchlist, with a slot-machine spin |
| `F` | recommended for you: watchlist titles most similar (by genre/director/cast) to your highly-rated watched movies; `Enter` opens a pick |
| `A` | aging watchlist: titles sitting unwatched 90+ days, oldest first; `Enter` opens a pick |
| `H` | watch activity heatmap: a GitHub-style grid of watch/rewatch dates over the last ~6 months |
| `P` | poster wall: browsable kitty-image grid of cached posters; arrows move, `Enter` opens details |
| `T` | trash screen: browse, restore, or permanently delete |
| `S` | stats screen: totals, watch time, averages, top genres, decades |
| `C` | collections: your tags as franchises, with watched/total count, average rating, and a ★ once complete; `Enter` drills into a tag's titles by year |
| `B` | back up the database now (timestamped copy) |
| `X` | export the collection to CSV, JSON, or an Obsidian vault |
| `I` | import from a CSV/JSON file (same format as `X`'s export, or a raw Letterboxd export - see below); skips anything already present, matched by IMDb id or title+year |
| `?` | full help screen |
| `q` | back / quit |

Ratings are color-coded (green ≥ 7.5, yellow ≥ 5, red below), using
your desktop theme's colors when available. A 🎬/📺 icon marks movies
vs. series, and a ✓ or `-` in Status shows watched vs. watchlist. Wide
terminals also show Tags and Format columns.

Inside a movie's detail screen:

| Key | Action |
|---|---|
| `r` | set your rating (0–10) |
| `n` | set your rank |
| `M` | set/edit content rating (R, PG-13, TV-14, etc.) |
| `w` | toggle watched / watchlist |
| `t` | mark watched today |
| `c` | +1 rewatch count (also logs today's date) |
| `v` | view rewatch history (every logged date) |
| `o` | where to watch: streaming/rent/buy sources, plus a trailer link (needs a free TMDb API key) |
| `e` | edit notes |
| `E` | edit every field, grouped into sections (Title / Status & Ratings / Details / Text); changed fields marked `*`; `q` saves, `Esc` discards (confirms if changed) |
| `p` | view the poster fullscreen |
| `u` | re-fetch the poster from OMDb (needs an IMDb id, so not for manually-added titles) |
| `x` | delete this movie |

"Where to watch" (`o`) needs its own free API key from
https://www.themoviedb.org/settings/api — press `K` in the list screen
and enter it at the second prompt, or set the `TMDB_API_KEY`
environment variable. Only works for titles added via OMDb search
(`a`), since it looks the title up by IMDb id.

## Data

- SQLite database: `~/.local/share/blockbuster/movies.db`
- Cached posters: `~/.local/share/blockbuster/posters/`
- Config (API keys): `~/.config/blockbuster/config.json`
- Manual backups (`B`): `~/.local/share/blockbuster/backups/`
- Exports (`X`): `~/.local/share/blockbuster/exports/`

Back up or sync `movies.db` to keep your data safe across machines, or
press `B` in the app for a timestamped copy. Deleting a movie (`d`/`x`)
just hides it — restore with `u` (or from the `T` trash screen), or
purge it permanently from trash by typing the exact title to confirm.

### Importing from Letterboxd

Press `I` and point it at a CSV from a Letterboxd data export
(https://letterboxd.com/settings/data/, or Settings → Import & Export
→ Export Your Data) — `watched.csv`, `ratings.csv`, `diary.csv`, and
`watchlist.csv` are all recognized automatically by their Letterboxd
column headers, no need to reformat them first. Star ratings (0.5–5)
are scaled to this app's 0–10 scale; a `diary.csv` row marked as a
rewatch logs a rewatch on the existing title (viewable with `v`)
instead of being skipped as a duplicate. Letterboxd doesn't export
genre, director, cast, or posters — re-fetch those later by deleting
and re-adding a title via OMDb search (`a`) if you want them filled in.

### Obsidian vault export

Press `X` in the app and pick "Obsidian vault", or run it from the
command line without opening the app:

```
blockbuster --export-obsidian ~/notes/BlockbusterVault
```

Writes a snapshot of your library as an Obsidian-ready markdown vault:
one note per movie/series (YAML frontmatter with rating, genre, cast,
tags, formats, watched date, etc., plus its poster), one stub note per
director/actor, genre, collection/tag, and format with backlinks to their
titles, a `Stats.md` mirroring the app's `S` stats screen (totals,
watch time, average ratings, top genres, by decade), and a
`Blockbuster.md` index grouped by status — enough for Obsidian's graph
and backlinks to connect everything. It's a one-shot export, not a
live sync; re-run the command to refresh the vault.

It also writes `Movies.base`, an Obsidian Base (requires Obsidian
1.9+, where Bases ships as a core plugin) with seven pre-built views:
six sortable/filterable table views (All, Watched, Watchlist, Top
Rated, TV Shows, Movies Only) over fields like status, my rating, IMDb
rating, rank, genre, cast, and watched date — click any column header
in Obsidian to re-sort — plus a Poster Wall cards view for browsing
the library as a visual gallery.

## CSV Export

You can also export to a CSV through the menu inside the app.

## Theming

On an Omarchy desktop, the app reads the active theme's accent/selection
colors from `~/.local/state/omarchy/current/theme/colors.toml` for the
header bar, selected row, and section headers. Switch themes and
relaunch to pick up the change. Elsewhere, or if that file is missing,
it falls back to plain default-color curses styling.

## Project layout

```
blockbuster/
  config.py     config + XDG data/cache paths
  db.py         SQLite schema and queries
  omdb.py       OMDb API client (search, details, poster download)
  tmdb.py       TMDb API client (watch providers, trailers)
  recommend.py  local genre/director/cast similarity scoring
  theme.py      reads the active Omarchy theme's colors, if present
  ui.py         curses TUI: main list screen and command-line entry point
  widgets.py    shared curses helpers: theme colors, prompts, help/poster screens
  adding.py     adding titles (OMDb search, manual entry)
  detail.py     movie detail screen and edit-all-fields form
  screens.py    stats, collections, trash, poster wall, heatmap, and other screens
  import_export.py  CSV/JSON/Obsidian export, CSV/JSON/Letterboxd import
  obsidian_export.py  Obsidian vault writer
  __main__.py   entry point (python3 -m blockbuster)
completions/    bash/zsh completion scripts for the blockbuster command
pyproject.toml  packaging metadata (pipx install / the blockbuster command)
```
