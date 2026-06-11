#!/usr/bin/env bash
set -euo pipefail
export COPYFILE_DISABLE=1

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
HOST="${1:-deploy@194.164.126.253}"
KEY_PATH="${DEPLOY_KEY_PATH:-$WORKSPACE_DIR/work/deploy-key/menwithvan_deploy_key}"
PACKAGE_DIR="$(mktemp -d)"

cleanup() {
  rm -rf "$PACKAGE_DIR"
}
trap cleanup EXIT

mkdir -p "$PACKAGE_DIR/backend" "$PACKAGE_DIR/html"
cp "$WORKSPACE_DIR/outputs/menwithvan-backend/app.py" "$PACKAGE_DIR/backend/app.py"
cp -R "$WORKSPACE_DIR/outputs/menwithvan-demo/." "$PACKAGE_DIR/html/"
tar -C "$PACKAGE_DIR" -czf "$PACKAGE_DIR/menwithvan-checkout-fix.tgz" backend html

SSH_ARGS=(-o StrictHostKeyChecking=accept-new)
if [ -f "$KEY_PATH" ]; then
  SSH_ARGS+=(-i "$KEY_PATH")
fi

echo "Uploading Men With a Van checkout fix to $HOST..."
ssh "${SSH_ARGS[@]}" "$HOST" "cat > /tmp/menwithvan-deploy.tgz" < "$PACKAGE_DIR/menwithvan-checkout-fix.tgz"

if [[ "$HOST" == deploy@* ]]; then
  echo "Installing update through the deploy helper..."
  ssh "${SSH_ARGS[@]}" "$HOST" "sudo -n /usr/local/sbin/menwithvan-deploy-from-tar /tmp/menwithvan-deploy.tgz"
  echo "Checkout fix deployed."
  exit 0
fi

echo "Installing update on server..."
ssh "${SSH_ARGS[@]}" "$HOST" 'bash -s' <<'REMOTE'
set -euo pipefail

backup="/root/menwithvan-backups/$(date +%Y%m%d-%H%M%S)-checkout-fix"
mkdir -p "$backup"
cp -a /var/www/menwithvan.com/html "$backup/html"
cp -a /opt/menwithvan/backend/app.py "$backup/app.py"

rm -rf /tmp/menwithvan-checkout-fix
mkdir -p /tmp/menwithvan-checkout-fix
tar -xzf /tmp/menwithvan-checkout-fix.tgz -C /tmp/menwithvan-checkout-fix

cp /tmp/menwithvan-checkout-fix/backend/app.py /opt/menwithvan/backend/app.py
cp -a /tmp/menwithvan-checkout-fix/html/. /var/www/menwithvan.com/html/

python3 -m py_compile /opt/menwithvan/backend/app.py
systemctl restart menwithvan-quote
systemctl is-active menwithvan-quote
nginx -t

for attempt in $(seq 1 20); do
  if curl -fsS http://127.0.0.1:3020/health >/dev/null 2>&1; then
    echo "quote_api_health_ok True"
    break
  fi
  if [ "$attempt" -eq 20 ]; then
    echo "quote_api_health_ok False"
    systemctl status menwithvan-quote --no-pager -l || true
    exit 1
  fi
  sleep 1
done

python3 - <<'PY'
import sqlite3
from pathlib import Path
path = "/var/lib/menwithvan/bookings.sqlite3"
with sqlite3.connect(path) as db:
    cols = {row[1] for row in db.execute("PRAGMA table_info(bookings)").fetchall()}
    print("additional_addresses_column", "additional_addresses" in cols)
    print("confirmation_email_column", "confirmation_email_sent_at" in cols)
    print("calendar_token_column", "calendar_token" in cols)
env_text = Path("/etc/menwithvan/quote.env").read_text()
print("stripe_test_key_configured", "STRIPE_SECRET_KEY=sk_test_" in env_text)
print("stripe_webhook_configured", "STRIPE_WEBHOOK_SECRET=whsec_" in env_text)
PY

python3 - <<'PY'
import json
import sqlite3
import urllib.error
import urllib.request

payload = {
    "quoteInputs": {
        "moveType": "House move",
        "lutonVans": 1,
        "movers": 2,
        "hours": 2,
        "pickup": "SW1A 1AA",
        "delivery": "W1A 1AA",
        "pickupStairs": 0,
        "deliveryStairs": 0,
        "items": "Automated deployment smoke test - delete",
    },
    "customer": {
        "name": "Deployment Smoke Test",
        "email": "deployment-smoke-test@example.com",
        "phone": "07123456789",
    },
    "booking": {
        "moveDate": "2026-07-01",
        "moveTime": "08:00",
        "pickupAddress": "SW1A 1AA, London, UK",
        "deliveryAddress": "W1A 1AA, London, UK",
        "accessNotes": "Smoke test only",
        "paymentOption": "deposit",
        "termsAccepted": True,
    },
}

request = urllib.request.Request(
    "http://127.0.0.1:3020/api/bookings",
    data=json.dumps(payload).encode("utf-8"),
    headers={"Content-Type": "application/json"},
    method="POST",
)
try:
    with urllib.request.urlopen(request, timeout=30) as response:
        result = json.loads(response.read().decode("utf-8"))
    print("smoke_booking_created", bool(result.get("reference")))
    print("smoke_checkout_url_present", bool(result.get("checkoutUrl")))
    if result.get("payment", {}).get("error"):
        print("smoke_payment_error", result["payment"]["error"][:180])
except urllib.error.HTTPError as error:
    try:
        result = json.loads(error.read().decode("utf-8"))
    except Exception:
        result = {"error": "Could not parse smoke test response"}
    print("smoke_booking_created", False)
    print("smoke_error", result)
finally:
    with sqlite3.connect("/var/lib/menwithvan/bookings.sqlite3") as db:
        db.execute("DELETE FROM bookings WHERE customer_email = ?", ("deployment-smoke-test@example.com",))
        print("smoke_test_cleaned", True)
PY

echo "Backup saved at: $backup"
echo "Checkout fix deployed."
REMOTE
