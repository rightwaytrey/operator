# Running operator on Windows — notes for whoever ports it

Status (2026-11-08): `op` is Linux/X11 only. On Windows today it runs inside
WSL2 and drives a Linux desktop *inside* WSL, not the Windows screen. These
notes record what a native Windows backend would involve, module by module,
so an agent (Codex, Claude Code) can take it from here. Nothing below has
been built or tested yet.

## What works today: WSL2

See the Windows section of `README.md`. Codex and Claude Code both run
natively on Windows now, but `op` has to run in the WSL shell, and sessions
appear in your Windows browser at `http://127.0.0.1:6090/`. Known blocker:
WSLg owns `/tmp/.X11-unix`, so a second X server cannot create its socket
until the directory is made writable and only WSLg's `X0` is linked in
([microsoft/WSL#9303](https://github.com/microsoft/WSL/issues/9303),
[microsoft/wslg#927](https://github.com/microsoft/wslg/issues/927)).

Good enough for web tasks. Not what "control my Windows desktop" means.

## What a native port keeps unchanged

- The CLI surface (`__main__.py`), the agent instructions (`AGENTS.md`,
  `skills/operator/SKILL.md`) and the operating rules.
- The **DOM tier** (`web.py`): Playwright's `connect_over_cdp` is
  cross-platform. Chrome's `--remote-debugging-port` and `--user-data-dir`
  flags are identical. `_chrome_prefs()` (turning off Chrome's password
  manager by editing the profile's `Preferences` JSON) is identical.
- Screenshot post-processing: scaling and the labelled grid overlay in
  `screen.py` are pure Pillow.
- Shot naming, pruning, the `shots/` directory, `--out`.

## What needs a Windows implementation

| Module | X11 today | Windows equivalent |
|---|---|---|
| `xconn.py` | pick a DISPLAY, `Xlib.display.Display`, XScreenSaver idle | no display concept (one interactive desktop); idle via `GetLastInputInfo` |
| `inputs.py` | XTEST `fake_input` for motion/buttons/keys; keysym→keycode lookup with temporary keymap for unmapped chars; `xclip` for paste | `SendInput`: `MOUSEEVENTF_MOVE\|ABSOLUTE` (coords scaled to 0–65535 over the **virtual** screen), `LEFTDOWN/UP` etc., `MOUSEEVENTF_WHEEL`/`HWHEEL` (±120 per notch). Text via `KEYEVENTF_UNICODE` (no keymap gymnastics needed). Chords via virtual-key codes (`VkKeyScanW`, `MapVirtualKeyW`). Clipboard via `win32clipboard`/`pyperclip`. Pointer save/restore via `GetCursorPos`/`SetCursorPos` |
| `screen.py` capture | `XGetImage` in strips | `mss` (`sct.grab(monitor)`) or `PIL.ImageGrab.grab(bbox, all_screens=True)` |
| `windows.py` | `_NET_CLIENT_LIST`, `_NET_ACTIVE_WINDOW`, `translate_coords` | `EnumWindows` (already Z-order, top first) + `IsWindowVisible` + non-empty `GetWindowTextW`, drop cloaked UWP ghosts via `DwmGetWindowAttribute(DWMWA_CLOAKED)`; rect via `DWMWA_EXTENDED_FRAME_BOUNDS` (visible frame, not the shadow); `GetForegroundWindow`; activate with `ShowWindow(SW_RESTORE)` + `SetForegroundWindow` (see traps) |
| `launch.py` | `google-chrome` on PATH, `dbus-run-session`, `start_new_session=True` | Chrome path from the registry (`HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe`) or `%ProgramFiles%\Google\Chrome\Application\chrome.exe`, `OP_CHROME` override as now; `creationflags=DETACHED_PROCESS \| CREATE_NEW_PROCESS_GROUP`; drop `--password-store=basic` (Linux-only) and the dbus wrapper |
| process scans | `/proc/*/cmdline`, `os.kill(pid, 0)`, `/proc/PID/stat` | `psutil.process_iter(['pid', 'cmdline'])`, `psutil.pid_exists`, `psutil.Process.terminate()/kill()` |
| `session.py` lock | `fcntl.flock` | `msvcrt.locking` or the `filelock` package |
| `op` shim | bash, resolves its own symlink | `op.cmd`: `@uv run --project "%~dp0." --quiet python -m opr %*` with `PYTHONPATH=%~dp0src`; put its folder on the user PATH |
| `install.sh` | symlinks, `apt-get download`, `ldd` | `install.ps1`: install uv, `uv sync`, add the repo to the user PATH, copy (not symlink: needs Developer Mode) `AGENTS.md` to `%USERPROFILE%\.codex\AGENTS.md` and `SKILL.md` to `%USERPROFILE%\.claude\skills\operator\`; no vendored X servers |

Suggested shape: keep the module names and the function signatures the CLI
calls (`inputs.click`, `screen.capture`, `windows.listing`, …) and branch
once at import: `if sys.platform == "win32": from ._win import *`. In
`pyproject.toml`, use environment markers so one lockfile serves both:

```toml
dependencies = [
    "pillow>=10.0", "playwright>=1.40", "psutil>=5.9",
    "python-xlib>=0.33; sys_platform == 'linux'",
    "websockify>=0.13.0; sys_platform == 'linux'",
    "pywin32>=306; sys_platform == 'win32'",
    "mss>=9; sys_platform == 'win32'",
    "pyperclip>=1.8; sys_platform == 'win32'",
]
```

## What has no Windows equivalent: sessions and the viewer

A Linux session is a private X server (`Xtigervnc`) with its own pointer,
focus and screen, watched through noVNC. Windows has one interactive
desktop per logged-in user and no user-space headless display, so there is
nothing to give an agent its own cursor, and nothing for the viewer to show.

The honest reduced model:

- **`op session new NAME` allocates a Chrome only**: a CDP port from 9300
  and a profile under `~/.config/operator-chrome-sessions/NAME` (or
  `%LOCALAPPDATA%\op\sessions\NAME`), cloned from the main operator profile
  as now. `op -s NAME web …` is then fully isolated per agent — that covers
  most web tasks. `rm` kills that profile's Chrome via psutil; `--purge`
  deletes the profile.
- **The pixel tier is shared** with the user and with other agents. `op idle`
  (via `GetLastInputInfo`) is the guard, exactly as on the Linux main
  desktop. `op -s NAME shot/click` act on the one real screen.
- **No viewer** on Windows: the user is sitting at the screen. `op viewer`
  should say so and exit 0. (If remote watching is wanted later, a
  whole-desktop VNC server plus the existing noVNC pages would do it, but
  that is a different feature.)
- `--xephyr` has no meaning; reject it with a message.

Alternatives considered and rejected: a Windows Sandbox or Hyper-V VM per
agent with RDP (heavy, needs Pro/Enterprise, and the agent is then driving a
VM again); a second RDP user session on the same machine (licensing and
RDP-wrapper hacks); Windows virtual desktops (they share one pointer and
one focus, so they isolate nothing).

## Traps the Linux code never had to think about

- **DPI scaling** is the first thing that will go wrong. At 125%/150% the
  screenshot, `GetWindowRect` and `SendInput` disagree unless the process
  declares itself DPI-aware before any window call:
  `ctypes.windll.user32.SetProcessDpiAwarenessContext(-4)` (per-monitor v2)
  or `SetProcessDPIAware()`. Do it at import in the Windows backend.
- **Multiple monitors**: the virtual screen origin can be negative
  (`SM_XVIRTUALSCREEN`/`SM_YVIRTUALSCREEN`); absolute `SendInput`
  coordinates are normalised over the virtual screen with
  `MOUSEEVENTF_VIRTUALDESK`. `op shot` with no region should capture the
  virtual screen and report its origin so grid labels stay true.
- **Foreground lock**: `SetForegroundWindow` fails from a background process
  unless the caller has recent input or the target is minimised-then-
  restored; common workarounds are sending a no-op key (`VK_MENU` down/up)
  first or `AttachThreadInput` to the foreground thread. Report failure
  rather than pretending.
- **UIPI**: an elevated (UAC) window ignores `SendInput` from a non-elevated
  process. Screenshot it fine, cannot click it. Say so in the error.
- **`KEYEVENTF_UNICODE`** types text but does not trigger shortcuts, so
  chords (`ctrl+l`) must use virtual-key codes, and `Return`/`Tab`/`Escape`
  in `type` should go through `VK_RETURN` etc., not Unicode.
- **Console windows and the agent's own terminal** show up in `EnumWindows`;
  `op windows` on Linux never listed the agent's shell because it was not
  on the display. Consider hiding windows with class `ConsoleWindowClass`
  or `CASCADIA_HOSTING_WINDOW_CLASS` by default.
- Paths: `os.path.expanduser("~")` works on Windows (`%USERPROFILE%`), so
  the existing `~/.config/op` / `~/.cache/op` paths run, but
  `%LOCALAPPDATA%\op` is the native convention.
- `shots/` lives in the repo; on Windows the agent runtime (Codex's sandbox
  in particular) may need that path allowed for reading images.

## Testing

Port `smoke.sh`'s structure to `smoke.ps1`: on the shared desktop open
Chrome on a scratch profile, `web goto example.com`, `web where p`, `click`
at those coordinates, `key Escape`, `type`, `shot --grid`, then
`session rm --purge`. Check on a 150%-scaled display and on two monitors —
the Linux smoke test never had to.

## Where the agent should start

1. `inputs.py` + `screen.py` Windows backends with the DPI call. Verify
   with `op shot --grid` and `op click` on a visible target.
2. `windows.py` backend. Verify `op windows`, `op activate`, `op geom`.
3. `launch.py` Chrome discovery; `web.py` already works once Chrome is up.
4. Reduced `session.py` (ports + profiles, psutil teardown).
5. `op.cmd`, `install.ps1`, README section, and make `op viewer` /
   `--xephyr` explain themselves.
