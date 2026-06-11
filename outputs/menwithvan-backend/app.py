#!/usr/bin/env python3
import base64
import csv
import hashlib
import html
import hmac
import io
import json
import math
import os
import re
import smtplib
import sqlite3
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta
from email.message import EmailMessage
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


HOST = os.environ.get("QUOTE_HOST", "127.0.0.1")
PORT = int(os.environ.get("QUOTE_PORT", "3020"))
DB_PATH = os.environ.get("BOOKING_DB_PATH", "/var/lib/menwithvan/bookings.sqlite3")
ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
VAT_RATE = float(os.environ.get("VAT_RATE", "0.20"))
MINIMUM_HOURS = float(os.environ.get("MINIMUM_BOOKING_HOURS", "2"))
MILEAGE_RATE = float(os.environ.get("MILEAGE_RATE_EX_VAT", "2.00"))
STAIR_RATE = float(os.environ.get("STAIR_RATE_PER_FLOOR_PER_MAN_EX_VAT", "15.00"))
CONGESTION_FEE = float(os.environ.get("CONGESTION_FEE_EX_VAT", "27.00"))
MAX_VANS = int(os.environ.get("MAX_BOOKABLE_LUTON_VANS", "5"))
MAX_ADDITIONAL_STOPS = int(os.environ.get("MAX_ADDITIONAL_STOPS", "5"))
GOOGLE_KEY = os.environ.get("GOOGLE_DISTANCE_MATRIX_API_KEY") or os.environ.get("GOOGLE_MAPS_API_KEY")
GOOGLE_GEOCODING_KEY = os.environ.get("GOOGLE_GEOCODING_API_KEY") or GOOGLE_KEY
SITE_BASE_URL = os.environ.get("SITE_BASE_URL", "https://www.menwithvan.com").rstrip("/")
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
STRIPE_API_VERSION = os.environ.get("STRIPE_API_VERSION", "2026-02-25.clover")
SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
SMTP_FROM = os.environ.get("SMTP_FROM") or SMTP_USER
OFFICE_EMAIL = os.environ.get("OFFICE_EMAIL", "")

ONE_VAN_RATES = {
    1: 50.0,
    2: 65.0,
    3: 80.0,
}

PAYMENT_OPTIONS = {"deposit", "full"}
BOOKING_STATUSES = {"new", "confirmed", "completed", "cancelled"}
PAYMENT_STATUSES = {"pending", "deposit_pending", "full_pending", "deposit_paid", "paid", "balance_due", "refunded"}

# Approximate point-in-zone boundary based on the official TfL Congestion Charge
# zone map. Business rule: only pickup or drop-off inside the zone triggers the
# charge. Routes that merely pass through the zone do not.
CONGESTION_ZONE_POLYGON = [
    (51.5208, -0.1700),
    (51.5221, -0.1603),
    (51.5268, -0.1425),
    (51.5308, -0.1246),
    (51.5300, -0.0985),
    (51.5255, -0.0873),
    (51.5215, -0.0740),
    (51.5135, -0.0710),
    (51.5050, -0.0755),
    (51.5025, -0.0784),
    (51.4946, -0.0990),
    (51.4865, -0.1210),
    (51.4869, -0.1275),
    (51.4905, -0.1410),
    (51.4975, -0.1458),
    (51.5035, -0.1527),
    (51.5130, -0.1595),
]


def money(value):
    return round(float(value) + 1e-9, 2)


def pence(value):
    return int(round(float(value) * 100))


def first_int(value, default=0):
    if isinstance(value, (int, float)):
        return int(value)
    match = re.search(r"\d+", str(value or ""))
    return int(match.group(0)) if match else default


def clean_postcode(value):
    return re.sub(r"\s+", " ", str(value or "").strip().upper())


def location_query(value):
    value = clean_postcode(value)
    if not value:
        return ""
    if "," in value:
        return value
    return f"{value}, UK"


def clean_postcode_list(values):
    if not isinstance(values, list):
        return []
    stops = []
    for value in values:
        postcode = clean_postcode(value)
        if postcode:
            stops.append(postcode)
    return stops


def json_response(handler, status, payload):
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def html_response(handler, status, body):
    encoded = body.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(encoded)))
    handler.end_headers()
    handler.wfile.write(encoded)


def redirect_response(handler, location):
    handler.send_response(303)
    handler.send_header("Location", location)
    handler.end_headers()


def read_json(handler, limit=50_000):
    length = min(int(handler.headers.get("Content-Length", "0")), limit)
    return json.loads(handler.rfile.read(length).decode("utf-8") or "{}")


def read_body(handler, limit=1_000_000):
    length = min(int(handler.headers.get("Content-Length", "0")), limit)
    return handler.rfile.read(length)


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def compact(value, limit=500):
    value = re.sub(r"\s+", " ", str(value or "").strip())
    return value[:limit]


def email_like(value):
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", str(value or "").strip()))


