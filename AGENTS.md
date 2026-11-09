# Operator — instructions for agents using `op`

This repo provides `op`, a command-line tool that lets you see and drive a
graphical Linux desktop: take screenshots, click, type, drag, scroll, launch
apps, and drive Chrome by CSS selector. Use it whenever a task needs a GUI
rather than a terminal — "open a browser", "log into this site", "fill in
this form", "what's on the screen" — or any web task that needs a real,
signed-in browser instead of curl.

`op` is on PATH after `./install.sh` and works from any directory. Run
`op --help` for the full surface; `CLAUDE.md` in the repo explains the
internals (it is written for Claude Code but the content is tool-neutral).

## Rule one: work in your own session, not on the user's desktop

The main desktop has one pointer, one keyboard focus and one Chrome. The
user may be working there, and other agents may be running `op` at the same
time. So start every task by creating a private session, and pass `-s NAME`
on **every** call (shell variables do not persist between your tool calls):

```bash
op session new <task-name>          # your own headless display, pointer, Chrome
op -s <task-name> web goto ...      # every command, every time; -s goes BEFORE the subcommand
op session rm <task-name> --purge   # when you are done
```

`op session new` prints a `view:` URL: a web page where the user can watch
your display live and, if they open it, use their own mouse and keyboard in
it. **Give the user that URL** whenever they might want to look, and always
when you need them to do something themselves — a password, a 2FA code, a
captcha, a confirmation you must not answer. `op session url NAME` prints it
again.

Use the main desktop (no `-s`) only when the task is about the user's own
screen: "what's on my screen", a system dialog, their own browser window.

## The core loop

```bash
op -s NAME shot --grid      # writes a PNG and prints its path
op -s NAME click 480 1157   # grid labels are TRUE screen coordinates
op -s NAME shot             # confirm what changed
```

`op shot` prints a file path. **Open that image** with your image-viewing
tool to see the screen; the grid numbers are real coordinates regardless of
`--scale`. If you cannot view images in this environment, stay in the DOM
tier below: `op web text` reads a page as text and `op web where SELECTOR`
gives you coordinates without a screenshot.

## Commands

```
op shot [--scale 0.5] [--grid] [--window <id|title>] [--region x,y,w,h]
op windows | op activate <id|title> | op geom <id|title> | op idle
op click [X Y] [--button N] [--double] [--no-restore] | op move X Y
op drag X1 Y1 X2 Y2 | op scroll up|down|left|right [n] [--x X --y Y]
op key ctrl+l | op key Return | op key ctrl+shift+t [--repeat N]
op type "text" [--paste] [--enter]
op launch chrome | firefox | <command...>
op web [--tab N|substr] goto|click|fill|text|eval|where|shot|tabs|newtab|close|url
op session new NAME [--size WxH] [--fresh] [--xephyr] | op session list
op session url NAME | op session rm NAME... [--purge] | op session rm --all
op -s NAME <any command above>       # act inside session NAME
op viewer status | url | start | stop | expose | unexpose   # the web page of all sessions
```

## Two tiers — pick the right one

**DOM tier (`op web`) for anything in Chrome.** Clicking a CSS selector
cannot miss, waits on real load events, and survives window resizes. Chrome
starts automatically on first use, on its own profile.

```bash
op -s NAME web goto wikipedia.org
op -s NAME web fill "input[name=search]" "X Window System"
op -s NAME web click "button.search"
op -s NAME web text            # page text, for reading rather than screenshotting
```

**Pixel tier (`shot` / `click` / `key` / `type`) for everything else** —
system dialogs, native apps, browsers without a debug port.

**Bridging them:** `op web where <selector>` turns a CSS selector into screen
coordinates for a genuine physical click or drag.

## Sessions in detail

- A session is a headless X display (Xtigervnc) with its own window manager
  and its own Chrome (own debug port, own profile). Nothing appears on the
  user's desktop; they watch through the viewer page.
- A session's Chrome starts on a copy of the main operator Chrome profile,
  so the user's logins and extensions come along. `--fresh` gives an empty
  profile instead. Reusing a name whose profile still exists keeps that
  profile as it is.
- The user may take over your session from the viewer at any time. If the
  screen changed under you, screenshot again rather than assuming your last
  action did it.
- Pick a distinctive session name (task or ticket id): it is what the user
  sees on the viewer page. `op session list` shows what exists; leave other
  agents' sessions alone. Always `op session rm` when done.

## Rules

- **Screenshot right before acting, and again after.** Never reuse
  coordinates from an earlier screenshot.
- **Never type into password, PIN, passphrase or 2FA fields.** Stop,
  screenshot, and hand it to the user with your session's `view:` URL so
  they can type it themselves, then carry on once they say so. Cancelling
  such a dialog is fine; filling it is not. Chrome's own password manager is
  switched off at launch; never offer to save a password there.
- **Ask before anything irreversible**: purchases, sending messages,
  deleting, granting permissions, settings that affect other people.
  Otherwise finish the task without checking in at each step.
- **On the main desktop, check `op idle` first.** Under ~3 seconds means the
  user is actively using it; wait rather than fight them for the cursor.
- **Report what changed on screen**, not just which commands you ran.

## If something hangs

Screenshot before blaming the network. A modal dialog (keyring prompt,
permission prompt, update nag) blocks the whole app and makes `op web`
calls time out with no hint in the error. `op web` also refuses to start a
second Chrome when one is running but not answering — take a screenshot,
do not retry in a loop.

## Working on this repo

The Python package is `opr` (not `operator`, which is a stdlib module).
`op` is Linux/X11 only today; `WINDOWS.md` maps out a native Windows
backend for anyone porting it.
`./smoke.sh` exercises every subcommand inside a private session. When you
change the command surface, update this file and
`skills/operator/SKILL.md` (the Claude Code copy of these instructions)
together.
