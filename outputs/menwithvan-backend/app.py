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
import threading
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
FLEET_LUTON_VANS = int(os.environ.get("FLEET_LUTON_VANS", "15"))
MOVERS_PER_LUTON_VAN = int(os.environ.get("MOVERS_PER_LUTON_VAN", "3"))
MAX_BOOKABLE_MOVERS = MAX_VANS * MOVERS_PER_LUTON_VAN
BOOKING_HOLD_MINUTES = int(os.environ.get("BOOKING_HOLD_MINUTES", "45"))
BOOKING_DRAFT_DAYS = int(os.environ.get("BOOKING_DRAFT_DAYS", "14"))
MAX_ADDITIONAL_STOPS = int(os.environ.get("MAX_ADDITIONAL_STOPS", "5"))
PACKING_SERVICE_MULTIPLIER = float(os.environ.get("PACKING_SERVICE_MULTIPLIER", "0.40"))
OVERTIME_MULTIPLIER = float(os.environ.get("OVERTIME_MULTIPLIER", "1.30"))
GOOGLE_KEY = os.environ.get("GOOGLE_DISTANCE_MATRIX_API_KEY") or os.environ.get("GOOGLE_MAPS_API_KEY")
GOOGLE_GEOCODING_KEY = os.environ.get("GOOGLE_GEOCODING_API_KEY") or GOOGLE_KEY
SITE_BASE_URL = os.environ.get("SITE_BASE_URL", "https://www.menwithvan.com").rstrip("/")
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PUBLISHABLE_KEY = os.environ.get("STRIPE_PUBLISHABLE_KEY", "pk_test_51IEj2JEftWU3gRCLfWJym3fyK1DkeDNVRqN0IC7H2IURNMICaPzCH01nTMKbb7Zqw2VdgipCcTXUTr26ryTzUXl400w5G4Pd93")
STRIPE_API_VERSION = os.environ.get("STRIPE_API_VERSION", "2026-03-25.dahlia")
if STRIPE_API_VERSION in {"", "2026-02-25.clover"}:
    STRIPE_API_VERSION = "2026-03-25.dahlia"
SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
SMTP_FROM = os.environ.get("SMTP_FROM") or SMTP_USER
OFFICE_EMAIL = os.environ.get("OFFICE_EMAIL", "")
ADMIN_CSRF_COOKIE = "mwv_admin_csrf"
PLACEHOLDER_ADMIN_PASSWORDS = {
    "",
    "admin",
    "password",
    "changeme",
    "change-me",
    "replace-me",
    "replace-with-long-random-password",
}
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(self \"https://checkout.stripe.com\")",
    "Content-Security-Policy": "default-src 'self'; connect-src 'self' https://checkout.stripe.com https://api.stripe.com https://r.stripe.com; frame-src https://checkout.stripe.com https://js.stripe.com https://hooks.stripe.com; img-src 'self' data: https: https://*.stripe.com; style-src 'self' 'unsafe-inline'; script-src 'self' https://js.stripe.com https://checkout.stripe.com; base-uri 'self'; form-action 'self'; frame-ancestors 'none'; object-src 'none'",
}
RATE_LIMITS = {
    "/api/quote": (60, 60),
    "/api/quotes": (60, 60),
    "/api/availability": (120, 60),
    "/api/booking-drafts": (30, 600),
    "/api/bookings": (10, 600),
}
rate_limit_lock = threading.Lock()
rate_limit_buckets = {}
booking_capacity_lock = threading.Lock()

ONE_VAN_RATES = {
    1: 50.0,
    2: 65.0,
    3: 80.0,
}

PAYMENT_OPTIONS = {"deposit", "full"}
BOOKING_STATUSES = [
    "new",
    "confirmed",
    "assigned",
    "in_progress",
    "completed",
    "cancelled",
    "refunded",
    "no_show",
]
PAYMENT_STATUSES = [
    "pending",
    "deposit_pending",
    "full_pending",
    "deposit_paid",
    "paid",
    "balance_due",
    "refunded",
    "failed",
]
STATUS_LABELS = {
    "new": "New",
    "confirmed": "Confirmed",
    "assigned": "Assigned",
    "in_progress": "In progress",
    "completed": "Completed",
    "cancelled": "Cancelled",
    "refunded": "Refunded",
    "no_show": "No show",
}
PAYMENT_STATUS_LABELS = {
    "pending": "Pending",
    "deposit_pending": "Deposit pending",
    "full_pending": "Full payment pending",
    "deposit_paid": "Deposit paid",
    "paid": "Paid in full",
    "balance_due": "Balance due",
    "refunded": "Refunded",
    "failed": "Failed",
}

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


def plural_count(value, singular, plural=None):
    number = float(value)
    count_text = f"{number:g}"
    word = singular if number == 1 else (plural or f"{singular}s")
    return f"{count_text} {word}"


def stair_fee_label(pickup_stairs, delivery_stairs):
    total_flights = int(pickup_stairs or 0) + int(delivery_stairs or 0)
    if not total_flights:
        return "Stair fee - no stairs involved in the move"
    flight_label = "flight" if total_flights == 1 else "flights"
    return f"Stair fee - {total_flights} {flight_label} of stairs"


def first_int(value, default=0):
    if isinstance(value, (int, float)):
        return int(value)
    match = re.search(r"\d+", str(value or ""))
    return int(match.group(0)) if match else default


def truthy(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


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


def display_move_date(value):
    try:
        return datetime.strptime(str(value or ""), "%Y-%m-%d").strftime("%A %-d %B %Y")
    except ValueError:
        return str(value or "")


def display_move_time(value):
    try:
        return datetime.strptime(str(value or ""), "%H:%M").strftime("%-I:%M %p").lower()
    except ValueError:
        return str(value or "")


def send_security_headers(handler):
    for name, value in SECURITY_HEADERS.items():
        handler.send_header(name, value)


def json_response(handler, status, payload, extra_headers=None):
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    send_security_headers(handler)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    for name, value in (extra_headers or {}).items():
        handler.send_header(name, value)
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def html_response(handler, status, body, extra_headers=None):
    encoded = body.encode("utf-8")
    handler.send_response(status)
    send_security_headers(handler)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    for name, value in (extra_headers or {}).items():
        handler.send_header(name, value)
    handler.send_header("Content-Length", str(len(encoded)))
    handler.end_headers()
    handler.wfile.write(encoded)


def redirect_response(handler, location, extra_headers=None):
    handler.send_response(303)
    send_security_headers(handler)
    handler.send_header("Location", location)
    handler.send_header("Cache-Control", "no-store")
    for name, value in (extra_headers or {}).items():
        handler.send_header(name, value)
    handler.end_headers()


def read_json(handler, limit=50_000):
    requested_length = int(handler.headers.get("Content-Length", "0"))
    if requested_length > limit:
        raise ValueError("Request body is too large.")
    length = min(requested_length, limit)
    return json.loads(handler.rfile.read(length).decode("utf-8") or "{}")


def read_body(handler, limit=1_000_000):
    requested_length = int(handler.headers.get("Content-Length", "0"))
    if requested_length > limit:
        raise ValueError("Request body is too large.")
    length = min(requested_length, limit)
    return handler.rfile.read(length)


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def compact(value, limit=500):
    value = re.sub(r"\s+", " ", str(value or "").strip())
    return value[:limit]


def max_movers_for_vans(vans):
    return max(1, first_int(vans, 1) * MOVERS_PER_LUTON_VAN)


def rate_limited(handler, path):
    config = RATE_LIMITS.get(path)
    if not config:
        return False
    max_requests, window_seconds = config
    now = time.time()
    client = handler.headers.get("X-Forwarded-For", handler.client_address[0]).split(",", 1)[0].strip()
    key = (client, path)
    with rate_limit_lock:
        bucket = [stamp for stamp in rate_limit_buckets.get(key, []) if now - stamp < window_seconds]
        if len(bucket) >= max_requests:
            rate_limit_buckets[key] = bucket
            return True
        bucket.append(now)
        rate_limit_buckets[key] = bucket
        return False


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
                assigned_vehicle_count INTEGER,
                assigned_mover_count INTEGER,
                assigned_team TEXT,
                admin_notes TEXT,
                last_admin_update_at TEXT,
                completed_at TEXT,
                cancelled_at TEXT,
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
            "assigned_vehicle_count": "INTEGER",
            "assigned_mover_count": "INTEGER",
            "assigned_team": "TEXT",
            "admin_notes": "TEXT",
            "last_admin_update_at": "TEXT",
            "completed_at": "TEXT",
            "cancelled_at": "TEXT",
        }
        for column, column_type in migrations.items():
            if column not in existing:
                db.execute(f"ALTER TABLE bookings ADD COLUMN {column} {column_type}")
        db.execute("CREATE INDEX IF NOT EXISTS idx_bookings_created ON bookings(created_at)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_bookings_reference ON bookings(reference)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_bookings_stripe_session ON bookings(stripe_checkout_session_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_bookings_calendar_token ON bookings(calendar_token)")
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS availability_blocks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                block_date TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                vans_blocked INTEGER NOT NULL,
                movers_blocked INTEGER NOT NULL,
                reason TEXT
            )
            """
        )
        db.execute("CREATE INDEX IF NOT EXISTS idx_availability_blocks_date ON availability_blocks(block_date)")
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS booking_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                reference TEXT NOT NULL,
                event_type TEXT NOT NULL,
                actor TEXT NOT NULL,
                summary TEXT NOT NULL,
                metadata_json TEXT
            )
            """
        )
        db.execute("CREATE INDEX IF NOT EXISTS idx_booking_events_reference ON booking_events(reference)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_booking_events_created ON booking_events(created_at)")
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS booking_drafts (
                token TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                quote_payload_json TEXT NOT NULL,
                quote_form_json TEXT NOT NULL,
                booking_json TEXT NOT NULL,
                quote_json TEXT NOT NULL
            )
            """
        )
        db.execute("CREATE INDEX IF NOT EXISTS idx_booking_drafts_expires ON booking_drafts(expires_at)")


def new_reference():
    return f"MWV-{time.strftime('%y%m%d')}-{uuid.uuid4().hex[:6].upper()}"


def row_to_booking(row):
    return {key: row[key] for key in row.keys()}


def csv_safe(value):
    if value is None:
        return ""
    text = str(value)
    if text.startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + text
    return text


def log_booking_event(reference, event_type, actor, summary, metadata=None, db=None):
    if not reference:
        return
    payload = json.dumps(metadata or {}, ensure_ascii=False)
    values = (now_iso(), reference, event_type, actor, compact(summary, 500), payload)
    try:
        if db is not None:
            db.execute(
                """
                INSERT INTO booking_events (
                    created_at, reference, event_type, actor, summary, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                values,
            )
            return
        with connect_db() as event_db:
            event_db.execute(
                """
                INSERT INTO booking_events (
                    created_at, reference, event_type, actor, summary, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                values,
            )
    except Exception as error:
        print(f"Booking event log failed for {reference}: {error}")


def booking_events(reference, limit=40):
    with connect_db() as db:
        return db.execute(
            """
            SELECT created_at, event_type, actor, summary, metadata_json
            FROM booking_events
            WHERE reference = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (reference, limit),
        ).fetchall()


def normalise_quote_payload(payload):
    payload = payload or {}
    vans = max(1, min(first_int(payload.get("lutonVans") or payload.get("vans"), 1), MAX_VANS))
    return {
        "moveType": compact(payload.get("moveType"), 80),
        "lutonVans": vans,
        "movers": max(1, min(first_int(payload.get("movers"), 2), max_movers_for_vans(vans))),
        "hours": max(first_int(payload.get("hours") or payload.get("estimatedHours"), int(MINIMUM_HOURS)), int(MINIMUM_HOURS)),
        "packAndMove": truthy(payload.get("packAndMove") or payload.get("packingService")),
        "pickup": clean_postcode(payload.get("pickup") or payload.get("pickupPostcode")),
        "delivery": clean_postcode(payload.get("delivery") or payload.get("deliveryPostcode")),
        "additionalStops": clean_postcode_list(payload.get("additionalStops") or []),
        "pickupStairs": first_int(payload.get("pickupStairs"), 0),
        "deliveryStairs": first_int(payload.get("deliveryStairs"), 0),
        "items": compact(payload.get("items"), 1500),
    }


def compact_string_list(values, limit=500):
    if not isinstance(values, list):
        return []
    return [compact(value, limit) for value in values if compact(value, limit)]