def connect_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    return db


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with connect_db() as db:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS bookings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                reference TEXT UNIQUE NOT NULL,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL,
                payment_status TEXT NOT NULL,
                payment_option TEXT NOT NULL,
                customer_name TEXT NOT NULL,
                customer_email TEXT NOT NULL,
                customer_phone TEXT NOT NULL,
                move_type TEXT,
                move_date TEXT,
                move_time TEXT,
                pickup_postcode TEXT,
                delivery_postcode TEXT,
                pickup_address TEXT,
                delivery_address TEXT,
                luton_vans INTEGER,
                movers INTEGER,
                estimated_hours REAL,
                pickup_stairs INTEGER,
                delivery_stairs INTEGER,
                distance_miles REAL,
                subtotal_ex_vat REAL,
                vat REAL,
                total_inc_vat REAL,
                deposit_amount REAL,
                balance_amount REAL,
                item_notes TEXT,
                access_notes TEXT,
                additional_addresses TEXT,
                stripe_checkout_session_id TEXT,
                stripe_payment_intent_id TEXT,
                stripe_payment_amount INTEGER,
                stripe_payment_currency TEXT,
                stripe_payment_url TEXT,
                paid_at TEXT,
                confirmation_email_sent_at TEXT,
                office_email_sent_at TEXT,
                calendar_token TEXT,
                quote_json TEXT NOT NULL
            )
            """
        )
        existing = {row["name"] for row in db.execute("PRAGMA table_info(bookings)").fetchall()}
        migrations = {
            "stripe_checkout_session_id": "TEXT",
            "stripe_payment_intent_id": "TEXT",
            "stripe_payment_amount": "INTEGER",
            "stripe_payment_currency": "TEXT",
            "stripe_payment_url": "TEXT",
            "paid_at": "TEXT",
            "additional_addresses": "TEXT",
            "confirmation_email_sent_at": "TEXT",
            "office_email_sent_at": "TEXT",
            "calendar_token": "TEXT",
        }
        for column, column_type in migrations.items():
            if column not in existing:
                db.execute(f"ALTER TABLE bookings ADD COLUMN {column} {column_type}")
        db.execute("CREATE INDEX IF NOT EXISTS idx_bookings_created ON bookings(created_at)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_bookings_reference ON bookings(reference)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_bookings_stripe_session ON bookings(stripe_checkout_session_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_bookings_calendar_token ON bookings(calendar_token)")


def new_reference():
    return f"MWV-{time.strftime('%y%m%d')}-{uuid.uuid4().hex[:6].upper()}"


def row_to_booking(row):
    return {key: row[key] for key in row.keys()}


def admin_authorised(handler):
    if not ADMIN_PASSWORD:
        return False
    auth = handler.headers.get("Authorization", "")
    if not auth.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(auth.split(" ", 1)[1]).decode("utf-8")
        username, password = decoded.split(":", 1)
    except Exception:
        return False
    return hmac.compare_digest(username, ADMIN_USER) and hmac.compare_digest(password, ADMIN_PASSWORD)


def require_admin(handler):
    if admin_authorised(handler):
        return True
    handler.send_response(401)
    handler.send_header("WWW-Authenticate", 'Basic realm="Men With a Van Admin"')
    handler.send_header("Content-Type", "text/plain; charset=utf-8")
    handler.end_headers()
    handler.wfile.write(b"Admin login required.")
    return False


def stripe_enabled():
    return STRIPE_SECRET_KEY.startswith("sk_")


def stripe_request(method, path, params):
    if not stripe_enabled():
        raise RuntimeError("Stripe secret key is not configured.")

    body = urllib.parse.urlencode(params).encode("utf-8") if params else None
    request = urllib.request.Request(
        f"https://api.stripe.com/v1{path}",
        data=body if method == "POST" else None,
        method=method,
    )
    request.add_header("Authorization", f"Bearer {STRIPE_SECRET_KEY}")
    request.add_header("Stripe-Version", STRIPE_API_VERSION)
    if method == "POST":
        request.add_header("Content-Type", "application/x-www-form-urlencoded")

    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        body_text = error.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(body_text)
            message = payload.get("error", {}).get("message") or body_text
        except Exception:
            message = body_text
        raise RuntimeError(f"Stripe request failed: {message}") from error


def create_stripe_checkout_session(reference, customer_email, payment_option, quote, move_date, move_time):
    totals = quote["totals"]
    amount = totals["deposit25"] if payment_option == "deposit" else totals["totalIncVat"]
    amount_pence = pence(amount)
    payment_label = "25% deposit" if payment_option == "deposit" else "full payment"
    overtime = quote.get("overtime", {})
    move_summary = (
        f"{quote['inputs']['lutonVans']} Luton van(s), "
        f"{quote['inputs']['movers']} mover(s), "
        f"{quote['inputs']['hours']:g} hour(s)"
    )
    description = (
        f"{move_summary}. Booked for {move_date} at {move_time}. "
        f"VAT included. Overtime after booked hours: £{overtime.get('hourlyRateIncVat', 0):g}/hour inc VAT."
    )
    params = {
        "mode": "payment",
        "client_reference_id": reference,
        "customer_email": customer_email,
        "success_url": f"{SITE_BASE_URL}/payment-success.html?session_id={{CHECKOUT_SESSION_ID}}",
        "cancel_url": f"{SITE_BASE_URL}/payment-cancelled.html?ref={urllib.parse.quote(reference)}",
        "line_items[0][quantity]": "1",
        "line_items[0][price_data][currency]": "gbp",
        "line_items[0][price_data][unit_amount]": str(amount_pence),
        "line_items[0][price_data][product_data][name]": f"Men With a Van booking {reference} - {payment_label}",
        "line_items[0][price_data][product_data][description]": description[:500],
        "metadata[booking_reference]": reference,
        "metadata[payment_option]": payment_option,
        "metadata[move_date]": move_date,
        "metadata[move_time]": move_time,
        "payment_intent_data[receipt_email]": customer_email,
        "payment_intent_data[metadata][booking_reference]": reference,
        "payment_intent_data[metadata][payment_option]": payment_option,
    }
    session = stripe_request("POST", "/checkout/sessions", params)
    return {
        "id": session.get("id"),
        "url": session.get("url"),
        "payment_intent": session.get("payment_intent"),
        "amount_total": session.get("amount_total") or amount_pence,
        "currency": session.get("currency") or "gbp",
    }


def verify_stripe_signature(payload, signature_header):
    if not STRIPE_WEBHOOK_SECRET or not signature_header:
        return False
    parts = {}
    signatures = []
    for item in signature_header.split(","):
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        if key == "v1":
            signatures.append(value)
        else:
            parts[key] = value
    timestamp = parts.get("t")
    if not timestamp or not signatures:
        return False
    try:
        if abs(time.time() - int(timestamp)) > 300:
            return False
    except ValueError:
        return False
    signed_payload = timestamp.encode("utf-8") + b"." + payload
    expected = hmac.new(
        STRIPE_WEBHOOK_SECRET.encode("utf-8"),
        signed_payload,
        hashlib.sha256,
    ).hexdigest()
    return any(hmac.compare_digest(expected, signature) for signature in signatures)


def update_booking_with_stripe_session(reference, session):
    with connect_db() as db:
        db.execute(
            """
            UPDATE bookings
            SET stripe_checkout_session_id = ?,
                stripe_payment_intent_id = ?,
                stripe_payment_amount = ?,
                stripe_payment_currency = ?,
                stripe_payment_url = ?
            WHERE reference = ?
            """,
            (
                session.get("id"),
                session.get("payment_intent"),
                session.get("amount_total"),
                session.get("currency"),
                session.get("url"),
                reference,
            ),
        )


def booking_start_end(row):
    date_text = row["move_date"] or ""
    time_text = row["move_time"] or "08:00"
    hours = float(row["estimated_hours"] or MINIMUM_HOURS)
    try:
        start = datetime.strptime(f"{date_text} {time_text}", "%Y-%m-%d %H:%M")
    except ValueError:
        start = datetime.utcnow()
    end = start + timedelta(hours=hours)
    return start, end


def calendar_datetime(value):
    return value.strftime("%Y%m%dT%H%M%S")


def calendar_escape(value):
    return str(value or "").replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;").replace("\n", "\\n")


def booking_calendar_summary(row):
    return f"Men With a Van booking {row['reference']}"


def booking_calendar_description(row):
    overtime_rate = row["total_inc_vat"]
    try:
        quote = json.loads(row["quote_json"] or "{}")
        overtime_rate = quote.get("overtime", {}).get("hourlyRateIncVat", overtime_rate)
    except Exception:
        pass
    return (
        f"Booking reference: {row['reference']}\n"
        f"{row['luton_vans']} Luton van(s), {row['movers']} mover(s), {row['estimated_hours']:g} booked hour(s).\n"
        f"Total including VAT: £{row['total_inc_vat']:.2f}.\n"
        f"Overtime after booked hours: £{float(overtime_rate):.2f} per extra hour or part-hour, payable on completion.\n"
        f"Pickup: {row['pickup_address']}\n"
        f"Delivery: {row['delivery_address']}"
    )


def google_calendar_url(row):
    start, end = booking_start_end(row)
    params = urllib.parse.urlencode(
        {
            "action": "TEMPLATE",
            "text": booking_calendar_summary(row),
            "dates": f"{calendar_datetime(start)}/{calendar_datetime(end)}",
            "details": booking_calendar_description(row),
            "location": row["pickup_address"] or row["pickup_postcode"],
        }
    )
    return f"https://calendar.google.com/calendar/render?{params}"


def booking_ics(row):
    start, end = booking_start_end(row)
    uid = f"{row['reference']}@menwithvan.com"
    return "\r\n".join(
        [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//Men With a Van//Booking//EN",
            "CALSCALE:GREGORIAN",
            "METHOD:PUBLISH",
            "BEGIN:VEVENT",
            f"UID:{calendar_escape(uid)}",
            f"DTSTAMP:{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}",
            f"DTSTART;TZID=Europe/London:{calendar_datetime(start)}",
            f"DTEND;TZID=Europe/London:{calendar_datetime(end)}",
            f"SUMMARY:{calendar_escape(booking_calendar_summary(row))}",
            f"LOCATION:{calendar_escape(row['pickup_address'] or row['pickup_postcode'])}",
            f"DESCRIPTION:{calendar_escape(booking_calendar_description(row))}",
            "END:VEVENT",
            "END:VCALENDAR",
            "",
        ]
    ).encode("utf-8")


def calendar_booking(reference, token):
    with connect_db() as db:
        return db.execute(
            "SELECT * FROM bookings WHERE reference = ? AND calendar_token = ?",
            (reference, token),
        ).fetchone()


def public_payment_session(session_id):
    with connect_db() as db:
        row = db.execute(
            """
            SELECT reference, status, payment_status, payment_option, move_date,
                   move_time, pickup_postcode, delivery_postcode, pickup_address,
                   delivery_address, luton_vans, movers, estimated_hours,
                   total_inc_vat, deposit_amount, balance_amount,
                   stripe_payment_amount, stripe_payment_currency, paid_at,
                   calendar_token, quote_json
            FROM bookings
            WHERE stripe_checkout_session_id = ?
            """,
            (session_id,),
        ).fetchone()
    if not row:
        return None
    return {
        "reference": row["reference"],
        "status": row["status"],
        "paymentStatus": row["payment_status"],
        "paymentOption": row["payment_option"],
        "moveDate": row["move_date"],
        "totalIncVat": row["total_inc_vat"],
        "depositAmount": row["deposit_amount"],
        "balanceAmount": row["balance_amount"],
        "stripePaymentAmount": row["stripe_payment_amount"],
        "stripePaymentCurrency": row["stripe_payment_currency"],
        "paidAt": row["paid_at"],
        "calendar": {
            "icsUrl": f"/api/bookings/{row['reference']}/calendar.ics?token={row['calendar_token']}",
            "googleUrl": google_calendar_url(row),
        },
    }


def email_enabled():
    return bool(SMTP_HOST and SMTP_FROM)


def send_email(to_address, subject, text_body, html_body=None):
    if not email_enabled() or not to_address:
        return False

    message = EmailMessage()
    message["From"] = SMTP_FROM
    message["To"] = to_address
    message["Subject"] = subject
    message.set_content(text_body)
    if html_body:
        message.add_alternative(html_body, subtype="html")

    context = ssl.create_default_context()
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as smtp:
        smtp.starttls(context=context)
        if SMTP_USER and SMTP_PASSWORD:
            smtp.login(SMTP_USER, SMTP_PASSWORD)
        smtp.send_message(message)
    return True


def payment_wording(row):
    if row["payment_option"] == "deposit":
        return (
            f"Deposit paid: £{row['deposit_amount']:.2f}. "
            f"Balance due on completion: £{row['balance_amount']:.2f}."
        )
    return f"Paid in full: £{row['total_inc_vat']:.2f}."


def render_confirmation_email(row):
    try:
        quote = json.loads(row["quote_json"] or "{}")
    except Exception:
        quote = {}
    try:
        additional_addresses = json.loads(row["additional_addresses"] or "[]")
    except Exception:
        additional_addresses = []

    line_items = quote.get("lineItems") or []
    item_lines = "\n".join(
        f"- {item.get('label')}: £{float(item.get('amountExVat') or 0):.2f} ex VAT"
        for item in line_items
    )
    extra_lines = "\n".join(
        f"- {item.get('postcode', '')}: {item.get('address', '')}".strip()
        for item in additional_addresses
    ) or "None"
    payment_text = payment_wording(row)
    overtime_rate = quote.get("overtime", {}).get("hourlyRateIncVat", 0)
    ics_url = f"{SITE_BASE_URL}/api/bookings/{row['reference']}/calendar.ics?token={row['calendar_token']}"
    google_url = google_calendar_url(row)

    text_body = f"""Hello {row['customer_name']},

