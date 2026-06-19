#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOST="${1:-root@194.164.126.253}"
KEY_PATH="${DEPLOY_KEY_PATH:-$ROOT_DIR/work/deploy-key/menwithvan_deploy_key}"
KNOWN_HOSTS_PATH="${DEPLOY_KNOWN_HOSTS_PATH:-$ROOT_DIR/work/deploy-key/known_hosts}"

if [ -x "$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3" ]; then
  PYTHON="$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"
else
  PYTHON="${PYTHON:-python3}"
fi

JSON_LD_HASH="$("$PYTHON" - <<'PY'
from pathlib import Path
import base64
import hashlib
import re

html = Path("outputs/menwithvan-demo/index.html").read_text()
match = re.search(r'<script type="application/ld\+json">\n(.*?)\n    </script>', html, re.S)
if not match:
    raise SystemExit("Could not find homepage JSON-LD block for CSP hash.")
digest = hashlib.sha256(match.group(1).encode("utf-8")).digest()
print("sha256-" + base64.b64encode(digest).decode("ascii"))
PY
)"

SSH_ARGS=(-o StrictHostKeyChecking=yes -o UserKnownHostsFile="$KNOWN_HOSTS_PATH")
if [ -f "$KEY_PATH" ]; then
  SSH_ARGS+=(-i "$KEY_PATH" -o IdentitiesOnly=yes)
fi

echo "Installing Men With A Van nginx security headers on $HOST..."
ssh "${SSH_ARGS[@]}" "$HOST" "JSON_LD_HASH='$JSON_LD_HASH' bash -s" <<'REMOTE'
set -euo pipefail

cat > /etc/nginx/conf.d/menwithvan-security-headers.conf <<EOF
client_max_body_size 0;
proxy_hide_header Strict-Transport-Security;
proxy_hide_header X-Content-Type-Options;
proxy_hide_header X-Frame-Options;
proxy_hide_header Referrer-Policy;
proxy_hide_header Permissions-Policy;
proxy_hide_header Content-Security-Policy;
add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
add_header X-Content-Type-Options "nosniff" always;
add_header X-Frame-Options "DENY" always;
add_header Referrer-Policy "strict-origin-when-cross-origin" always;
add_header Permissions-Policy "camera=(), microphone=(), geolocation=(), payment=(self \"https://checkout.stripe.com\")" always;
add_header Content-Security-Policy "default-src 'self'; script-src 'self' https://js.stripe.com https://checkout.stripe.com '$JSON_LD_HASH'; connect-src 'self' https://checkout.stripe.com https://api.stripe.com https://r.stripe.com; img-src 'self' data: https: https://*.stripe.com; font-src 'self' data:; style-src 'self' 'unsafe-inline'; frame-src https://checkout.stripe.com https://js.stripe.com https://hooks.stripe.com; form-action 'self' https://checkout.stripe.com; base-uri 'self'; object-src 'none'; frame-ancestors 'none'; upgrade-insecure-requests" always;
EOF

nginx -t
systemctl reload nginx
echo "nginx_security_headers_installed True"
REMOTE