def normalise_quote_form_draft(value, quote_payload):
    value = value or {}
    return {
        "moveType": compact(value.get("moveType") or quote_payload.get("moveType"), 80),
        "lutonVans": compact(value.get("lutonVans") or f"{quote_payload.get('lutonVans', 1)} Luton van", 80),
        "movers": compact(value.get("movers") or f"{quote_payload.get('movers', 2)} men", 80),
        "estimatedHours": compact(value.get("estimatedHours") or f"{quote_payload.get('hours', 2)} hours", 80),
        "packAndMove": compact(value.get("packAndMove") or ("yes" if quote_payload.get("packAndMove") else "no"), 20),
        "pickup": compact(value.get("pickup") or quote_payload.get("pickup"), 120),
        "delivery": compact(value.get("delivery") or quote_payload.get("delivery"), 120),
        "additionalStops": compact_string_list(value.get("additionalStops") or quote_payload.get("additionalStops") or [], 120),
        "pickupStairs": compact(value.get("pickupStairs"), 80),
        "deliveryStairs": compact(value.get("deliveryStairs"), 80),
        "items": compact(value.get("items") or quote_payload.get("items"), 1500),
    }


def normalise_booking_draft(value):
    value = value or {}
    return {
        "customerName": compact(value.get("customerName"), 140),
        "customerEmail": compact(value.get("customerEmail"), 180).lower(),
        "customerPhone": compact(value.get("customerPhone"), 60),
        "moveDate": compact(value.get("moveDate"), 30),
        "moveTime": compact(value.get("moveTime"), 30),
        "pickupAddress": compact(value.get("pickupAddress"), 500),
        "deliveryAddress": compact(value.get("deliveryAddress"), 500),
        "additionalAddresses": compact_string_list(value.get("additionalAddresses") or [], 500),
        "paymentOption": compact(value.get("paymentOption") or "deposit", 20),
        "termsAccepted": bool(value.get("termsAccepted")),
    }