Thank you for booking Men With a Van. Your move is confirmed for the date and arrival time below.

Booking reference: {row['reference']}
Move date/time: {row['move_date']} / {row['move_time']}
Pickup: {row['pickup_address']}
Additional stops:
{extra_lines}
Delivery: {row['delivery_address']}

Move summary:
- {row['luton_vans']} Luton van(s)
- {row['movers']} mover(s)
- {row['estimated_hours']:g} estimated hour(s)
- Route distance: {row['distance_miles']:g} miles

Quote breakdown:
{item_lines}
- VAT: £{row['vat']:.2f}
- Total including VAT: £{row['total_inc_vat']:.2f}

Payment:
{payment_text}

Overtime:
If the move runs beyond the booked {row['estimated_hours']:g} hour(s), overtime is charged at £{float(overtime_rate):.2f} per extra hour or part-hour and is payable to the driver on completion.

Calendar:
Apple/Outlook/Android calendar file: {ics_url}
Google Calendar: {google_url}

Important notes:
- Your selected date and arrival time are confirmed once Stripe payment clears.
- Please make sure parking, lifts, building access and any concierge/loading bay arrangements are confirmed before the move.
- Stripe will send the official payment receipt for the card payment.

Men With a Van
"""

    html_rows = "".join(
        f"<tr><td>{html.escape(item.get('label', ''))}</td><td>£{float(item.get('amountExVat') or 0):.2f} ex VAT</td></tr>"
        for item in line_items
    )
    html_extra = "".join(
        f"<li>{html.escape((item.get('postcode', '') + ' ' + item.get('address', '')).strip())}</li>"
        for item in additional_addresses
    ) or "<li>None</li>"
    html_body = f"""<!doctype html>
