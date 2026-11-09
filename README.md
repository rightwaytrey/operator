# Operator

`op` lets a coding agent (Claude Code, Codex, or anything that can run a
shell command) **see and drive a graphical Linux desktop**: take a
screenshot, click, type, drag, scroll, launch apps, and drive Chrome by CSS
selector over the DevTools protocol. Each agent gets its own **private
headless display** with its own Chrome, and you watch it live — or take over
the mouse and keyboard — from a web page on your laptop or phone.

```bash
op session new my-task              # a private display + window manager; prints a view: URL
op -s my-task web goto example.com  # Chrome starts on its own profile and debug port
op -s my-task web click "a"         # DOM tier: CSS selectors, real load waits
op -s my-task shot --grid           # pixel tier: a PNG with labelled screen coordinates
op -s my-task click 640 400
op -s my-task type "hello" --enter
op session rm my-task --purge
```

No root, no `xdotool`: input goes through the X TEST extension via
`python-xlib`, screenshots through `XGetImage`, Chrome through Playwright's
`connect_over_cdp`. Sessions are `Xtigervnc` servers on loopback; the viewer
is one `websockify` process serving noVNC.

## What you need

- **Linux with X11.** Developed and used on Ubuntu 22.04 and 24.04 (GNOME).
  Wayland-only desktops work for *sessions* (they are their own X servers)
  but not for driving the main desktop.
