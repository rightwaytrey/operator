#!/usr/bin/env bash
# Replicate this operator checkout onto another machine and wire it up there,
# so Claude Code sessions on that machine get `op` and the operator skill.
#
#   ./replicate.sh HOST [--dest PATH] [--settings] [--claude] [--sync-only]
#   ./replicate.sh --bootstrap [--settings] [--claude]      (runs ON the target)
#
# HOST is anything ssh(1) accepts; an alias from ~/.ssh/config is ideal.
#   --dest       checkout path on the target, relative to its $HOME
#                (default projects/operator)
#   --settings   also add the three Claude Code permission entries that
#                install.sh reports as missing to ~/.claude/settings.json on
#                the target (backed up first); otherwise they are only reported
#   --claude     install Claude Code on the target if it is not there
#                (official native installer, user-level; logging in is manual)
#   --sync-only  copy the tree and stop, no bootstrap
#
# The working tree is copied as it is, uncommitted changes included: what
# runs here is what runs there. Not copied: .venv (uv rebuilds it from
# uv.lock), shots/, vendor/tigervnc (install.sh fetches the .deb that matches
# THAT machine's Ubuntu release) and the Chrome profiles. vendor/novnc is
# static and is copied, so the target needs no GitHub access for it.
#
# --bootstrap is what the target runs after the copy, from inside the copied
# checkout: install uv for that user if missing, run ./install.sh, take an
# inventory of what the harness needs on a desktop (Chrome, xfwm4 for
# sessions, Xtigervnc, Claude Code, tailscale, X sockets), then prove a
# private session works there by creating one, screenshotting it and
# removing it. Safe to run on this machine too.
#
# Idempotent and root-free on both sides. Re-run to push a newer tree.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

