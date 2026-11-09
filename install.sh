#!/usr/bin/env bash
# Wire the operator harness into this machine so any agent session can use it.
#
# Idempotent: safe to re-run. Puts `op` on PATH, links the Claude Code skill
# and/or the Codex instructions if those tools are installed, syncs the uv
# environment, fetches the two vendored pieces the viewer needs (no root),
# and reports what is missing. It never edits user settings itself: editing
# those is the user's call, so it only says what to add.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

BIN_LINK="$HOME/.local/bin/op"
SKILL_DIR="$HOME/.claude/skills/operator"
SETTINGS="$HOME/.claude/settings.json"
CODEX_AGENTS="$HOME/.codex/AGENTS.md"

echo "== op on PATH =="
mkdir -p "$(dirname "$BIN_LINK")"
ln -sfn "$HERE/op" "$BIN_LINK"
printf '  %-14s -> %s\n' "op" "$(readlink -f "$BIN_LINK")"
case ":$PATH:" in
    *":$HOME/.local/bin:"*) ;;
    *) echo "  WARN  $HOME/.local/bin is not on PATH in this shell. Log out and back"
       echo "        in (Ubuntu adds it once it exists), or add it to your shell profile." ;;
esac

echo
echo "== agent integrations =="
if [ -d "$HOME/.claude" ]; then
    mkdir -p "$SKILL_DIR"
    ln -sfn "$HERE/skills/operator/SKILL.md" "$SKILL_DIR/SKILL.md"
    printf '  %-14s -> %s\n' "Claude skill" "$(readlink -f "$SKILL_DIR/SKILL.md")"
else
    echo "  --    Claude Code: no ~/.claude, skill not linked (re-run after installing it)"
fi
if [ -d "$HOME/.codex" ]; then
    if [ ! -e "$CODEX_AGENTS" ] || [ "$(readlink -f "$CODEX_AGENTS")" = "$HERE/AGENTS.md" ]; then
        ln -sfn "$HERE/AGENTS.md" "$CODEX_AGENTS"
        printf '  %-14s -> %s\n' "Codex AGENTS" "$(readlink -f "$CODEX_AGENTS")"
    else
        echo "  --    Codex: $CODEX_AGENTS already exists and is yours; add to it:"
        echo "        'For driving the desktop, follow $HERE/AGENTS.md.'"
    fi
else
    echo "  --    Codex: no ~/.codex; in a repo, Codex reads ./AGENTS.md on its own"
fi

echo
echo "== toolchain =="
if command -v uv >/dev/null 2>&1; then
    echo "  ok    uv $(uv --version 2>/dev/null | awk '{print $2}')"
    if uv sync --project "$HERE" --quiet; then
        echo "  ok    uv environment synced"
    else
        echo "  FAIL  uv sync failed"
    fi
else
    echo "  FAIL  uv is not installed:  curl -LsSf https://astral.sh/uv/install.sh | sh"
fi

# What the harness execs. mcookie/xauth make session cookies, xfwm4 is the
# window manager inside a session, xclip backs `type --paste`,
# dbus-run-session keeps file pickers inside a session, Chrome is the DOM
# tier. Xephyr and the DejaVu font are optional (sessions as a window on the
# main desktop; prettier grid labels).
echo
echo "== system tools =="
missing_pkgs=""
need() {   # binary  apt-package  [note]
    if command -v "$1" >/dev/null 2>&1; then
        printf '  ok    %-18s %s\n' "$1" "$(command -v "$1")"
    else
        printf '  MISSING %-16s %s\n' "$1" "${3:-apt package: $2}"
        missing_pkgs="$missing_pkgs $2"
    fi
}
need xauth xauth
need mcookie util-linux
need xfwm4 xfwm4
need xclip xclip
need dbus-run-session dbus
chrome=""
for c in ${OP_CHROME:-} google-chrome google-chrome-stable chromium chromium-browser; do
    if command -v "$c" >/dev/null 2>&1; then chrome="$c"; break; fi
done
if [ -n "$chrome" ]; then
    printf '  ok    %-18s %s\n' "chrome" "$(command -v "$chrome")"
else
    echo "  MISSING chrome             Google Chrome (https://www.google.com/chrome/, the .deb)"
    echo "                             or:  sudo apt install chromium   (set OP_CHROME if needed)"
fi
if command -v Xephyr >/dev/null 2>&1; then
    printf '  ok    %-18s %s\n' "Xephyr (optional)" "$(command -v Xephyr)"
else
    echo "  --    Xephyr (optional)   apt package: xserver-xephyr  (for --xephyr sessions)"
fi
[ -f /usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf ] \
    || echo "  --    DejaVu font (optional)  apt package: fonts-dejavu-core  (grid labels)"
[ -n "$missing_pkgs" ] && echo "  install:  sudo apt install$missing_pkgs"

