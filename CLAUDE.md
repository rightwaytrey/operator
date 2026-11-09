# Operator

A CLI (`./op`) that lets a coding agent see and drive a graphical desktop: by
default a private headless display per agent that the user watches from a
web page, or the desktop the user is logged into. Screenshot → click → type →
screenshot, plus a DOM-level tier for Chrome.

This file is the internals guide for agents working *on* the repo. The
human-facing guide is `README.md`. What an agent *using* `op` needs is in
`skills/operator/SKILL.md` (Claude Code) and `AGENTS.md` (Codex) — the two
say the same thing in each tool's format; **when you change the command
surface, update both.** Anything specific to one machine (which display is
the main desktop, where its viewer is published, the hosts it has been
replicated to) goes in `CLAUDE.local.md`, which is gitignored and loaded by
Claude Code beside this file.

## The environment

`./op` targets one X display, chosen in `xconn.py`: `--display`, else
`$OP_DISPLAY`, else `~/.config/op/display`, else `$DISPLAY` if it names a
local server that exists, else `:0`. The config file is for machines whose
main desktop is not `:0` (an xrdp session, say); the `$DISPLAY` check exists
because agent shells often inherit a `DISPLAY` with nothing behind it.
`XAUTHORITY` defaults to `~/.Xauthority`. Input is injected with the X TEST
extension through `python-xlib` — no root, no `xdotool`. Deps live in a uv
venv (`uv sync`); nothing is installed system-wide.

**The user may be watching live.** The main desktop is their real desktop,
not a sandbox, and their own signed-in browser may be running on it.

## Commands

```
./op shot [--scale 0.5] [--grid] [--window <id|title>] [--region x,y,w,h]
./op windows | ./op activate <id|title> | ./op geom <id|title> | ./op idle
./op move X Y | ./op click [X Y] [--button N] [--double] [--no-restore]
./op drag X1 Y1 X2 Y2 | ./op scroll up|down|left|right [n] [--x X --y Y]
./op key ctrl+l | ./op key Return | ./op key ctrl+shift+t [--repeat N]
./op type "text" [--paste] [--enter] [--delay S]
./op launch chrome | firefox | <command...>
./op web [--tab N|substr] goto|click|fill|text|eval|where|shot|tabs|newtab|close|url
./op session new NAME [--size WxH] [--fresh] [--xephyr] | list | url NAME | rm NAME... [--purge]
./op -s NAME <command>      # run any command inside an isolated session
./op viewer status | start | stop | url | expose [--https-port N] [--url URL] | unexpose | serve
```

`shot` writes a PNG and prints its path — then **read that path** to actually
see it. `--grid` overlays labelled gridlines whose numbers are TRUE screen
coordinates regardless of `--scale`, so they can be passed straight to
`op click`.

## Operating rules

- **Screenshot immediately before acting, and again after.** Never reuse
  coordinates from an earlier screenshot: remote-desktop gateways (Guacamole,
  xrdp) resize the screen to match the user's browser window, so geometry
  genuinely changes between calls.
- **Never type into password, PIN, passphrase or 2FA fields.** Stop,
  screenshot, and hand it to the user. This has come up for real: a GNOME
  keyring prompt and a password-manager master-password prompt. Dismissing
  such a dialog (Cancel) is fine; filling it is not.
- **Pause and ask before anything irreversible**: purchases, sending messages,
  deleting, granting permissions, changing settings that affect other people.
  Otherwise, free rein to finish the task the user asked for.
- **Check `./op idle` before acting on the main desktop.** Under ~3s means the
  user is actively using it right now — you share one cursor, so wait rather
  than fight them for it, unless they said to go ahead.
- **Report what changed on screen**, not just which commands ran.

## Which tier to use

Prefer the **DOM tier** (`op web`) for anything in Chrome: `web click` on a CSS
selector cannot miss, waits for real load events, and survives window resizes.
Use `op web where <selector>` to convert a selector into screen coordinates
when something genuinely needs a physical click or drag.