def create_booking_draft(payload):
    quote_payload = normalise_quote_payload(payload.get("quoteInputs") or payload.get("quotePayload") or payload.get("quote") or {})
    quote, errors = build_quote(quote_payload)
    if errors:
        return None, errors

    quote_form = normalise_quote_form_draft(payload.get("quoteForm") or {}, quote_payload)
    booking = normalise_booking_draft(payload.get("booking") or {})
    token = uuid.uuid4().hex + uuid.uuid4().hex[:12]
    created_at = now_iso()
    expires_at = (datetime.utcnow() + timedelta(days=BOOKING_DRAFT_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with connect_db() as db:
        db.execute(
            """
            INSERT INTO booking_drafts (
                token, created_at, expires_at, quote_payload_json,
                quote_form_json, booking_json, quote_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                token,
                created_at,
                expires_at,
                json.dumps(quote_payload, ensure_ascii=False),
                json.dumps(quote_form, ensure_ascii=False),
                json.dumps(booking, ensure_ascii=False),
                json.dumps(quote, ensure_ascii=False),
            ),
        )
    resume_url = f"{SITE_BASE_URL}/?draft={urllib.parse.quote(token)}#quote"
    return {
        "token": token,
        "resumeUrl": resume_url,
        "expiresAt": expires_at,
    }, None


def get_booking_draft(token):
    token = compact(token, 80)
    if not re.fullmatch(r"[a-f0-9]{44}", token):
        return None
    with connect_db() as db:
        row = db.execute(
            """
            SELECT token, created_at, expires_at, quote_payload_json,
                   quote_form_json, booking_json, quote_json
            FROM booking_drafts
            WHERE token = ?
            """,
            (token,),
        ).fetchone()
    if not row or row["expires_at"] < now_iso():
        return None
    try:
        return {
            "token": row["token"],
            "createdAt": row["created_at"],
            "expiresAt": row["expires_at"],
            "quotePayload": json.loads(row["quote_payload_json"] or "{}"),
            "quoteForm": json.loads(row["quote_form_json"] or "{}"),
            "booking": json.loads(row["booking_json"] or "{}"),
            "quote": json.loads(row["quote_json"] or "{}"),
        }
    except Exception:
        return None


def secure_admin_configured():
    return bool(ADMIN_USER and ADMIN_PASSWORD and ADMIN_PASSWORD.lower() not in PLACEHOLDER_ADMIN_PASSWORDS)


def admin_authorised(handler):
    if not secure_admin_configured():
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


def admin_actor(handler):
    return f"admin:{ADMIN_USER or 'unknown'}"


def require_admin(handler):
    if admin_authorised(handler):
        return True
    handler.send_response(401)
    send_security_headers(handler)
    handler.send_header("WWW-Authenticate", 'Basic realm="Men With a Van Admin"')
    handler.send_header("Content-Type", "text/plain; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(b"Admin login required.")
    return False


def parse_cookies(handler):
    cookies = {}
    for item in handler.headers.get("Cookie", "").split(";"):
        if "=" not in item:
            continue
        name, value = item.strip().split("=", 1)
        cookies[name] = value
    return cookies


def admin_csrf_token(handler):
    token = parse_cookies(handler).get(ADMIN_CSRF_COOKIE, "")
    if re.fullmatch(r"[a-f0-9]{64}", token):
        return token
    return uuid.uuid4().hex + uuid.uuid4().hex


def admin_csrf_cookie_header(token):
    return f"{ADMIN_CSRF_COOKIE}={token}; Max-Age=7200; Path=/admin; Secure; HttpOnly; SameSite=Strict"


def same_origin_request(handler):
    expected_origin = SITE_BASE_URL
    origin = handler.headers.get("Origin")
    if origin:
        return origin == expected_origin
    referer = handler.headers.get("Referer")
    if referer:
        return referer.startswith(expected_origin + "/")
    return False


def admin_csrf_valid(handler, form):
    cookie_token = parse_cookies(handler).get(ADMIN_CSRF_COOKIE, "")
    form_token = compact((form.get("_csrf") or [""])[0], 140)
    return (
        same_origin_request(handler)
        and re.fullmatch(r"[a-f0-9]{64}", cookie_token or "")
        and hmac.compare_digest(cookie_token, form_token)
    )


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
    embedded_mode = bool(STRIPE_PUBLISHABLE_KEY)
    payment_label = "25% DEPOSIT" if payment_option == "deposit" else "FULL PAYMENT"
    params = {
        "mode": "payment",
        "submit_type": "book",
        "client_reference_id": reference,
        "customer_email": customer_email,
        "line_items[0][quantity]": "1",
        "line_items[0][price_data][currency]": "gbp",
        "line_items[0][price_data][unit_amount]": str(amount_pence),
        "line_items[0][price_data][product_data][name]": payment_label,
        "metadata[booking_reference]": reference,
        "metadata[payment_option]": payment_option,
        "metadata[move_date]": move_date,
        "metadata[move_time]": move_time,
        "payment_intent_data[receipt_email]": customer_email,
        "payment_intent_data[description]": f"Men With a Van {payment_label} - {reference}",
        "payment_intent_data[metadata][booking_reference]": reference,
        "payment_intent_data[metadata][payment_option]": payment_option,
        "excluded_payment_method_types[0]": "amazon_pay",
        "wallet_options[link][display]": "never",
    }
    if embedded_mode:
        params.update(
            {
                "ui_mode": "embedded_page",
                "return_url": f"{SITE_BASE_URL}/payment-success.html?session_id={{CHECKOUT_SESSION_ID}}",
            }
        )
    else:
        params.update(
            {
                "success_url": f"{SITE_BASE_URL}/payment-success.html?session_id={{CHECKOUT_SESSION_ID}}",
                "cancel_url": f"{SITE_BASE_URL}/payment-cancelled.html?ref={urllib.parse.quote(reference)}",
            }
        )
    session = stripe_request("POST", "/checkout/sessions", params)
    return {
        "id": session.get("id"),
        "url": session.get("url"),
        "client_secret": session.get("client_secret"),
        "ui_mode": session.get("ui_mode") or ("embedded_page" if embedded_mode else "hosted_page"),
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
        log_booking_event(
            reference,
            "stripe_session_created",
            "system",
            "Stripe checkout session created.",
            {"sessionId": session.get("id"), "amountTotal": session.get("amount_total"), "currency": session.get("currency")},
            db=db,
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


def preferred_time_slots():
    slots = []
    for hour in range(8, 11):
        slots.append(f"{hour:02d}:00")
        if hour < 10:
            slots.append(f"{hour:02d}:30")
    for hour in range(13, 22):
        slots.append(f"{hour:02d}:00")
        if hour < 21:
            slots.append(f"{hour:02d}:30")
    return slots


def valid_move_date(value):
    try:
        datetime.strptime(value, "%Y-%m-%d")
        return True
    except Exception:
        return False


def datetime_for_slot(date_text, time_text):
    try:
        return datetime.strptime(f"{date_text} {time_text}", "%Y-%m-%d %H:%M")
    except Exception:
        return None


def ranges_overlap(start_a, end_a, start_b, end_b):
    return start_a < end_b and start_b < end_a


def booking_holds_capacity(row):
    if row["status"] in {"confirmed", "assigned", "in_progress"}:
        return True
    if row["payment_status"] in {"deposit_paid", "paid", "balance_due"}:
        return True
    if row["status"] == "new" and row["payment_status"] in {"deposit_pending", "full_pending"}:
        try:
            created_at = datetime.strptime(row["created_at"], "%Y-%m-%dT%H:%M:%SZ")
            return datetime.utcnow() - created_at <= timedelta(minutes=BOOKING_HOLD_MINUTES)
        except Exception:
            return False
    return False


def status_holds_capacity(status, payment_status):
    return status in {"confirmed", "assigned", "in_progress"} or payment_status in {"deposit_paid", "paid", "balance_due"}


def row_capacity(row):
    vans = row["assigned_vehicle_count"] or row["luton_vans"] or 0
    movers = row["assigned_mover_count"] or row["movers"] or 0
    return int(vans), int(movers)


def block_start_end(block):
    start = datetime_for_slot(block["block_date"], block["start_time"])
    end = datetime_for_slot(block["block_date"], block["end_time"])
    if start and end and end <= start:
        end += timedelta(days=1)
    return start, end


def capacity_usage_for_window(date_text, start, end, exclude_reference=""):
    used_vans = 0
    blocking_notes = []
    with connect_db() as db:
        rows = db.execute(
            """
            SELECT reference, created_at, status, payment_status, move_date, move_time,
                   estimated_hours, luton_vans, movers, assigned_vehicle_count,
                   assigned_mover_count
            FROM bookings
            WHERE move_date = ?
              AND status NOT IN ('cancelled', 'refunded', 'no_show', 'completed')
            """,
            (date_text,),
        ).fetchall()
        blocks = db.execute(
            """
            SELECT id, block_date, start_time, end_time, vans_blocked, movers_blocked, reason
            FROM availability_blocks
            WHERE block_date = ?
            """,
            (date_text,),
        ).fetchall()

    for row in rows:
        if exclude_reference and row["reference"] == exclude_reference:
            continue
        if not booking_holds_capacity(row):
            continue
        booking_start, booking_end = booking_start_end(row)
        if ranges_overlap(start, end, booking_start, booking_end):
            vans, _movers = row_capacity(row)
            used_vans += vans
            blocking_notes.append(f"Booking {row['reference']}")

    for block in blocks:
        block_start, block_end = block_start_end(block)
        if not block_start or not block_end:
            continue
        if ranges_overlap(start, end, block_start, block_end):
            used_vans += int(block["vans_blocked"] or 0)
            if block["reason"]:
                blocking_notes.append(str(block["reason"]))

    return used_vans, blocking_notes


def availability_for_date(date_text, hours=MINIMUM_HOURS, vans=1, movers=1, exclude_reference=""):
    hours = max(MINIMUM_HOURS, min(float(hours or MINIMUM_HOURS), 24.0))
    vans = max(1, min(first_int(vans, 1), MAX_VANS))
    movers = max(1, min(first_int(movers, 1), max_movers_for_vans(vans)))
    slots = []
    for slot in preferred_time_slots():
        start = datetime_for_slot(date_text, slot)
        if not start:
            continue
        end = start + timedelta(hours=hours)
        used_vans, notes = capacity_usage_for_window(date_text, start, end, exclude_reference)
        remaining_vans = max(0, FLEET_LUTON_VANS - used_vans)
        available = remaining_vans >= vans
        reason = ""
        if not available:
            reason = "Not enough Luton vans are available for this start time."
        slots.append(
            {
                "time": slot,
                "available": available,
                "remainingVans": remaining_vans,
                "usedVans": used_vans,
                "reason": reason,
                "notes": notes[:3],
            }
        )
    return {
        "date": date_text,
        "hours": hours,
        "requestedVans": vans,
        "requestedMovers": movers,
        "fleetVans": FLEET_LUTON_VANS,
        "moverAvailabilityLimited": False,
        "slots": slots,
    }


def slot_available(date_text, move_time, hours, vans, movers, exclude_reference=""):
    if move_time not in preferred_time_slots():
        return False, "Choose an available start time."
    availability = availability_for_date(date_text, hours, vans, movers, exclude_reference)
    for slot in availability["slots"]:
        if slot["time"] == move_time:
            if slot["available"]:
                return True, ""
            return False, slot["reason"] or "This start time is no longer available."
    return False, "Choose an available start time."


def calendar_datetime(value):
    return value.strftime("%Y%m%dT%H%M%S")


def calendar_escape(value):
    return str(value or "").replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;").replace("\n", "\\n")


def booking_calendar_summary(row):
    return f"Men With a Van booking {row['reference']}"


def booking_calendar_description(row):
    overtime_rate = row["total_inc_vat"]
    overtime_half_hour = overtime_rate / 2
    packing_line = "Package and move service: Not included."
    try:
        quote = json.loads(row["quote_json"] or "{}")
        overtime_rate = quote.get("overtime", {}).get("hourlyRateIncVat", overtime_rate)
        overtime_half_hour = quote.get("overtime", {}).get("halfHourRateIncVat", float(overtime_rate) / 2)
        if quote.get("inputs", {}).get("packAndMove"):
            packing_line = "Package and move service: Included. We bring brand new complimentary packing materials such as wardrobe boxes, different size boxes, bubble wrap, tape and paper. Materials are yours to keep, and packing time is included within the total booked hours."
    except Exception:
        pass
    team_summary = (
        f"{plural_count(row['luton_vans'], 'Luton van')}, "
        f"{plural_count(row['movers'], 'mover')}, "
        f"{plural_count(row['estimated_hours'], 'booked hour')}"
    )
    return (
        f"Booking reference: {row['reference']}\n"
        f"{team_summary}.\n"
        f"{packing_line}\n"
        f"Furniture dismantling/reassembly: Included as standard.\n"
        f"Pricing basis: No hidden item-count charge. The quote is based on movers, booked hours, distance, stairs/floors, congestion zone and VAT.\n"
        f"Total including VAT: £{row['total_inc_vat']:.2f}.\n"
        f"Overtime after booked hours: £{float(overtime_rate):.2f} per hour, billed every 30 minutes at £{float(overtime_half_hour):.2f}, payable on completion by cash, card or bank transfer.\n"
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
    result = {
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
    }
    if row["paid_at"] and row["calendar_token"]:
        result["calendar"] = {
            "icsUrl": f"/api/bookings/{row['reference']}/calendar.ics?token={row['calendar_token']}",
            "googleUrl": google_calendar_url(row),
        }
    return result


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
    overtime_half_hour = quote.get("overtime", {}).get("halfHourRateIncVat", float(overtime_rate or 0) / 2)
    pack_and_move = bool(quote.get("inputs", {}).get("packAndMove"))
    packing_text = (
        "Included - we bring brand new complimentary packing materials such as wardrobe boxes, different size boxes, bubble wrap, tape and paper. We pack everything that needs packing, then move it. The materials are yours to keep, and packing time is included within the total booked hours."
        if pack_and_move
        else "Not included - move only."
    )
    vans_text = plural_count(row["luton_vans"], "Luton van")
    movers_text = plural_count(row["movers"], "mover")
    hours_text = plural_count(row["estimated_hours"], "estimated hour")
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
- {vans_text}
- {movers_text}
- {hours_text}
- Route distance: {row['distance_miles']:g} miles
- Package and move service: {packing_text}
- Furniture dismantling/reassembly: Included as standard
- Pricing basis: No hidden item-count charge. The quote is based on movers, booked hours, distance, stairs/floors, congestion zone and VAT.

Quote breakdown:
{item_lines}
- VAT: £{row['vat']:.2f}
- Total including VAT: £{row['total_inc_vat']:.2f}

Payment:
{payment_text}

Overtime:
If the move runs beyond the booked {hours_text}, overtime is charged at £{float(overtime_rate):.2f} per hour, billed every 30 minutes at £{float(overtime_half_hour):.2f}, and payable on completion by cash, card or bank transfer.

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
  <p><strong>Team:</strong> {html.escape(vans_text)}, {html.escape(movers_text)}, {html.escape(hours_text)}</p>
  <p><strong>Package and move service:</strong> {html.escape(packing_text)}</p>
  <p><strong>Furniture dismantling/reassembly:</strong> Included as standard.</p>
  <p><strong>Pricing basis:</strong> No hidden item-count charge. The quote is based on movers, booked hours, distance, stairs/floors, congestion zone and VAT.</p>
  <h2>Price</h2>
  <table cellpadding="8" cellspacing="0" border="1" style="border-collapse:collapse;border-color:#dce3eb">{html_rows}
    <tr><td>VAT</td><td>£{row['vat']:.2f}</td></tr>
    <tr><td><strong>Total including VAT</strong></td><td><strong>£{row['total_inc_vat']:.2f}</strong></td></tr>
  </table>
  <h2>Payment</h2>
  <p>{html.escape(payment_text)}</p>
  <h2>Overtime</h2>
  <p>If the move runs beyond the booked {html.escape(hours_text)}, overtime is charged at <strong>£{float(overtime_rate):.2f} per hour</strong>, billed every 30 minutes at <strong>£{float(overtime_half_hour):.2f}</strong>, and payable on completion by cash, card or bank transfer.</p>
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
                    log_booking_event(
                        reference,
                        "customer_email_sent",
                        "system",
                        "Customer confirmation email sent.",
                        {"to": row["customer_email"]},
                        db=db,
                    )
                    result["customer"] = "sent"
                else:
                    result["customer"] = "not_sent"
            except Exception as error:
                result["customer"] = "failed"
                result["errors"].append(f"Customer email failed: {error}")
                log_booking_event(
                    reference,
                    "customer_email_failed",
                    "system",
                    "Customer confirmation email failed.",
                    {"error": str(error)},
                    db=db,
                )
                print(f"Customer confirmation email failed for {reference}: {error}")
        else:
            result["customer"] = "already_sent"
        if OFFICE_EMAIL and (force_office or not office_sent):
            try:
                office_text = "New paid booking received.\n\n" + text_body
                if send_email(OFFICE_EMAIL, f"Paid booking {reference}", office_text, html_body):
                    db.execute("UPDATE bookings SET office_email_sent_at = ? WHERE reference = ?", (now_iso(), reference))
                    log_booking_event(
                        reference,
                        "office_email_sent",
                        "system",
                        "Office copy email sent.",
                        {"to": OFFICE_EMAIL},
                        db=db,
                    )
                    result["office"] = "sent"
                else:
                    result["office"] = "not_sent"
            except Exception as error:
                result["office"] = "failed"
                result["errors"].append(f"Office email failed: {error}")
                log_booking_event(
                    reference,
                    "office_email_failed",
                    "system",
                    "Office copy email failed.",
                    {"error": str(error)},
                    db=db,
                )
                print(f"Office confirmation email failed for {reference}: {error}")
        elif OFFICE_EMAIL and office_sent:
            result["office"] = "already_sent"
        elif not OFFICE_EMAIL:
            result["office"] = "not_configured"
    return result


def handle_stripe_event(event):
    event_type = event.get("type")
    obj = (event.get("data") or {}).get("object") or {}
    if event_type not in {
        "checkout.session.completed",
        "checkout.session.async_payment_succeeded",
        "checkout.session.async_payment_failed",
        "checkout.session.expired",
    }:
        return

    reference = obj.get("client_reference_id") or (obj.get("metadata") or {}).get("booking_reference")
    session_id = obj.get("id")
    payment_intent = obj.get("payment_intent")
    amount_total = obj.get("amount_total")
    currency = obj.get("currency")
    payment_status_from_stripe = obj.get("payment_status")
    livemode = obj.get("livemode")
    if not reference or not session_id:
        return

    with connect_db() as db:
        row = db.execute(
            """
            SELECT reference, status, payment_status, payment_option, deposit_amount, total_inc_vat,
                   stripe_checkout_session_id, stripe_payment_amount,
                   stripe_payment_currency
            FROM bookings
            WHERE reference = ?
            """,
            (reference,),
        ).fetchone()
        if not row:
            return
        expected_session = row["stripe_checkout_session_id"]
        if expected_session and not hmac.compare_digest(expected_session, session_id):
            print(f"Stripe webhook ignored for {reference}: session mismatch.")
            return
        if event_type in {"checkout.session.async_payment_failed", "checkout.session.expired"}:
            db.execute(
                """
                UPDATE bookings
                SET payment_status = 'failed',
                    stripe_checkout_session_id = COALESCE(?, stripe_checkout_session_id),
                    stripe_payment_intent_id = COALESCE(?, stripe_payment_intent_id),
                    stripe_payment_amount = COALESCE(?, stripe_payment_amount),
                    stripe_payment_currency = COALESCE(?, stripe_payment_currency)
                WHERE reference = ?
                  AND paid_at IS NULL
                """,
                (session_id, payment_intent, amount_total, currency, reference),
            )
            log_booking_event(
                reference,
                "stripe_payment_failed",
                "stripe",
                "Stripe checkout expired or payment failed.",
                {"eventType": event_type, "sessionId": session_id},
                db=db,
            )
            return
        if payment_status_from_stripe != "paid":
            print(f"Stripe webhook ignored for {reference}: payment status is not paid.")
            return
        expected_livemode = STRIPE_SECRET_KEY.startswith("sk_live_")
        if livemode is not None and bool(livemode) != expected_livemode:
            print(f"Stripe webhook ignored for {reference}: livemode mismatch.")
            return
        expected_amount = pence(row["deposit_amount"] if row["payment_option"] == "deposit" else row["total_inc_vat"])
        try:
            received_amount = int(amount_total)
        except (TypeError, ValueError):
            print(f"Stripe webhook ignored for {reference}: amount missing.")
            return
        if received_amount != expected_amount:
            print(f"Stripe webhook ignored for {reference}: amount mismatch.")
            return
        if (currency or "").lower() != "gbp":
            print(f"Stripe webhook ignored for {reference}: currency mismatch.")
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
              AND (stripe_checkout_session_id IS NULL OR stripe_checkout_session_id = ?)
            """,
            (payment_status, session_id, payment_intent, received_amount, currency, now_iso(), reference, session_id),
        )
        log_booking_event(
            reference,
            "stripe_payment_confirmed",
            "stripe",
            "Stripe payment confirmed and booking marked confirmed.",
            {"sessionId": session_id, "paymentIntent": payment_intent, "amount": received_amount, "currency": currency},
            db=db,
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
    pack_and_move = truthy(payload.get("packAndMove") or payload.get("pack-and-move") or payload.get("packingService"))

    errors = []
    if vans < 1 or vans > MAX_VANS:
        errors.append(f"Choose between 1 and {MAX_VANS} Luton vans.")
    if movers < vans:
        errors.append("Choose at least one mover per Luton van.")
    max_movers = max_movers_for_vans(vans)
    if movers > max_movers:
        van_label = "Luton van" if vans == 1 else "Luton vans"
        errors.append(f"Choose no more than {max_movers} men for {vans} {van_label}. Each Luton van can be booked with up to {MOVERS_PER_LUTON_VAN} men.")
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
    packing_service_rate = hourly_rate * PACKING_SERVICE_MULTIPLIER
    packing_subtotal = packing_service_rate * hours if pack_and_move else 0
    selected_hourly_rate = hourly_rate + (packing_service_rate if pack_and_move else 0)
    overtime_hourly_rate = selected_hourly_rate * OVERTIME_MULTIPLIER
    overtime_hourly_total = overtime_hourly_rate * (1 + VAT_RATE)
    overtime_half_hour_total = overtime_hourly_total / 2
    mileage_subtotal = distance_miles * MILEAGE_RATE
    stairs_floors = pickup_stairs + delivery_stairs
    stairs_subtotal = stairs_floors * movers * STAIR_RATE

    congestion_applied, congestion_details = congestion_zone_status(pickup, delivery, additional_stops)
    congestion_subtotal = CONGESTION_FEE if congestion_applied else 0
    address_hints = {
        "pickup": google_address_hint(pickup),
        "delivery": google_address_hint(delivery),
        "additionalStops": [google_address_hint(stop) for stop in additional_stops],
        "note": "Postcode/address hints are prefilled for convenience only. Customers must add door number, flat, building name and any missing access details before booking.",
    }

    subtotal = hourly_subtotal + packing_subtotal + mileage_subtotal + stairs_subtotal + congestion_subtotal
    vat = subtotal * VAT_RATE
    total = subtotal + vat
    deposit = total * 0.25
    line_items = [
        {
            "label": f"{vans} Luton van{'s' if vans != 1 else ''}, {movers} mover{'s' if movers != 1 else ''}, {hours:g} hour{'s' if hours != 1 else ''}",
            "amountExVat": money(hourly_subtotal),
        },
    ]
    if pack_and_move:
        line_items.append(
            {
                "label": "Pack and move service",
                "amountExVat": money(packing_subtotal),
                "note": "Includes brand new complimentary packing materials such as wardrobe boxes, different size boxes, bubble wrap, tape and paper. Materials are yours to keep, and packing is completed within the booked hours.",
            }
        )
    line_items.extend(
        [
            {
                "label": f"Route mileage fee ({distance_miles:g} miles)",
                "amountExVat": money(mileage_subtotal),
            },
            {
                "label": stair_fee_label(pickup_stairs, delivery_stairs),
                "amountExVat": money(stairs_subtotal),
            },
            {
                "label": "Congestion zone fee",
                "amountExVat": money(congestion_subtotal),
                "applied": congestion_applied,
                "note": congestion_details["note"],
            },
        ]
    )

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
            "packAndMove": pack_and_move,
            "pickupStairs": pickup_stairs,
            "deliveryStairs": delivery_stairs,
        },
        "rates": {
            "hourlyRateExVat": money(hourly_rate),
            "hourlyRateIncVat": money(hourly_rate * (1 + VAT_RATE)),
            "packingServiceMultiplier": PACKING_SERVICE_MULTIPLIER,
            "packingServiceRateExVat": money(packing_service_rate),
            "packingServiceRateIncVat": money(packing_service_rate * (1 + VAT_RATE)),
            "selectedJobHourlyRateExVat": money(selected_hourly_rate),
            "selectedJobHourlyRateIncVat": money(selected_hourly_rate * (1 + VAT_RATE)),
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
            "hourlyRateExVat": money(overtime_hourly_rate),
            "hourlyRateIncVat": money(overtime_hourly_total),
            "halfHourRateExVat": money(overtime_hourly_rate / 2),
            "halfHourRateIncVat": money(overtime_half_hour_total),
            "billingIntervalMinutes": 30,
            "multiplier": OVERTIME_MULTIPLIER,
            "upliftPercent": money((OVERTIME_MULTIPLIER - 1) * 100),
            "note": f"Overtime after the booked hours is charged at {int((OVERTIME_MULTIPLIER - 1) * 100)}% above the booked hourly rate. The displayed overtime rate is the final amount, billed every 30 minutes at £{money(overtime_half_hour_total):.2f}, and payable on completion by cash, card or bank transfer.",
        },
        "lineItems": line_items,
        "totals": {
            "subtotalExVat": money(subtotal),
            "vat": money(vat),
            "totalIncVat": money(total),
            "deposit25": money(deposit),
            "balanceAfterDeposit": money(total - deposit),
        },
        "messages": [
            f"Overtime after the booked time is £{money(overtime_hourly_total):.2f} per hour, billed every 30 minutes at £{money(overtime_half_hour_total):.2f}, payable on completion by cash, card or bank transfer.",
            "Once online payment is completed, we send a confirmation email confirming the booking is final.",
            "Furniture dismantling and reassembly is included as standard.",
            "No hidden item-count charge: pricing is based on movers, booked hours, distance, stairs/floors, congestion zone and VAT.",
        ],
    }

    if pack_and_move:
        quote["messages"].append(
            "Package and move selected: we bring brand new complimentary packing materials such as wardrobe boxes, different size boxes, bubble wrap, tape and paper. We pack everything that needs packing, then move it. The materials are yours to keep, and packing time must be included within the total hours booked."
        )
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

    with booking_capacity_lock:
        available, availability_error = slot_available(
            move_date,
            move_time,
            inputs["hours"],
            inputs["lutonVans"],
            inputs["movers"],
        )
        if not available:
            return None, [availability_error]

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
            log_booking_event(
                reference,
                "booking_created",
                "customer",
                "Customer created a booking request.",
                {
                    "paymentOption": payment_option,
                    "moveDate": move_date,
                    "moveTime": move_time,
                    "vans": inputs["lutonVans"],
                    "movers": inputs["movers"],
                    "hours": inputs["hours"],
                },
                db=db,
            )

    result = {
        "reference": reference,
        "status": "new",
        "paymentStatus": payment_status,
        "paymentOption": payment_option,
        "quote": quote,
        "booking": {
            "moveDate": move_date,
            "moveTime": move_time,
            "pickupAddress": pickup_address,
            "deliveryAddress": delivery_address,
        },
        "payment": {
            "provider": "stripe",
            "configured": stripe_enabled(),
            "amountDueNow": totals["deposit25"] if payment_option == "deposit" else totals["totalIncVat"],
            "currency": "GBP",
        },
        "message": "Booking request received. Continue to payment to confirm the selected moving date and arrival time.",
    }

    if stripe_enabled():
        try:
            session = create_stripe_checkout_session(reference, email, payment_option, quote, move_date, move_time)
            update_booking_with_stripe_session(reference, session)
            if session.get("client_secret") and STRIPE_PUBLISHABLE_KEY:
                result["stripeClientSecret"] = session.get("client_secret")
                result["stripePublishableKey"] = STRIPE_PUBLISHABLE_KEY
                result["stripeCheckoutMode"] = "embedded_page"
            if session.get("url"):
                result["checkoutUrl"] = session.get("url")
            result["stripeSessionId"] = session.get("id")
            result["message"] = "Booking saved. Complete secure payment to confirm your move."
        except Exception as error:
            result["payment"]["error"] = str(error)
            result["message"] = "Booking request saved, but the payment page could not be created. Please contact the office to complete payment."
    else:
        result["message"] = "Booking request saved. Online Stripe payment is not connected on the server yet, so please contact the office to complete payment."

    return result, None


def refresh_booking_payment(reference, payload):
    customer = payload.get("customer") or {}
    booking = payload.get("booking") or {}
    payment_option = compact(booking.get("paymentOption") or payload.get("paymentOption"), 20) or "deposit"
    if payment_option not in PAYMENT_OPTIONS:
        return None, ["Choose deposit or full payment."]

    reference = compact(urllib.parse.unquote(reference), 80)
    if not reference:
        return None, ["Booking reference is required."]

    with connect_db() as db:
        row = db.execute("SELECT * FROM bookings WHERE reference = ?", (reference,)).fetchone()

    if not row:
        return None, ["Booking could not be found."]
    if row["paid_at"] or row["payment_status"] in {"deposit_paid", "paid", "balance_due"}:
        return None, ["Payment has already been completed for this booking."]
    if row["payment_status"] not in {"deposit_pending", "full_pending", "pending"}:
        return None, ["This booking cannot be changed online. Please contact the office."]

    try:
        quote = json.loads(row["quote_json"] or "{}")
    except Exception:
        return None, ["The saved quote could not be loaded. Please create a fresh quote."]

    totals = quote.get("totals") or {}
    inputs = quote.get("inputs") or {}
    name = compact(customer.get("name"), 140) or row["customer_name"]
    email = (compact(customer.get("email"), 180) or row["customer_email"]).lower()
    phone = compact(customer.get("phone"), 60) or row["customer_phone"]
    move_date = compact(booking.get("moveDate"), 30) or row["move_date"]
    move_time = compact(booking.get("moveTime"), 30) or row["move_time"]
    pickup_address = compact(booking.get("pickupAddress"), 500) or row["pickup_address"]
    delivery_address = compact(booking.get("deliveryAddress"), 500) or row["delivery_address"]
    existing_additional_addresses = row["additional_addresses"] or "[]"
    additional_addresses = [
        compact(address, 500)
        for address in (booking.get("additionalAddresses") or [])
        if compact(address, 500)
    ]
    additional_postcodes = inputs.get("additionalStops") or []
    additional_addresses_json = existing_additional_addresses
    if additional_addresses or additional_postcodes:
        additional_addresses_json = json.dumps(
            [
                {"postcode": postcode, "address": additional_addresses[index] if index < len(additional_addresses) else ""}
                for index, postcode in enumerate(additional_postcodes)
            ],
            ensure_ascii=False,
        )

    errors = []
    if not name:
        errors.append("Customer name is required.")
    if not email_like(email):
        errors.append("A valid email address is required.")
    if not phone:
        errors.append("Phone number is required.")
    if not valid_move_date(move_date):
        errors.append("Move date is required.")
    if not move_time:
        errors.append("Move time is required.")
    if not pickup_address:
        errors.append("Full pickup address is required.")
    if not delivery_address:
        errors.append("Full delivery address is required.")
    if additional_postcodes and additional_addresses and len(additional_addresses) < len(additional_postcodes):
        errors.append("Full address is required for each additional stop.")
    if errors:
        return None, errors

    available, availability_error = slot_available(
        move_date,
        move_time,
        inputs.get("hours", row["estimated_hours"]),
        inputs.get("lutonVans", row["luton_vans"]),
        inputs.get("movers", row["movers"]),
        exclude_reference=reference,
    )
    if not available:
        return None, [availability_error]

    payment_status = "deposit_pending" if payment_option == "deposit" else "full_pending"
    balance_amount = float(totals.get("balanceAfterDeposit") or 0) if payment_option == "deposit" else 0
    amount_due_now = float(totals.get("deposit25") or 0) if payment_option == "deposit" else float(totals.get("totalIncVat") or 0)

    old_session_id = compact(row["stripe_checkout_session_id"], 180)
    if stripe_enabled() and old_session_id:
        try:
            stripe_request("POST", f"/checkout/sessions/{urllib.parse.quote(old_session_id)}/expire", {})
        except Exception:
            pass

    with connect_db() as db:
        db.execute(
            """
            UPDATE bookings
            SET payment_option = ?,
                payment_status = ?,
                balance_amount = ?,
                customer_name = ?,
                customer_email = ?,
                customer_phone = ?,
                move_date = ?,
                move_time = ?,
                pickup_address = ?,
                delivery_address = ?,
                additional_addresses = ?
            WHERE reference = ?
            """,
            (
                payment_option,
                payment_status,
                balance_amount,
                name,
                email,
                phone,
                move_date,
                move_time,
                pickup_address,
                delivery_address,
                additional_addresses_json,
                reference,
            ),
        )
        log_booking_event(
            reference,
            "payment_choice_updated",
            "customer",
            "Customer refreshed the payment choice before payment.",
            {"paymentOption": payment_option, "amountDueNow": amount_due_now},
            db=db,
        )

    result = {
        "reference": reference,
        "status": row["status"],
        "paymentStatus": payment_status,
        "paymentOption": payment_option,
        "quote": quote,
        "booking": {
            "moveDate": move_date,
            "moveTime": move_time,
            "pickupAddress": pickup_address,
            "deliveryAddress": delivery_address,
        },
        "payment": {
            "provider": "stripe",
            "configured": stripe_enabled(),
            "amountDueNow": amount_due_now,
            "currency": "GBP",
        },
        "message": "Payment choice updated. Complete secure payment to confirm your move.",
    }

    if stripe_enabled():
        try:
            session = create_stripe_checkout_session(reference, email, payment_option, quote, move_date, move_time)
            update_booking_with_stripe_session(reference, session)
            if session.get("client_secret") and STRIPE_PUBLISHABLE_KEY:
                result["stripeClientSecret"] = session.get("client_secret")
                result["stripePublishableKey"] = STRIPE_PUBLISHABLE_KEY
                result["stripeCheckoutMode"] = "embedded_page"
            if session.get("url"):
                result["checkoutUrl"] = session.get("url")
            result["stripeSessionId"] = session.get("id")
        except Exception as error:
            result["payment"]["error"] = str(error)
            result["message"] = "Payment choice was saved, but the payment section could not be refreshed. Please contact the office."
    else:
        result["message"] = "Payment choice was saved. Online Stripe payment is not connected on the server yet."

    return result, None


def admin_money(value):
    try:
        return f"£{float(value or 0):,.2f}"
    except Exception:
        return "£0.00"


def status_label(value):
    return STATUS_LABELS.get(value, value or "Unknown")


def payment_status_label(value):
    return PAYMENT_STATUS_LABELS.get(value, value or "Unknown")


def status_badge_class(value):
    if value in {"completed", "paid"}:
        return "ok"
    if value in {"confirmed", "assigned", "in_progress", "deposit_paid"}:
        return "active"
    if value in {"cancelled", "refunded", "no_show", "failed"}:
        return "danger"
    return "warn"


def option_html(options, current, labels=None):
    labels = labels or {}
    items = []
    for option in options:
        selected = " selected" if option == current else ""
        items.append(f'<option value="{html.escape(option)}"{selected}>{html.escape(labels.get(option, option))}</option>')
    return "".join(items)


def time_options_html(current):
    items = []
    for slot in preferred_time_slots():
        selected = " selected" if slot == current else ""
        items.append(f'<option value="{slot}"{selected}>{slot}</option>')
    return "".join(items)


def changed_fields(row, updates):
    labels = {
        "customer_name": "Customer name",
        "customer_email": "Customer email",
        "customer_phone": "Customer phone",
        "move_type": "Move type",
        "move_date": "Move date",
        "move_time": "Move time",
        "pickup_address": "Pickup address",
        "delivery_address": "Delivery address",
        "item_notes": "Item notes",
        "access_notes": "Access notes",
    }
    changes = []
    for field, new_value in updates.items():
        old_value = "" if row[field] is None else str(row[field])
        new_text = "" if new_value is None else str(new_value)
        if old_value != new_text:
            changes.append({"field": field, "label": labels.get(field, field), "old": old_value, "new": new_text})
    return changes


def parse_additional_addresses(row):
    try:
        items = json.loads(row["additional_addresses"] or "[]")
        return items if isinstance(items, list) else []
    except Exception:
        return []


def quote_from_row(row):
    try:
        quote = json.loads(row["quote_json"] or "{}")
        return quote if isinstance(quote, dict) else {}
    except Exception:
        return {}


def admin_styles():
    return """
      :root { --ink:#122034; --text:#314155; --muted:#68778a; --line:#dce3eb; --soft:#f4f8fb; --brand:#0b5d7a; --brand-dark:#0f2d3a; --accent:#f0b429; --ok:#11623c; --danger:#9d3f2f; }
      * { box-sizing: border-box; }
      body { margin: 0; font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: var(--ink); background: var(--soft); }
      header { padding: 28px clamp(18px, 4vw, 42px); color: #fff; background: linear-gradient(135deg, var(--brand-dark), var(--brand)); }
      header h1 { margin: 0 0 6px; font-size: clamp(30px, 4vw, 46px); line-height: 1.05; }
      header p { max-width: 820px; margin: 0; color: rgba(255,255,255,.76); }
      main { padding: 24px clamp(18px, 4vw, 42px) 44px; }
      a { color: var(--brand); font-weight: 800; text-decoration: none; }
      small { color: var(--muted); line-height: 1.45; }
      input, select, textarea, button { font: inherit; }
      input, select, textarea { width: 100%; min-height: 42px; padding: 9px 10px; color: var(--ink); background: #fff; border: 1px solid var(--line); border-radius: 8px; }
      textarea { min-height: 120px; resize: vertical; }
      button, .button-link { display: inline-flex; align-items: center; justify-content: center; min-height: 42px; padding: 0 14px; color: #fff; font-weight: 900; background: var(--brand); border: 0; border-radius: 8px; cursor: pointer; }
      .button-link { text-decoration: none; }
      .button-secondary { color: var(--brand); background: #eef7fa; border: 1px solid rgba(11,93,122,.22); }
      .notice { margin: 0 0 18px; padding: 12px 14px; color: #533f00; background: #fff9db; border: 1px solid #f0d878; border-radius: 8px; }
      .toolbar { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 14px; margin-bottom: 18px; }
      .toolbar-actions { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
      .summary-grid { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 12px; margin-bottom: 18px; }
      .summary-card, .panel, .filters, .table-wrap, .detail-card { background: #fff; border: 1px solid var(--line); border-radius: 8px; box-shadow: 0 12px 34px rgba(13,32,51,.06); }
      .summary-card { padding: 16px; }
      .summary-card span { display: block; color: var(--muted); font-size: 12px; font-weight: 900; text-transform: uppercase; }
      .summary-card strong { display: block; margin-top: 6px; font-size: 28px; line-height: 1.05; }
      .filters { margin-bottom: 18px; padding: 14px; }
      .filter-grid { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 10px; align-items: end; }
      label { display: grid; gap: 6px; color: var(--text); font-size: 13px; font-weight: 850; }
      table { width: 100%; border-collapse: collapse; min-width: 1120px; }
      th, td { padding: 14px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }
      th { color: var(--muted); font-size: 12px; font-weight: 950; text-transform: uppercase; }
      tr:last-child td { border-bottom: 0; }
      .table-wrap { overflow-x: auto; }
      .badge { display: inline-flex; align-items: center; margin: 0 4px 6px 0; padding: 4px 8px; border-radius: 999px; font-size: 12px; font-weight: 900; }
      .badge.ok { color: var(--ok); background: #daf5e8; }
      .badge.active { color: #075d78; background: #dff4fb; }
      .badge.warn { color: #7a4f00; background: #fff0c2; }
      .badge.danger { color: var(--danger); background: #ffe3dd; }
      .mini-form { display: grid; gap: 8px; min-width: 220px; }
      .mini-form .two { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
      .email-panel { margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--line); }
      .detail-grid { display: grid; grid-template-columns: 1.2fr .8fr; gap: 18px; align-items: start; }
      .detail-card { padding: 18px; }
      .detail-card h2, .detail-card h3 { margin: 0 0 12px; }
      .detail-list { display: grid; gap: 10px; margin: 0; }
      .detail-list div { display: grid; grid-template-columns: 170px 1fr; gap: 12px; padding-bottom: 10px; border-bottom: 1px solid var(--line); }
      .detail-list div:last-child { border-bottom: 0; padding-bottom: 0; }
      .detail-list dt { color: var(--muted); font-weight: 850; }
      .detail-list dd { margin: 0; color: var(--ink); }
      .ops-form { display: grid; gap: 12px; }
      .ops-form .two { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
      .quote-lines { display: grid; gap: 8px; padding: 0; list-style: none; }
      .quote-lines li { display: flex; justify-content: space-between; gap: 12px; padding-bottom: 8px; border-bottom: 1px solid var(--line); }
      .empty { padding: 24px; color: var(--muted); text-align: center; }
      @media (max-width: 980px) {
        .summary-grid, .filter-grid, .detail-grid { grid-template-columns: 1fr; }
        .detail-list div { grid-template-columns: 1fr; gap: 2px; }
      }
      @media print {
        body { background: #fff; }
        header { color: var(--ink); background: #fff; padding: 0 0 14px; border-bottom: 2px solid var(--line); }
        main { padding: 14px 0 0; }
        .toolbar-actions, .filters { display: none !important; }
        .summary-card, .panel, .filters, .table-wrap, .detail-card { box-shadow: none; border-color: #cfd8e3; }
        table { min-width: 0; font-size: 11px; }
        th, td { padding: 8px; }
      }
    """


def admin_shell(title, body):
    return f"""<!doctype html>
    <html lang="en-GB">
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <title>{html.escape(title)}</title>
      <style>{admin_styles()}</style>
    </head>
    <body>{body}</body>
    </html>"""


def admin_filter_options(options, selected, labels, include_all=True):
    all_option = '<option value="">All</option>' if include_all else ""
    return all_option + option_html(options, selected, labels)


def upcoming_availability_blocks(limit=12):
    today = datetime.utcnow().strftime("%Y-%m-%d")
    with connect_db() as db:
        return db.execute(
            """
            SELECT id, block_date, start_time, end_time, vans_blocked, movers_blocked, reason
            FROM availability_blocks
            WHERE block_date >= ?
            ORDER BY block_date, start_time
            LIMIT ?
            """,
            (today, limit),
        ).fetchall()


def render_availability_panel(csrf_token):
    blocks = upcoming_availability_blocks()
    rows = []
    for block in blocks:
        rows.append(
            f"""
            <tr>
              <td>{html.escape(block['block_date'])}<br><small>{html.escape(block['start_time'])} to {html.escape(block['end_time'])}</small></td>
              <td>{int(block['vans_blocked'])} vans</td>
              <td>{html.escape(block['reason'] or 'No reason added')}</td>
              <td>
                <form method="post" action="/admin/availability/{int(block['id'])}/delete">
                  <input type="hidden" name="_csrf" value="{html.escape(csrf_token)}">
                  <button type="submit" class="button-secondary">Remove</button>
                </form>
              </td>
            </tr>
            """
        )
    return f"""
      <section class="detail-grid" style="margin-bottom:18px">
        <div class="detail-card">
          <h2>Block availability</h2>
          <p><small>Use this when Luton vans are already committed, off the road, or unavailable. Movers do not limit online availability.</small></p>
          <form class="ops-form" method="post" action="/admin/availability">
            <input type="hidden" name="_csrf" value="{html.escape(csrf_token)}">
            <div class="two">
              <label>Date<input type="date" name="block_date" required></label>
              <label>Reason<input name="reason" placeholder="Private job, maintenance, staff unavailable"></label>
            </div>
            <div class="two">
              <label>Start time<select name="start_time">{''.join(f'<option value="{slot}">{slot}</option>' for slot in preferred_time_slots())}</select></label>
              <label>End time<select name="end_time">{''.join(f'<option value="{slot}">{slot}</option>' for slot in preferred_time_slots())}</select></label>
            </div>
            <div class="two">
              <label>Vans blocked<input type="number" min="0" max="{FLEET_LUTON_VANS}" name="vans_blocked" value="{FLEET_LUTON_VANS}"></label>
            </div>
            <button>Add availability block</button>
          </form>
        </div>
        <div class="detail-card">
          <h2>Upcoming blocks</h2>
          <div class="table-wrap">
            <table style="min-width:0">
              <thead><tr><th>Date</th><th>Capacity</th><th>Reason</th><th></th></tr></thead>
              <tbody>{''.join(rows) if rows else '<tr><td colspan="4" class="empty">No upcoming blocks.</td></tr>'}</tbody>
            </table>
          </div>
        </div>
      </section>
    """


def safe_admin_return_path(value, fallback="/admin"):
    value = str(value or "").strip()
    if not value:
        return fallback
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme or parsed.netloc:
        return fallback
    if not parsed.path.startswith("/admin"):
        return fallback
    return urllib.parse.urlunparse(("", "", parsed.path, "", parsed.query, ""))


def optional_form_int(form, name, maximum):
    raw = compact((form.get(name) or [""])[0], 20)
    if raw == "":
        return None
    return max(0, min(first_int(raw), maximum))


def render_manifest(date_text):
    if not valid_move_date(date_text):
        date_text = datetime.utcnow().strftime("%Y-%m-%d")
    with connect_db() as db:
        rows = db.execute(
            """
            SELECT *
            FROM bookings
            WHERE move_date = ?
              AND status NOT IN ('cancelled', 'refunded', 'no_show')
            ORDER BY move_time, id
            """,
            (date_text,),
        ).fetchall()

    total_vans = 0
    total_movers = 0
    total_revenue = 0.0
    total_balance = 0.0
    job_rows = []
    for row in rows:
        vans, movers = row_capacity(row)
        total_vans += vans
        total_movers += movers
        total_revenue += float(row["total_inc_vat"] or 0)
        total_balance += float(row["balance_amount"] or 0)
        reference_url = urllib.parse.quote(row["reference"])
        additional_addresses = parse_additional_addresses(row)
        extras = "".join(
            f"<li>{html.escape((item.get('postcode', '') + ' ' + item.get('address', '')).strip())}</li>"
            for item in additional_addresses
            if isinstance(item, dict)
        ) or "<li>No additional stops</li>"
        team = row["assigned_team"] or "Not assigned"
        notes = row["admin_notes"] or row["item_notes"] or "No job notes"
        job_rows.append(
            f"""
            <tr>
              <td><strong>{html.escape(row['move_time'] or '')}</strong><br><a href="/admin/bookings/{reference_url}">{html.escape(row['reference'])}</a></td>
              <td>{html.escape(row['customer_name'])}<br><small>{html.escape(row['customer_phone'])}<br>{html.escape(row['customer_email'])}</small></td>
              <td>
                <strong>Pickup</strong><br>{html.escape(row['pickup_address'] or row['pickup_postcode'] or '')}<br>
                <strong>Stops</strong><ul>{extras}</ul>
                <strong>Delivery</strong><br>{html.escape(row['delivery_address'] or row['delivery_postcode'] or '')}
              </td>
              <td>{vans} vans<br>{movers} movers<br>{row['estimated_hours']:g} hours<br><small>{html.escape(team)}</small></td>
              <td><span class="badge {status_badge_class(row['status'])}">{html.escape(status_label(row['status']))}</span><br><span class="badge {status_badge_class(row['payment_status'])}">{html.escape(payment_status_label(row['payment_status']))}</span><br><small>Balance {admin_money(row['balance_amount'])}</small></td>
              <td>{html.escape(notes)}</td>
            </tr>
            """
        )

    body = f"""
      <header>
        <h1>Daily operations manifest</h1>
        <p>{html.escape(date_text)} · {len(rows)} active job{'s' if len(rows) != 1 else ''}</p>
      </header>
      <main>
        <p class="toolbar-actions"><a class="button-link button-secondary" href="/admin">Back to dashboard</a></p>
        <form class="filters" method="get" action="/admin/manifest">
          <div class="filter-grid">
            <label>Manifest date<input type="date" name="date" value="{html.escape(date_text)}" required></label>
          </div>
          <p class="toolbar-actions" style="margin:12px 0 0"><button>View date</button></p>
        </form>
        <section class="summary-grid" aria-label="Manifest summary">
          <div class="summary-card"><span>Jobs</span><strong>{len(rows)}</strong></div>
          <div class="summary-card"><span>Vans committed</span><strong>{total_vans}</strong></div>
          <div class="summary-card"><span>Movers committed</span><strong>{total_movers}</strong></div>
          <div class="summary-card"><span>Revenue booked</span><strong>{admin_money(total_revenue)}</strong></div>
          <div class="summary-card"><span>Balance due</span><strong>{admin_money(total_balance)}</strong></div>
        </section>
        <section class="table-wrap">
          <table>
            <thead><tr><th>Time / ref</th><th>Customer</th><th>Route</th><th>Team</th><th>Status</th><th>Notes</th></tr></thead>
            <tbody>{''.join(job_rows) if job_rows else '<tr><td colspan="6" class="empty">No active jobs for this date.</td></tr>'}</tbody>
          </table>
        </section>
      </main>
    """
    return admin_shell(f"Manifest {date_text}", body)


def render_admin(notice="", csrf_token="", filters=None, return_to="/admin"):
    filters = filters or {}
    status_filter = filters.get("status", "")
    payment_filter = filters.get("payment_status", "")
    q = filters.get("q", "")
    date_from = filters.get("date_from", "")
    date_to = filters.get("date_to", "")
    where = []
    params = []
    if status_filter in BOOKING_STATUSES:
        where.append("status = ?")
        params.append(status_filter)
    if payment_filter in PAYMENT_STATUSES:
        where.append("payment_status = ?")
        params.append(payment_filter)
    if q:
        like = f"%{q.lower()}%"
        where.append(
            "(lower(reference) LIKE ? OR lower(customer_name) LIKE ? OR lower(customer_email) LIKE ? "
            "OR lower(customer_phone) LIKE ? OR lower(pickup_postcode) LIKE ? OR lower(delivery_postcode) LIKE ?)"
        )
        params.extend([like] * 6)
    if date_from:
        where.append("move_date >= ?")
        params.append(date_from)
    if date_to:
        where.append("move_date <= ?")
        params.append(date_to)
    where_sql = " WHERE " + " AND ".join(where) if where else ""

    with connect_db() as db:
        rows = db.execute(f"SELECT * FROM bookings{where_sql} ORDER BY id DESC LIMIT 200", params).fetchall()
        summary = db.execute(
            """
            SELECT
              COUNT(*) AS total,
              COALESCE(SUM(total_inc_vat), 0) AS gross_total,
              COALESCE(SUM(balance_amount), 0) AS balance_total,
              COALESCE(SUM(CASE WHEN status IN ('new','confirmed','assigned','in_progress') THEN 1 ELSE 0 END), 0) AS active_jobs,
              COALESCE(SUM(CASE WHEN payment_status IN ('pending','deposit_pending','full_pending') THEN 1 ELSE 0 END), 0) AS awaiting_payment
            FROM bookings
            """
        ).fetchone()

    email_state = "Ready" if email_enabled() else "Not configured"
    email_state_class = "ok" if email_enabled() else "warn"
    today_manifest = datetime.utcnow().strftime("%Y-%m-%d")
    notice_html = f'<p class="notice">{html.escape(notice)}</p>' if notice else ""
    booking_rows = []
    for row in rows:
        reference_text = row["reference"]
        reference = html.escape(reference_text)
        reference_url = urllib.parse.quote(reference_text)
        total = admin_money(row["total_inc_vat"])
        due_now_value = row["deposit_amount"] if row["payment_option"] == "deposit" else row["total_inc_vat"]
        due_now = admin_money(due_now_value)
        balance = admin_money(row["balance_amount"])
        stripe_detail = row["stripe_checkout_session_id"] or "No Stripe session yet"
        paid_at = f"<br><small>Paid {html.escape(row['paid_at'])}</small>" if row["paid_at"] else ""
        quote = quote_from_row(row)
        overtime_rate = float(quote.get("overtime", {}).get("hourlyRateIncVat") or 0)
        overtime_half_hour = float(quote.get("overtime", {}).get("halfHourRateIncVat") or overtime_rate / 2)
        additional_addresses = parse_additional_addresses(row)
        extra_stop_text = ""
        if additional_addresses:
            extra_stop_text = "<br><small>Extra stops: " + html.escape(
                "; ".join(
                    f"{item.get('postcode', '')} {item.get('address', '')}".strip()
                    for item in additional_addresses
                    if isinstance(item, dict)
                )
            ) + "</small>"
        customer_email_state = row["confirmation_email_sent_at"] or "Not sent"
        office_email_state = row["office_email_sent_at"] or "Not sent"
        email_badge_class = "ok" if row["confirmation_email_sent_at"] and row["office_email_sent_at"] else "warn"
        assigned = []
        if row["assigned_vehicle_count"]:
            assigned.append(f"{row['assigned_vehicle_count']} vans")
        if row["assigned_mover_count"]:
            assigned.append(f"{row['assigned_mover_count']} movers")
        if row["assigned_team"]:
            assigned.append(row["assigned_team"])
        assigned_text = ", ".join(assigned) if assigned else "Not assigned"
        booking_rows.append(
            f"""
            <tr>
              <td><a href="/admin/bookings/{reference_url}"><strong>{reference}</strong></a><br><small>{html.escape(row['created_at'])}</small></td>
              <td>{html.escape(row['customer_name'])}<br><small>{html.escape(row['customer_email'])}<br>{html.escape(row['customer_phone'])}</small></td>
              <td>{html.escape(row['move_date'] or '')}<br><small>{html.escape(row['move_time'] or '')}</small></td>
              <td>{html.escape(row['pickup_postcode'] or '')} → {html.escape(row['delivery_postcode'] or '')}<br><small>{row['luton_vans']} vans, {row['movers']} men, {row['estimated_hours']:g} hrs<br>{html.escape(assigned_text)}</small>{extra_stop_text}</td>
              <td>{total}<br><small>Due now {due_now}<br>Balance {balance}<br>Overtime £{overtime_rate:.2f}/hr<br>£{overtime_half_hour:.2f}/30 mins<br>{html.escape(stripe_detail)}</small>{paid_at}</td>
              <td>
                <span class="badge {status_badge_class(row['status'])}">{html.escape(status_label(row['status']))}</span>
                <span class="badge {status_badge_class(row['payment_status'])}">{html.escape(payment_status_label(row['payment_status']))}</span>
                <form class="mini-form" method="post" action="/admin/bookings/{reference_url}/status">
                  <input type="hidden" name="_csrf" value="{html.escape(csrf_token)}">
                  <input type="hidden" name="return_to" value="{html.escape(return_to)}">
                  <div class="two">
                    <select name="status">{option_html(BOOKING_STATUSES, row["status"], STATUS_LABELS)}</select>
                    <select name="payment_status">{option_html(PAYMENT_STATUSES, row["payment_status"], PAYMENT_STATUS_LABELS)}</select>
                  </div>
                  <button>Update</button>
                </form>
                <div class="email-panel">
                  <span class="badge {email_badge_class}">Email</span>
                  <small>Customer: {html.escape(customer_email_state)}<br>Office: {html.escape(office_email_state)}</small>
                </div>
              </td>
            </tr>
            """
        )

    body = f"""
      <header>
        <h1>Men With a Van Admin</h1>
        <p>Manage bookings, payment state, move dates, assigned resources and customer confirmation records.</p>
      </header>
      <main>
        {notice_html}
        <section class="summary-grid" aria-label="Booking summary">
          <div class="summary-card"><span>Total bookings</span><strong>{int(summary['total'])}</strong></div>
          <div class="summary-card"><span>Active jobs</span><strong>{int(summary['active_jobs'])}</strong></div>
          <div class="summary-card"><span>Awaiting payment</span><strong>{int(summary['awaiting_payment'])}</strong></div>
          <div class="summary-card"><span>Gross booked</span><strong>{admin_money(summary['gross_total'])}</strong></div>
          <div class="summary-card"><span>Open balances</span><strong>{admin_money(summary['balance_total'])}</strong></div>
        </section>
        <section class="toolbar">
          <div>
            <strong>Email confirmations</strong><br>
            <span class="badge {email_state_class}">{email_state}</span>
            <small>Sender: {html.escape(SMTP_FROM or 'Not configured')} · Office copy: {html.escape(OFFICE_EMAIL or 'Not configured')}</small>
          </div>
          <div class="toolbar-actions">
            <form method="post" action="/admin/email/test">
              <input type="hidden" name="_csrf" value="{html.escape(csrf_token)}">
              <button type="submit">Send test email</button>
            </form>
            <a class="button-link button-secondary" href="/admin/bookings.csv">Download CSV</a>
            <a class="button-link button-secondary" href="/admin/manifest?date={today_manifest}">Today manifest</a>
            <a class="button-link button-secondary" href="/">View website</a>
          </div>
        </section>
        {render_availability_panel(csrf_token)}
        <form class="filters" method="get" action="/admin">
          <div class="filter-grid">
            <label>Search<input name="q" value="{html.escape(q)}" placeholder="Reference, customer, email, phone"></label>
            <label>Job status<select name="status">{admin_filter_options(BOOKING_STATUSES, status_filter, STATUS_LABELS)}</select></label>
            <label>Payment<select name="payment_status">{admin_filter_options(PAYMENT_STATUSES, payment_filter, PAYMENT_STATUS_LABELS)}</select></label>
            <label>From date<input type="date" name="date_from" value="{html.escape(date_from)}"></label>
            <label>To date<input type="date" name="date_to" value="{html.escape(date_to)}"></label>
          </div>
          <p class="toolbar-actions" style="margin:12px 0 0"><button>Apply filters</button><a class="button-link button-secondary" href="/admin">Clear</a></p>
        </form>
        <section class="table-wrap">
          <table>
            <thead><tr><th>Reference</th><th>Customer</th><th>Date</th><th>Move</th><th>Total</th><th>Status</th></tr></thead>
            <tbody>{''.join(booking_rows) if booking_rows else '<tr><td colspan="6" class="empty">No bookings match this view.</td></tr>'}</tbody>
          </table>
        </section>
      </main>
    """
    return admin_shell("Men With a Van Admin", body)


def render_booking_detail(reference, notice="", csrf_token=""):
    with connect_db() as db:
        row = db.execute("SELECT * FROM bookings WHERE reference = ?", (reference,)).fetchone()
    if not row:
        return admin_shell(
            "Booking not found",
            """<header><h1>Booking not found</h1><p>The requested booking could not be found.</p></header><main><a class="button-link" href="/admin">Back to dashboard</a></main>""",
        )

    quote = quote_from_row(row)
    line_items = quote.get("lineItems") or []
    quote_lines = "".join(
        f"<li><span>{html.escape(str(item.get('label', 'Charge')))}</span><strong>{admin_money(item.get('amountExVat'))} ex VAT</strong></li>"
        for item in line_items
        if isinstance(item, dict)
    )
    additional_addresses = parse_additional_addresses(row)
    additional_html = "".join(
        f"<li>{html.escape((item.get('postcode', '') + ' ' + item.get('address', '')).strip())}</li>"
        for item in additional_addresses
        if isinstance(item, dict)
    ) or "<li>No additional stops</li>"
    reference_url = urllib.parse.quote(row["reference"])
    notice_html = f'<p class="notice">{html.escape(notice)}</p>' if notice else ""
    calendar_url = (
        f"{SITE_BASE_URL}/api/bookings/{urllib.parse.quote(row['reference'])}/calendar.ics?token={urllib.parse.quote(row['calendar_token'] or '')}"
        if row["calendar_token"]
        else ""
    )
    due_now = row["deposit_amount"] if row["payment_option"] == "deposit" else row["total_inc_vat"]
    event_rows = []
    for event in booking_events(row["reference"]):
        event_rows.append(
            f"""
            <tr>
              <td>{html.escape(event['created_at'])}<br><small>{html.escape(event['actor'] or '')}</small></td>
              <td>{html.escape(event['summary'] or '')}<br><small>{html.escape(event['event_type'] or '')}</small></td>
            </tr>
            """
        )
    body = f"""
      <header>
        <h1>Booking {html.escape(row['reference'])}</h1>
        <p>{html.escape(row['customer_name'])} · {html.escape(row['move_date'] or 'No date')} at {html.escape(row['move_time'] or 'No time')}</p>
      </header>
      <main>
        {notice_html}
        <p class="toolbar-actions"><a class="button-link button-secondary" href="/admin">Back to dashboard</a><a class="button-link button-secondary" href="/admin/bookings.csv">Download CSV</a></p>
        <section class="detail-grid">
          <div class="detail-card">
            <h2>Booking details</h2>
            <dl class="detail-list">
              <div><dt>Status</dt><dd><span class="badge {status_badge_class(row['status'])}">{html.escape(status_label(row['status']))}</span><span class="badge {status_badge_class(row['payment_status'])}">{html.escape(payment_status_label(row['payment_status']))}</span></dd></div>
              <div><dt>Customer</dt><dd>{html.escape(row['customer_name'])}<br><small>{html.escape(row['customer_email'])}<br>{html.escape(row['customer_phone'])}</small></dd></div>
              <div><dt>Move date</dt><dd>{html.escape(row['move_date'] or '')} {html.escape(row['move_time'] or '')}</dd></div>
              <div><dt>Move type</dt><dd>{html.escape(row['move_type'] or '')}</dd></div>
              <div><dt>Resources quoted</dt><dd>{row['luton_vans']} vans · {row['movers']} men · {row['estimated_hours']:g} booked hours</dd></div>
              <div><dt>Pickup</dt><dd>{html.escape(row['pickup_postcode'] or '')}<br><small>{html.escape(row['pickup_address'] or '')}</small></dd></div>
              <div><dt>Delivery</dt><dd>{html.escape(row['delivery_postcode'] or '')}<br><small>{html.escape(row['delivery_address'] or '')}</small></dd></div>
              <div><dt>Extra stops</dt><dd><ul>{additional_html}</ul></dd></div>
              <div><dt>Items / notes</dt><dd>{html.escape(row['item_notes'] or 'No item notes')}</dd></div>
              <div><dt>Customer access notes</dt><dd>{html.escape(row['access_notes'] or 'No access notes')}</dd></div>
            </dl>
          </div>
          <aside class="detail-card">
            <h2>Operations control</h2>
            <form class="ops-form" method="post" action="/admin/bookings/{reference_url}/status">
              <input type="hidden" name="_csrf" value="{html.escape(csrf_token)}">
              <input type="hidden" name="return_to" value="/admin/bookings/{reference_url}">
              <div class="two">
                <label>Job status<select name="status">{option_html(BOOKING_STATUSES, row["status"], STATUS_LABELS)}</select></label>
                <label>Payment status<select name="payment_status">{option_html(PAYMENT_STATUSES, row["payment_status"], PAYMENT_STATUS_LABELS)}</select></label>
              </div>
              <div class="two">
                <label>Assigned vans<input type="number" min="0" max="{MAX_VANS}" name="assigned_vehicle_count" value="{html.escape(str(row['assigned_vehicle_count'] or ''))}"></label>
                <label>Assigned movers<input type="number" min="0" max="{MAX_BOOKABLE_MOVERS}" name="assigned_mover_count" value="{html.escape(str(row['assigned_mover_count'] or ''))}"></label>
              </div>
              <label>Team / driver<input name="assigned_team" value="{html.escape(row['assigned_team'] or '')}" placeholder="Driver, crew or van IDs"></label>
              <label>Internal office notes<textarea name="admin_notes" placeholder="Private notes for office/admin only">{html.escape(row['admin_notes'] or '')}</textarea></label>
              <button>Save operations update</button>
            </form>
            <div class="email-panel">
              <h3>Email confirmation</h3>
              <p><small>Customer: {html.escape(row['confirmation_email_sent_at'] or 'Not sent')}<br>Office: {html.escape(row['office_email_sent_at'] or 'Not sent')}</small></p>
              <form method="post" action="/admin/bookings/{reference_url}/email">
                <input type="hidden" name="_csrf" value="{html.escape(csrf_token)}">
                <input type="hidden" name="return_to" value="/admin/bookings/{reference_url}">
                <button>Send / resend confirmation</button>
              </form>
            </div>
          </aside>
        </section>
        <section class="detail-card" style="margin-top:18px">
          <h2>Edit customer and move details</h2>
          <p><small>Use this for office corrections or rescheduling. Date and time changes are checked against live Luton van availability before they save.</small></p>
          <form class="ops-form" method="post" action="/admin/bookings/{reference_url}/details">
            <input type="hidden" name="_csrf" value="{html.escape(csrf_token)}">
            <input type="hidden" name="return_to" value="/admin/bookings/{reference_url}">
            <div class="two">
              <label>Customer name<input name="customer_name" value="{html.escape(row['customer_name'] or '')}" required></label>
              <label>Email<input type="email" name="customer_email" value="{html.escape(row['customer_email'] or '')}" required></label>
            </div>
            <div class="two">
              <label>Phone<input name="customer_phone" value="{html.escape(row['customer_phone'] or '')}" required></label>
              <label>Move type<input name="move_type" value="{html.escape(row['move_type'] or '')}" placeholder="House move, office move, storage run"></label>
            </div>
            <div class="two">
              <label>Move date<input type="date" name="move_date" value="{html.escape(row['move_date'] or '')}" required></label>
              <label>Arrival time<select name="move_time">{time_options_html(row['move_time'])}</select></label>
            </div>
            <div class="two">
              <label>Pickup full address<textarea name="pickup_address" required>{html.escape(row['pickup_address'] or '')}</textarea></label>
              <label>Delivery full address<textarea name="delivery_address" required>{html.escape(row['delivery_address'] or '')}</textarea></label>
            </div>
            <label>What needs moving / customer notes<textarea name="item_notes">{html.escape(row['item_notes'] or '')}</textarea></label>
            <button>Save booking details</button>
          </form>
        </section>
        <section class="detail-grid" style="margin-top:18px">
          <div class="detail-card">
            <h2>Payment and pricing</h2>
            <dl class="detail-list">
              <div><dt>Total inc VAT</dt><dd>{admin_money(row['total_inc_vat'])}</dd></div>
              <div><dt>Due now</dt><dd>{admin_money(due_now)} <small>({html.escape(row['payment_option'])})</small></dd></div>
              <div><dt>Balance</dt><dd>{admin_money(row['balance_amount'])}</dd></div>
              <div><dt>Stripe session</dt><dd>{html.escape(row['stripe_checkout_session_id'] or 'No Stripe session yet')}</dd></div>
              <div><dt>Stripe payment</dt><dd>{html.escape(row['stripe_payment_intent_id'] or 'No payment intent yet')}<br><small>{html.escape(row['paid_at'] or 'Not marked paid')}</small></dd></div>
              <div><dt>Calendar</dt><dd>{f'<a href="{html.escape(calendar_url)}">Download calendar invite</a>' if calendar_url else 'No calendar invite token'}</dd></div>
            </dl>
          </div>
          <div class="detail-card">
            <h2>Quote line items</h2>
            <ul class="quote-lines">{quote_lines or '<li><span>No quote line items found</span></li>'}</ul>
          </div>
        </section>
        <section class="detail-card" style="margin-top:18px">
          <h2>Activity history</h2>
          <div class="table-wrap">
            <table style="min-width:0">
              <thead><tr><th>Time</th><th>Event</th></tr></thead>
              <tbody>{''.join(event_rows) if event_rows else '<tr><td colspan="2" class="empty">No history recorded yet.</td></tr>'}</tbody>
            </table>
          </div>
        </section>
      </main>
    """
    return admin_shell(f"Booking {row['reference']}", body)


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
        "confirmation_email_sent_at", "office_email_sent_at",
        "assigned_vehicle_count", "assigned_mover_count", "assigned_team",
        "admin_notes", "last_admin_update_at", "completed_at", "cancelled_at"
    ]
    writer.writerow(columns)
    for row in rows:
        writer.writerow([csv_safe(row[col]) for col in columns])
    return output.getvalue().encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    server_version = "MenWithVanQuote/1.0"

    def log_message(self, fmt, *args):
        message = fmt % args
        message = re.sub(r"([?&](?:session_id|token|signature|key)=)[^\s&]+", r"\1[redacted]", message, flags=re.I)
        print("%s - %s" % (self.address_string(), message))

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/health":
            return json_response(self, 200, {"ok": True})
        if path == "/api/availability":
            if rate_limited(self, path):
                return json_response(self, 429, {"error": "Too many availability checks. Please wait a moment and try again."})
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            date_text = compact((query.get("date") or [""])[0], 30)
            if not valid_move_date(date_text):
                return json_response(self, 400, {"error": "Valid move date is required."})
            hours = first_int((query.get("hours") or [MINIMUM_HOURS])[0], int(MINIMUM_HOURS))
            vans = first_int((query.get("vans") or [1])[0], 1)
            movers = first_int((query.get("movers") or [1])[0], 1)
            return json_response(self, 200, availability_for_date(date_text, hours, vans, movers))
        if path == "/api/payments/session":
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            session_id = compact((query.get("session_id") or [""])[0], 140)
            if not session_id:
                return json_response(self, 400, {"error": "Session ID is required."})
            session = public_payment_session(session_id)
            if not session:
                return json_response(self, 404, {"error": "Payment session not found."})
            return json_response(self, 200, session)
        draft_match = re.match(r"^/api/booking-drafts/([a-f0-9]{44})$", path)
        if draft_match:
            if rate_limited(self, "/api/booking-drafts"):
                return json_response(self, 429, {"error": "Too many saved quote requests. Please wait a moment and try again."})
            draft = get_booking_draft(draft_match.group(1))
            if not draft:
                return json_response(self, 404, {"error": "Saved quote link was not found or has expired."})
            return json_response(self, 200, draft)
        calendar_match = re.match(r"^/api/bookings/([^/]+)/calendar\.ics$", path)
        if calendar_match:
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            token = compact((query.get("token") or [""])[0], 140)
            row = calendar_booking(calendar_match.group(1), token)
            if not row:
                return json_response(self, 404, {"error": "Calendar invite not found."})
            body = booking_ics(row)
            self.send_response(200)
            send_security_headers(self)
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
            filters = {
                "status": compact((query.get("status") or [""])[0], 30),
                "payment_status": compact((query.get("payment_status") or [""])[0], 30),
                "q": compact((query.get("q") or [""])[0], 120).lower(),
                "date_from": compact((query.get("date_from") or [""])[0], 30),
                "date_to": compact((query.get("date_to") or [""])[0], 30),
            }
            csrf_token = admin_csrf_token(self)
            return html_response(
                self,
                200,
                render_admin(notice, csrf_token, filters, self.path),
                {"Set-Cookie": admin_csrf_cookie_header(csrf_token)},
            )
        if path == "/admin/manifest":
            if not require_admin(self):
                return
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            date_text = compact((query.get("date") or [datetime.utcnow().strftime("%Y-%m-%d")])[0], 30)
            csrf_token = admin_csrf_token(self)
            return html_response(
                self,
                200,
                render_manifest(date_text),
                {"Set-Cookie": admin_csrf_cookie_header(csrf_token)},
            )
        detail_match = re.match(r"^/admin/bookings/([^/]+)$", path)
        if detail_match:
            if not require_admin(self):
                return
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            notice = compact((query.get("notice") or [""])[0], 240)
            reference = compact(urllib.parse.unquote(detail_match.group(1)), 80)
            csrf_token = admin_csrf_token(self)
            return html_response(
                self,
                200,
                render_booking_detail(reference, notice, csrf_token),
                {"Set-Cookie": admin_csrf_cookie_header(csrf_token)},
            )
        if path == "/admin/bookings.csv":
            if not require_admin(self):
                return
            body = bookings_csv()
            self.send_response(200)
            send_security_headers(self)
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
            if rate_limited(self, path):
                return json_response(self, 429, {"error": "Too many quote requests. Please wait a moment and try again."})
            try:
                payload = read_json(self)
            except Exception:
                return json_response(self, 400, {"error": "Invalid JSON."})

            quote, errors = build_quote(payload)
            if errors:
                return json_response(self, 400, {"errors": errors})
            return json_response(self, 200, quote)

        if path == "/api/bookings":
            if rate_limited(self, path):
                return json_response(self, 429, {"error": "Too many booking attempts. Please wait a moment and try again."})
            try:
                payload = read_json(self)
            except Exception:
                return json_response(self, 400, {"error": "Invalid JSON."})
            booking, errors = create_booking(payload)
            if errors:
                return json_response(self, 400, {"errors": errors})
            return json_response(self, 201, booking)

        payment_refresh_match = re.match(r"^/api/bookings/([^/]+)/payment-session$", path)
        if payment_refresh_match:
            if rate_limited(self, "/api/bookings"):
                return json_response(self, 429, {"error": "Too many booking updates. Please wait a moment and try again."})
            try:
                payload = read_json(self)
            except Exception:
                return json_response(self, 400, {"error": "Invalid JSON."})
            booking, errors = refresh_booking_payment(payment_refresh_match.group(1), payload)
            if errors:
                return json_response(self, 400, {"errors": errors})
            return json_response(self, 200, booking)

        if path == "/api/booking-drafts":
            if rate_limited(self, path):
                return json_response(self, 429, {"error": "Too many saved quote requests. Please wait a moment and try again."})
            try:
                payload = read_json(self)
            except Exception:
                return json_response(self, 400, {"error": "Invalid JSON."})
            draft, errors = create_booking_draft(payload)
            if errors:
                return json_response(self, 400, {"errors": errors})
            return json_response(self, 201, draft)

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

        if path == "/admin/availability":
            if not require_admin(self):
                return
            length = min(int(self.headers.get("Content-Length", "0")), 5_000)
            form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
            if not admin_csrf_valid(self, form):
                return json_response(self, 403, {"error": "Admin security token is invalid. Refresh the admin page and try again."})
            block_date = compact((form.get("block_date") or [""])[0], 30)
            start_time = compact((form.get("start_time") or [""])[0], 10)
            end_time = compact((form.get("end_time") or [""])[0], 10)
            vans_blocked = optional_form_int(form, "vans_blocked", FLEET_LUTON_VANS) or 0
            movers_blocked = 0
            reason = compact((form.get("reason") or [""])[0], 240)
            notice = "Availability block was not added. Check the date, times and capacity."
            if (
                valid_move_date(block_date)
                and start_time in preferred_time_slots()
                and end_time in preferred_time_slots()
                and end_time > start_time
                and vans_blocked > 0
            ):
                with connect_db() as db:
                    db.execute(
                        """
                        INSERT INTO availability_blocks (
                            created_at, block_date, start_time, end_time,
                            vans_blocked, movers_blocked, reason
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (now_iso(), block_date, start_time, end_time, vans_blocked, movers_blocked, reason),
                    )
                notice = f"Availability blocked for {block_date} from {start_time} to {end_time}."
            return redirect_response(self, "/admin?" + urllib.parse.urlencode({"notice": notice}))

        availability_delete_match = re.match(r"^/admin/availability/(\d+)/delete$", path)
        if availability_delete_match:
            if not require_admin(self):
                return
            length = min(int(self.headers.get("Content-Length", "0")), 5_000)
            form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
            if not admin_csrf_valid(self, form):
                return json_response(self, 403, {"error": "Admin security token is invalid. Refresh the admin page and try again."})
            with connect_db() as db:
                db.execute("DELETE FROM availability_blocks WHERE id = ?", (int(availability_delete_match.group(1)),))
            return redirect_response(self, "/admin?" + urllib.parse.urlencode({"notice": "Availability block removed."}))

        details_match = re.match(r"^/admin/bookings/([^/]+)/details$", path)
        if details_match:
            if not require_admin(self):
                return
            length = min(int(self.headers.get("Content-Length", "0")), 20_000)
            form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
            if not admin_csrf_valid(self, form):
                return json_response(self, 403, {"error": "Admin security token is invalid. Refresh the admin page and try again."})
            reference = compact(urllib.parse.unquote(details_match.group(1)), 80)
            return_to = safe_admin_return_path((form.get("return_to") or [""])[0], f"/admin/bookings/{urllib.parse.quote(reference)}")
            with connect_db() as db:
                row = db.execute("SELECT * FROM bookings WHERE reference = ?", (reference,)).fetchone()
            if not row:
                return redirect_response(self, return_to + ("&" if "?" in return_to else "?") + urllib.parse.urlencode({"notice": "Booking not found."}))

            updates = {
                "customer_name": compact((form.get("customer_name") or [""])[0], 140),
                "customer_email": compact((form.get("customer_email") or [""])[0], 180).lower(),
                "customer_phone": compact((form.get("customer_phone") or [""])[0], 60),
                "move_type": compact((form.get("move_type") or [""])[0], 80),
                "move_date": compact((form.get("move_date") or [""])[0], 30),
                "move_time": compact((form.get("move_time") or [""])[0], 30),
                "pickup_address": compact((form.get("pickup_address") or [""])[0], 500),
                "delivery_address": compact((form.get("delivery_address") or [""])[0], 500),
                "item_notes": compact((form.get("item_notes") or [""])[0], 1500),
            }
            errors = []
            if not updates["customer_name"]:
                errors.append("Customer name is required.")
            if not email_like(updates["customer_email"]):
                errors.append("A valid customer email is required.")
            if not updates["customer_phone"]:
                errors.append("Customer phone is required.")
            if not valid_move_date(updates["move_date"]):
                errors.append("A valid move date is required.")
            if updates["move_time"] not in preferred_time_slots():
                errors.append("Choose an available arrival time.")
            if not updates["pickup_address"]:
                errors.append("Pickup address is required.")
            if not updates["delivery_address"]:
                errors.append("Delivery address is required.")

            schedule_changed = updates["move_date"] != (row["move_date"] or "") or updates["move_time"] != (row["move_time"] or "")
            separator = "&" if "?" in return_to else "?"
            if errors:
                return redirect_response(self, return_to + separator + urllib.parse.urlencode({"notice": "Could not update booking: " + " ".join(errors)}))

            changes = changed_fields(row, updates)
            with booking_capacity_lock:
                if schedule_changed and row["status"] not in {"completed", "cancelled", "refunded", "no_show"}:
                    available, availability_error = slot_available(
                        updates["move_date"],
                        updates["move_time"],
                        row["estimated_hours"],
                        row["luton_vans"],
                        row["movers"],
                        exclude_reference=reference,
                    )
                    if not available:
                        return redirect_response(
                            self,
                            return_to
                            + separator
                            + urllib.parse.urlencode({"notice": "Could not update booking: " + availability_error}),
                        )

                with connect_db() as db:
                    db.execute(
                        """
                        UPDATE bookings
                        SET customer_name = ?,
                            customer_email = ?,
                            customer_phone = ?,
                            move_type = ?,
                            move_date = ?,
                            move_time = ?,
                            pickup_address = ?,
                            delivery_address = ?,
                            item_notes = ?,
                            last_admin_update_at = ?
                        WHERE reference = ?
                        """,
                        (
                            updates["customer_name"],
                            updates["customer_email"],
                            updates["customer_phone"],
                            updates["move_type"],
                            updates["move_date"],
                            updates["move_time"],
                            updates["pickup_address"],
                            updates["delivery_address"],
                            updates["item_notes"],
                            now_iso(),
                            reference,
                        ),
                    )
                    if changes:
                        log_booking_event(
                            reference,
                            "admin_booking_details_updated",
                            admin_actor(self),
                            "Admin updated booking details: " + ", ".join(change["label"] for change in changes),
                            {"changedFields": [change["field"] for change in changes]},
                            db=db,
                        )
            notice = "Booking details updated." if changes else "No booking detail changes were needed."
            return redirect_response(self, return_to + separator + urllib.parse.urlencode({"notice": notice}))

        status_match = re.match(r"^/admin/bookings/([^/]+)/status$", path)
        if status_match:
            if not require_admin(self):
                return
            length = min(int(self.headers.get("Content-Length", "0")), 15_000)
            form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
            if not admin_csrf_valid(self, form):
                return json_response(self, 403, {"error": "Admin security token is invalid. Refresh the admin page and try again."})
            status = compact((form.get("status") or [""])[0], 30)
            payment_status = compact((form.get("payment_status") or [""])[0], 30)
            return_to = safe_admin_return_path((form.get("return_to") or [""])[0])
            if status in BOOKING_STATUSES and payment_status in PAYMENT_STATUSES:
                updated_at = now_iso()
                completed_now = 1 if status == "completed" else 0
                cancelled_now = 1 if status in {"cancelled", "refunded", "no_show"} else 0
                reference = compact(urllib.parse.unquote(status_match.group(1)), 80)
                with booking_capacity_lock:
                    with connect_db() as db:
                        existing = db.execute(
                            """
                            SELECT status, payment_status, move_date, move_time, estimated_hours,
                                   luton_vans, movers, assigned_vehicle_count, assigned_mover_count,
                                   assigned_team, admin_notes
                            FROM bookings
                            WHERE reference = ?
                            """,
                            (reference,),
                        ).fetchone()
                        if not existing:
                            return redirect_response(self, return_to)
                        assigned_vehicle_count = (
                            optional_form_int(form, "assigned_vehicle_count", MAX_VANS)
                            if "assigned_vehicle_count" in form
                            else existing["assigned_vehicle_count"]
                        )
                        assigned_mover_count = (
                            optional_form_int(form, "assigned_mover_count", MAX_BOOKABLE_MOVERS)
                            if "assigned_mover_count" in form
                            else existing["assigned_mover_count"]
                        )
                        assigned_team = (
                            compact((form.get("assigned_team") or [""])[0], 180)
                            if "assigned_team" in form
                            else existing["assigned_team"]
                        )
                        admin_notes = (
                            compact((form.get("admin_notes") or [""])[0], 3000)
                            if "admin_notes" in form
                            else existing["admin_notes"]
                        )
                        requested_vans = assigned_vehicle_count or existing["luton_vans"]
                        requested_movers = assigned_mover_count or existing["movers"]
                        if status_holds_capacity(status, payment_status):
                            available, availability_error = slot_available(
                                existing["move_date"],
                                existing["move_time"],
                                existing["estimated_hours"],
                                requested_vans,
                                requested_movers,
                                exclude_reference=reference,
                            )
                            if not available:
                                separator = "&" if "?" in return_to else "?"
                                return redirect_response(
                                    self,
                                    return_to
                                    + separator
                                    + urllib.parse.urlencode({"notice": "Could not update booking: " + availability_error}),
                                )
                        db.execute(
                            """
                            UPDATE bookings
                            SET status = ?,
                                payment_status = ?,
                                assigned_vehicle_count = ?,
                                assigned_mover_count = ?,
                                assigned_team = ?,
                                admin_notes = ?,
                                last_admin_update_at = ?,
                                completed_at = CASE WHEN ? THEN COALESCE(completed_at, ?) ELSE completed_at END,
                                cancelled_at = CASE WHEN ? THEN COALESCE(cancelled_at, ?) ELSE cancelled_at END
                            WHERE reference = ?
                            """,
                            (
                                status,
                                payment_status,
                                assigned_vehicle_count,
                                assigned_mover_count,
                                assigned_team,
                                admin_notes,
                                updated_at,
                                completed_now,
                                updated_at,
                                cancelled_now,
                                updated_at,
                                reference,
                            ),
                        )
                        status_changes = []
                        if existing["status"] != status:
                            status_changes.append(f"job status {status_label(existing['status'])} → {status_label(status)}")
                        if existing["payment_status"] != payment_status:
                            status_changes.append(
                                f"payment {payment_status_label(existing['payment_status'])} → {payment_status_label(payment_status)}"
                            )
                        if (existing["assigned_vehicle_count"] or "") != (assigned_vehicle_count or ""):
                            status_changes.append("assigned vans changed")
                        if (existing["assigned_mover_count"] or "") != (assigned_mover_count or ""):
                            status_changes.append("assigned movers changed")
                        if (existing["assigned_team"] or "") != (assigned_team or ""):
                            status_changes.append("team/driver changed")
                        if (existing["admin_notes"] or "") != (admin_notes or ""):
                            status_changes.append("internal notes changed")
                        if status_changes:
                            log_booking_event(
                                reference,
                                "admin_status_updated",
                                admin_actor(self),
                                "Admin updated " + ", ".join(status_changes) + ".",
                                {
                                    "status": status,
                                    "paymentStatus": payment_status,
                                    "assignedVehicles": assigned_vehicle_count,
                                    "assignedMovers": assigned_mover_count,
                                },
                                db=db,
                            )
            return redirect_response(self, return_to)

        email_match = re.match(r"^/admin/bookings/([^/]+)/email$", path)
        if email_match:
            if not require_admin(self):
                return
            length = min(int(self.headers.get("Content-Length", "0")), 5_000)
            form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
            if not admin_csrf_valid(self, form):
                return json_response(self, 403, {"error": "Admin security token is invalid. Refresh the admin page and try again."})
            reference = compact(urllib.parse.unquote(email_match.group(1)), 80)
            return_to = safe_admin_return_path((form.get("return_to") or [""])[0])
            result = send_booking_confirmations(reference, force_customer=True, force_office=True)
            if result["errors"]:
                notice = f"Email issue for {reference}: " + "; ".join(result["errors"])
            else:
                notice = f"Email sent for {reference}. Customer: {result['customer']}. Office: {result['office']}."
            separator = "&" if "?" in return_to else "?"
            return redirect_response(self, return_to + separator + urllib.parse.urlencode({"notice": notice}))

        if path == "/admin/email/test":
            if not require_admin(self):
                return
            length = min(int(self.headers.get("Content-Length", "0")), 5_000)
            form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
            if not admin_csrf_valid(self, form):
                return json_response(self, 403, {"error": "Admin security token is invalid. Refresh the admin page and try again."})
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
    if not secure_admin_configured():
        print("WARNING: Admin dashboard is disabled until ADMIN_USER and a strong ADMIN_PASSWORD are configured.")
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Quote service listening on {HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