# The viewer needs two things apt would normally provide but which need no
# root to unpack: TigerVNC's X server (the Ubuntu package, extracted as-is --
# every library it links is already on a GNOME desktop) and noVNC (static
# HTML/JS; the apt package is years old, so a pinned upstream release is
# used). Both land under vendor/, which is gitignored. If Xtigervnc is ever
# installed system-wide it is preferred automatically.
echo
echo "== vendored components (vendor/) =="
VENDOR="$HERE/vendor"
NOVNC_VERSION="v1.7.0"
mkdir -p "$VENDOR"

if [ "$(cat "$VENDOR/novnc/.version" 2>/dev/null)" = "$NOVNC_VERSION" ] \
        && [ -f "$VENDOR/novnc/vnc.html" ]; then
    echo "  ok    noVNC $NOVNC_VERSION"
else
    tmp="$(mktemp -d)"
    if curl -fsSL -o "$tmp/novnc.tar.gz" \
            "https://github.com/novnc/noVNC/archive/refs/tags/$NOVNC_VERSION.tar.gz" \
        && rm -rf "$VENDOR/novnc" && mkdir -p "$VENDOR/novnc" \
        && tar -xzf "$tmp/novnc.tar.gz" --strip-components=1 -C "$VENDOR/novnc" \
        && [ -f "$VENDOR/novnc/vnc.html" ]; then
        echo "$NOVNC_VERSION" > "$VENDOR/novnc/.version"
        echo "  ok    noVNC $NOVNC_VERSION downloaded"
    else
        echo "  FAIL  noVNC download (github.com unreachable?) -- the viewer cannot start"
    fi
    rm -rf "$tmp"
fi

if command -v Xtigervnc >/dev/null; then
    echo "  ok    Xtigervnc (system package: $(command -v Xtigervnc))"
elif [ -x "$VENDOR/tigervnc/usr/bin/Xtigervnc" ]; then
    echo "  ok    Xtigervnc (vendored)"
else
    tmp="$(mktemp -d)"
    if (cd "$tmp" && apt-get download tigervnc-standalone-server >/dev/null 2>&1) \
        && mkdir -p "$VENDOR/tigervnc" \
        && dpkg-deb -x "$tmp"/tigervnc-standalone-server_*.deb "$VENDOR/tigervnc" \
        && [ -x "$VENDOR/tigervnc/usr/bin/Xtigervnc" ]; then
        echo "  ok    Xtigervnc unpacked from the Ubuntu package"
    else
        echo "  FAIL  could not fetch tigervnc-standalone-server (on a fresh system run"
        echo "        'sudo apt update' first). Simplest fix if you have sudo:"
        echo "        sudo apt install tigervnc-standalone-server"
    fi
    rm -rf "$tmp"
fi
if [ -x "$VENDOR/tigervnc/usr/bin/Xtigervnc" ]; then
    missing="$(ldd "$VENDOR/tigervnc/usr/bin/Xtigervnc" | awk '/not found/{print $1}' | tr '\n' ' ')"
    [ -n "$missing" ] && echo "  WARN  Xtigervnc is missing libraries: $missing"
fi

echo
echo "== main display =="
cfg="$(cat "$HOME/.config/op/display" 2>/dev/null)"
if [ -n "${OP_DISPLAY:-}" ]; then
    echo "  OP_DISPLAY=$OP_DISPLAY"
elif [ -n "$cfg" ]; then
    echo "  ~/.config/op/display: $cfg"
else
    echo "  (none configured: op uses \$DISPLAY when it points at a live server, else :0)"
    echo "  If the desktop you want to drive is another display (an xrdp session, say),"
    echo "  run:  mkdir -p ~/.config/op && echo :10 > ~/.config/op/display"
fi

if [ -f "$SETTINGS" ]; then
    echo
    echo "== Claude Code settings ($SETTINGS) =="
    # These govern whether sessions are prompted. Reading the PNG back matters
    # as much as capturing it: without the read scope a session can take a
    # screenshot and then be unable to look at it.
    python3 - "$SETTINGS" "$HERE" <<'PY'
import json
import sys

settings_path, root = sys.argv[1], sys.argv[2]
try:
    with open(settings_path) as fh:
        perms = json.load(fh).get("permissions", {})
except Exception as exc:
    print(f"  WARN  could not read settings: {exc}")
    sys.exit(0)

allow = perms.get("allow", [])
extra = perms.get("additionalDirectories", [])
shots = f"{root}/shots"

wanted = [
    ("run op without prompting", "Bash(op:*)" in allow),
    ("read screenshots back", any(s.startswith("Read(" + shots) for s in allow)),
    ("shots dir in scope", shots in extra),
]
missing = [label for label, ok in wanted if not ok]
for label, ok in wanted:
    print(f"  {'ok   ' if ok else 'MISSING'} {label}")

if missing:
    print("\n  Add to permissions in that file:")
    print(f'    "allow": ["Bash(op:*)", "Read({shots}/**)"]')
    print(f'    "additionalDirectories": ["{shots}"]')
PY
fi

echo
echo "Run ./smoke.sh to verify against a live display."