Use the **pixel tier** (`op shot` / `click` / `key` / `type`) for everything
else: GNOME dialogs, native apps, and browsers that cannot take a debug port
(a snap-packaged Firefox, for one) — those are screenshot-driven only.

## Isolated sessions (`src/opr/session.py`)

Concurrent agents on the main display share one pointer, focus and Chrome
with each other and with the user, so they trample everything. `op session
new NAME` starts a private X server for one agent on the first free display
from `:20`, with its own MIT cookie, an `xfwm4` window manager (so EWMH
`windows`/`activate` work), and a Chrome debug port from `9300` plus profile
under `~/.config/operator-chrome-sessions/NAME`. `op -s NAME` (or
`$OP_SESSION`) swaps display, XAUTHORITY, CDP port and profile for one
invocation; nothing else in the code knows sessions exist — `launch.py` and
`web.py` just ask `session.cdp_port()` / `chrome_profile()`.

The profile starts as a copy of `~/.config/operator-chrome` (the main
operator Chrome: its logins and extensions; caches and locks skipped).
`--fresh` skips the copy, as do `smoke.sh` and the `replicate.sh` probe. A
name whose profile already exists (an `rm` without `--purge`) is reused
untouched, never overwritten by a clone.

Two kinds of X server:

- **`xvnc` (default)** — `Xtigervnc`, a headless X server that is also a VNC
  server on `127.0.0.1:5900+N` (loopback only, `-SecurityTypes None`,
  `-AlwaysShared`, and `-AcceptSetDesktopSize=0` so a watcher cannot resize
  the screen under the agent). Nothing appears on the user's desktop; they
  watch it through the viewer below.
- **`xephyr` (`--xephyr`)** — a window titled `op session: NAME` on the main
  desktop, the pre-viewer behaviour. `--hidden` is still accepted and ignored.

- Registry: `~/.cache/op/sessions/NAME.json` + `NAME.xauth`; allocation is
  under an `flock` so racing `new` calls get distinct displays and ports. A
  display is free only if its X socket, lock file **and** VNC port are.
- `rm` kills that profile's Chrome, the WM and the server; it refuses to
  touch the main profile whatever the JSON says. `--purge` deletes the profile.
- Screenshots taken in a session are named `shot-NAME-…` / `web-NAME-…`.

## The viewer (`src/opr/viewer.py`)

One websockify process on `127.0.0.1:6090` (`OP_VIEWER_PORT` to change) that
serves noVNC and an index page:

```
/                       one card per session: live view-only tile, Open / Watch links
/tile.html?token=NAME   viewer_tile.html — bare noVNC client, scaled, view-only by default
/open.html?token=NAME   viewer_open.html — noVNC's full client (vnc.html) in an iframe
                        under a bar with "‹ sessions"; what "Open" links to
/zoom.js                viewer_zoom.js — pinch-to-zoom and pan, used by both pages
/live.js                viewer_live.js — dead-connection probe and stop-when-session-ends
/api/sessions           JSON: name, display, size, alive, chrome, age, links
/websockify?token=NAME  WebSocket → that session's VNC port, resolved per
                        connection from the registry (SessionTokens plugin)
```

- The index is built to work as a **home-screen web app on a phone**, which
  has no browser chrome: every page it opens needs its own way back
  (`‹ sessions`), and the index keeps links in-window when it detects
  standalone mode (`navigator.standalone` / `display-mode: standalone`).
- **Pinch zooms the picture on the phone**, not the remote app. noVNC has
  no local zoom (it maps a pinch to ctrl+wheel *in the session*), so
  `viewer_zoom.js` does it on top: zooming is resizing and translating the
  element the RFB draws into, then setting `rfb.scaleViewport = true` so
  noVNC refits its canvas and its own pointer maths stays right. During a
  two-finger zoom/pan the RFB is put in `viewOnly` so what noVNC's gesture
  handler makes of the same touches is dropped; one finger still clicks,
  drags and long-presses, a two-finger tap still right-clicks, and at 1x a
  two-finger drag still scrolls the session. Watch (view-only) also pans
  with one finger, toggles zoom on double-tap, and zooms on ctrl+wheel.
  `open.html` reaches noVNC's private `UI.rfb` by appending a module script
  *inside* the same-origin iframe that imports `./app/ui.js` (same module
  instance) and puts it on `window.UI`.
