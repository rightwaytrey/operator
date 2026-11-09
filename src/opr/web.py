"""DOM-level control of Chrome over the DevTools protocol.

Chrome runs as a normal visible window on the target display (so the user
can watch it happen), and we attach to it with Playwright's
connect_over_cdp. Selecting elements by CSS beats guessing pixels: no
coordinate drift when the window resizes, and real waits for load state
instead of sleeping and hoping.

Each subcommand attaches, acts, and detaches -- Chrome keeps running.
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime

from . import session
from .screen import SHOTS_DIR


def _endpoint() -> str:
    return f"http://127.0.0.1:{session.cdp_port()}"


def _attach(pw, autostart: bool = True):
    """Attach to Chrome, starting it first if it isn't already running.

    Auto-starting means a caller can go straight to `op web goto ...` without
    knowing about the launch step.
    """
    try:
        return pw.chromium.connect_over_cdp(_endpoint(), timeout=8000)
    except Exception:
        pass

    if not autostart:
        raise SystemExit(f"cannot reach Chrome's debug port at {_endpoint()}")

    # Running but not answering (a hung page) is not "not running": launching
    # again only hands a new window to the stuck Chrome, one per retry.
    pid = _running_chrome_pid()
    if pid:
        raise SystemExit(
            f"Chrome is running (pid {pid}) but not responding on {_endpoint()} -- "
            "a page is probably hung. Do not retry in a loop; take a screenshot "
            "(op shot) or wait and try once more."
        )

    from .launch import launch

    print("(chrome was not running -- starting it)", file=sys.stderr)
    launch("chrome", wait=10)
    for _ in range(12):
        time.sleep(1)
        try:
            return pw.chromium.connect_over_cdp(_endpoint(), timeout=8000)
        except Exception as exc:
            last = exc
    raise SystemExit(
        f"started Chrome but still cannot reach {_endpoint()}: {last}\n"
        "take a screenshot -- a modal dialog may be blocking it:  op shot"
    )


def _running_chrome_pid() -> int | None:
    """PID of the browser process already using our profile, if any."""
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/cmdline", "rb") as f:
                # Chrome rewrites its argv into one space-joined string
                args = f.read().decode(errors="replace").replace("\0", " ").split()
        except OSError:
            continue
        if (f"--user-data-dir={session.chrome_profile()}" in args
                and not any(a.startswith("--type=") for a in args)):
            return int(entry)
    return None


def _frontmost_url() -> str | None:
    """URL of Chrome's most recently active tab, per its own target list."""
    import json
    import urllib.request

    try:
        with urllib.request.urlopen(f"{_endpoint()}/json/list", timeout=5) as r:
            for t in json.load(r):
                if t.get("type") == "page" and not \
                        t.get("url", "").startswith("devtools://"):
                    return t.get("url")
    except Exception:
        return None
    return None


def _all_pages(browser):
    pages = []
    for context in browser.contexts:
        pages.extend(p for p in context.pages
                     if not p.url.startswith("devtools://"))
    return pages


def _page(browser, want: str | None = None):
    """The tab the user is actually looking at, or one matching `want`.

    Playwright's page order does not track which tab is frontmost, and
    document.visibilityState is useless here because Playwright turns on focus
    emulation for every page it attaches to (they all claim to be visible).
    Chrome's own /json/list, however, is ordered most-recently-active first,
    so that is our source of truth.
    """
    pages = _all_pages(browser)

    if want:
        if want.isdigit():
            idx = int(want) - 1
            if not 0 <= idx < len(pages):
                raise SystemExit(f"no tab {want} (have {len(pages)})")
            return pages[idx]
        matches = [p for p in pages
                   if want.lower() in p.url.lower()
                   or want.lower() in (p.title() or "").lower()]
        if not matches:
            raise SystemExit(f"no tab matching {want!r}")
        return matches[0]

    front = _frontmost_url()
    if front:
        for p in pages:
            if p.url == front:
                return p

    if pages:
        return pages[-1]
    context = browser.contexts[0] if browser.contexts else browser.new_context()
    return context.new_page()


