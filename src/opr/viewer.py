"""The viewer: one small web server that shows every agent's display, live.

A default session (see session.py) is an Xtigervnc server: a headless X
server that is also a VNC server on 127.0.0.1:59NN. This module is the web
side of that -- a single websockify process on 127.0.0.1:6090 that serves

  /                       a live tile per session, with Open / Watch links
  /tile.html?token=NAME   one bare, scaled, view-only client (what the tiles embed)
  /open.html?token=NAME   noVNC's full interactive client under a bar with a way back
  /zoom.js                pinch-to-zoom and pan, shared by both pages
  /api/sessions           the same as JSON
  /vnc.html, /core/...    noVNC itself (unpacked under vendor/novnc by install.sh)
  /websockify?token=NAME  the WebSocket -> VNC bridge for session NAME

Nothing listens beyond loopback. Two things publish it:

  - `op viewer expose` maps it onto the tailnet through `tailscale serve`
    (HTTPS, tailnet-only).
  - nginx can serve it under a path of an existing site that already has
    authentication in front (deploy/nginx-operator.conf), which works from
    any device without Tailscale. `op viewer expose --url` records that URL
    so the CLI can print it; nothing here manages nginx.

`op session new` starts the viewer if it is not running and prints the
resulting URL, so an agent can hand the user a link when it needs them to
look, or to type something it must not.

The daemon is this module run as a script (`python -m opr.viewer serve`);
`op viewer start|stop|status|url|expose|unexpose` manage it from the CLI.
"""
from __future__ import annotations

import http.client
import json
import logging
import os
import signal
import socket
import subprocess
import sys
import time
from urllib.parse import quote, urlparse

from . import session

STATE = os.path.expanduser("~/.cache/op/viewer.json")
LOG = os.path.expanduser("~/.cache/op/viewer.log")
# Where something else (nginx, see deploy/) serves the viewer, as recorded by
# `op viewer expose --url`. It works from anywhere, so it is the preferred base.
PUBLIC_URL_FILE = os.path.expanduser("~/.config/op/viewer-public-url")
DEFAULT_PORT = int(os.environ.get("OP_VIEWER_PORT", "6090"))
DEFAULT_HTTPS_PORT = 9700          # the tailnet-facing port `expose` uses
NOVNC_DIR = os.path.join(session.VENDOR_DIR, "novnc")
_HERE = os.path.dirname(os.path.abspath(__file__))
INDEX_HTML = os.path.join(_HERE, "viewer_index.html")
TILE_HTML = os.path.join(_HERE, "viewer_tile.html")
OPEN_HTML = os.path.join(_HERE, "viewer_open.html")
ZOOM_JS = os.path.join(_HERE, "viewer_zoom.js")
LIVE_JS = os.path.join(_HERE, "viewer_live.js")
SRC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def novnc_dir() -> str:
    if not os.path.isfile(os.path.join(NOVNC_DIR, "vnc.html")):
        raise SystemExit(f"noVNC is not unpacked at {NOVNC_DIR}: run "
                         f"./install.sh in the operator repo")
    return NOVNC_DIR


# --------------------------------------------------------------------- content

def links(name: str) -> dict:
    """noVNC URLs for one session, relative to the viewer root."""
    path = quote(f"websockify?token={name}", safe="")
    return {
        # The full client (viewer_open.html wrapping noVNC's vnc.html):
        # interactive, scaled to the window, reconnects by itself.
        "open": f"open.html?token={quote(name, safe='')}",
        # Our bare client (viewer_tile.html), view-only: what the tiles embed.
        "watch": f"tile.html?token={quote(name, safe='')}",
    }


def _age(created: str | None) -> str:
    try:
        secs = time.time() - time.mktime(time.strptime(created, "%Y-%m-%d %H:%M:%S"))
    except (TypeError, ValueError):
        return "?"
    if secs < 90:
        return f"{int(secs)}s"
    if secs < 5400:
        return f"{int(secs / 60)}m"
    if secs < 172800:
        return f"{secs / 3600:.1f}h"
    return f"{int(secs / 86400)}d"


def sessions_json() -> dict:
    rows = []
    for s in session.all_sessions():
        alive = session.alive(s.get("server_pid"))
        row = {k: s.get(k) for k in ("name", "display", "kind", "size",
                                     "created", "vnc_port")}
        row["alive"] = alive
        row["chrome"] = bool(session.chrome_pids(s["chrome_profile"])) if alive else False
        row["age"] = _age(s.get("created"))
        if alive and s.get("kind") == session.KIND_XVNC:
            row.update(links(s["name"]))
        rows.append(row)
    return {"host": socket.gethostname(), "sessions": rows,
            "time": time.strftime("%Y-%m-%d %H:%M:%S")}


# ---------------------------------------------------------------------- daemon

