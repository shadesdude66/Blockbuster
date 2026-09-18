"""Thin client for TMDb (https://www.themoviedb.org) - watch-provider
availability and trailer links, looked up by IMDb id.

Free API key: https://www.themoviedb.org/settings/api
"""

import json
import urllib.error
import urllib.parse
import urllib.request

from . import config

BASE_URL = "https://api.themoviedb.org/3"

# Order here controls display order in the "where to watch" screen.
PROVIDER_TYPE_LABELS = {
    "flatrate": "Streaming (subscription)",
    "free": "Free",
    "ads": "Free with ads",
    "rent": "Rent",
    "buy": "Buy",
}


class TmdbError(Exception):
    pass


def _request(path: str, params: dict | None = None) -> dict:
    api_key = config.get_tmdb_api_key()
    if not api_key:
        raise TmdbError(
            "No TMDb API key configured. Get a free one at "
            "https://www.themoviedb.org/settings/api and set it from the app (press 'K')."
        )
    params = {**(params or {}), "api_key": api_key}
    url = f"{BASE_URL}{path}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        raise TmdbError(f"Network error contacting TMDb: {e}") from e


def _find_tmdb_id(imdb_id: str, media_type: str) -> int | None:
    data = _request(f"/find/{imdb_id}", {"external_source": "imdb_id"})
    key = "movie_results" if media_type == "movie" else "tv_results"
    results = data.get(key) or []
    return results[0]["id"] if results else None


def get_watch_providers(
    imdb_id: str, media_type: str = "movie", region: str = "US"
) -> list[tuple[str, list[str]]]:
    """(label, [provider names]) pairs for where to stream/rent/buy this
    title in `region`, in a fixed readable order. Empty if TMDb has
    provider data for the title but none for this region."""
    if not imdb_id:
        raise TmdbError(
            "This title has no IMDb id on file (added manually?) - can't "
            "look up watch providers."
        )
    tmdb_id = _find_tmdb_id(imdb_id, media_type)
    if tmdb_id is None:
        raise TmdbError("TMDb has no record of this title.")
    kind = "movie" if media_type == "movie" else "tv"
    data = _request(f"/{kind}/{tmdb_id}/watch/providers")
    country = data.get("results", {}).get(region, {})
    out = []
    for ptype, label in PROVIDER_TYPE_LABELS.items():
        names = sorted({p["provider_name"] for p in country.get(ptype, []) if p.get("provider_name")})
        if names:
            out.append((label, names))
    return out


def get_trailer_url(imdb_id: str, media_type: str = "movie") -> str | None:
    """A YouTube trailer URL for this title, or None if TMDb has no
    trailer (or no record of the title) on file."""
    if not imdb_id:
        return None
    tmdb_id = _find_tmdb_id(imdb_id, media_type)
    if tmdb_id is None:
        return None
    kind = "movie" if media_type == "movie" else "tv"
    data = _request(f"/{kind}/{tmdb_id}/videos")
    videos = data.get("results") or []
    trailers = [v for v in videos if v.get("site") == "YouTube" and v.get("type") == "Trailer"]
    pick = trailers[0] if trailers else next((v for v in videos if v.get("site") == "YouTube"), None)
    return f"https://www.youtube.com/watch?v={pick['key']}" if pick else None