<html><body style="font-family:Arial,sans-serif;color:#122034;line-height:1.5">
  <h1 style="color:#0f2d3a">Booking confirmation</h1>
  <p>Hello {html.escape(row['customer_name'])},</p>
  <p>Thank you for booking Men With a Van. Your move is confirmed and your booking reference is <strong>{html.escape(row['reference'])}</strong>.</p>
  <h2>Move details</h2>
  <p><strong>Date/time:</strong> {html.escape(row['move_date'] or '')} / {html.escape(row['move_time'] or '')}</p>
  <p><strong>Pickup:</strong> {html.escape(row['pickup_address'] or '')}</p>
  <p><strong>Delivery:</strong> {html.escape(row['delivery_address'] or '')}</p>
  <p><strong>Additional stops:</strong></p><ul>{html_extra}</ul>
  <p><strong>Team:</strong> {row['luton_vans']} Luton van(s), {row['movers']} mover(s), {row['estimated_hours']:g} estimated hour(s)</p>
  <h2>Price</h2>
  <table cellpadding="8" cellspacing="0" border="1" style="border-collapse:collapse;border-color:#dce3eb">{html_rows}
    <tr><td>VAT</td><td>£{row['vat']:.2f}</td></tr>
    <tr><td><strong>Total including VAT</strong></td><td><strong>£{row['total_inc_vat']:.2f}</strong></td></tr>
  </table>
  <h2>Payment</h2>
  <p>{html.escape(payment_text)}</p>
  <h2>Overtime</h2>
  <p>If the move runs beyond the booked {row['estimated_hours']:g} hour(s), overtime is charged at <strong>£{float(overtime_rate):.2f} per extra hour or part-hour</strong> and is payable to the driver on completion.</p>
  <h2>Add to calendar</h2>
  <p><a href="{html.escape(ics_url)}">Apple / Outlook / Android calendar file</a></p>
  <p><a href="{html.escape(google_url)}">Add to Google Calendar</a></p>
  <p>Stripe will send the official card payment receipt separately.</p>
  <p>Please make sure parking, lifts, building access and any concierge/loading bay arrangements are ready before the move.</p>
  <p>Men With a Van</p>