def _handler_class():
    from websockify.token_plugins import BasePlugin
    from websockify.websocketproxy import ProxyRequestHandler

    class SessionTokens(BasePlugin):
        """`?token=NAME` -> that session's VNC port, looked up at connect
        time in the registry, so sessions come and go without restarts."""

        def lookup(self, token):
            s = session.find(token)
            if (not s or s.get("kind") != session.KIND_XVNC
                    or not session.alive(s.get("server_pid"))):
                return None
            return ("127.0.0.1", int(s["vnc_port"]))

    class Handler(ProxyRequestHandler):
        # websockify answers WebSocket upgrades before do_GET is reached, so
        # this only ever sees plain page requests. Anything not ours falls
        # through to noVNC's static files (the server chdir'd into them).
        def do_GET(self):
            path = urlparse(self.path).path
            if path in ("/", "/index.html"):
                with open(INDEX_HTML, "rb") as f:
                    self._reply(200, "text/html; charset=utf-8", f.read())
            elif path == "/tile.html":
                with open(TILE_HTML, "rb") as f:
                    self._reply(200, "text/html; charset=utf-8", f.read())
            elif path == "/open.html":
                with open(OPEN_HTML, "rb") as f:
                    self._reply(200, "text/html; charset=utf-8", f.read())
            elif path in ("/zoom.js", "/live.js"):
                with open(ZOOM_JS if path == "/zoom.js" else LIVE_JS, "rb") as f:
                    self._reply(200, "text/javascript; charset=utf-8", f.read())
            elif path == "/api/sessions":
                body = json.dumps(sessions_json()).encode()
                self._reply(200, "application/json", body)
            elif path == "/health":
                self._reply(200, "text/plain", b"ok\n")
            else:
                super().do_GET()

        def _reply(self, code: int, ctype: str, body: bytes) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

    return Handler, SessionTokens


def serve(port: int | None = None) -> None:
    """Run the viewer in the foreground (what `start` spawns detached)."""
    from websockify.websocketproxy import WebSocketProxy

    port = port or DEFAULT_PORT

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    handler, tokens = _handler_class()
    server = WebSocketProxy(
        RequestHandlerClass=handler, listen_host="127.0.0.1", listen_port=port,
        web=novnc_dir(), file_only=True, token_plugin=tokens(None),
        heartbeat=30)
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE, "w") as f:
        json.dump({"pid": os.getpid(), "port": port,
                   "started": time.strftime("%Y-%m-%d %H:%M:%S")}, f)
    server.start_server()


# ------------------------------------------------------------------ management

def _state() -> dict | None:
    try:
        with open(STATE) as f:
            st = json.load(f)
    except (OSError, ValueError):
        return None
    return st if session.alive(st.get("pid")) else None


def _healthy(port: int, timeout: float = 1.0) -> bool:
    try:
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
        c.request("GET", "/health")
        return c.getresponse().status == 200
    except (OSError, http.client.HTTPException):
        return False


def status() -> dict | None:
    """The running viewer's state, or None."""
    st = _state()
    return st if st and _healthy(st["port"]) else None


def _viewer_pids() -> list[int]:
    """Every viewer process on the machine, by command line: the daemon and
    the per-connection children websockify forks (they inherit the listening
    socket, so a leftover child keeps the port busy)."""
    pids = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit() or int(entry) == os.getpid():
            continue
        try:
            with open(f"/proc/{entry}/cmdline", "rb") as f:
                args = f.read().split(b"\0")
        except OSError:
            continue
        if b"opr.viewer" in args and b"serve" in args:
            pids.append(int(entry))
    return pids


