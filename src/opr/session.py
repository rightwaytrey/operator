"""Isolated sessions: one X server per agent.

Agents sharing the main display share one pointer, one keyboard focus and one
Chrome, so their clicks, keystrokes and tabs interleave -- with each other and
with the user. A session gives an agent all three to itself:

  - its own X server. By default that is Xtigervnc: a headless X server that
    is also a VNC server on 127.0.0.1:59NN, which the viewer (viewer.py) turns
    into a web page the user can watch, or take over, from a browser.
    `--xephyr` instead opens the session as a window on the user's own
    desktop, the way it worked before the viewer existed;
  - a window manager inside it (xfwm4), so `op windows` / `op activate` work;
  - its own Chrome, on its own debug port and profile, started lazily by the
    first `op -s NAME web ...` exactly as on the main desktop.

Every other subcommand is unchanged: `op -s NAME shot|click|type|web ...`
simply points at the session's display, cookie and debug port instead of the
defaults. The registry is one JSON file per session under ~/.cache/op/sessions.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import time
from contextlib import contextmanager

from . import xconn

STATE_DIR = os.path.expanduser("~/.cache/op/sessions")
PROFILES_DIR = os.path.expanduser("~/.config/operator-chrome-sessions")

# The main desktop. A session overrides all four.
DEFAULT = {
    "name": None,
    "display": xconn.default_display(),
    "xauthority": xconn.DEFAULT_XAUTHORITY,
    "cdp_port": 9222,
    "chrome_profile": os.path.expanduser("~/.config/operator-chrome"),
}

FIRST_DISPLAY = 20      # leave low numbers to real servers (:0, :10, xrdp)
FIRST_CDP_PORT = 9300
VNC_PORT_BASE = 5900    # a session on :N serves VNC on 5900+N, the convention

KIND_XVNC = "xvnc"      # headless, watched through the viewer (default)
KIND_XEPHYR = "xephyr"  # a window on the user's desktop

REPO_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
VENDOR_DIR = os.path.join(REPO_DIR, "vendor")   # filled by install.sh

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,39}$")

_current = dict(DEFAULT)


def current() -> dict:
    """The session this process is acting on (the main desktop by default)."""
    return _current


def cdp_port() -> int:
    return _current["cdp_port"]


def chrome_profile() -> str:
    return _current["chrome_profile"]


def chrome_pids(profile: str) -> list[int]:
    return _chrome_pids(profile)


def _path(name: str, ext: str = "json") -> str:
    return os.path.join(STATE_DIR, f"{name}.{ext}")


def _alive(pid) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # A zombie still answers kill(0); it is not running.
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return False


def alive(pid) -> bool:
    return _alive(pid)


def find(name: str) -> dict | None:
    """The registry entry for `name`, or None (never raises)."""
    try:
        with open(_path(name)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def load(name: str) -> dict:
    s = find(name)
    if s is None:
        raise SystemExit(f"no session {name!r}; create it with:  "
                         f"op session new {name}")
    return s


def xvnc_binary() -> str | None:
    """TigerVNC's X server: the distro package if installed, else the copy
    install.sh unpacks under vendor/ (no root needed for that)."""
    for name in ("Xtigervnc", "Xvnc"):
        found = shutil.which(name)
        if found:
            return found
    vendored = os.path.join(VENDOR_DIR, "tigervnc", "usr", "bin", "Xtigervnc")
    return vendored if os.access(vendored, os.X_OK) else None


def use(name: str | None) -> None:
    """Point this process at session `name` (None = the main desktop)."""
    global _current
    if not name:
        _current = dict(DEFAULT)
        return
    s = load(name)
    if not _alive(s.get("server_pid")):
        raise SystemExit(f"session {name!r} is dead (its X server exited); "
                         f"clean up with:  op session rm {name}")
    _current = {**DEFAULT, **s}


@contextmanager
def _registry_lock():
    """Serialise allocation, so two agents creating sessions at once cannot
    be handed the same display number or debug port."""
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(os.path.join(STATE_DIR, ".lock"), "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _all() -> list[dict]:
    if not os.path.isdir(STATE_DIR):
        return []
    out = []
    for entry in sorted(os.listdir(STATE_DIR)):
        if entry.endswith(".json"):
            try:
                with open(os.path.join(STATE_DIR, entry)) as f:
                    out.append(json.load(f))
            except (OSError, ValueError):
                continue
    return out


def all_sessions() -> list[dict]:
    return _all()


def port_free(port: int) -> bool:
    with socket.socket() as s:
        # Servers set SO_REUSEADDR, so connections lingering in TIME_WAIT on
        # this port would not stop them; the probe must not count them either.
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _free_display(taken: set[int]) -> int:
    n = FIRST_DISPLAY
    while (n in taken or os.path.exists(f"/tmp/.X11-unix/X{n}")
           or os.path.exists(f"/tmp/.X{n}-lock")
           or not port_free(VNC_PORT_BASE + n)):
        n += 1
    return n


def _free_port(taken: set[int]) -> int:
    port = FIRST_CDP_PORT
    while port in taken or not port_free(port):
        port += 1
    return port


def _spawn(argv, env) -> int:
    return subprocess.Popen(
        argv, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, start_new_session=True).pid


def _wait_for_server(display: str, xauth: str, pid: int, timeout=10.0) -> None:
    from Xlib import display as xdisplay

    os.environ["XAUTHORITY"] = xauth
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        if not _alive(pid):
            raise SystemExit(f"X server for {display} exited during startup")
        try:
            xdisplay.Display(display).close()
            return
        except Exception as exc:
            last = exc
            time.sleep(0.2)
    raise SystemExit(f"X server {display} did not come up: {last}")


def _clone_profile(dest: str) -> None:
    """Copy the main operator-chrome profile, minus caches and locks, so the
    session starts with the same logins. Run only while that Chrome is idle
    enough not to matter -- cookies are SQLite, a torn copy just logs out."""
    src = DEFAULT["chrome_profile"]
    if not os.path.isdir(src):
        raise SystemExit(f"no profile to clone at {src}")
    skip = shutil.ignore_patterns(
        "Singleton*", "*Cache*", "Code Cache", "GPUCache", "Service Worker",
        "Crashpad", "*.lock", "lockfile")
    shutil.copytree(src, dest, ignore=skip, dirs_exist_ok=True, symlinks=True)


def new(name: str, size: str = "1280x800", xephyr: bool = False,
        clone_profile: bool = True, hidden: bool = False) -> dict:
    # `hidden` is accepted for callers written against the old surface; a
    # default session is already off the user's desktop.
    if not _NAME_RE.match(name):
        raise SystemExit("session names are letters, digits, . _ - "
                         "(max 40, starting with a letter or digit)")
    if not re.match(r"^\d{3,5}x\d{3,5}$", size):
        raise SystemExit("--size wants WIDTHxHEIGHT, e.g. 1280x800")

    with _registry_lock():
        if os.path.exists(_path(name)):
            s = load(name)
            if _alive(s.get("server_pid")):
                raise SystemExit(f"session {name!r} already exists "
                                 f"(display {s['display']})")
            _teardown(s)    # dead leftover with the same name: replace it

        others = _all()
        n = _free_display({int(s["display"][1:]) for s in others})
        port = _free_port({s["cdp_port"] for s in others})
        display = f":{n}"
        vnc_port = VNC_PORT_BASE + n

        # A fresh cookie per session: only processes given this file (that
        # is, op itself and the apps it launches) can reach the server.
        xauth = _path(name, "xauth")
        open(xauth, "w").close()
        os.chmod(xauth, 0o600)
        cookie = subprocess.run(["mcookie"], capture_output=True, text=True,
                                check=True).stdout.strip()
        subprocess.run(["xauth", "-f", xauth, "add", display,
                        "MIT-MAGIC-COOKIE-1", cookie], check=True)

        host_env = dict(os.environ)   # Xephyr is a client of the MAIN desktop
        host_env["DISPLAY"] = DEFAULT["display"]
        host_env["XAUTHORITY"] = DEFAULT["xauthority"]
        common = [display, "-auth", xauth, "-nolisten", "tcp"]
        if xephyr:
            kind = KIND_XEPHYR
            argv = ["Xephyr", *common, "-screen", size,
                    "-title", f"op session: {name}", "-no-host-grab"]
        else:
            kind = KIND_XVNC
            xvnc = xvnc_binary()
            if not xvnc:
                raise SystemExit(
                    "Xtigervnc is not installed. Run ./install.sh in the "
                    "operator repo (it unpacks the Ubuntu package under "
                    "vendor/, no root needed) or  sudo apt install "
                    "tigervnc-standalone-server. For a window on the main "
                    "desktop instead, pass --xephyr.")
            argv = [xvnc, *common, "-geometry", size, "-depth", "24",
                    # VNC side: loopback only, no VNC auth (the viewer is the
                    # only door, and it listens on loopback), any number of
                    # watchers, and viewers may not resize the screen under
                    # the agent -- its coordinates come from screenshots.
                    "-rfbport", str(vnc_port), "-localhost",
                    "-SecurityTypes", "None", "-AlwaysShared",
                    "-AcceptSetDesktopSize=0",
                    "-desktop", f"op session: {name}"]
        server_pid = _spawn(argv, host_env)

        state = {
            "name": name, "display": display, "xauthority": xauth,
            "cdp_port": port,
            "chrome_profile": os.path.join(PROFILES_DIR, name),
            "kind": kind, "size": size,
            "server_pid": server_pid, "wm_pid": None,
            "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        if kind == KIND_XVNC:
            state["vnc_port"] = vnc_port
        # Register before waiting, so a failed start is still cleanable.
        with open(_path(name), "w") as f:
            json.dump(state, f, indent=2)

    try:
        _wait_for_server(display, xauth, server_pid)
    except SystemExit:
        _teardown(state)
        raise

    session_env = dict(os.environ)
    session_env["DISPLAY"] = display
    session_env["XAUTHORITY"] = xauth
    state["wm_pid"] = _spawn(["xfwm4", f"--display={display}",
                              "--compositor=off"], session_env)

    # Clone by default: a session should come up with the user's logins and
    # extensions. Never over an existing profile -- a reused name keeps the
    # logins made in it -- and not where there is no main profile to copy.
    profile_note = "  (fresh)"
    if os.path.isdir(os.path.join(state["chrome_profile"], "Default")):
        profile_note = "  (reused; its own logins kept)"
    elif clone_profile and os.path.isdir(DEFAULT["chrome_profile"]):
        _clone_profile(state["chrome_profile"])
        profile_note = "  (cloned from main)"
    os.makedirs(state["chrome_profile"], exist_ok=True)

    with open(_path(name), "w") as f:
        json.dump(state, f, indent=2)

    out = {"session": name, "display": display}
    if kind == KIND_XEPHYR:
        out["kind"] = f"xephyr {size}  (window 'op session: {name}' on {DEFAULT['display']})"
    else:
        out["kind"] = f"xvnc {size}  (headless; vnc 127.0.0.1:{vnc_port}, loopback only)"
        out["view"] = _view_line(name)
    out["cdp"] = f"http://127.0.0.1:{port}"
    out["chrome_profile"] = state["chrome_profile"] + profile_note
    out["use"] = f"op -s {name} <command>    e.g.  op -s {name} web goto example.com"
    return out


def _view_line(name: str) -> str:
    """Start the viewer if needed and describe where this session shows up.
    A viewer problem must not cost the agent its session, so it is reported
    rather than raised."""
    from . import viewer
    try:
        viewer.start()
    except SystemExit as exc:
        return f"(viewer not running: {exc})"
    urls = viewer.view_urls(name)
    note = "" if viewer.public_url() else \
        "   (local only -- publish tailnet-only with:  op viewer expose)"
    return f"{urls['open']}{note}"


def url(name: str) -> dict:
    s = load(name)
    if s.get("kind") != KIND_XVNC:
        return {"session": name,
                "view": f"(a {s.get('kind')} session: a window on the main desktop "
                        f"{DEFAULT['display']}, watch it there)"}
    from . import viewer
    urls = viewer.view_urls(name)
    return {"session": name, "open": urls["open"], "watch": urls["watch"],
            "index": viewer.base_url()}


def _chrome_pids(profile: str) -> list[int]:
    pids = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/cmdline", "rb") as f:
                args = f.read().decode(errors="replace").replace("\0", " ").split()
        except OSError:
            continue
        if f"--user-data-dir={profile}" in args \
                and not any(a.startswith("--type=") for a in args):
            pids.append(int(entry))
    return pids


def _stop(pid, grace=3.0) -> None:
    if not _alive(pid):
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.time() + grace
    while time.time() < deadline and _alive(pid):
        time.sleep(0.1)
    if _alive(pid):
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _teardown(s: dict, purge: bool = False) -> list[str]:
    stopped = []
    # Never touch the main desktop's Chrome, whatever a corrupt file says.
    profile = s.get("chrome_profile")
    if profile and profile != DEFAULT["chrome_profile"]:
        for pid in _chrome_pids(profile):
            _stop(pid)
            stopped.append(f"chrome {pid}")
    for key, label in (("wm_pid", "xfwm4"), ("server_pid", s.get("kind", "X"))):
        if _alive(s.get(key)):
            _stop(s[key])
            stopped.append(f"{label} {s[key]}")
    for ext in ("json", "xauth"):
        try:
            os.remove(_path(s["name"], ext))
        except FileNotFoundError:
            pass
    if purge and profile and profile.startswith(PROFILES_DIR + os.sep):
        shutil.rmtree(profile, ignore_errors=True)
        stopped.append(f"profile {profile}")
    return stopped


def rm(names: list[str], everything: bool = False, purge: bool = False) -> None:
    targets = _all() if everything else [load(n) for n in names]
    if not targets:
        print("(no sessions)")
        return
    for s in targets:
        stopped = _teardown(s, purge=purge)
        print(f"removed {s['name']} ({s['display']}): "
              + (", ".join(stopped) if stopped else "nothing was running"))


def listing() -> None:
    rows = _all()
    if not rows:
        print("(no sessions)  create one with:  op session new NAME")
        return
    from . import viewer
    base = viewer.base_url().rstrip("/") + "/"
    for s in rows:
        up = _alive(s.get("server_pid"))
        server = "up" if up else "DEAD"
        chrome = "chrome" if _chrome_pids(s["chrome_profile"]) else "-"
        view = ""
        if s.get("kind") == KIND_XVNC:
            view = ("  " + base + viewer.links(s["name"])["open"]) if up else ""
        elif s.get("kind") == KIND_XEPHYR:
            view = f"  (window on {DEFAULT['display']})"
        print(f"  {s['name']:<20} {s['display']:<5} {server:<5} "
              f"{s['kind']:<7} {s['size']:<10} cdp:{s['cdp_port']}  {chrome:<7}{view}")
    print(f"\n{len(rows)} session(s); target one with  op -s NAME <command>")
    print(f"viewer: {base}" + (
        "" if viewer.public_urls() else
        "   (local only -- publish tailnet-only with:  op viewer expose)"))