</body></html>"""
    return text_body, html_body


def send_booking_confirmations(reference, force_customer=False, force_office=False):
    result = {"customer": "skipped", "office": "skipped", "errors": []}
    if not email_enabled():
        result["errors"].append("Email is not configured.")
        return result
    with connect_db() as db:
        row = db.execute("SELECT * FROM bookings WHERE reference = ?", (reference,)).fetchone()
        if not row:
            result["errors"].append("Booking not found.")
            return result
        customer_sent = bool(row["confirmation_email_sent_at"])
        office_sent = bool(row["office_email_sent_at"])
        text_body, html_body = render_confirmation_email(row)
        if force_customer or not customer_sent:
            try:
                if send_email(row["customer_email"], f"Men With a Van booking {reference}", text_body, html_body):
                    db.execute("UPDATE bookings SET confirmation_email_sent_at = ? WHERE reference = ?", (now_iso(), reference))
                    result["customer"] = "sent"
                else:
                    result["customer"] = "not_sent"
            except Exception as error:
                result["customer"] = "failed"
                result["errors"].append(f"Customer email failed: {error}")
                print(f"Customer confirmation email failed for {reference}: {error}")
        else:
            result["customer"] = "already_sent"
        if OFFICE_EMAIL and (force_office or not office_sent):
            try:
                office_text = "New paid booking received.\n\n" + text_body
                if send_email(OFFICE_EMAIL, f"Paid booking {reference}", office_text, html_body):
                    db.execute("UPDATE bookings SET office_email_sent_at = ? WHERE reference = ?", (now_iso(), reference))
                    result["office"] = "sent"
                else:
                    result["office"] = "not_sent"
            except Exception as error:
                result["office"] = "failed"
                result["errors"].append(f"Office email failed: {error}")
                print(f"Office confirmation email failed for {reference}: {error}")
        elif OFFICE_EMAIL and office_sent:
            result["office"] = "already_sent"
        elif not OFFICE_EMAIL:
            result["office"] = "not_configured"
    return result


def handle_stripe_event(event):
    event_type = event.get("type")
    obj = (event.get("data") or {}).get("object") or {}
    if event_type not in {"checkout.session.completed", "checkout.session.async_payment_succeeded"}:
        return

    reference = obj.get("client_reference_id") or (obj.get("metadata") or {}).get("booking_reference")
    session_id = obj.get("id")
    payment_intent = obj.get("payment_intent")
    amount_total = obj.get("amount_total")
    currency = obj.get("currency")
    if not reference:
        return

    with connect_db() as db:
        row = db.execute("SELECT payment_option FROM bookings WHERE reference = ?", (reference,)).fetchone()
        if not row:
            return
        payment_status = "deposit_paid" if row["payment_option"] == "deposit" else "paid"
        db.execute(
            """
            UPDATE bookings
            SET status = 'confirmed',
                payment_status = ?,
                stripe_checkout_session_id = COALESCE(?, stripe_checkout_session_id),
                stripe_payment_intent_id = COALESCE(?, stripe_payment_intent_id),
                stripe_payment_amount = COALESCE(?, stripe_payment_amount),
                stripe_payment_currency = COALESCE(?, stripe_payment_currency),
                paid_at = COALESCE(paid_at, ?)
            WHERE reference = ?
            """,
            (payment_status, session_id, payment_intent, amount_total, currency, now_iso(), reference),
        )
    send_booking_confirmations(reference)


def google_distance_miles(pickup, delivery):
    if not pickup or not delivery:
        return None, "Enter pickup and delivery postcodes."
    if not GOOGLE_KEY:
        return None, "Distance service is not configured."

    params = urllib.parse.urlencode(
        {
            "origins": location_query(pickup),
            "destinations": location_query(delivery),
            "units": "imperial",
            "region": "uk",
            "key": GOOGLE_KEY,
        }
    )
    url = f"https://maps.googleapis.com/maps/api/distancematrix/json?{params}"

    try:
        with urllib.request.urlopen(url, timeout=8) as response:
            data = json.loads(response.read().decode("utf-8"))
    except Exception:
        return None, "Distance lookup failed. Please check the postcodes."

    if data.get("status") != "OK":
        return None, "Distance lookup was not accepted by Google."

    rows = data.get("rows") or []
    elements = rows[0].get("elements") if rows else []
    element = elements[0] if elements else {}
    if element.get("status") != "OK":
        return None, "No driving route was found for those postcodes."

    meters = element.get("distance", {}).get("value")
    if not meters:
        return None, "Google did not return a route distance."

    miles = meters / 1609.344
    return round(miles, 1), None


def google_route_miles(stops):
    if len(stops) < 2:
        return None, [], "Enter pickup and delivery postcodes."

    total = 0.0
    legs = []
    for origin, destination in zip(stops, stops[1:]):
        miles, error = google_distance_miles(origin, destination)
        if error:
            return None, [], error
        total += miles
        legs.append({"from": origin, "to": destination, "miles": miles})

    return round(total, 1), legs, None


def google_geocode(value):
    if not value or not GOOGLE_GEOCODING_KEY:
        return None, "Geocoding is not configured."

    params = urllib.parse.urlencode(
        {
            "address": location_query(value),
            "region": "uk",
            "key": GOOGLE_GEOCODING_KEY,
        }
    )
    url = f"https://maps.googleapis.com/maps/api/geocode/json?{params}"

    try:
        with urllib.request.urlopen(url, timeout=8) as response:
            data = json.loads(response.read().decode("utf-8"))
    except Exception:
        return None, "Geocoding lookup failed."

    if data.get("status") != "OK" or not data.get("results"):
        return None, "Geocoding did not return a location."

    location = data["results"][0].get("geometry", {}).get("location", {})
    lat = location.get("lat")
    lng = location.get("lng")
    if lat is None or lng is None:
        return None, "Geocoding did not return coordinates."
    return (float(lat), float(lng)), None


def google_address_hint(value):
    postcode = clean_postcode(value)
    if not postcode or not GOOGLE_GEOCODING_KEY:
        return {"input": postcode, "formatted": postcode, "source": "postcode"}

    params = urllib.parse.urlencode(
        {
            "address": location_query(postcode),
            "region": "uk",
            "key": GOOGLE_GEOCODING_KEY,
        }
    )
    url = f"https://maps.googleapis.com/maps/api/geocode/json?{params}"

    try:
        with urllib.request.urlopen(url, timeout=8) as response:
            data = json.loads(response.read().decode("utf-8"))
    except Exception:
        return {"input": postcode, "formatted": postcode, "source": "postcode"}

    if data.get("status") != "OK" or not data.get("results"):
        return {"input": postcode, "formatted": postcode, "source": "postcode"}

    formatted = compact(data["results"][0].get("formatted_address") or postcode, 240)
    return {
        "input": postcode,
        "formatted": formatted or postcode,
        "source": "google_geocoding" if formatted else "postcode",
    }


def point_in_polygon(point, polygon):
    lat, lng = point
    inside = False
    j = len(polygon) - 1
    for i in range(len(polygon)):
        lat_i, lng_i = polygon[i]
        lat_j, lng_j = polygon[j]
        crosses = (lng_i > lng) != (lng_j > lng)
        if crosses:
            intersect_lat = (lat_j - lat_i) * (lng - lng_i) / (lng_j - lng_i) + lat_i
            if lat < intersect_lat:
                inside = not inside
        j = i
    return inside


def congestion_zone_status(pickup, delivery, additional_stops=None):
    additional_stops = additional_stops or []
    details = {
        "pickupInside": False,
        "deliveryInside": False,
        "additionalStops": [],
        "checked": False,
        "note": "Congestion zone check unavailable; office will review.",
    }

    stop_points = []
    for stop in [pickup] + additional_stops + [delivery]:
        point, error = google_geocode(stop)
        if error:
            return False, details
        stop_points.append((stop, point))

    inside_results = [(stop, point_in_polygon(point, CONGESTION_ZONE_POLYGON)) for stop, point in stop_points]
    pickup_inside = inside_results[0][1]
    delivery_inside = inside_results[-1][1]
    additional_results = [
        {"postcode": stop, "inside": inside}
        for stop, inside in inside_results[1:-1]
    ]
    details.update(
        {
            "pickupInside": pickup_inside,
            "deliveryInside": delivery_inside,
            "additionalStops": additional_results,
            "checked": True,
            "note": "Applied if any pickup, delivery or additional stop is inside the Congestion Charge zone.",
        }
    )
    return any(inside for _, inside in inside_results), details


def hourly_rate_for(vans, movers):
    if vans == 1 and movers in ONE_VAN_RATES:
        return ONE_VAN_RATES[movers], "confirmed"

    # Fleet formula: every Luton van includes one mover at £50/hr; each additional
    # mover above one per van adds the same £15/hr uplift used in one-van pricing.
    base = vans * ONE_VAN_RATES[1]
    extra_movers = max(0, movers - vans)
    rate = base + (extra_movers * 15)
    return rate, "confirmed"


def build_quote(payload):
    vans = first_int(payload.get("lutonVans") or payload.get("luton-vans") or payload.get("vans"), 1)
    movers = first_int(payload.get("movers"), 2)
    hours = max(float(first_int(payload.get("hours") or payload.get("estimatedHours"), MINIMUM_HOURS)), MINIMUM_HOURS)
    pickup_stairs = first_int(payload.get("pickupStairs") or payload.get("pickup-stairs"), 0)
    delivery_stairs = first_int(payload.get("deliveryStairs") or payload.get("delivery-stairs"), 0)
    pickup = clean_postcode(payload.get("pickup") or payload.get("pickupPostcode"))
    delivery = clean_postcode(payload.get("delivery") or payload.get("deliveryPostcode"))
    additional_stops = clean_postcode_list(payload.get("additionalStops") or payload.get("additional-stops") or [])

    errors = []
    if vans < 1 or vans > MAX_VANS:
        errors.append(f"Choose between 1 and {MAX_VANS} Luton vans.")
    if movers < vans:
        errors.append("Choose at least one mover per Luton van.")
    max_movers_for_vans = min(15, vans * 3)
    if movers > max_movers_for_vans:
        errors.append(f"Choose no more than {max_movers_for_vans} movers for {vans} Luton van{'s' if vans != 1 else ''}. The online maximum is 5 vans and 15 men.")
    if not pickup:
        errors.append("Pickup postcode is required.")
    if not delivery:
        errors.append("Delivery postcode is required.")
    if len(additional_stops) > MAX_ADDITIONAL_STOPS:
        errors.append(f"Add no more than {MAX_ADDITIONAL_STOPS} additional stops online.")
    if errors:
        return None, errors

    route_stops = [pickup] + additional_stops + [delivery]
    distance_miles, route_legs, distance_error = google_route_miles(route_stops)
    if distance_error:
        return None, [distance_error]

    hourly_rate, pricing_status = hourly_rate_for(vans, movers)
    hourly_subtotal = hourly_rate * hours
    mileage_subtotal = distance_miles * MILEAGE_RATE
    stairs_flights = pickup_stairs + delivery_stairs
    stairs_subtotal = stairs_flights * movers * STAIR_RATE

    congestion_applied, congestion_details = congestion_zone_status(pickup, delivery, additional_stops)
    congestion_subtotal = CONGESTION_FEE if congestion_applied else 0
    address_hints = {
        "pickup": google_address_hint(pickup),
        "delivery": google_address_hint(delivery),
        "additionalStops": [google_address_hint(stop) for stop in additional_stops],
        "note": "Postcode/address hints are prefilled for convenience only. Customers must add door number, flat, building name and any missing access details before booking.",
    }

    subtotal = hourly_subtotal + mileage_subtotal + stairs_subtotal + congestion_subtotal
    vat = subtotal * VAT_RATE
    total = subtotal + vat
    deposit = total * 0.25

    quote = {
        "quoteId": f"MWV-{int(time.time())}",
        "pricingStatus": pricing_status,
        "currency": "GBP",
        "inputs": {
            "pickup": pickup,
            "delivery": delivery,
            "additionalStops": additional_stops,
            "lutonVans": vans,
            "movers": movers,
            "hours": hours,
            "pickupStairs": pickup_stairs,
            "deliveryStairs": delivery_stairs,
        },
        "rates": {
            "hourlyRateExVat": money(hourly_rate),
            "hourlyRateIncVat": money(hourly_rate * (1 + VAT_RATE)),
            "mileageRateExVat": money(MILEAGE_RATE),
            "stairRatePerFloorPerManExVat": money(STAIR_RATE),
            "congestionFeeExVat": money(CONGESTION_FEE),
            "vatRate": VAT_RATE,
            "minimumHours": MINIMUM_HOURS,
        },
        "distance": {
            "miles": distance_miles,
            "roundedBillableMiles": math.ceil(distance_miles),
            "legs": route_legs,
        },
        "congestionZone": congestion_details,
        "addressHints": address_hints,
        "overtime": {
            "hourlyRateExVat": money(hourly_rate),
            "hourlyRateIncVat": money(hourly_rate * (1 + VAT_RATE)),
            "note": "Overtime after the booked hours is charged at the same selected vans/men hourly rate, per extra hour or part-hour, and is payable to the driver on completion.",
        },
        "lineItems": [
            {
                "label": f"{vans} Luton van{'s' if vans != 1 else ''}, {movers} mover{'s' if movers != 1 else ''}, {hours:g} hour{'s' if hours != 1 else ''}",
                "amountExVat": money(hourly_subtotal),
            },
            {
                "label": f"Route mileage ({distance_miles:g} miles across {len(route_legs)} leg{'s' if len(route_legs) != 1 else ''} at £{MILEAGE_RATE:g}/mile)",
                "amountExVat": money(mileage_subtotal),
            },
            {
                "label": f"Stairs ({stairs_flights} flight{'s' if stairs_flights != 1 else ''} × {movers} mover{'s' if movers != 1 else ''})",
                "amountExVat": money(stairs_subtotal),
            },
            {
                "label": "Congestion zone",
                "amountExVat": money(congestion_subtotal),
                "applied": congestion_applied,
                "note": congestion_details["note"],
            },
        ],
        "totals": {
            "subtotalExVat": money(subtotal),
            "vat": money(vat),
            "totalIncVat": money(total),
            "deposit25": money(deposit),
            "balanceAfterDeposit": money(total - deposit),
        },
        "messages": [
            "Minimum booking is two hours.",
            "Once online payment is completed, the selected date and arrival time are booked.",
            f"Overtime after the booked time is £{money(hourly_rate * (1 + VAT_RATE)):.2f} per extra hour or part-hour, payable on completion.",
        ],
    }

    if not congestion_details["checked"]:
        quote["messages"].append("Congestion zone could not be checked automatically; the office will review it.")
    if additional_stops:
        quote["messages"].append(f"Mileage includes {len(additional_stops)} additional stop{'s' if len(additional_stops) != 1 else ''} in the order entered.")

    return quote, None


def create_booking(payload):
    quote_inputs = payload.get("quoteInputs") or payload.get("quote") or {}
    customer = payload.get("customer") or {}
    booking = payload.get("booking") or {}

    quote, quote_errors = build_quote(quote_inputs)
    if quote_errors:
        return None, quote_errors

    name = compact(customer.get("name"), 140)
    email = compact(customer.get("email"), 180).lower()
    phone = compact(customer.get("phone"), 60)
    pickup_address = compact(booking.get("pickupAddress"), 500)
    delivery_address = compact(booking.get("deliveryAddress"), 500)
    additional_postcodes = quote["inputs"].get("additionalStops", [])
    additional_addresses = [
        compact(address, 500)
        for address in (booking.get("additionalAddresses") or [])
        if compact(address, 500)
    ]
    move_date = compact(booking.get("moveDate") or quote_inputs.get("moveDate"), 30)
    move_time = compact(booking.get("moveTime"), 30)
    item_notes = compact(quote_inputs.get("items") or booking.get("itemNotes"), 1500)
    access_notes = compact(booking.get("accessNotes"), 1500)
    payment_option = compact(booking.get("paymentOption"), 20) or "deposit"
    terms_accepted = bool(booking.get("termsAccepted"))

    errors = []
    if not name:
        errors.append("Customer name is required.")
    if not email_like(email):
        errors.append("A valid email address is required.")
    if not phone:
        errors.append("Phone number is required.")
    if not move_date:
        errors.append("Move date is required.")
    if not move_time:
        errors.append("Move time is required.")
    if not pickup_address:
        errors.append("Full pickup address is required.")
    if not delivery_address:
        errors.append("Full delivery address is required.")
    if len(additional_addresses) < len(additional_postcodes):
        errors.append("Full address is required for each additional stop.")
    if payment_option not in PAYMENT_OPTIONS:
        errors.append("Choose deposit or full payment.")
    if not terms_accepted:
        errors.append("Terms must be accepted before booking.")
    if errors:
        return None, errors

    payment_status = "deposit_pending" if payment_option == "deposit" else "full_pending"
    reference = new_reference()
    calendar_token = uuid.uuid4().hex
    inputs = quote["inputs"]
    totals = quote["totals"]
    balance_amount = totals["balanceAfterDeposit"] if payment_option == "deposit" else 0

    with connect_db() as db:
        db.execute(
            """
            INSERT INTO bookings (
                reference, created_at, status, payment_status, payment_option,
                customer_name, customer_email, customer_phone,
                move_type, move_date, move_time,
                pickup_postcode, delivery_postcode, pickup_address, delivery_address,
                luton_vans, movers, estimated_hours, pickup_stairs, delivery_stairs,
                distance_miles, subtotal_ex_vat, vat, total_inc_vat, deposit_amount,
                balance_amount, item_notes, access_notes, additional_addresses, calendar_token, quote_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                reference,
                now_iso(),
                "new",
                payment_status,
                payment_option,
                name,
                email,
                phone,
                compact(quote_inputs.get("moveType"), 80),
                move_date,
                move_time,
                inputs["pickup"],
                inputs["delivery"],
                pickup_address,
                delivery_address,
                inputs["lutonVans"],
                inputs["movers"],
                inputs["hours"],
                inputs["pickupStairs"],
                inputs["deliveryStairs"],
                quote["distance"]["miles"],
                totals["subtotalExVat"],
                totals["vat"],
                totals["totalIncVat"],
                totals["deposit25"],
                balance_amount,
                item_notes,
                access_notes,
                json.dumps(
                    [
                        {"postcode": postcode, "address": additional_addresses[index] if index < len(additional_addresses) else ""}
                        for index, postcode in enumerate(additional_postcodes)
                    ],
                    ensure_ascii=False,
                ),
                calendar_token,
                json.dumps(quote, ensure_ascii=False),
            ),
        )

    result = {
        "reference": reference,
        "status": "new",
        "paymentStatus": payment_status,
        "paymentOption": payment_option,
        "quote": quote,
        "payment": {
            "provider": "stripe",
            "configured": stripe_enabled(),
            "amountDueNow": totals["deposit25"] if payment_option == "deposit" else totals["totalIncVat"],
            "currency": "GBP",
        },
        "calendar": {
            "icsUrl": f"/api/bookings/{reference}/calendar.ics?token={calendar_token}",
        },
        "message": "Booking request received. Continue to payment to confirm the selected moving date and arrival time.",
    }

    if stripe_enabled():
        try:
            session = create_stripe_checkout_session(reference, email, payment_option, quote, move_date, move_time)
            update_booking_with_stripe_session(reference, session)
            result["checkoutUrl"] = session.get("url")
            result["stripeSessionId"] = session.get("id")
            result["message"] = "Booking saved. Continue to secure Stripe checkout to confirm your move."
        except Exception as error:
            result["payment"]["error"] = str(error)
            result["message"] = "Booking request saved, but the payment page could not be created. Please contact the office to complete payment."
    else:
        result["message"] = "Booking request saved. Online Stripe payment is not connected on the server yet, so please contact the office to complete payment."

    return result, None