def _kill_all(port: int, grace: float = 3.0) -> None:
    """Stop every viewer process and wait until the port is really free."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        for pid in _viewer_pids():
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                pass
        deadline = time.time() + grace
        while time.time() < deadline:
            if not _viewer_pids() and session.port_free(port):
                return
            time.sleep(0.1)


def start(port: int | None = None) -> dict:
    """Make sure the viewer is up; return its state. Idempotent."""
    port = port or DEFAULT_PORT
    st = status()
    if st:
        return st
    web = novnc_dir()
    # A daemon that is alive but not answering, or children of a stopped one
    # still holding the socket: clear them out before binding again.
    if _state() or _viewer_pids():
        _kill_all(port)
    if not session.port_free(port):
        raise SystemExit(f"port {port} is taken by something else; "
                         f"set OP_VIEWER_PORT to another port")
    env = dict(os.environ)
    env["PYTHONPATH"] = SRC_DIR + (
        ":" + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "ab") as log:
        subprocess.Popen(
            [sys.executable, "-m", "opr.viewer", "serve", "--port", str(port)],
            env=env, cwd=web, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
            start_new_session=True)
    deadline = time.time() + 8
    while time.time() < deadline:
        st = status()
        if st:
            return st
        time.sleep(0.2)
    raise SystemExit(f"viewer did not come up on port {port}; see {LOG}")


def stop() -> str:
    st = _state()
    port = st["port"] if st else _viewer_port()
    if not st and not _viewer_pids():
        return "viewer is not running"
    _kill_all(port)
    try:
        os.remove(STATE)
    except FileNotFoundError:
        pass
    return (f"stopped viewer (pid {st['pid']}, port {port})" if st
            else f"stopped leftover viewer processes on port {port}")


def public_url(port: int | None = None) -> str | None:
    """The tailnet URL `tailscale serve` maps onto the viewer, if any."""
    port = port or _viewer_port()
    try:
        out = subprocess.run(["tailscale", "serve", "status", "--json"],
                             capture_output=True, text=True, timeout=5)
        cfg = json.loads(out.stdout or "{}") if out.returncode == 0 else {}
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
    for hostport, web in (cfg.get("Web") or {}).items():
        for path, h in (web.get("Handlers") or {}).items():
            proxy = (h or {}).get("Proxy", "")
            u = urlparse(proxy)
            if u.port == port and u.hostname in ("127.0.0.1", "localhost"):
                return f"https://{hostport}" + ("" if path == "/" else path)
    return None


def recorded_url() -> str | None:
    """The URL something else (nginx) serves the viewer at, if recorded."""
    try:
        with open(PUBLIC_URL_FILE) as f:
            url = f.read().strip()
    except OSError:
        return None
    return url or None


def public_urls(port: int | None = None) -> list[tuple[str, str]]:
    """Every non-local base the viewer is reachable at: the recorded public
    one first (it works from anywhere), then the tailnet one."""
    out = []
    rec = recorded_url()
    if rec:
        out.append(("public", rec))
    ts = public_url(port)
    if ts:
        out.append(("tailnet", ts))
    return out


def _viewer_port() -> int:
    st = _state()
    return st["port"] if st else DEFAULT_PORT


def base_url() -> str:
    """The best base: the recorded public URL, else tailnet, else loopback."""
    port = _viewer_port()
    urls = public_urls(port)
    return urls[0][1] if urls else f"http://127.0.0.1:{port}"


def view_urls(name: str) -> dict:
    """Absolute Open / Watch URLs for a session, on the best base we have."""
    base = base_url().rstrip("/") + "/"
    return {k: base + v for k, v in links(name).items()}


def expose(https_port: int | None = None, url: str | None = None) -> str:
    """Publish the viewer. With `url`, record that something else (nginx, see
    deploy/) already serves it there; otherwise map it onto the tailnet with
    `tailscale serve`."""
    if url:
        u = urlparse(url)
        if u.scheme not in ("http", "https") or not u.netloc:
            raise SystemExit(f"not an http(s) URL: {url}")
        url = url.rstrip("/") + "/"
        os.makedirs(os.path.dirname(PUBLIC_URL_FILE), exist_ok=True)
        with open(PUBLIC_URL_FILE, "w") as f:
            f.write(url + "\n")
        return url
    https_port = https_port or DEFAULT_HTTPS_PORT
    st = start()
    r = subprocess.run(["tailscale", "serve", "--bg", f"--https={https_port}",
                        f"http://127.0.0.1:{st['port']}"],
                       capture_output=True, text=True)
    if r.returncode:
        raise SystemExit("tailscale serve failed: "
                         + (r.stderr.strip() or r.stdout.strip()))
    url = public_url(st["port"])
    if not url:
        raise SystemExit("tailscale serve accepted the mapping but does not "
                         "report it; check  tailscale serve status")
    return url


def unexpose() -> str:
    """Forget the recorded public URL and remove the tailnet mapping."""
    done = []
    rec = recorded_url()
    if rec:
        os.remove(PUBLIC_URL_FILE)
        done.append(f"forgot {rec} (nginx still serves it; see deploy/)")
    url = public_url()
    if url:
        https_port = urlparse(url).port or 443
        r = subprocess.run(["tailscale", "serve", f"--https={https_port}", "off"],
                           capture_output=True, text=True)
        if r.returncode:
            raise SystemExit("tailscale serve failed: "
                             + (r.stderr.strip() or r.stdout.strip()))
        done.append(f"removed {url}")
    return "; ".join(done) if done else "viewer is not exposed"


def report() -> dict:
    st = status()
    port = st["port"] if st else DEFAULT_PORT
    pub = public_url(port)
    rows = session.all_sessions()
    live = [s for s in rows if session.alive(s.get("server_pid"))]
    viewable = [s for s in live if s.get("kind") == session.KIND_XVNC]
    return {
        "viewer": (f"running (pid {st['pid']}, since {st['started']})" if st
                   else "not running  (op viewer start, or op session new)"),
        "local": f"http://127.0.0.1:{port}/",
        "public": recorded_url() or ("(none recorded)  nginx: sudo deploy/install-nginx.sh,"
                                     " then  op viewer expose --url URL"),
        "tailnet": pub or "(not exposed)  tailnet-only HTTPS with:  op viewer expose",
        "sessions": f"{len(live)} live, {len(viewable)} viewable here",
        "log": LOG,
    }


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(prog="python -m opr.viewer")
    p.add_argument("cmd", choices=["serve"])
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    a = p.parse_args()
    serve(a.port)
