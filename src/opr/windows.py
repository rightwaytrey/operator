"""Window listing / focusing via EWMH properties on the target display."""
from __future__ import annotations

from Xlib import X, Xatom

from . import xconn


def _atom(name: str):
    return xconn.display().intern_atom(name)


def _prop(win, name: str, kind=X.AnyPropertyType):
    try:
        p = win.get_full_property(_atom(name), kind)
    except Exception:
        return None
    return p.value if p else None


def _title(win) -> str:
    for name, kind in (("_NET_WM_NAME", 0), ("WM_NAME", X.AnyPropertyType)):
        v = _prop(win, name, kind if kind else X.AnyPropertyType)
        if v:
            if isinstance(v, bytes):
                return v.decode("utf-8", "replace")
            if isinstance(v, str):
                return v
    return "<untitled>"


def _geometry(win):
    """Window position in ROOT coordinates, plus size."""
    g = win.get_geometry()
    try:
        coords = win.translate_coords(xconn.root(), 0, 0)
        # translate_coords returns the offset of the target origin relative to
        # this window, so negate to get the window's position on the root.
        x, y = -coords.x, -coords.y
    except Exception:
        x, y = g.x, g.y
    return x, y, g.width, g.height


def listing() -> list[dict]:
    """All managed top-level windows, in the window manager's stacking order."""
    ids = _prop(xconn.root(), "_NET_CLIENT_LIST", Xatom.WINDOW) or []
    active = active_id()
    out = []
    for wid in ids:
        try:
            win = xconn.display().create_resource_object("window", wid)
            x, y, w, h = _geometry(win)
            out.append({
                "id": hex(wid),
                "title": _title(win),
                "geom": f"{w}x{h}+{x}+{y}",
                "active": wid == active,
            })
        except Exception:
            continue
    return out


def active_id():
    v = _prop(xconn.root(), "_NET_ACTIVE_WINDOW", Xatom.WINDOW)
    return v[0] if v else None


def resolve(ref: str):
    """Resolve a window id (0x...) or case-insensitive title substring."""
    if ref in ("active", "focused"):
        wid = active_id()
        if not wid:
            raise SystemExit("no active window")
        return wid
    if ref.lower().startswith("0x"):
        return int(ref, 16)

    needle = ref.lower()
    matches = [w for w in listing() if needle in w["title"].lower()]
    if not matches:
        raise SystemExit(f"no window matching {ref!r}")
    if len(matches) > 1:
        lines = "\n".join(f"  {m['id']}  {m['title']}" for m in matches)
        raise SystemExit(f"{ref!r} matches {len(matches)} windows:\n{lines}")
    return int(matches[0]["id"], 16)


def geometry(ref: str) -> dict:
    wid = resolve(ref)
    win = xconn.display().create_resource_object("window", wid)
    x, y, w, h = _geometry(win)
    return {"id": hex(wid), "title": _title(win), "x": x, "y": y,
            "width": w, "height": h, "geom": f"{w}x{h}+{x}+{y}"}


def activate(ref: str) -> dict:
    """Raise and focus a window (EWMH _NET_ACTIVE_WINDOW client message)."""
    from Xlib.protocol import event

    wid = resolve(ref)
    d = xconn.display()
    win = d.create_resource_object("window", wid)
    msg = event.ClientMessage(
        window=win,
        client_type=_atom("_NET_ACTIVE_WINDOW"),
        data=(32, [2, X.CurrentTime, 0, 0, 0]),  # source 2 = pager/tool
    )
    xconn.root().send_event(
        msg, event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask)
    win.configure(stack_mode=X.Above)
    d.sync()
    return {"id": hex(wid), "title": _title(win), "activated": True}