def render_admin(notice=""):
    with connect_db() as db:
        rows = db.execute("SELECT * FROM bookings ORDER BY id DESC LIMIT 200").fetchall()

    email_state = "Ready" if email_enabled() else "Not configured"
    email_state_class = "ok" if email_enabled() else "warn"
    notice_html = f'<p class="notice">{html.escape(notice)}</p>' if notice else ""
    booking_rows = []
    for row in rows:
        reference = html.escape(row["reference"])
        total = f"£{row['total_inc_vat']:.2f}"
        due_now_value = row["deposit_amount"] if row["payment_option"] == "deposit" else row["total_inc_vat"]
        due_now = f"£{due_now_value:.2f}"
        balance = f"£{row['balance_amount']:.2f}"
        stripe_detail = row["stripe_checkout_session_id"] or "No Stripe session yet"
        paid_at = f"<br><small>Paid {html.escape(row['paid_at'])}</small>" if row["paid_at"] else ""
        try:
            quote = json.loads(row["quote_json"] or "{}")
            overtime_rate = float(quote.get("overtime", {}).get("hourlyRateIncVat") or 0)
        except Exception:
            overtime_rate = 0
        try:
            additional_addresses = json.loads(row["additional_addresses"] or "[]")
        except Exception:
            additional_addresses = []
        extra_stop_text = ""
        if additional_addresses:
            extra_stop_text = "<br><small>Extra stops: " + html.escape(
                "; ".join(
                    f"{item.get('postcode', '')} {item.get('address', '')}".strip()
                    for item in additional_addresses
                )
            ) + "</small>"
        customer_email_state = row["confirmation_email_sent_at"] or "Not sent"
        office_email_state = row["office_email_sent_at"] or "Not sent"
        email_badge_class = "ok" if row["confirmation_email_sent_at"] and row["office_email_sent_at"] else "warn"
        booking_rows.append(
            f"""
            <tr>
              <td><strong>{reference}</strong><br><small>{html.escape(row['created_at'])}</small></td>
              <td>{html.escape(row['customer_name'])}<br><small>{html.escape(row['customer_email'])}<br>{html.escape(row['customer_phone'])}</small></td>
              <td>{html.escape(row['move_date'] or '')}<br><small>{html.escape(row['move_time'] or '')}</small></td>
              <td>{html.escape(row['pickup_postcode'] or '')} → {html.escape(row['delivery_postcode'] or '')}<br><small>{row['luton_vans']} vans, {row['movers']} men, {row['estimated_hours']:g} hrs</small>{extra_stop_text}</td>
              <td>{total}<br><small>Due now {due_now}<br>Balance {balance}<br>Overtime £{overtime_rate:.2f}/hr<br>{html.escape(stripe_detail)}</small>{paid_at}</td>
              <td>
                <form method="post" action="/admin/bookings/{reference}/status">
                  <select name="status">{''.join(f'<option value="{s}" {"selected" if s == row["status"] else ""}>{s}</option>' for s in BOOKING_STATUSES)}</select>
                  <select name="payment_status">{''.join(f'<option value="{s}" {"selected" if s == row["payment_status"] else ""}>{s}</option>' for s in PAYMENT_STATUSES)}</select>
                  <button>Update</button>
                </form>
                <div class="email-status">
                  <span class="badge {email_badge_class}">Email</span>
                  <small>Customer: {html.escape(customer_email_state)}<br>Office: {html.escape(office_email_state)}</small>
                  <form method="post" action="/admin/bookings/{reference}/email">
                    <button type="submit">Send / resend email</button>
                  </form>
                </div>
              </td>
            </tr>
            """
        )

    return f"""<!doctype html>
    <html lang="en-GB">
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <title>Men With a Van Admin</title>
      <style>
        body {{ margin: 0; font-family: system-ui, -apple-system, Segoe UI, sans-serif; color: #122034; background: #f4f8fb; }}
        header {{ padding: 24px 32px; color: #fff; background: #0f2d3a; }}
        main {{ padding: 24px 32px; }}
        table {{ width: 100%; border-collapse: collapse; background: #fff; border: 1px solid #dce3eb; }}
        th, td {{ padding: 12px; border-bottom: 1px solid #dce3eb; text-align: left; vertical-align: top; }}
        th {{ font-size: 13px; color: #68778a; text-transform: uppercase; }}
        small {{ color: #68778a; }}
        select, button {{ margin: 0 4px 6px 0; padding: 8px; border: 1px solid #dce3eb; border-radius: 6px; }}
        button {{ color: #fff; background: #0b5d7a; cursor: pointer; }}
        .actions {{ margin-bottom: 18px; }}
        .actions a {{ color: #0b5d7a; font-weight: 700; }}
        .notice {{ padding: 12px 14px; background: #fff9db; border: 1px solid #f0d878; border-radius: 8px; }}
        .status-panel {{ display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 18px; padding: 14px; background: #fff; border: 1px solid #dce3eb; border-radius: 8px; }}
        .status-panel form {{ margin: 0; }}
        .badge {{ display: inline-flex; margin: 8px 0 4px; padding: 3px 8px; border-radius: 999px; font-size: 12px; font-weight: 800; }}
        .badge.ok {{ color: #11623c; background: #daf5e8; }}
        .badge.warn {{ color: #7a4f00; background: #fff0c2; }}
        .email-status form {{ margin-top: 6px; }}
      </style>
    </head>
    <body>
      <header>
        <h1>Men With a Van Admin</h1>
        <p>Bookings, payment status and move requests.</p>
      </header>
      <main>
        {notice_html}
        <section class="status-panel">
          <div>
            <strong>Email confirmations</strong><br>
            <span class="badge {email_state_class}">{email_state}</span>
            <small>Sender: {html.escape(SMTP_FROM or 'Not configured')} · Office copy: {html.escape(OFFICE_EMAIL or 'Not configured')}</small>
          </div>
          <form method="post" action="/admin/email/test">
            <button type="submit">Send test email</button>
          </form>
        </section>
        <p class="actions"><a href="/admin/bookings.csv">Download CSV</a> · <a href="/">View website</a></p>
        <table>
          <thead><tr><th>Reference</th><th>Customer</th><th>Date</th><th>Move</th><th>Total</th><th>Status</th></tr></thead>
          <tbody>{''.join(booking_rows) if booking_rows else '<tr><td colspan="6">No bookings yet.</td></tr>'}</tbody>
        </table>
      </main>
    </body>
    </html>"""


