"""Thin client for the Watchmode API (https://api.watchmode.com) - looks up
streaming/rental/purchase availability for a title by IMDb id.

Free API key (1,000 requests/month): https://api.watchmode.com/
"""

import json
import urllib.error
import urllib.parse
import urllib.request

from . import config

BASE_URL = "https://api.watchmode.com/v1"

SOURCE_TYPE_LABELS = {
    "sub": "Streaming (subscription)",
    "free": "Free",
    "rent": "Rent",
    "buy": "Buy",
    "tve": "TV Everywhere",
}


class WatchmodeError(Exception):
    pass


def _request(path: str, params: dict):
    api_key = config.get_watchmode_key()
    if not api_key:
        raise WatchmodeError(
            "No Watchmode API key configured. Get a free one at "
            "https://api.watchmode.com/ and set it from the app (press 'K')."
        )
    params = {**params, "apiKey": api_key}
    url = f"{BASE_URL}{path}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        raise WatchmodeError(f"Network error contacting Watchmode: {e}") from e
    if isinstance(data, dict) and data.get("statusMessage") and "title_results" not in data and "id" not in data:
        raise WatchmodeError(data["statusMessage"])
    return data


def get_streaming_sources(imdb_id: str, region: str = "US") -> list[dict]:
    """Look up a title by IMDb id, then list its streaming sources for
    `region`. Raises WatchmodeError if there's no imdb_id on file, no key
    configured, or Watchmode has no record of the title."""
    if not imdb_id:
        raise WatchmodeError(
            "This title has no IMDb id on file (added manually?) - can't "
            "look up streaming availability."
        )
    search_data = _request("/search/", {"search_field": "imdb_id", "search_value": imdb_id})
    results = search_data.get("title_results", []) if isinstance(search_data, dict) else []
    if not results:
        raise WatchmodeError("Watchmode has no record of this title.")
    title_id = results[0]["id"]

    sources = _request(f"/title/{title_id}/sources/", {"regions": region})
    if not isinstance(sources, list):
        return []

    seen = set()
    out = []
    for s in sources:
        name = s.get("name")
        if not name:
            continue
        key = (name, s.get("type"))
        if key in seen:
            continue
        seen.add(key)
        out.append({"name": name, "type": s.get("type"), "web_url": s.get("web_url")})
    return out


def group_sources_by_type(sources: list[dict]) -> list[tuple[str, list[str]]]:
    """Group sources into (label, [names]) pairs in a fixed, readable order."""
    by_type: dict[str, list[str]] = {}
    for s in sources:
        by_type.setdefault(s["type"] or "other", []).append(s["name"])
    order = ["sub", "free", "tve", "rent", "buy"]
    ordered_types = order + [t for t in by_type if t not in order]
    return [
        (SOURCE_TYPE_LABELS.get(t, t.title()), sorted(set(by_type[t])))
        for t in ordered_types
        if t in by_type
    ]