def dispatch(a) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise SystemExit("playwright is not installed: run  uv sync")

    with sync_playwright() as pw:
        browser = _attach(pw)
        page = _page(browser, getattr(a, "tab", None))
        cmd = a.webcmd

        if cmd == "goto":
            url = a.url if "://" in a.url else "https://" + a.url
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            print(f"url: {page.url}")
            print(f"title: {page.title()}")

        elif cmd == "click":
            page.click(a.selector, timeout=10000)
            page.wait_for_load_state("domcontentloaded", timeout=10000)
            print(f"clicked: {a.selector}")
            print(f"url: {page.url}")

        elif cmd == "fill":
            page.fill(a.selector, a.value, timeout=10000)
            print(f"filled: {a.selector} ({len(a.value)} chars)")

        elif cmd == "text":
            text = page.inner_text(a.selector, timeout=10000)
            text = "\n".join(line.rstrip() for line in text.splitlines()
                             if line.strip())
            if len(text) > a.max:
                text = text[:a.max] + f"\n... [truncated at {a.max} chars]"
            print(f"url: {page.url}")
            print(f"--- text of {a.selector} ---")
            print(text)

        elif cmd == "where":
            # Bridge from the DOM tier to the pixel tier: turn a CSS selector
            # into real screen coordinates, so `op click` can act on something
            # we located reliably rather than guessed at from a screenshot.
            box = page.evaluate(
                """(sel) => {
                    const el = document.querySelector(sel);
                    if (!el) return null;
                    el.scrollIntoViewIfNeeded ? el.scrollIntoViewIfNeeded()
                                              : el.scrollIntoView({block:'center'});
                    const r = el.getBoundingClientRect();
                    const chromeH = window.outerHeight - window.innerHeight;
                    return {
                        x: Math.round(window.screenX + r.x),
                        y: Math.round(window.screenY + chromeH + r.y),
                        w: Math.round(r.width), h: Math.round(r.height),
                        text: (el.innerText || el.value || '').trim().slice(0, 60),
                    };
                }""",
                a.selector,
            )
            if not box:
                raise SystemExit(f"no element matches {a.selector!r}")
            cx = box["x"] + box["w"] // 2
            cy = box["y"] + box["h"] // 2
            print(f"selector: {a.selector}")
            print(f"screen_box: {box['w']}x{box['h']}+{box['x']}+{box['y']}")
            print(f"center: {cx},{cy}")
            print(f"click_with: op click {cx} {cy}")
            if box["text"]:
                print(f"text: {box['text']}")

        elif cmd == "eval":
            print(page.evaluate(a.expression))

        elif cmd == "newtab":
            context = browser.contexts[0] if browser.contexts \
                else browser.new_context()
            fresh = context.new_page()
            if a.url:
                url = a.url if "://" in a.url else "https://" + a.url
                fresh.goto(url, wait_until="domcontentloaded", timeout=30000)
            fresh.bring_to_front()
            print(f"opened: {fresh.url}")
            print(f"tabs: {len(_all_pages(browser))}")

        elif cmd == "close":
            if len(_all_pages(browser)) == 1:
                print("note: this is the last tab; closing it exits Chrome")
            closing = page.url
            page.close()
            print(f"closed: {closing}")
            print(f"remaining: {len(_all_pages(browser))} tab(s)")

        elif cmd == "url":
            print(f"url: {page.url}")
            print(f"title: {page.title()}")

        elif cmd == "tabs":
            pages = _all_pages(browser)
            front = _frontmost_url()
            marked = False
            for n, p in enumerate(pages, 1):
                visible = not marked and p.url == front
                marked = marked or visible
                mark = "*" if visible else " "
                print(f"{mark} {n}. {p.title()[:60]!r}  {p.url}")
            print(f"\n{len(pages)} tab(s); * = frontmost"
                  if pages else "(no open tabs)")

        elif cmd == "shot":
            out = a.out
            if not out:
                os.makedirs(SHOTS_DIR, exist_ok=True)
                stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
                tag = session.current()["name"]
                out = os.path.join(SHOTS_DIR, f"web-{tag + '-' if tag else ''}"
                                              f"{stamp}.png")
            # Chrome paints only the visible tab; a capture of a background
            # one (two tabs on one URL, say) waits 30s for a frame that never
            # comes. A screenshot of a tab implies looking at it anyway.
            page.bring_to_front()
            page.screenshot(path=out, full_page=False)
            print(f"path: {os.path.abspath(out)}")
            print(f"url: {page.url}")

        else:  # pragma: no cover - argparse constrains this
            print(f"unknown web subcommand: {cmd}", file=sys.stderr)
