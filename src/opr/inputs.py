"""Synthetic pointer and keyboard input via the X TEST extension.

No root and no xdotool: every X server this targets (Xorg, Xtigervnc, Xephyr)
advertises XTEST, so we drive it directly through python-xlib.

Keyboard handling is the fiddly part. A character is typed by finding a
keycode whose mapping contains the needed keysym, pressing whatever modifiers
that mapping level requires, tapping the key, then releasing. Characters the
current layout cannot produce at all are typed by temporarily binding them to
a spare keycode and restoring the keymap afterwards -- the same trick xdotool
uses. `type --paste` sidesteps all of it via the clipboard.
"""
from __future__ import annotations

import subprocess
import time

from Xlib import X, XK
from Xlib.ext import xtest

from . import xconn

# Delays that make synthetic input land reliably in real toolkits.
MOVE_SETTLE = 0.03
CLICK_HOLD = 0.04
KEY_HOLD = 0.02
MOD_SETTLE = 0.02

MODIFIER_ALIASES = {
    "ctrl": "Control_L", "control": "Control_L",
    "shift": "Shift_L",
    "alt": "Alt_L", "meta": "Alt_L",
    "super": "Super_L", "win": "Super_L", "cmd": "Super_L",
    "altgr": "ISO_Level3_Shift",
}

KEY_ALIASES = {
    "enter": "Return", "ret": "Return", "esc": "Escape",
    "del": "Delete", "backspace": "BackSpace", "bksp": "BackSpace",
    "pgup": "Prior", "pageup": "Prior", "pgdn": "Next", "pagedown": "Next",
    "up": "Up", "down": "Down", "left": "Left", "right": "Right",
    "home": "Home", "end": "End", "tab": "Tab", "space": "space",
    "ins": "Insert", "menu": "Menu", "print": "Print",
}

SCROLL_BUTTONS = {"up": 4, "down": 5, "left": 6, "right": 7}


def _d():
    return xconn.display()


def _sync():
    _d().sync()


# ---------------------------------------------------------------- pointer


def pointer_position() -> tuple[int, int]:
    p = xconn.root().query_pointer()
    return p.root_x, p.root_y


def move(x: int, y: int) -> dict:
    xtest.fake_input(_d(), X.MotionNotify, x=x, y=y)
    _sync()
    time.sleep(MOVE_SETTLE)
    return {"moved": f"{x},{y}"}


def _tap_button(button: int) -> None:
    xtest.fake_input(_d(), X.ButtonPress, button)
    _sync()
    time.sleep(CLICK_HOLD)
    xtest.fake_input(_d(), X.ButtonRelease, button)
    _sync()


def click(x=None, y=None, button: int = 1, double: bool = False,
          restore: bool = True) -> dict:
    """Click at (x, y), or wherever the pointer already is.

    By default the pointer is returned to where the user left it, so our
    actions don't strand their cursor. Hover-driven UIs (submenus that close
    when the pointer leaves) want --no-restore.
    """
    origin = pointer_position()
    if x is not None and y is not None:
        move(x, y)
    where = pointer_position()

    _tap_button(button)
    if double:
        time.sleep(0.06)
        _tap_button(button)

    result = {"clicked": f"{where[0]},{where[1]}", "button": button,
              "double": double}
    if restore and origin != where:
        time.sleep(MOVE_SETTLE)
        xtest.fake_input(_d(), X.MotionNotify, x=origin[0], y=origin[1])
        _sync()
        result["pointer_restored_to"] = f"{origin[0]},{origin[1]}"
    return result


def drag(x1: int, y1: int, x2: int, y2: int, button: int = 1,
         steps: int = 18) -> dict:
    """Press at one point, glide to another, release.

    Intermediate motion events matter: toolkits ignore a teleporting drag.
    """
    move(x1, y1)
    xtest.fake_input(_d(), X.ButtonPress, button)
    _sync()
    time.sleep(CLICK_HOLD)

    for i in range(1, steps + 1):
        xtest.fake_input(
            _d(), X.MotionNotify,
            x=int(x1 + (x2 - x1) * i / steps),
            y=int(y1 + (y2 - y1) * i / steps),
        )
        _sync()
        time.sleep(0.012)

    time.sleep(CLICK_HOLD)
    xtest.fake_input(_d(), X.ButtonRelease, button)
    _sync()
    return {"dragged": f"{x1},{y1} -> {x2},{y2}", "button": button}


def scroll(direction: str, amount: int = 3, x=None, y=None) -> dict:
    button = SCROLL_BUTTONS[direction]
    if x is not None and y is not None:
        move(x, y)
    for _ in range(amount):
        _tap_button(button)
        time.sleep(0.03)
    return {"scrolled": direction, "clicks": amount,
            "at": "%s,%s" % pointer_position()}


# --------------------------------------------------------------- keyboard


