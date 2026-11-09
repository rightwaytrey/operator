#!/usr/bin/env bash
# Exercise every op subcommand. By default everything runs inside a private
# session (its own X server, Chrome and viewer tile), so the user's desktop
# is not touched beyond read-only checks. Pass --main to also run the
# shared-desktop checks, which open a scratch tab in the shared operator
# Chrome on the main display and click in it.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
with_main=0
[ "${1:-}" = "--main" ] && with_main=1

pass=0; fail=0
check() {
    local label="$1"; shift
    if out=$("$@" 2>&1); then
        printf '  ok   %-28s %s\n' "$label" "$(head -1 <<<"$out")"
        pass=$((pass + 1))
    else
        printf '  FAIL %-28s %s\n' "$label" "$(head -2 <<<"$out" | tr '\n' ' ')"
        fail=$((fail + 1))
    fi
}

echo "== wiring =="
# The skill other sessions discover must resolve to the copy in this repo,
# otherwise it silently drifts from the command surface below.
installed="$HOME/.claude/skills/operator/SKILL.md"
if [ ! -d "$HOME/.claude" ]; then
    echo "  SKIP skill -> repo (no ~/.claude: Claude Code is not installed here)"
elif [ "$(readlink -f "$installed" 2>/dev/null)" = "$PWD/skills/operator/SKILL.md" ]; then
    printf '  ok   %-28s %s\n' "skill -> repo" "$installed"
    pass=$((pass + 1))
else
    printf '  FAIL %-28s %s\n' "skill -> repo" "run ./install.sh"
    fail=$((fail + 1))
fi
check "op on PATH" command -v op

echo "== main desktop (read-only) =="
check "idle"        ./op idle
check "windows"     ./op windows
check "shot"        ./op shot --scale 0.3

echo "== viewer =="
check "viewer start"  ./op viewer start
check "viewer status" ./op viewer status
vurl="$(./op viewer url 2>/dev/null)"
vport="$(python3 -c 'import json,os;print(json.load(open(os.path.expanduser("~/.cache/op/viewer.json")))["port"])' 2>/dev/null || echo 6090)"
local_url="http://127.0.0.1:$vport"
check "viewer index"  curl -sf -o /dev/null "$local_url/"
check "viewer api"    curl -sf -o /dev/null "$local_url/api/sessions"
check "viewer novnc"  curl -sf -o /dev/null "$local_url/vnc.html"
check "viewer tile"   curl -sf -o /dev/null "$local_url/tile.html?token=none"
check "viewer zoom"   curl -sf -o /dev/null "$local_url/zoom.js"
if [ "${vurl#https://}" != "$vurl" ]; then
    check "viewer tailnet" curl -sf --max-time 5 -o /dev/null "$vurl/api/sessions"
else
    echo "  SKIP viewer tailnet (not exposed; run: op viewer expose)"
fi

echo "== isolated session (Xtigervnc, own Chrome, viewer tile) =="
sess="smoke-$$"
check "session new"     ./op session new "$sess" --fresh
check "session url"     ./op session url "$sess"
check "session list"    ./op session list
check "session windows" ./op -s "$sess" windows
check "session shot"    ./op -s "$sess" shot --scale 0.3
check "session grid"    ./op -s "$sess" shot --scale 0.3 --grid
# The viewer must bridge a WebSocket to this session's VNC server: expect
# the HTTP 101 upgrade and then the RFB banner inside the first frame.
check "viewer ws->vnc"  python3 - "$vport" "$sess" <<'WSCHECK'
import base64, os, socket, sys
port, name = int(sys.argv[1]), sys.argv[2]
s = socket.create_connection(("127.0.0.1", port), timeout=5)
key = base64.b64encode(os.urandom(16)).decode()
s.sendall((f"GET /websockify?token={name} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
           "Upgrade: websocket\r\nConnection: Upgrade\r\n"
           f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
           "Sec-WebSocket-Protocol: binary\r\n\r\n").encode())
data = b""
while b"RFB 003.008" not in data and len(data) < 8192:
    chunk = s.recv(4096)
    if not chunk:
        break
    data += chunk
ok = data.startswith(b"HTTP/1.1 101") and b"RFB 003.008" in data
print("101 + RFB banner" if ok else data[:120])
sys.exit(0 if ok else 1)
WSCHECK

echo "== chrome (DOM tier), inside the session =="
check "web goto"    ./op -s "$sess" web goto https://example.com
check "web newtab"  ./op -s "$sess" web newtab https://example.com
check "web url"     ./op -s "$sess" web url
check "web text"    ./op -s "$sess" web text
check "web where"   ./op -s "$sess" web where p
check "web eval"    ./op -s "$sess" web eval "document.title"
check "web tabs"    ./op -s "$sess" web tabs
check "web shot"    ./op -s "$sess" web shot
check "web close"   ./op -s "$sess" web --tab example.com close

echo "== input (pixel tier), inside the session =="
if coords=$(./op -s "$sess" web where p 2>/dev/null | awk -F': ' '/^center/{print $2}'); then
    x=${coords%,*}; y=${coords#*,}
    check "activate chrome" ./op -s "$sess" activate chrome
    check "move"            ./op -s "$sess" move "$x" "$y"
    check "click"           ./op -s "$sess" click "$x" "$y"
    check "drag (select)"   ./op -s "$sess" drag "$((x - 60))" "$y" "$((x + 60))" "$y"
    check "scroll"          ./op -s "$sess" scroll down 2
    check "key"             ./op -s "$sess" key Escape
    check "type"            ./op -s "$sess" type ""
else
    echo "  SKIP input tests (could not locate the page text)"
fi
check "session rm"      ./op session rm "$sess" --purge

echo "== xephyr session (a window on the main desktop) =="
sess="smoke-x-$$"
check "xephyr new"      ./op session new "$sess" --xephyr --fresh --size 800x600
check "xephyr shot"     ./op -s "$sess" shot --scale 0.3
check "xephyr rm"       ./op session rm "$sess" --purge

if [ "$with_main" = 1 ]; then
    echo "== shared desktop: a scratch tab in the shared Chrome =="
    if ! curl -sf --max-time 3 http://127.0.0.1:9222/json/version >/dev/null; then
        ./op launch chrome --wait 10
        sleep 2
    fi
    check "main web newtab" ./op web newtab https://example.com
    check "main web goto"   ./op web goto https://example.com
    check "main web where"  ./op web where p
    if coords=$(./op web where p 2>/dev/null | awk -F': ' '/^center/{print $2}'); then
        x=${coords%,*}; y=${coords#*,}
        check "main click"  ./op click "$x" "$y"
        check "main key"    ./op key Escape
    fi
    check "main web close"  ./op web --tab example.com close
else
    echo "== shared desktop input checks skipped; pass --main to run them =="
fi

echo
echo "$pass passed, $fail failed"
ls -t shots/*.png 2>/dev/null | head -3 | sed 's/^/  latest: /'
exit $((fail > 0))