- `op session new` starts it if needed and prints the session's `view:` URL;
  `op viewer start` is idempotent. State: `~/.cache/op/viewer.json`, log:
  `~/.cache/op/viewer.log`. websockify forks a child per connection and the
  children inherit the listening socket, so `stop`/`start` kill every
  `opr.viewer serve` process they can find, not just the recorded pid.
- **Two doors, both HTTPS.** `op viewer expose` runs `tailscale serve --bg
  --https=9700 http://127.0.0.1:6090` once (tailnet-only; the mapping
  persists across reboots; `public_url()` reads it back from `tailscale serve
  status --json`, so the printed URL is what is actually mapped; serve proxies
  WebSockets fine). For devices **not on the tailnet**, nginx can serve the
  same viewer under a path of an existing site that already has TLS and
  whole-site authentication: `deploy/nginx-operator.conf` is a `location
  /operator/ { proxy_pass http://127.0.0.1:6090/; }` block,
  `sudo deploy/install-nginx.sh SITE` installs it, and `op viewer expose --url
  https://host/operator/` records the URL in `~/.config/op/viewer-public-url`
  so `op session new`, `op viewer url` and `op session list` print it first.
  A prefix works because every page uses relative URLs and noVNC resolves
  `path` against the page (`new URL(path, location)`), so `open.html` passes
  a plain `websockify?token=…` — do not prepend the prefix yourself (tried:
  double prefix). Verified with an unprivileged scratch nginx mirroring a
  cookie gate: tiles and the Open client connect, and without the cookie both
  pages and the upgrade get 401.
- The viewer itself has no auth: the trust boundary is "this machine's
  loopback, the tailnet, and whoever gets past the nginx site's gate". Anyone
  there can watch and **drive** a session from the Open link — that is the
  feature (the user types their own passwords there). Never `funnel` it and
  never bind it beyond loopback; a public path exists only behind an
  authenticating proxy.
- Chosen over the alternatives after research: xpra's HTML5 client (an
  extra repo, old in jammy, more moving parts), KasmVNC (its own web client,
  but a third-party .deb and no plain-VNC compatibility), and registering
  each session as a Guacamole VNC connection (needs Guacamole admin
  credentials on disk, or MySQL writes; the sessions *are* plain VNC on
  `127.0.0.1:59NN`, so a Guacamole user can still add one by hand).
  Xtigervnc + noVNC is what Anthropic's own computer-use reference container
  does, in one process per display instead of Xvfb + x11vnc polling.

### Nothing is installed system-wide

An agent shell usually cannot answer a `sudo` password prompt, so
`install.sh` fetches both pieces into `vendor/` (gitignored) without root:
`apt-get download tigervnc-standalone-server` + `dpkg-deb -x` (every library
it links is already on a GNOME desktop; `install.sh` runs `ldd` and warns
otherwise), and a pinned noVNC release tarball from GitHub (the `novnc` apt
package is 1.0.0 from 2018). `session.xvnc_binary()` prefers a system
`Xtigervnc` if one is installed. websockify is an ordinary uv dependency.
`install.sh` also lists the system tools the harness execs (`xauth`,
`mcookie`, `xfwm4`, `xclip`, `dbus-run-session`, a Chrome) with the apt
packages for any that are missing.

## Replicating to another machine (`replicate.sh`)