- **Python 3.10+** and [`uv`](https://docs.astral.sh/uv/).
- System tools: `xauth`, `mcookie` (util-linux), `xfwm4`, `xclip`, `dbus`,
  `tigervnc-standalone-server`, and **Google Chrome** or Chromium.
  Optional: `xserver-xephyr` (sessions as a window on your desktop),
  `fonts-dejavu-core` (nicer grid labels), `tailscale` (publish the viewer
  to your tailnet), `nginx` (publish it behind an existing site).

```bash
sudo apt install xauth xfwm4 xclip dbus tigervnc-standalone-server xserver-xephyr fonts-dejavu-core
# Chrome: https://www.google.com/chrome/  (the .deb), or  sudo apt install chromium
```

If you cannot `sudo`, `install.sh` unpacks `tigervnc-standalone-server` and a
pinned noVNC release under `vendor/` without root.

## Install

```bash
git clone https://github.com/rightwaytrey/operator.git ~/projects/operator
cd ~/projects/operator
./install.sh        # op on PATH, uv env, vendored bits, checks, agent wiring
./smoke.sh          # exercises every subcommand in a throwaway session
```

`install.sh` is idempotent and prints what is missing. It links `op` into
`~/.local/bin`, links the Claude Code skill and/or the Codex instructions if
those tools are present, and tells you which Claude Code permission entries
to add (it never edits your settings itself).

**Which display is "the main desktop"?** By default `op` uses `$DISPLAY`
when it points at a live X server, else `:0`. If your desktop lives
elsewhere (an xrdp session on `:10`, say):

```bash
mkdir -p ~/.config/op && echo :10 > ~/.config/op/display   # or export OP_DISPLAY=:10
```

### Windows

`op` needs a Linux X11 environment, so on Windows run it inside **WSL2**
(Windows Subsystem for Linux). The headless sessions are the whole product
there: the agent works in its own display and you watch it in your Windows
browser.

1. `wsl --install -d Ubuntu-24.04` from an administrator PowerShell, reboot,
   create your Linux user.
2. Inside Ubuntu: `sudo apt update`, then the `apt install` line above, then
   Chrome (`wget https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb && sudo apt install ./google-chrome-stable_current_amd64.deb`)
   and uv (`curl -LsSf https://astral.sh/uv/install.sh | sh`).
3. Clone the repo **inside the Linux filesystem** (`~/projects/operator`, not
   `/mnt/c/...`): the X sockets, `flock` and `/proc` scanning this tool uses
   need a real Linux filesystem, and it is much faster.
4. `./install.sh`, then `./smoke.sh`.
5. The viewer runs on `http://127.0.0.1:6090/`; WSL2 forwards localhost, so
   open that URL in your Windows browser. `op session new NAME` prints the
   link for each session.

Known WSL gotcha: under WSLg, `/tmp/.X11-unix` is owned by the Windows side
(a read-only mount or a symlink into `/mnt/wslg`), so a second X server
such as `Xtigervnc` cannot create its socket there and `op session new`
fails with "Cannot establish any listening sockets"
([microsoft/WSL#9303](https://github.com/microsoft/WSL/issues/9303),
[microsoft/wslg#927](https://github.com/microsoft/wslg/issues/927)). The
usual fix is to make the directory writable and link only WSLg's own `X0`
socket into it, once per boot (or from `/etc/wsl.conf` under
`[boot] command=`):

```bash
sudo umount /tmp/.X11-unix 2>/dev/null; sudo rm -rf /tmp/.X11-unix \
  && sudo mkdir -p /tmp/.X11-unix && sudo chmod 1777 /tmp/.X11-unix \
  && sudo ln -sf /mnt/wslg/.X11-unix/X0 /tmp/.X11-unix/X0
```

Run your agent (Codex, Claude Code) **inside the WSL shell**, so `op` is on
its PATH. The WSL path has been written up from known WSLg behaviour and
not yet run end to end by the author; please open an issue with what you hit.

WSL drives a Linux desktop inside the VM, not your Windows screen. What a
**native Windows backend** would take, module by module, is written up in
`WINDOWS.md`.

## Using it from an agent

- **Claude Code** — `install.sh` symlinks `skills/operator/SKILL.md` into
  `~/.claude/skills/operator/`, so an `operator` skill with full usage
  instructions appears in every session. Add to `~/.claude/settings.json`
  what `install.sh` reports: `Bash(op:*)` in `permissions.allow`, the repo's
  `shots/` directory in `permissions.additionalDirectories`, and a
  `Read(.../shots/**)` rule — without the last two a session can take a
  screenshot but not look at it.
- **Codex** — reads `AGENTS.md` at the repo root when working inside it;
  `install.sh` links it to `~/.codex/AGENTS.md` (unless you already have
  one, in which case it tells you what line to add) so it applies from any
  directory. Codex's image support decides whether it can look at
  screenshots; the DOM tier (`op web text`, `op web where`) works without
  them.
- **Anything else** — `op --help`. The two files above are the same
  instructions in two formats; either is a good system prompt.

The instructions tell the agent to **always work in its own session** and
**never type into a password or 2FA field** — it hands you the viewer link
and waits for you to do that part yourself.

## Sessions and the viewer

`op session new NAME` starts an `Xtigervnc` server on the first free display
from `:20` (VNC on `127.0.0.1:5900+N`, loopback only), an `xfwm4` window
manager, and reserves a Chrome debug port from `9300` with a profile under
`~/.config/operator-chrome-sessions/NAME`. The profile is cloned from the
main operator Chrome profile (`~/.config/operator-chrome`) so logins and
extensions carry over; `--fresh` starts empty.

The **viewer** (`op viewer status|url|start|stop`) is one websockify process
on `127.0.0.1:6090` (`OP_VIEWER_PORT`) that serves an index of every session
with a live tile, and for each an **Open** page (full interactive noVNC
client, pinch-zoom on phones) and a **Watch** page (view-only). It starts by
itself when a session is created.

Three ways to reach it:

| From | How |
|---|---|
| this machine | `http://127.0.0.1:6090/` |
| your tailnet | `op viewer expose` runs `tailscale serve --https=9700` once; `op viewer url` prints the HTTPS URL |
| anywhere | serve it under a path of an nginx site you already protect: `sudo deploy/install-nginx.sh /etc/nginx/sites-available/SITE`, then `op viewer expose --url https://host/operator/` so `op` prints that link |

**The viewer has no authentication of its own.** Anyone who can reach it can
watch *and drive* every session. It binds to loopback only; publish it only
through Tailscale or behind a site that already authenticates every request,
and never through `tailscale funnel`.

## Layout

```
op                      shim: runs the CLI in the uv environment; works via symlink
src/opr/                the package (not "operator": that is a stdlib module)
  __main__.py           argparse surface
  xconn.py              which display, X connection, idle time
  inputs.py  screen.py  XTEST input; XGetImage screenshots + grid overlay
  windows.py launch.py  EWMH window list/activate; starting apps (Chrome flags, prefs)
  web.py                DOM tier over CDP via Playwright
  session.py            private displays: registry, allocation, teardown
  viewer.py + html/js   websockify daemon, noVNC pages, token plugin
skills/operator/        Claude Code skill (symlinked into ~/.claude/skills)
AGENTS.md               the same guidance for Codex
install.sh  smoke.sh    wiring + checks; end-to-end exercise
replicate.sh            copy the tree to another Linux host over ssh and bootstrap it
deploy/                 nginx snippet + installer for publishing the viewer
vendor/  shots/         gitignored: unpacked Xtigervnc/noVNC; screenshots (last 200 kept)
CLAUDE.md               internals for agents working on the repo
CLAUDE.local.md         your machine's specifics (gitignored; create it if you want one)
```

## License

MIT — see `LICENSE`.
