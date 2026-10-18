# Version History

## Unreleased

- Obsidian export: the Base is now `BLOCKBUSTER.base` and the index note
  is `watched.md`; adds a `movielog.md` of everything in the library by
  date added, and this version history
- `L`: add, tag, or format titles from a CSV/Excel list
- Fill in missing OMDb info for Letterboxd imports and manual adds

## 1.3.0 — 2026-10-17

- Formats, which work the same way as tags
- Removed total-seasons and current-season tracking
- Split `ui.py` into modules and added `pyproject.toml`
- Fixed shell completions

## 1.2.0 — 2026-09-17

- Letterboxd import
- Watch heatmap and aging watchlist
- TMDb where-to-watch and trailer lookup
- Rating and runtime filters
- Obsidian vault export (also offered from `X`), with collections, a
  stats note, and a poster gallery
- Poster refresh from OMDb on the detail screen
- Slot-machine animation for the random watchlist pick
- Fixed a crash from `DB.list` shadowing the builtin `list`

## 1.1.0 — 2026-09-06

- Removed the Watchmode where-to-watch feature

## 1.0.0 — 2026-09-06

- First release: keyboard-driven terminal tracker for movies and series,
  with OMDb lookup, posters, tags, ratings, and ranking
- Decade filter, recommendations, rewatch history, shell completion
