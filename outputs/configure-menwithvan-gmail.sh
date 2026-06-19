#!/usr/bin/env bash
set -euo pipefail

HOST="${1:-root@194.164.126.253}"
SMTP_EMAIL="${SMTP_EMAIL:-menwithvan4@gmail.com}"

echo "This one-time setup stores Gmail SMTP settings in /etc/menwithvan/quote.env."
echo "Use a Google app password for $SMTP_EMAIL, not the normal Gmail password."
echo
read -r -s -p "Paste Gmail app password for $SMTP_EMAIL: " SMTP_PASSWORD
echo

if [ -z "$SMTP_PASSWORD" ]; then
  echo "No password entered. Nothing changed."
  exit 1
fi

if base64 --help 2>&1 | grep -q -- "-w"; then
  EMAIL_B64="$(printf '%s' "$SMTP_EMAIL" | base64 -w 0)"
  PASSWORD_B64="$(printf '%s' "$SMTP_PASSWORD" | base64 -w 0)"
else
  EMAIL_B64="$(printf '%s' "$SMTP_EMAIL" | base64 | tr -d '\n')"
  PASSWORD_B64="$(printf '%s' "$SMTP_PASSWORD" | base64 | tr -d '\n')"
fi

echo "Updating email settings on $HOST..."
ssh "$HOST" "EMAIL_B64='$EMAIL_B64' PASSWORD_B64='$PASSWORD_B64' bash -s" <<'REMOTE'
set -euo pipefail

SMTP_EMAIL="$(printf '%s' "$EMAIL_B64" | base64 -d)"
SMTP_PASSWORD="$(printf '%s' "$PASSWORD_B64" | base64 -d)"
export SMTP_EMAIL SMTP_PASSWORD

python3 - <<'PY'
import os
import shlex
from pathlib import Path

path = Path("/etc/menwithvan/quote.env")
text = path.read_text() if path.exists() else ""
email = os.environ["SMTP_EMAIL"]
password = "".join(os.environ["SMTP_PASSWORD"].split())

updates = {
    "SMTP_HOST": "smtp.gmail.com",
    "SMTP_PORT": "587",
    "SMTP_USER": email,
    "SMTP_PASSWORD": password,
    "SMTP_FROM": f"Men With Van <{email}>",
    "OFFICE_EMAIL": email,
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

python3 - <<'PY'
from pathlib import Path
text = Path("/etc/menwithvan/quote.env").read_text()
print("smtp_host_configured", "SMTP_HOST=smtp.gmail.com" in text)
print("smtp_user_configured", "SMTP_USER=menwithvan4@gmail.com" in text)
print("smtp_password_present", "SMTP_PASSWORD=" in text and "replace-with" not in text)
print("office_email_configured", "OFFICE_EMAIL=menwithvan4@gmail.com" in text)
PY

set -a
. /etc/menwithvan/quote.env
set +a

python3 - <<'PY'
import os
import smtplib
import ssl
from email.message import EmailMessage

host = os.environ.get("SMTP_HOST", "")
port = int(os.environ.get("SMTP_PORT", "587"))
user = os.environ.get("SMTP_USER", "")
password = os.environ.get("SMTP_PASSWORD", "")
sender = os.environ.get("SMTP_FROM") or user
recipient = os.environ.get("OFFICE_EMAIL") or user

message = EmailMessage()
message["From"] = sender
message["To"] = recipient
message["Subject"] = "Men With Van Gmail SMTP test"
message.set_content("Gmail SMTP is working for the Men With Van booking system.")

try:
    with smtplib.SMTP(host, port, timeout=20) as smtp:
        smtp.starttls(context=ssl.create_default_context())
        smtp.login(user, password)
        smtp.send_message(message)
    print("gmail_smtp_test_sent True")
except smtplib.SMTPAuthenticationError as error:
    print("gmail_smtp_test_sent False")
    print("gmail_smtp_error Authentication failed. Create a new Google app password and run this setup again.")
    raise SystemExit(1) from error
except Exception as error:
    print("gmail_smtp_test_sent False")
    print(f"gmail_smtp_error {error}")
    raise SystemExit(1) from error
PY

echo "Gmail SMTP settings installed."
REMOTE
