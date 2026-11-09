"""op — drive the desktop on an X display.

Every subcommand is a one-shot process: connect, act, print a short report,
exit. Modules are imported lazily inside handlers so that a missing optional
dependency (e.g. playwright) only breaks the subcommand that needs it.
"""
from __future__ import annotations

import argparse
import os
import sys

from . import xconn


def _report(d: dict) -> None:
    for k, v in d.items():
        print(f"{k}: {v}")


def cmd_shot(a):
    from . import screen, windows

    region = None
    if a.region:
        region = tuple(int(v) for v in a.region.replace("x", ",").split(","))
        if len(region) != 4:
            raise SystemExit("--region needs x,y,w,h")
    elif a.window:
        g = windows.geometry(a.window)
        region = (g["x"], g["y"], g["width"], g["height"])

    info = screen.shot(region=region, scale=a.scale, grid=a.grid, out=a.out)
    _report(info)


def cmd_windows(a):
    from . import windows

    rows = windows.listing()
    if not rows:
        print("(no managed windows)")
        return
    width = max(len(r["geom"]) for r in rows)
    for r in rows:
        mark = "*" if r["active"] else " "
        print(f"{mark} {r['id']:>12}  {r['geom']:<{width}}  {r['title']}")
    print(f"\n{len(rows)} window(s); * = active")


def cmd_activate(a):
    from . import windows
    _report(windows.activate(a.window))


def cmd_geom(a):
    from . import windows
    _report(windows.geometry(a.window))


def cmd_idle(a):
    secs = xconn.idle_seconds()
    if secs < 0:
        print("idle: unknown (XScreenSaver extension unavailable)")
        return
    print(f"idle: {secs:.1f}s")
    if secs < 3:
        print("busy: the user is actively using this desktop right now")


def cmd_move(a):
    from . import inputs
    _report(inputs.move(a.x, a.y))


def cmd_click(a):
    from . import inputs
    _report(inputs.click(a.x, a.y, button=a.button, double=a.double,
                         restore=not a.no_restore))


def cmd_drag(a):
    from . import inputs
    _report(inputs.drag(a.x1, a.y1, a.x2, a.y2, button=a.button))


def cmd_scroll(a):
    from . import inputs
    _report(inputs.scroll(a.direction, a.amount, x=a.x, y=a.y))


def cmd_key(a):
    from . import inputs
    _report(inputs.key(a.chord, repeat=a.repeat))


def cmd_type(a):
    from . import inputs
    text = a.text
    if text == "-":
        text = sys.stdin.read()
    _report(inputs.type_text(text, paste=a.paste, delay=a.delay,
                             enter=a.enter))


def cmd_launch(a):
    from . import launch
    _report(launch.launch(a.what, wait=a.wait))


def cmd_web(a):
    from . import web
    web.dispatch(a)


def cmd_session(a):
    from . import session
    if a.sesscmd == "new":
        _report(session.new(a.name, size=a.size, xephyr=a.xephyr,
                            clone_profile=not a.fresh, hidden=a.hidden))
    elif a.sesscmd == "rm":
        if not a.names and not a.all:
            raise SystemExit("name a session to remove, or pass --all")
        session.rm(a.names, everything=a.all, purge=a.purge)
    elif a.sesscmd == "url":
        _report(session.url(a.name))
    else:
        session.listing()


