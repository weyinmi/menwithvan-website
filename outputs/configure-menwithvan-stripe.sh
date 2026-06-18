#!/usr/bin/env bash
set -euo pipefail

HOST="${1:-root@194.164.126.253}"
DEFAULT_PUBLISHABLE_KEY="pk_test_51Tje7mIRhSEU8P0kQCiRaghD49tS05tTtooT5yQiPyXrf9v1lE9PmTiApRFQBrjz9IjQJ2bUVYk4hCpU8HqAj19x00UeQhA5F3"
STRIPE_API_VERSION="${STRIPE_API_VERSION:-2026-03-25.dahlia}"

echo "This one-time setup switches Men With a Van to a different Stripe account."
echo "Use keys from the same Stripe account and the same mode: test with test, live with live."
echo
read -r -p "Stripe publishable key [$DEFAULT_PUBLISHABLE_KEY]: " STRIPE_PUBLISHABLE_KEY
STRIPE_PUBLISHABLE_KEY="${STRIPE_PUBLISHABLE_KEY:-$DEFAULT_PUBLISHABLE_KEY}"
read -r -s -p "Stripe secret key (sk_test_... or sk_live_...): " STRIPE_SECRET_KEY
echo
read -r -s -p "Stripe webhook signing secret (whsec_...): " STRIPE_WEBHOOK_SECRET
echo

if [[ ! "$STRIPE_PUBLISHABLE_KEY" =~ ^pk_(test|live)_ ]]; then
  echo "Publishable key must start with pk_test_ or pk_live_." >&2
  exit 1
fi

if [[ ! "$STRIPE_SECRET_KEY" =~ ^sk_(test|live)_ ]]; then
  echo "Secret key must start with sk_test_ or sk_live_." >&2
  exit 1
fi

if [[ ! "$STRIPE_WEBHOOK_SECRET" =~ ^whsec_ ]]; then
  echo "Webhook signing secret must start with whsec_." >&2
  exit 1
fi

publishable_mode="${STRIPE_PUBLISHABLE_KEY#pk_}"
publishable_mode="${publishable_mode%%_*}"
secret_mode="${STRIPE_SECRET_KEY#sk_}"
secret_mode="${secret_mode%%_*}"

if [ "$publishable_mode" != "$secret_mode" ]; then
  echo "Publishable key and secret key are not in the same Stripe mode." >&2
  exit 1
fi

if base64 --help 2>&1 | grep -q -- "-w"; then
  PUBLISHABLE_B64="$(printf '%s' "$STRIPE_PUBLISHABLE_KEY" | base64 -w 0)"
  SECRET_B64="$(printf '%s' "$STRIPE_SECRET_KEY" | base64 -w 0)"
  WEBHOOK_B64="$(printf '%s' "$STRIPE_WEBHOOK_SECRET" | base64 -w 0)"
  API_VERSION_B64="$(printf '%s' "$STRIPE_API_VERSION" | base64 -w 0)"
else
  PUBLISHABLE_B64="$(printf '%s' "$STRIPE_PUBLISHABLE_KEY" | base64 | tr -d '\n')"
  SECRET_B64="$(printf '%s' "$STRIPE_SECRET_KEY" | base64 | tr -d '\n')"
  WEBHOOK_B64="$(printf '%s' "$STRIPE_WEBHOOK_SECRET" | base64 | tr -d '\n')"
  API_VERSION_B64="$(printf '%s' "$STRIPE_API_VERSION" | base64 | tr -d '\n')"
fi

echo "Updating Stripe settings on $HOST..."
ssh "$HOST" \
  "PUBLISHABLE_B64='$PUBLISHABLE_B64' SECRET_B64='$SECRET_B64' WEBHOOK_B64='$WEBHOOK_B64' API_VERSION_B64='$API_VERSION_B64' bash -s" <<'REMOTE'
set -euo pipefail

STRIPE_PUBLISHABLE_KEY="$(printf '%s' "$PUBLISHABLE_B64" | base64 -d)"
STRIPE_SECRET_KEY="$(printf '%s' "$SECRET_B64" | base64 -d)"
STRIPE_WEBHOOK_SECRET="$(printf '%s' "$WEBHOOK_B64" | base64 -d)"
STRIPE_API_VERSION="$(printf '%s' "$API_VERSION_B64" | base64 -d)"
export STRIPE_PUBLISHABLE_KEY STRIPE_SECRET_KEY STRIPE_WEBHOOK_SECRET STRIPE_API_VERSION

python3 - <<'PY'
import json
import os
import shlex
import urllib.error
import urllib.request
from pathlib import Path

secret_key = os.environ["STRIPE_SECRET_KEY"]
publishable_key = os.environ["STRIPE_PUBLISHABLE_KEY"]
webhook_secret = os.environ["STRIPE_WEBHOOK_SECRET"]
api_version = os.environ["STRIPE_API_VERSION"]

request = urllib.request.Request("https://api.stripe.com/v1/account")
request.add_header("Authorization", f"Bearer {secret_key}")
request.add_header("Stripe-Version", api_version)
try:
    with urllib.request.urlopen(request, timeout=20) as response:
        account = json.loads(response.read().decode("utf-8"))
except urllib.error.HTTPError as error:
    body = error.read().decode("utf-8", errors="replace")
    raise SystemExit(f"Stripe secret key validation failed: {body}") from error

secret_live = secret_key.startswith("sk_live_")
publishable_live = publishable_key.startswith("pk_live_")
if bool(account.get("livemode")) != secret_live or secret_live != publishable_live:
    raise SystemExit("Stripe key mode mismatch. Use test keys together or live keys together.")

path = Path("/etc/menwithvan/quote.env")
text = path.read_text() if path.exists() else ""
updates = {
    "STRIPE_PUBLISHABLE_KEY": publishable_key,
    "STRIPE_SECRET_KEY": secret_key,
    "STRIPE_WEBHOOK_SECRET": webhook_secret,
    "STRIPE_API_VERSION": api_version,
}

def env_line(key, value):
    return f"{key}={shlex.quote(str(value))}"

lines = []
seen = set()
for line in text.splitlines():
    if "=" in line and not line.lstrip().startswith("#"):
        key = line.split("=", 1)[0].strip()
        if key in updates:
            lines.append(env_line(key, updates[key]))
            seen.add(key)
        else:
            lines.append(line)
    else:
        lines.append(line)

if lines and lines[-1].strip():
    lines.append("")

for key, value in updates.items():
    if key not in seen:
        lines.append(env_line(key, value))

path.parent.mkdir(parents=True, exist_ok=True)
path.write_text("\n".join(lines).rstrip() + "\n")
path.chmod(0o600)

print("stripe_account_id", account.get("id", "unknown"))
print("stripe_livemode", bool(account.get("livemode")))
print("stripe_settings_written True")
PY

systemctl restart menwithvan-quote
systemctl is-active menwithvan-quote

for attempt in $(seq 1 20); do
  if curl -fsS http://127.0.0.1:3020/health >/dev/null 2>&1; then
    echo "quote_api_health_ok True"
    break
  fi
  if [ "$attempt" -eq 20 ]; then
    echo "quote_api_health_ok False"
    systemctl status menwithvan-quote --no-pager -l || true
    journalctl -u menwithvan-quote -n 60 --no-pager || true
    exit 1
  fi
  sleep 1
done

echo "Stripe settings installed."
REMOTE
