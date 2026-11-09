---
name: operator
description: See and control the user's graphical desktop (a Linux/X11 session — their own logged-in desktop, or a private headless display per agent that they can watch live in a browser). Take screenshots, click, type, drag, scroll, launch apps, and drive Chrome by CSS selector. Use whenever a task needs a GUI rather than a terminal: "open a browser on my desktop", "screenshot my screen", "click that button", "log into <site> for me", "what's on my screen right now", "fill in this form", or any web task that needs a real signed-in browser instead of curl. Start your own isolated instance first (`op session new NAME`, then `op -s NAME ...` on every call) — the shared desktop is only for the user's own windows.
---

# Operator — driving the user's desktop

`op` is on PATH and works from any directory. The repo is where `op`
resolves to (`readlink -f "$(command -v op)"`); read its `CLAUDE.md` for
internals. Run `op --help` for the full surface.

## Rule one: use your own session, never the user's desktop

**Other agents may use `op` at the same time as you, and the user is working
on the main desktop.** It has one pointer, one keyboard focus and one
Chrome, so anything you do there lands in their work and in other agents'.
Start every task by creating your own session, and pass `-s NAME` on every
call:

```bash
op session new <task-name>          # your own headless display, pointer, Chrome
op -s <task-name> web goto ...      # every command, every time
op session rm <task-name> --purge   # when you are done
```

`op session new` prints a **`view:` URL**. That is a web page (a public
gated address if one is recorded, else the tailnet one, else local;
`op viewer status` lists them) where the user can watch your display live
and, if they open it, use their own mouse and keyboard in it. **Tell the
user that URL** whenever they might want to look, and always when you need
them to do something themselves — a password, a 2FA code, a captcha, an
"are you sure" you must not answer. `op session url NAME` prints it again;
the index of every session is `op viewer url`.

Use the main desktop (no `-s`) **only** when the task is about the user's own
screen: "what's on my screen", a system dialog, their own browser, a window
they opened. Need a site login? A new session starts on a copy of the
operator Chrome profile, so whatever logins and password-manager extension
the user keeps there come along.

## The core loop

```bash
op shot --grid          # prints a PNG path; READ that path to actually see it
op click 480 1157       # grid labels are TRUE screen coords, pass them straight in
op shot                 # confirm what changed
```

`op shot` writes a file and prints its path — you must then use the **Read tool
on that path** to see the image. `--grid` overlays labelled coordinate lines;
the numbers are real screen coordinates regardless of `--scale`.

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
op -s NAME <any command above>       # act inside session NAME, not on the main desktop
op viewer status | url | start | stop | expose | unexpose   # the web page of all sessions
```

## Sessions in detail

Without one, agents trample each other: your `goto` lands in another agent's
tab, your typing lands in their window.

```bash
op session new transit-21-4         # a headless X server + window manager; prints view: URL
op -s transit-21-4 web goto example.com     # its own Chrome, own port, own profile
op -s transit-21-4 shot --grid      # coordinates are the SESSION's, not the main desktop's
op -s transit-21-4 click 640 400
op session rm transit-21-4          # always clean up when done
```

- A session is a **headless display (Xtigervnc)**: nothing appears on the
  user's desktop. They watch it through the viewer — a web page listing every
  session with a live tile, plus **Open** (interactive) and **Watch**
  (view-only) links. Its URL is what `op session new` prints as `view:`.
  `--xephyr` instead puts the session in a window on the user's own desktop;
  only use it if they ask for that.
- The user may **take over** your session from the viewer at any time (to log
  in, to fix something). If the screen changes under you, screenshot again
  rather than assuming your last action did it.
- `-s NAME` goes on **every** call (`$OP_SESSION` also works, but shell
  variables do not persist between Bash calls). Put `-s` **before** the
  subcommand: `op -s NAME web ...`, not `op web -s NAME ...`.
- A session's Chrome starts on a copy of the main operator profile, made at
  creation time: its logins and extensions come along (`--fresh` for an
  empty profile instead). Reusing a name whose profile still exists keeps
  that profile as it is. Logins made inside a session stay there. Chrome's
  own password manager is switched off at launch, so never offer to save a
  password there.
- Pick a distinctive name (task or ticket id); it is what the user sees on
  the viewer page. `op session list` shows what exists; leave other agents'
  sessions alone.
- `op idle` is meaningless inside a session — the user never types there
  unless they have your viewer page open.

## Two tiers — pick the right one

**DOM tier (`op web`) for anything in Chrome.** Clicking a CSS selector cannot
miss, waits on real load events, and survives window resizes. Chrome starts
automatically on first use, on its own profile.

```bash
op web goto wikipedia.org
op web fill "input[name=search]" "X Window System"
op web click "button.search"
op web text            # page text, for reading rather than screenshotting
```

**Pixel tier for everything else** — system dialogs, native apps, and
browsers that cannot take a debug port (a snap-packaged Firefox, say), which
are screenshot-driven only.

**Bridging them:** `op web where <selector>` converts a CSS selector into screen
coordinates, for when something needs a genuine physical click or drag.

## Rules

- **Screenshot right before acting, and again after.** Never reuse coordinates
  from an earlier screenshot. On the main desktop a remote-desktop gateway
  may resize the screen to the user's browser window; in a session the user
  may have moved things from the viewer.
- **Never type into password, PIN, passphrase, or 2FA fields.** Stop, screenshot,
  and hand it to the user **with your session's `view:` URL** so they can type
  it themselves in the browser, then carry on once they say so. Dismissing such
  a dialog (Cancel) is fine; filling it is not. The user's password manager
  may be signed in on this machine; it is theirs to operate.
- **Ask before anything irreversible**: purchases, sending messages, deleting,
  granting permissions, settings affecting other people. Otherwise finish the
  task you were given without checking in at each step.
- **Check `op idle` first** when working on the main desktop. Under ~3s means
  the user is actively using it — you share one cursor, so wait rather than
  fight them for it.
- **Report what changed on screen**, not just which commands you ran.

## If something hangs

Screenshot before blaming the network. A modal dialog (GNOME keyring, a
permission prompt, an update nag) blocks the whole app and makes CDP calls time
out with no on-screen hint in the error.
