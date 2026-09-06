"""Thin client for the OMDb API (https://www.omdbapi.com).

Free API key: https://www.omdbapi.com/apikey.aspx
"""

import json
import urllib.error
import urllib.parse
import urllib.request

from . import config

BASE_URL = "https://www.omdbapi.com/"


class OmdbError(Exception):
    pass


def _request(params: dict) -> dict:
    api_key = config.get_api_key()
    if not api_key:
        raise OmdbError(
            "No OMDb API key configured. Get a free one at "
            "https://www.omdbapi.com/apikey.aspx and set it from the app (press 'K')."
        )
    params = {**params, "apikey": api_key}
    url = f"{BASE_URL}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        raise OmdbError(f"Network error contacting OMDb: {e}") from e
    if data.get("Response") == "False":
        raise OmdbError(data.get("Error", "Unknown OMDb error"))
    return data


def search(title: str, page: int = 1, media_type: str | None = None) -> tuple[list[dict], int]:
    """Search by title, return (results, total_results_available).

    OMDb paginates at 10 results per page; pass a higher `page` to fetch
    more of `total_results_available` results. `media_type`, if given,
    restricts to "movie" or "series"; left unset, results include both.

    OMDb's fuzzy `s=` search rejects very short/common titles (e.g. "Up",
    "It") with "Too many results" even though an exact match exists, so on
    page 1 fall back to an exact `t=` lookup for the typed title.
    """
    params = {"s": title, "page": page}
    if media_type:
        params["type"] = media_type
    try:
        data = _request(params)
    except OmdbError:
        if page != 1:
            raise
        return _exact_match_fallback(title, media_type)
    results = []
    for item in data.get("Search", []):
        raw_type = item.get("Type", "movie")
        results.append(
            {
                "title": item.get("Title", ""),
                "year": item.get("Year", ""),
                "imdb_id": item.get("imdbID", ""),
                "poster_url": item.get("Poster", ""),
                "media_type": "series" if raw_type in ("series", "episode") else "movie",
            }
        )
    try:
        total = int(data.get("totalResults", "0") or "0")
    except ValueError:
        total = len(results)
    return results, total


def _exact_match_fallback(title: str, media_type: str | None) -> tuple[list[dict], int]:
    params = {"t": title}
    if media_type:
        params["type"] = media_type
    data = _request(params)
    raw_type = data.get("Type", "movie")
    result = {
        "title": data.get("Title", ""),
        "year": data.get("Year", ""),
        "imdb_id": data.get("imdbID", ""),
        "poster_url": data.get("Poster", ""),
        "media_type": "series" if raw_type in ("series", "episode") else "movie",
    }
    return [result], 1


def get_by_id(imdb_id: str) -> dict:
    """Fetch full details for a single title by IMDb id."""
    data = _request({"i": imdb_id, "plot": "full"})
    runtime_raw = data.get("Runtime", "")
    runtime_minutes = None
    if runtime_raw and runtime_raw != "N/A":
        digits = "".join(ch for ch in runtime_raw if ch.isdigit())
        runtime_minutes = int(digits) if digits else None
    imdb_rating_raw = data.get("imdbRating", "")
    imdb_rating = None
    if imdb_rating_raw and imdb_rating_raw != "N/A":
        try:
            imdb_rating = float(imdb_rating_raw)
        except ValueError:
            imdb_rating = None
    rated = data.get("Rated", "")
    if rated in ("N/A", ""):
        rated = "Not Rated"
    raw_type = data.get("Type", "movie")
    media_type = "series" if raw_type in ("series", "episode") else "movie"
    seasons_raw = data.get("totalSeasons", "")
    total_seasons = int(seasons_raw) if seasons_raw and seasons_raw != "N/A" and seasons_raw.isdigit() else None
    return {
        "title": data.get("Title", ""),
        "year": data.get("Year", ""),
        "media_type": media_type,
        "total_seasons": total_seasons,
        "runtime_minutes": runtime_minutes,
        "genre": data.get("Genre", ""),
        "director": data.get("Director", ""),
        "actors": data.get("Actors", ""),
        "plot": data.get("Plot", ""),
        "rated": rated,
        "imdb_rating": imdb_rating,
        "imdb_id": data.get("imdbID", ""),
        "poster_url": data.get("Poster", ""),
    }


def download_poster(poster_url: str, imdb_id: str) -> str | None:
    """Download a poster image to the local poster cache, return its filename.

    Only the filename is returned (not an absolute path) so the database
    stays portable across machines/home directories; resolve it against
    config.POSTER_DIR at display time via config.poster_full_path().
    """
    if not poster_url or poster_url == "N/A":
        return None
    config.ensure_dirs()
    ext = poster_url.rsplit(".", 1)[-1].split("?")[0]
    if len(ext) > 4 or not ext.isalnum():
        ext = "jpg"
    filename = f"{imdb_id}.{ext}"
    dest = config.POSTER_DIR / filename
    try:
        req = urllib.request.Request(poster_url, headers={"User-Agent": "blockbuster/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            dest.write_bytes(resp.read())
    except urllib.error.URLError:
        return None
    return filename