`./replicate.sh HOST [--settings] [--claude]` copies this working tree
(uncommitted changes included; not `.venv`, `shots/`, `vendor/tigervnc` or
Chrome profiles) to `HOST:~/projects/operator` over ssh, then runs
`./replicate.sh --bootstrap` there: installs `uv` for that user if missing,
runs `./install.sh`, inventories the desktop (X sockets, login session type,
Chrome/Firefox, xfwm4, Xtigervnc, tailscale, claude), optionally adds the
three Claude Code permission entries (`--settings`, backed up first) and
installs Claude Code (`--claude`), then proves a private session works by
creating, screenshotting and removing one. Re-run to push a newer tree.
Nothing needs root on either side.

- On a target with no remote-desktop display, the sessions tier is the whole
  product; the main-desktop commands need `--display` (or
  `~/.config/op/display`) pointing at its real X server, and will not work at
  all if its desktop runs on Wayland.
- To see a remote host's viewer without Tailscale, forward it here (`ssh -N
  -L 127.0.0.1:6091:127.0.0.1:6090 HOST`) and add a second `location` block
  for `127.0.0.1:6091` in `deploy/nginx-operator.local.conf` (gitignored,
  appended by the installer); on that host record the resulting URL with
  `op viewer expose --url`.
- The bootstrap cannot be run from an agent shell on the machine that
  already hosts the harness: it re-links the agent's own skill/`op` and may
  edit the agent's settings, which permission classifiers read as
  self-modification. Test its pieces separately (the settings merge against
  scratch files, the probe session for real).

## Gotchas learned the hard way

- The Python package is `opr`, **not** `operator` — `operator` is a stdlib
  module and would be shadowed.
- Chrome runs on a dedicated profile (`~/.config/operator-chrome`) so the
  user's everyday Chrome and its logins are never touched. The binary is
  `$OP_CHROME`, else the first of `google-chrome`, `google-chrome-stable`,
  `chromium`, `chromium-browser` on PATH (`launch.chrome_binary()`).
- Passwords belong in the user's **password manager**, never Chrome.
  `launch._chrome_prefs()` turns off Chrome's password manager
  (`credentials_enable_service` etc.) in a profile's Preferences before
  starting it, and only while that profile is not running, because Chrome
  rewrites Preferences on exit. Its save bubble used to get dismissed with
  "Never", leaving blacklist rows that every cloned session profile then
  inherited.
- On first launch Chrome raises a **GNOME keyring modal** that blocks CDP
  entirely — navigation just times out. `--password-store=basic` avoids it;
  screenshot before assuming the network is at fault; dismiss with Cancel.
- Do **not** trust `document.visibilityState` to find the frontmost tab.
  Playwright enables focus emulation on every attached page, so they all claim
  to be visible. `web.py` reads Chrome's own `/json/list`, which is ordered
  most-recently-active first.
- `op click` returns the pointer to where the user left it by default. Pass
  `--no-restore` for hover-dependent UI (submenus that close when the pointer
  leaves).
- Playwright does not attach to `chrome://` targets, so Chrome's own New Tab
  page never shows up in `op web tabs`. Chrome therefore usually survives
  closing the last *listed* tab.
- example.com no longer has an `<h1>` (it is all `<p>` now); `smoke.sh`
  targets `p`.
- `op web goto` prefixes `https://` to anything without `://`, so `data:`
  URLs fail; inject test markup with `op web eval` instead.
- Full-screen `XGetImage` is read back in horizontal strips because a single X
  request is capped near 16 MB.
- `port_free()` must set `SO_REUSEADDR`: the servers do, so a port with
  connections lingering in TIME_WAIT is free for them but looked busy to a
  plain `bind()` probe, which made `op viewer start` refuse right after `stop`.
- Debian names TigerVNC's server `Xtigervnc`, not `Xvnc`; `tigervnc-common`
  is not needed (no VNC auth is used).
- Agent shells can carry a `DISPLAY` with no X server behind it (`:99` was
  the case that bit). `xconn.default_display()` ignores a `$DISPLAY` whose
  local socket does not exist, otherwise every main-desktop command in such a
  shell fails with "Can't connect to display".
