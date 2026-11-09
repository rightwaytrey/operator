"""Start applications on the target display, detached from this session."""
from __future__ import annotations

import json
import os
import shutil
import shlex
import subprocess
import time

from . import session, windows, xconn


CHROME_CANDIDATES = ("google-chrome", "google-chrome-stable",
                     "chromium", "chromium-browser")


def chrome_binary() -> str:
    """$OP_CHROME, else the first Chrome/Chromium on PATH."""
    want = os.environ.get("OP_CHROME")
    if want:
        return want
    for name in CHROME_CANDIDATES:
        if shutil.which(name):
            return name
    raise SystemExit("no Chrome found on PATH (tried " + ", ".join(CHROME_CANDIDATES)
                     + "); install Google Chrome or Chromium, or set OP_CHROME")


# A dedicated Chrome profile keeps the user's everyday Chrome (and its logins)
# untouched, while --remote-debugging-port gives us DOM-level control of a
# window that is still fully visible on the display. Port and profile come
# from the active session, so each `op -s NAME` gets its own.
def _chrome_argv() -> list[str]:
    return [
        chrome_binary(),
        f"--remote-debugging-port={session.cdp_port()}",
        f"--user-data-dir={session.chrome_profile()}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-session-crashed-bubble",
        # Without this, GNOME raises a modal keyring "Authentication required"
        # dialog on every launch which blocks Chrome entirely -- CDP calls just
        # time out. This profile stores no passwords, so the basic store is
        # exactly right, and it means we never face a credential prompt we are
        # not allowed to answer.
        "--password-store=basic",
    ]


def _chrome_prefs() -> None:
    """Turn off Chrome's own password manager: the user keeps passwords in
    their own password manager, and Chrome's competing save bubble got dismissed with
    "Never" until each profile carried a blacklist. Chrome rewrites
    Preferences on exit, so only touch it while that profile is not running."""
    profile = session.chrome_profile()
    if session.chrome_pids(profile):
        return
    path = os.path.join(profile, "Default", "Preferences")
    try:
        with open(path) as f:
            prefs = json.load(f)
    except FileNotFoundError:
        prefs = {}
    except (OSError, ValueError):
        return  # unreadable: let Chrome deal with it rather than clobber it
    want = {"credentials_enable_service": False,
            "credentials_enable_autosignin": False}
    pm = prefs.setdefault("profile", {})
    if all(prefs.get(k) is v for k, v in want.items()) \
            and pm.get("password_manager_enabled") is False:
        return
    prefs.update(want)
    pm["password_manager_enabled"] = False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".op-tmp"
    with open(tmp, "w") as f:
        json.dump(prefs, f)
    os.replace(tmp, path)


def _chrome() -> list[str]:
    _chrome_prefs()
    return _chrome_argv()


PRESETS = {
    "chrome": _chrome,
    "firefox": lambda: ["firefox"],
}


def _spawn(argv: list[str]) -> int:
    env = dict(os.environ)
    env["DISPLAY"] = os.environ.get("DISPLAY") or xconn.default_display()
    if session.current()["name"] and shutil.which("dbus-run-session"):
        # A private session bus. On the user's own bus, Chrome asks
        # xdg-desktop-portal for file pickers, and GNOME opens that "Open
        # Files" dialog on the *main desktop*, out of sight of anyone
        # watching the session, while Chrome ignores every click in its
        # window until it is answered. Without a portal Chrome falls back to
        # its own GTK picker, which opens inside the session's display.
        env.pop("DBUS_SESSION_BUS_ADDRESS", None)
        argv = ["dbus-run-session", "--", *argv]
    proc = subprocess.Popen(
        argv, env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return proc.pid


def launch(what, wait: float = 3.0) -> dict:
    parts = [what] if isinstance(what, str) else list(what)

    if len(parts) == 1 and parts[0] in PRESETS:
        name = parts[0]
        argv = PRESETS[name]()
    else:
        name = parts[0]
        argv = parts if len(parts) > 1 else shlex.split(parts[0])

    before = {w["id"] for w in windows.listing()}
    pid = _spawn(argv)

    appeared = []
    deadline = time.time() + wait
    while time.time() < deadline:
        time.sleep(0.35)
        now = windows.listing()
        appeared = [w for w in now if w["id"] not in before]
        if appeared:
            break

    result = {"launched": name, "pid": pid, "argv": " ".join(argv)}
    if appeared:
        result["window"] = f"{appeared[0]['id']}  {appeared[0]['title']}"
        result["geom"] = appeared[0]["geom"]
    else:
        result["window"] = f"(none within {wait}s -- may still be starting)"
    if name == "chrome":
        result["cdp"] = f"http://127.0.0.1:{session.cdp_port()}"
    return result