def _keysym(name: str) -> int:
    """Resolve a key name or single character to an X keysym."""
    name = KEY_ALIASES.get(name.lower(), name)
    ks = XK.string_to_keysym(name)
    if ks:
        return ks
    if len(name) == 1:
        cp = ord(name)
        # Latin-1 maps 1:1 onto keysyms; everything else uses the Unicode range.
        return cp if 0x20 <= cp <= 0xFF else 0x01000000 + cp
    for variant in (name.capitalize(), name.upper(), name.lower()):
        ks = XK.string_to_keysym(variant)
        if ks:
            return ks
    raise SystemExit(f"unknown key: {name!r}")


def _lookup(keysym: int):
    """Find (keycode, mapping_level) for a keysym, or None if unmapped."""
    candidates = _d().keysym_to_keycodes(keysym)
    best = None
    for keycode, index in candidates:
        if keycode and (best is None or index < best[1]):
            best = (keycode, index)
    return best


def _spare_keycode() -> int:
    """A keycode with no keysyms bound to it, usable for temporary mapping."""
    info = _d().get_keyboard_mapping(8, 248 - 8)
    for offset, syms in enumerate(info):
        if not any(syms):
            return 8 + offset
    raise SystemExit("no spare keycode available for remapping")


def _modifiers_for_level(index: int) -> list[str]:
    return {0: [], 1: ["Shift_L"], 2: ["ISO_Level3_Shift"],
            3: ["ISO_Level3_Shift", "Shift_L"]}.get(index, [])


def _press(keycode: int) -> None:
    xtest.fake_input(_d(), X.KeyPress, keycode)
    _sync()


def _release(keycode: int) -> None:
    xtest.fake_input(_d(), X.KeyRelease, keycode)
    _sync()


def _tap_keysym(keysym: int, extra_mods: list[str] | None = None) -> None:
    """Press a keysym with whatever modifiers its mapping level requires."""
    found = _lookup(keysym)
    temporary = None
    if found is None:
        temporary = _spare_keycode()
        _d().change_keyboard_mapping(temporary, [[keysym] * 4])
        _sync()
        time.sleep(MOD_SETTLE)
        found = (temporary, 0)

    keycode, level = found
    mod_codes = [_lookup(_keysym(m))[0] for m in
                 (_modifiers_for_level(level) + (extra_mods or []))]
    try:
        for mc in mod_codes:
            _press(mc)
        if mod_codes:
            time.sleep(MOD_SETTLE)
        _press(keycode)
        time.sleep(KEY_HOLD)
        _release(keycode)
    finally:
        for mc in reversed(mod_codes):
            _release(mc)
        if temporary is not None:
            _d().change_keyboard_mapping(temporary, [[X.NoSymbol] * 4])
            _sync()


def key(chord, repeat: int = 1) -> dict:
    """Press one or more chords, e.g. 'ctrl+l', 'Return', 'ctrl+shift+t'."""
    chords = [chord] if isinstance(chord, str) else list(chord)
    for _ in range(repeat):
        for c in chords:
            parts = c.split("+") if c != "+" else ["plus"]
            *mods, final = [p for p in parts if p]
            mod_names = [MODIFIER_ALIASES.get(m.lower(), m) for m in mods]
            _tap_keysym(_keysym(final), extra_mods=mod_names)
            time.sleep(0.03)
    return {"pressed": " ".join(chords), "repeat": repeat}


def _clipboard_get() -> str | None:
    try:
        r = subprocess.run(["xclip", "-o", "-selection", "clipboard"],
                           capture_output=True, timeout=3)
        return r.stdout.decode("utf-8", "replace") if r.returncode == 0 else None
    except Exception:
        return None


def _clipboard_set(text: str) -> None:
    subprocess.run(["xclip", "-i", "-selection", "clipboard"],
                   input=text.encode("utf-8"), check=True, timeout=5)


def type_text(text: str, paste: bool = False, delay: float = 0.012,
              enter: bool = False) -> dict:
    """Type text keystroke by keystroke, or via the clipboard with --paste."""
    if paste:
        saved = _clipboard_get()
        _clipboard_set(text)
        time.sleep(0.08)
        key("ctrl+v")
        time.sleep(0.15)
        if saved is not None:
            _clipboard_set(saved)  # leave the user's clipboard as we found it
        result = {"typed": f"{len(text)} chars", "method": "clipboard paste",
                  "clipboard_restored": saved is not None}
    else:
        for ch in text:
            if ch == "\n":
                _tap_keysym(_keysym("Return"))
            elif ch == "\t":
                _tap_keysym(_keysym("Tab"))
            else:
                _tap_keysym(_keysym(ch))
            time.sleep(delay)
        result = {"typed": f"{len(text)} chars", "method": "keystrokes"}

    if enter:
        time.sleep(0.05)
        key("Return")
        result["enter"] = True
    return result