- A viewer connection can die **without a close event** (laptop sleep,
  network change, iOS freezing a backgrounded page), and a VNC server sends
  nothing while the screen is still, so a dead stream and a quiet one look
  alike: the page keeps a stale picture that reads as a frozen session. A
  handshake that never completes is worse: it holds the browser's single
  "connecting" WebSocket slot for the host, so every other viewer tab sits
  on "Connecting…". `viewer_live.js` (`/live.js`, used by tile and open)
  sends a 1x1 non-incremental FramebufferUpdateRequest every 15s and on
  return to the page; no reply in 5s, or 10s without a connection, means
  reconnect, unless `/api/sessions` says the session is gone, in which case
  the page says so and stops retrying (also from a background tab).
  Diagnose with `ss -tnp '( dport = :59NN )'`: no viewer connection to the
  session means the browser side is stuck, not the session.
- On iOS the on-screen keyboard does not resize the page: only the
  *visual* viewport shrinks, and Safari scrolls the page to show noVNC's
  hidden input even under `overflow:hidden`, so the Open page's bar went off
  the top and the picture sat under the keyboard with no way to scroll back.
  `viewer_open.html` pins its body (`position:fixed`) to
  `visualViewport.offsetTop`/`height` and undoes any scroll in either
  document. Tested only in Playwright WebKit with a stand-in
  `visualViewport` (no keyboard there): confirm on a phone after changing it.
- A file `<input>` in a session's Chrome used to open GNOME's portal
  "Open Files" dialog **on the main desktop**, invisible from the session,
  and Chrome then ignored every click in its window (CDP clicks still
  worked, which hid it). `launch._spawn` now runs apps started inside a
  session under `dbus-run-session` with no `DBUS_SESSION_BUS_ADDRESS`, so
  Chrome falls back to its own GTK picker inside the session. To upload
  without any dialog, use Playwright's `set_input_files` over the CDP port.
- `web shot` brings the tab to the front first: Chrome paints only the
  visible tab, and `_page()` matches the frontmost tab by URL, so with two
  tabs on one URL it could pick the hidden one and the capture hung for 30s.

## How agent sessions reach this

Any agent session on the machine can drive the desktop without knowing this
repo exists:

1. `~/.local/bin/op` — a symlink to `./op`, which resolves its own symlink and
   so works from any directory.
2. **Claude Code:** `~/.claude/skills/operator/SKILL.md` is a **symlink** to
   `skills/operator/SKILL.md` here, so `operator` appears in every session's
   skill list with usage instructions inline. Edit the repo copy; there is
   only one file, so it cannot drift from the command surface. Plus, in
   `~/.claude/settings.json`: `Bash(op:*)` in `permissions.allow` (no
   prompts), `shots/` in `additionalDirectories` and a `Read(...)` allow rule.
   That second half matters: without it a session can *capture* a screenshot
   but not read the PNG back, since it lives outside that session's workspace.
3. **Codex:** reads `AGENTS.md` at the repo root when working in it;
   `install.sh` links it to `~/.codex/AGENTS.md` (if nothing is there yet) so
   it applies from any directory.

`./install.sh` creates the symlinks idempotently, syncs the uv environment,
checks the system tools, and reports which Claude settings entries are
missing (it does not edit user settings itself). `smoke.sh` re-checks the
wiring on every run.

Those symlinks hold absolute paths, so moving or renaming this directory would
strand them and the skill would quietly vanish from other sessions. The `op`
shim therefore repairs a *dangling* link on the next run and says so on stderr.
A link that is absent entirely is left alone — that reads as deliberate removal,
and `./install.sh` is how you put it back.

## Verifying a change

`./smoke.sh` exercises every subcommand inside a private session (its own
Xtigervnc, Chrome and viewer tile, including a raw WebSocket→RFB handshake
through the viewer) plus read-only checks of the main desktop, and prints the
screenshots it produced. `./smoke.sh --main` additionally opens a scratch tab
in the shared Chrome on the main desktop and clicks in it — only run that
when the user is not working there.

To see the viewer as the user does: `op session new NAME`, then from another
session `op -s OTHER web goto $(op viewer url)` and `op -s OTHER shot`.
