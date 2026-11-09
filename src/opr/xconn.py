"""Shared X display connection and small helpers.

Everything in this package talks to one X display. Which one is decided
once per invocation, in this order:

  1. `--display` on the command line (or the session's display with `-s`);
  2. `$OP_DISPLAY`;
  3. the contents of `~/.config/op/display`  -- the per-machine setting,
     for hosts whose main desktop is not `:0` (an xrdp session, say);
  4. `$DISPLAY`, if it names a local server that actually exists -- agent
     shells sometimes inherit a DISPLAY with nothing behind it;
  5. `:0`.

DISPLAY and XAUTHORITY are set in os.environ so that child processes we
launch (chrome, xclip, ffmpeg) inherit the same target without extra
plumbing.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

CONFIG_DIR = os.path.expanduser("~/.config/op")
DISPLAY_FILE = os.path.join(CONFIG_DIR, "display")
FALLBACK_DISPLAY = ":0"
DEFAULT_XAUTHORITY = os.path.expanduser("~/.Xauthority")

_display = None


def _configured_display() -> str | None:
    """$OP_DISPLAY, else the first line of ~/.config/op/display, else None."""
    env = os.environ.get("OP_DISPLAY")
    if env:
        return env
    try:
        with open(DISPLAY_FILE) as f:
            return f.readline().strip() or None
    except OSError:
        return None


def _usable(display: str | None) -> str | None:
    """`display`, unless it names a local server whose socket does not exist."""
    if not display:
        return None
    m = re.match(r"^(?:unix)?:(\d+)(?:\.\d+)?$", display)
    if m and not os.path.exists(f"/tmp/.X11-unix/X{m.group(1)}"):
        return None
    return display


def default_display() -> str:
    """The display `op` targets when none is given explicitly."""
    return (_configured_display() or _usable(os.environ.get("DISPLAY"))
            or FALLBACK_DISPLAY)


def setup_env(display: str | None = None, xauthority: str | None = None) -> None:
    """Point this process (and its children) at the target X display."""
    os.environ["DISPLAY"] = display or default_display()
    xauth = xauthority or os.environ.get("XAUTHORITY") or DEFAULT_XAUTHORITY
    if os.path.exists(xauth):
        os.environ["XAUTHORITY"] = xauth


def display():
    """Lazily open (and cache) the Xlib display connection."""
    global _display
    if _display is None:
        from Xlib import display as xdisplay

        _display = xdisplay.Display(os.environ.get("DISPLAY"))
    return _display


def root():
    return display().screen().root


@dataclass
class Geometry:
    width: int
    height: int

    def __str__(self) -> str:
        return f"{self.width}x{self.height}"


def screen_geometry() -> Geometry:
    """Current root window size.

    Not stable: remote-desktop gateways (Guacamole, xrdp) resize the screen
    to match the user's browser window, so callers must re-read it rather
    than caching.
    """
    g = root().get_geometry()
    return Geometry(g.width, g.height)


def idle_seconds() -> float:
    """Seconds since the user's last real keyboard/mouse input.

    Used to avoid fighting the user for the shared cursor. Returns -1.0 if the
    XScreenSaver extension is unavailable.
    """
    try:
        from Xlib.ext import screensaver  # noqa: F401

        info = display().screen().root.screensaver_query_info()
        return info.idle / 1000.0
    except Exception:
        return -1.0