usage() { sed -n '2,/^$/p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

bootstrap() {
    local want_settings="$1" want_claude="$2"
    cd "$HERE" || return 1
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

    echo "== bootstrap on $(hostname) as $(id -un) =="
    echo "  $(. /etc/os-release 2>/dev/null; echo "${PRETTY_NAME:-unknown OS}")"
    echo "  checkout: $HERE"

    echo
    echo "== toolchain =="
    if ! command -v uv >/dev/null 2>&1; then
        echo "  installing uv for $(id -un) from https://astral.sh/uv/install.sh"
        curl -LsSf https://astral.sh/uv/install.sh | sh -s -- --quiet 2>&1 | sed 's/^/    /'
        hash -r
    fi
    if command -v uv >/dev/null 2>&1; then
        echo "  ok    uv $(uv --version 2>/dev/null | awk '{print $2}')  ($(command -v uv))"
    else
        echo "  FAIL  uv is not installed and the installer did not put it on PATH"
        return 1
    fi
    local t
    for t in python3 git curl; do
        if command -v "$t" >/dev/null 2>&1; then
            printf '  ok    %-8s %s\n' "$t" "$("$t" --version 2>&1 | head -1 | grep -oE '[0-9]+(\.[0-9]+)+' | head -1)"
        else
            printf '  WARN  %-8s missing\n' "$t"
        fi
    done

    echo
    ./install.sh

    echo
    echo "== desktop inventory =="
    local sockets
    sockets="$(ls /tmp/.X11-unix/ 2>/dev/null | sed 's/^X/:/' | tr '\n' ' ')"
    echo "  X sockets: ${sockets:-none}"
    if command -v loginctl >/dev/null 2>&1; then
        loginctl list-sessions --no-legend 2>/dev/null | while read -r sid _uid user _rest; do
            printf '  login session %s user=%s type=%s desktop=%s\n' "$sid" "$user" \
                "$(loginctl show-session "$sid" -p Type --value 2>/dev/null)" \
                "$(loginctl show-session "$sid" -p Desktop --value 2>/dev/null)"
        done
    fi
    local b p
    for b in google-chrome google-chrome-stable chromium chromium-browser firefox \
             xfwm4 Xtigervnc tailscale claude; do
        if p="$(command -v "$b" 2>/dev/null)"; then
            printf '  ok    %-22s %s\n' "$b" "$p"
        else
            printf '  --    %-22s not found\n' "$b"
        fi
    done
    if command -v snap >/dev/null 2>&1; then
        local snaps
        snaps="$(snap list 2>/dev/null | awk 'NR>1 && /chromium|firefox|chrome/{print $1}' | tr '\n' ' ')"
        [ -n "$snaps" ] && echo "  snaps: $snaps"
    fi

    echo
    echo "== Claude Code =="
    if command -v claude >/dev/null 2>&1; then
        echo "  ok    $(claude --version 2>/dev/null | head -1)"
    elif [ "$want_claude" = 1 ]; then
        echo "  installing Claude Code from https://claude.ai/install.sh (user-level)"
        curl -fsSL https://claude.ai/install.sh | bash 2>&1 | tail -3 | sed 's/^/    /'
        hash -r
        if command -v claude >/dev/null 2>&1; then
            echo "  ok    $(claude --version 2>/dev/null | head -1)"
            echo "        run  claude  once in a terminal on that machine to log in"
        else
            echo "  FAIL  claude is not on PATH after the installer ran"
        fi
    else
        echo "  --    not installed (re-run with --claude to install it)"
    fi

    echo
    echo "== Claude Code settings =="
    if [ "$want_settings" = 1 ]; then
        python3 - "$HOME/.claude/settings.json" "$HERE/shots" <<'PY'
import datetime
import json
import os
import shutil
import sys

path, shots = sys.argv[1], sys.argv[2]
data = {}
try:
    if os.path.exists(path):
        with open(path) as fh:
            data = json.load(fh)
except Exception as exc:
    print(f"  FAIL  could not parse {path}: {exc}")
    sys.exit(0)

perms = data.setdefault("permissions", {})
allow = perms.setdefault("allow", [])
extra = perms.setdefault("additionalDirectories", [])
added = []
for rule in ("Bash(op:*)", f"Read({shots}/**)"):
    if rule not in allow:
        allow.append(rule)
        added.append(rule)
if shots not in extra:
    extra.append(shots)
    added.append(shots)

if not added:
    print(f"  ok    {path} already has them")
    sys.exit(0)
os.makedirs(os.path.dirname(path), exist_ok=True)
if os.path.exists(path):
    backup = f"{path}.bak-{datetime.date.today().isoformat()}"
    shutil.copy2(path, backup)
    print(f"  backup {backup}")
with open(path, "w") as fh:
    json.dump(data, fh, indent=2)
    fh.write("\n")
for item in added:
    print(f"  added {item}")
PY
    else
        echo "  (reported by install.sh above; re-run with --settings to add what is missing)"
    fi

    echo
    echo "== self-test: a private session =="
    local name="replicate-probe" out shot
    ./op session rm "$name" >/dev/null 2>&1 || true
    if out="$(./op session new "$name" --fresh --size 800x600 2>&1)"; then
        sed 's/^/  /' <<<"$out"
        if shot="$(./op -s "$name" shot --scale 0.5 2>&1)"; then
            echo "  ok    shot $shot"
        else
            echo "  FAIL  shot: $(tr '\n' ' ' <<<"$shot")"
        fi
        ./op -s "$name" windows 2>&1 | head -3 | sed 's/^/  /'
        ./op session rm "$name" >/dev/null 2>&1 && echo "  ok    probe session removed (viewer left running)"
    else
        echo "  FAIL  session new: $(tail -2 <<<"$out" | tr '\n' ' ')"
    fi
}

push() {
    local host="$1" dest="$2" want_settings="$3" want_claude="$4" sync_only="$5"
    echo "== reach $host =="
    local who
    if ! who="$(ssh -o BatchMode=yes -o ConnectTimeout=15 "$host" \
            'echo "$(hostname) as $(id -un), $(. /etc/os-release 2>/dev/null; echo "${PRETTY_NAME:-unknown OS}")"' 2>&1)"; then
        echo "  FAIL  $who"
        return 1
    fi
    echo "  $who"

    echo
    echo "== sync $HERE/ -> $host:$dest/ =="
    ssh "$host" "mkdir -p '$dest'" || return 1
    rsync -az --delete --info=stats1 \
        --exclude=/.venv/ --exclude=/shots/ --exclude='__pycache__/' --exclude='*.pyc' \
        --exclude=/vendor/tigervnc/ \
        "$HERE/" "$host:$dest/" \
        | grep -E "Number of (regular files transferred|created files)|Total transferred" | sed 's/^/  /'
    if [ "${PIPESTATUS[0]}" -ne 0 ]; then
        echo "  FAIL  rsync"
        return 1
    fi
    if [ "$sync_only" = 1 ]; then
        echo "  (sync only; bootstrap skipped)"
        return 0
    fi

    echo
    local flags=""
    [ "$want_settings" = 1 ] && flags="$flags --settings"
    [ "$want_claude" = 1 ] && flags="$flags --claude"
    ssh "$host" "cd '$dest' && bash ./replicate.sh --bootstrap$flags"
}

host=""; dest="projects/operator"; settings=0; claude=0; sync_only=0; mode=push
while [ $# -gt 0 ]; do
    case "$1" in
        --bootstrap) mode=bootstrap; shift ;;
        --dest) dest="$2"; shift 2 ;;
        --settings) settings=1; shift ;;
        --claude) claude=1; shift ;;
        --sync-only) sync_only=1; shift ;;
        -h|--help) usage; exit 0 ;;
        -*) echo "unknown option $1" >&2; usage >&2; exit 2 ;;
        *)  if [ -n "$host" ]; then echo "one host only" >&2; exit 2; fi
            host="$1"; shift ;;
    esac
done

if [ "$mode" = bootstrap ]; then
    bootstrap "$settings" "$claude"
else
    [ -n "$host" ] || { usage >&2; exit 2; }
    push "$host" "$dest" "$settings" "$claude" "$sync_only"
fi