def bookings_csv():
    with connect_db() as db:
        rows = db.execute("SELECT * FROM bookings ORDER BY id DESC").fetchall()
    output = io.StringIO()
    writer = csv.writer(output)
    columns = [
        "reference", "created_at", "status", "payment_status", "payment_option",
        "customer_name", "customer_email", "customer_phone", "move_date", "move_time",
        "pickup_postcode", "delivery_postcode", "luton_vans", "movers",
        "estimated_hours", "distance_miles", "total_inc_vat", "deposit_amount",
        "balance_amount", "additional_addresses", "stripe_checkout_session_id", "stripe_payment_intent_id",
        "stripe_payment_amount", "stripe_payment_currency", "paid_at",
        "confirmation_email_sent_at", "office_email_sent_at"
    ]
    writer.writerow(columns)
    for row in rows:
        writer.writerow([row[col] for col in columns])
    return output.getvalue().encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    server_version = "MenWithVanQuote/1.0"

    def log_message(self, fmt, *args):
        print("%s - %s" % (self.address_string(), fmt % args))

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/health":
            return json_response(self, 200, {"ok": True})
        if path == "/api/payments/session":
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            session_id = compact((query.get("session_id") or [""])[0], 140)
            if not session_id:
                return json_response(self, 400, {"error": "Session ID is required."})
            session = public_payment_session(session_id)
            if not session:
                return json_response(self, 404, {"error": "Payment session not found."})
            return json_response(self, 200, session)
        calendar_match = re.match(r"^/api/bookings/([^/]+)/calendar\.ics$", path)
        if calendar_match:
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            token = compact((query.get("token") or [""])[0], 140)
            row = calendar_booking(calendar_match.group(1), token)
            if not row:
                return json_response(self, 404, {"error": "Calendar invite not found."})
            body = booking_ics(row)
            self.send_response(200)
            self.send_header("Content-Type", "text/calendar; charset=utf-8")
            self.send_header("Content-Disposition", f"attachment; filename={row['reference']}.ics")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path in ("/admin", "/admin/"):
            if not require_admin(self):
                return
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            notice = compact((query.get("notice") or [""])[0], 240)
            return html_response(self, 200, render_admin(notice))
        if path == "/admin/bookings.csv":
            if not require_admin(self):
                return
            body = bookings_csv()
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", "attachment; filename=menwithvan-bookings.csv")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/admin/bookings":
            if not require_admin(self):
                return
            with connect_db() as db:
                rows = db.execute("SELECT * FROM bookings ORDER BY id DESC LIMIT 200").fetchall()
            return json_response(self, 200, {"bookings": [row_to_booking(row) for row in rows]})
        return json_response(self, 404, {"error": "Not found"})

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path

        if path in ("/api/quote", "/api/quotes"):
            try:
                payload = read_json(self)
            except Exception:
                return json_response(self, 400, {"error": "Invalid JSON."})

            quote, errors = build_quote(payload)
            if errors:
                return json_response(self, 400, {"errors": errors})
            return json_response(self, 200, quote)

        if path == "/api/bookings":
            try:
                payload = read_json(self)
            except Exception:
                return json_response(self, 400, {"error": "Invalid JSON."})
            booking, errors = create_booking(payload)
            if errors:
                return json_response(self, 400, {"errors": errors})
            return json_response(self, 201, booking)

        if path == "/api/stripe/webhook":
            body = read_body(self)
            signature = self.headers.get("Stripe-Signature", "")
            if not verify_stripe_signature(body, signature):
                return json_response(self, 400, {"error": "Invalid Stripe signature."})
            try:
                event = json.loads(body.decode("utf-8"))
            except Exception:
                return json_response(self, 400, {"error": "Invalid Stripe event."})
            handle_stripe_event(event)
            return json_response(self, 200, {"received": True})

        status_match = re.match(r"^/admin/bookings/([^/]+)/status$", path)
        if status_match:
            if not require_admin(self):
                return
            length = min(int(self.headers.get("Content-Length", "0")), 5_000)
            form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
            status = compact((form.get("status") or [""])[0], 30)
            payment_status = compact((form.get("payment_status") or [""])[0], 30)
            if status in BOOKING_STATUSES and payment_status in PAYMENT_STATUSES:
                with connect_db() as db:
                    db.execute(
                        "UPDATE bookings SET status = ?, payment_status = ? WHERE reference = ?",
                        (status, payment_status, status_match.group(1)),
                    )
            return redirect_response(self, "/admin")

        email_match = re.match(r"^/admin/bookings/([^/]+)/email$", path)
        if email_match:
            if not require_admin(self):
                return
            reference = compact(urllib.parse.unquote(email_match.group(1)), 80)
            result = send_booking_confirmations(reference, force_customer=True, force_office=True)
            if result["errors"]:
                notice = f"Email issue for {reference}: " + "; ".join(result["errors"])
            else:
                notice = f"Email sent for {reference}. Customer: {result['customer']}. Office: {result['office']}."
            return redirect_response(self, "/admin?" + urllib.parse.urlencode({"notice": notice}))

        if path == "/admin/email/test":
            if not require_admin(self):
                return
            try:
                if not OFFICE_EMAIL:
                    raise RuntimeError("Office email is not configured.")
                sent = send_email(
                    OFFICE_EMAIL,
                    "Men With a Van email test",
                    "This is a test email from the Men With a Van booking system.",
                    "<p>This is a test email from the Men With a Van booking system.</p>",
                )
                notice = "Test email sent to office address." if sent else "Email is not configured."
            except Exception as error:
                notice = f"Test email failed: {error}"
            return redirect_response(self, "/admin?" + urllib.parse.urlencode({"notice": notice}))

        return json_response(self, 404, {"error": "Not found"})


def main():
    init_db()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Quote service listening on {HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
