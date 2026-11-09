#!/usr/bin/env bash
# Publish the operator viewer at /operator/ on an existing, authenticated
# nginx site, by installing deploy/nginx-operator.conf as a snippet and
# including it from that site's server block.
#
#   sudo ./deploy/install-nginx.sh /etc/nginx/sites-available/SITE
#
# SITE must already carry TLS and authentication for the whole server (the
# viewer has none of its own; see the notes in nginx-operator.conf). A file
# deploy/nginx-operator.local.conf, if present, is appended to the snippet
# for machine-specific extras; it is gitignored.
#
# Idempotent. Backs up the site file before touching it, and reloads nginx
# only if `nginx -t` passes. Afterwards, tell op where the viewer lives:
#   op viewer expose --url https://your.host/operator/
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SNIP=/etc/nginx/snippets/operator.conf
SITE="${1:-${OPERATOR_NGINX_SITE:-}}"

[ "$(id -u)" = 0 ] || { echo "run with sudo" >&2; exit 1; }
if [ -z "$SITE" ]; then
    echo "usage: sudo $0 /etc/nginx/sites-available/SITE" >&2
    echo "sites here:" >&2
    ls /etc/nginx/sites-available/ 2>/dev/null | sed 's/^/  /' >&2
    exit 2
fi
[ -f "$SITE" ] || { echo "no such site file: $SITE" >&2; exit 1; }

tmp="$(mktemp)"
cat "$HERE/nginx-operator.conf" > "$tmp"
if [ -f "$HERE/nginx-operator.local.conf" ]; then
    { echo; cat "$HERE/nginx-operator.local.conf"; } >> "$tmp"
    echo "including nginx-operator.local.conf"
fi
install -m 644 "$tmp" "$SNIP"
rm -f "$tmp"
echo "installed $SNIP"

if grep -q "snippets/operator.conf" "$SITE"; then
    echo "$SITE already includes it"
else
    cp -a "$SITE" "$SITE.bak-$(date +%Y%m%d-%H%M%S)"
    python3 - "$SITE" "$HERE" <<'PY'
import sys
path, here = sys.argv[1], sys.argv[2]
lines = open(path).read().split("\n")
for i, line in enumerate(lines):
    if line.rstrip() == "    location / {":
        lines[i:i] = [f"    # Operator viewer: {here}/nginx-operator.conf",
                      "    include /etc/nginx/snippets/operator.conf;", ""]
        break
else:
    sys.exit("could not find the catch-all `location / {` to insert before; "
             "add `include /etc/nginx/snippets/operator.conf;` to the server block by hand")
open(path, "w").write("\n".join(lines))
PY
    echo "added the include to $SITE (backup beside it)"
fi

nginx -t
systemctl reload nginx
echo "reloaded nginx. Now record the URL so op prints it:"
echo "  op viewer expose --url https://YOUR.HOST/operator/"