def cmd_viewer(a):
    from . import viewer
    if a.viewcmd == "start":
        st = viewer.start(port=a.port)
        _report({"viewer": f"running (pid {st['pid']}, port {st['port']})",
                 "url": viewer.base_url()})
    elif a.viewcmd == "stop":
        print(viewer.stop())
    elif a.viewcmd == "url":
        print(viewer.base_url())
    elif a.viewcmd == "expose":
        print(viewer.expose(https_port=a.https_port, url=a.url))
    elif a.viewcmd == "unexpose":
        print(viewer.unexpose())
    elif a.viewcmd == "serve":
        viewer.serve(port=a.port)
    else:
        _report(viewer.report())


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="op", description=__doc__.splitlines()[0])
    p.add_argument("--display", default=None,
                   help=f"X display (default {xconn.default_display()})")
    p.add_argument("--xauthority", default=None)
    p.add_argument("-s", "--session", default=os.environ.get("OP_SESSION"),
                   help="act inside an isolated session (see `op session`) "
                        "instead of the main desktop; also $OP_SESSION")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("shot", help="capture a PNG of the screen")
    s.add_argument("--scale", type=float, default=0.5,
                   help="resize factor for the saved PNG (default 0.5); "
                        "grid labels always show TRUE screen coordinates")
    s.add_argument("--grid", action="store_true",
                   help="overlay labelled coordinate gridlines")
    s.add_argument("--window", help="capture just this window (id or title)")
    s.add_argument("--region", help="capture x,y,w,h")
    s.add_argument("--out", help="output path (default shots/shot-<time>.png)")
    s.set_defaults(func=cmd_shot)

    s = sub.add_parser("windows", help="list top-level windows")
    s.set_defaults(func=cmd_windows)

    s = sub.add_parser("activate", help="raise and focus a window")
    s.add_argument("window")
    s.set_defaults(func=cmd_activate)

    s = sub.add_parser("geom", help="show a window's position and size")
    s.add_argument("window")
    s.set_defaults(func=cmd_geom)

    s = sub.add_parser("idle", help="seconds since the user's last input")
    s.set_defaults(func=cmd_idle)

    s = sub.add_parser("move", help="move the pointer")
    s.add_argument("x", type=int)
    s.add_argument("y", type=int)
    s.set_defaults(func=cmd_move)

    s = sub.add_parser("click", help="click at a point (default: current position)")
    s.add_argument("x", type=int, nargs="?")
    s.add_argument("y", type=int, nargs="?")
    s.add_argument("--button", type=int, default=1, help="1=left 2=middle 3=right")
    s.add_argument("--double", action="store_true")
    s.add_argument("--no-restore", action="store_true",
                   help="leave the pointer at the click point")
    s.set_defaults(func=cmd_click)

    s = sub.add_parser("drag", help="press at one point, release at another")
    s.add_argument("x1", type=int)
    s.add_argument("y1", type=int)
    s.add_argument("x2", type=int)
    s.add_argument("y2", type=int)
    s.add_argument("--button", type=int, default=1)
    s.set_defaults(func=cmd_drag)

    s = sub.add_parser("scroll", help="scroll the wheel")
    s.add_argument("direction", choices=["up", "down", "left", "right"])
    s.add_argument("amount", type=int, nargs="?", default=3)
    s.add_argument("--x", type=int, help="scroll at this point instead of the cursor")
    s.add_argument("--y", type=int)
    s.set_defaults(func=cmd_scroll)

    s = sub.add_parser("key", help="press a key chord, e.g. ctrl+l, Return, alt+Tab")
    s.add_argument("chord", nargs="+")
    s.add_argument("--repeat", type=int, default=1)
    s.set_defaults(func=cmd_key)

    s = sub.add_parser("type", help="type text ('-' reads stdin)")
    s.add_argument("text")
    s.add_argument("--paste", action="store_true",
                   help="send via clipboard + ctrl+v (robust for unicode/long text)")
    s.add_argument("--enter", action="store_true", help="press Return afterwards")
    s.add_argument("--delay", type=float, default=0.012,
                   help="seconds between keystrokes")
    s.set_defaults(func=cmd_type)

    s = sub.add_parser("launch", help="start an app on the target display")
    s.add_argument("what", nargs="+", help="chrome | firefox | <command...>")
    s.add_argument("--wait", type=float, default=3.0,
                   help="seconds to wait for the window to appear")
    s.set_defaults(func=cmd_launch)

    s = sub.add_parser("web", help="drive Chrome over the DevTools protocol")
    s.add_argument("--tab", help="target a tab by number or title/url substring "
                                 "(default: the frontmost tab)")
    wsub = s.add_subparsers(dest="webcmd", required=True)
    w = wsub.add_parser("goto"); w.add_argument("url")
    w = wsub.add_parser("click"); w.add_argument("selector")
    w = wsub.add_parser("fill")
    w.add_argument("selector"); w.add_argument("value")
    w = wsub.add_parser("text")
    w.add_argument("selector", nargs="?", default="body")
    w.add_argument("--max", type=int, default=4000)
    w = wsub.add_parser("eval"); w.add_argument("expression")
    w = wsub.add_parser("where",
                        help="screen coordinates of a CSS selector, for op click")
    w.add_argument("selector")
    w = wsub.add_parser("shot"); w.add_argument("--out")
    wsub.add_parser("tabs")
    wsub.add_parser("close", help="close the targeted tab")
    w = wsub.add_parser("newtab", help="open a new tab (does not disturb others)")
    w.add_argument("url", nargs="?")
    wsub.add_parser("url")
    s.set_defaults(func=cmd_web)

    s = sub.add_parser("session",
                       help="isolated displays, one per agent: new | list | rm")
    ssub = s.add_subparsers(dest="sesscmd", required=True)
    w = ssub.add_parser("new", help="start an X server + WM for one agent, "
                                    "viewable in the browser via the viewer")
    w.add_argument("name")
    w.add_argument("--size", default="1280x800", help="WIDTHxHEIGHT")
    w.add_argument("--xephyr", action="store_true",
                   help="a window on the user's own desktop instead of a "
                        "headless, web-viewable display")
    w.add_argument("--hidden", action="store_true",
                   help=argparse.SUPPRESS)   # old surface; now the default
    w.add_argument("--fresh", action="store_true",
                   help="start Chrome on an empty profile instead of a copy "
                        "of the main one (logins, extensions)")
    w.add_argument("--clone-profile", action="store_true",
                   help=argparse.SUPPRESS)   # old surface; now the default
    ssub.add_parser("list", help="show sessions, whether they are alive, "
                                 "and their viewer URLs")
    w = ssub.add_parser("url", help="the viewer URLs for one session")
    w.add_argument("name")
    w = ssub.add_parser("rm", help="stop a session's Chrome, WM and X server")
    w.add_argument("names", nargs="*")
    w.add_argument("--all", action="store_true")
    w.add_argument("--purge", action="store_true",
                   help="also delete the session's Chrome profile")
    s.set_defaults(func=cmd_session)

    s = sub.add_parser("viewer",
                       help="the web page that shows every session live: "
                            "status | start | stop | url | expose | unexpose")
    vsub = s.add_subparsers(dest="viewcmd")
    vsub.add_parser("status", help="is it running, and at which URLs")
    w = vsub.add_parser("start", help="start it (sessions do this themselves)")
    w.add_argument("--port", type=int, default=None)
    vsub.add_parser("stop")
    vsub.add_parser("url", help="print the best URL: public if recorded, else "
                                "tailnet if exposed, else local")
    w = vsub.add_parser("expose", help="publish it: tailnet-only over HTTPS with "
                                       "`tailscale serve`, or --url to record where "
                                       "nginx already serves it (see deploy/)")
    w.add_argument("--https-port", type=int, default=None,
                   help="tailnet port (default 9700)")
    w.add_argument("--url", default=None,
                   help="record an externally served URL instead, e.g. "
                        "https://example.com/operator/")
    vsub.add_parser("unexpose", help="forget the recorded URL and remove the "
                                     "tailscale serve mapping")
    w = vsub.add_parser("serve", help="run the daemon in the foreground")
    w.add_argument("--port", type=int, default=None)
    s.set_defaults(func=cmd_viewer)

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    from . import session
    # `op session ...` manages sessions; it never runs inside one.
    if args.session and args.cmd != "session":
        session.use(args.session)
        cur = session.current()
        xconn.setup_env(args.display or cur["display"],
                        args.xauthority or cur["xauthority"])
    else:
        xconn.setup_env(args.display, args.xauthority)
        # The shared Chrome is where agents collide; say so every time.
        if args.cmd == "web" or (args.cmd == "launch" and args.what == ["chrome"]):
            print("op: note: this is the SHARED Chrome on the main desktop -- "
                  "other agents may be using it. Use your own instance:  "
                  "op session new NAME, then  op -s NAME web ...",
                  file=sys.stderr)
    try:
        args.func(args)
    except SystemExit:
        raise
    except Exception as exc:  # keep failures short and legible in transcripts
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
