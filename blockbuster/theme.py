"""Reads the active Omarchy desktop theme, if present, for curses colors.

Omarchy keeps the live theme's semantic palette at a fixed path and
regenerates it whenever the user switches themes (see
`omarchy-theme-color` for the canonical resolution logic this mirrors a
subset of). On a non-Omarchy system this file simply won't exist, and
callers should fall back to plain curses attributes.
"""

import tomllib
from pathlib import Path

OMARCHY_COLORS_PATH = Path.home() / ".local/state/omarchy/current/theme/colors.toml"


def _get(raw: dict, *keys: str) -> str | None:
    for key in keys:
        val = raw.get(key)
        if val:
            return val
    return None


def load() -> dict[str, str] | None:
    """Return the semantic colors of the active Omarchy theme, or None."""
    try:
        raw = tomllib.loads(OMARCHY_COLORS_PATH.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return None
    foreground = _get(raw, "foreground", "fg", "color7")
    background = _get(raw, "background", "bg", "color0")
    accent = _get(raw, "accent", "color1", "red")
    selection = _get(raw, "selection", "selection_background", "color8")
    bright_foreground = _get(raw, "bright_foreground", "bright_fg", "color15") or foreground
    muted = _get(raw, "muted", "dark_foreground", "color8") or foreground
    green = _get(raw, "green", "color2")
    yellow = _get(raw, "yellow", "color3")
    red = _get(raw, "red", "color1") or accent
    blue = _get(raw, "blue", "color4")
    if not (foreground and background and accent and selection):
        return None
    return {
        "foreground": foreground,
        "background": background,
        "accent": accent,
        "selection": selection,
        "bright_foreground": bright_foreground,
        "muted": muted,
        "green": green,
        "yellow": yellow,
        "red": red,
        "blue": blue,
    }


def hex_to_xterm256(hex_color: str | None) -> int | None:
    """Approximate an RGB hex color as the nearest xterm 256-color index."""
    if not hex_color or not hex_color.startswith("#") or len(hex_color) != 7:
        return None
    try:
        r, g, b = (int(hex_color[i : i + 2], 16) for i in (1, 3, 5))
    except ValueError:
        return None
    if r == g == b:
        if r < 8:
            return 16
        if r > 248:
            return 231
        return round((r - 8) / 247 * 24) + 232
    return 16 + 36 * round(r / 255 * 5) + 6 * round(g / 255 * 5) + round(b / 255 * 5)
