# Men With Van Backend Blueprint

## Goal

Build a professional booking system for Men With Van that supports online quotes, multi-Luton-van bookings, deposits, full payments, admin management, and secure operations.

The business model is Luton-only: the customer does not choose a van size. They choose the size of the job, and the system calculates the number of Luton vans, movers, hours, mileage, stairs, congestion zone, VAT, deposit and balance.

## Confirmed Front-End Positioning

- Company name: Men With Van
- Fleet: 15 Luton vans
- Customer-facing capacity: book up to 5 Luton vans at once
- Service types: house removals, office removals, flat moves, student moves, furniture delivery, storage moves, packing/loading help
- Quote model: vans + movers + hours + mileage + stairs + congestion zone + VAT
- Minimum booking: 2 hours

## Pricing Rules

### VAT

- VAT should be added to all chargeable items.
- Assumed VAT rate: 20%.

### One Luton Van Hourly Rates

| Setup | Rate excluding VAT |
| --- | ---: |
| 1 Luton van + 1 man | £50/hour |
| 1 Luton van + 2 men | £65/hour |
| 1 Luton van + 3 men | £80/hour |

Minimum booking: 2 hours.

### Multi-Van Rates

Confirmed formula:

```text
hourly_rate = (£50 × number_of_luton_vans) + (£15 × extra_movers)
extra_movers = max(0, movers - number_of_luton_vans)
```

Examples:

| Setup | Rate excluding VAT |
| --- | ---: |
| 1 Luton van + 1 man | £50/hour |
| 1 Luton van + 2 men | £65/hour |
| 1 Luton van + 3 men | £80/hour |
| 2 Luton vans + 2 men | £100/hour |
| 2 Luton vans + 3 men | £115/hour |
| 3 Luton vans + 3 men | £150/hour |

The system requires at least one mover per Luton van.

### Mileage

- Mileage charge applies after pickup and delivery postcodes/addresses are entered.
- Current note: "£1 / £2 plus VAT per mile" needs confirmation.
- Recommended default until confirmed: keep mileage rate admin-editable, with an initial value of £2 + VAT per mile.

### Stairs

- Current note: £15 per floor per man plus VAT.
- Confirmed rule:
  - `stairs_fee = total_chargeable_floors * movers * £15`
  - VAT added after subtotal.

### Congestion Zone

- If pickup or delivery postcode/address is inside the London Congestion Charge zone, add £27 + VAT once per booking.
- Current live implementation geocodes pickup and delivery and checks them against a central London congestion-zone polygon based on the official TfL map.
- Business rule: routes that merely pass through the congestion zone do not trigger the charge.

### Payment Options

At checkout, customer can choose:

- Pay 25% deposit online, then pay balance on completion to driver by bank transfer, card or cash.
- Pay 100% online at booking.

The booking should store:

- Full quote
- VAT breakdown
- Deposit paid
- Balance due
- Payment method chosen
- Payment status
- Booking status

## Recommended Backend Architecture

### Stack

Recommended for speed, security and long-term maintainability:

- Front end: existing static marketing site
- Backend API: Node.js with Fastify or NestJS
- Database: PostgreSQL
- Payments: Stripe Checkout or PaymentIntents
- Email: transactional email provider such as Resend, Postmark or SendGrid
- Maps/distance: Google Maps Distance Matrix or Mapbox
- Hosting: same VPS initially, later Dockerised
- Reverse proxy: nginx

### Google Maps / Places Key Handling

The Google Places/Maps key must not be committed into source code.

Recommended setup:

- Store Google keys on the VPS as environment variables, never in public HTML.
- Use `GOOGLE_PLACES_API_KEY` for address lookup/autocomplete.
- Use `GOOGLE_DISTANCE_MATRIX_API_KEY` for distance calculation, postcode/address routing and mileage pricing.
- Use `GOOGLE_GEOCODING_API_KEY` for postcode/address coordinates used by the congestion-zone check.
- Use these keys server-side for distance calculation, postcode/address geocoding and congestion-zone checks.
- If browser address autocomplete is added, use a separate browser-restricted Google key, or restrict this key by HTTP referrer before it is exposed to the public page.

Recommended Google Cloud restrictions:

- For server-side quote calculations:
  - Application restriction: IP address `194.164.126.253`
  - API restrictions: Geocoding API, Distance Matrix API or Routes API
- For browser autocomplete:
  - Application restriction: HTTP referrers `https://www.menwithvan.com/*` and `https://menwithvan.com/*`
  - API restrictions: Places API and Maps JavaScript API

Do not leave the key unrestricted.

### Core Modules

1. Quote engine
   - Validates input
   - Calculates distance
   - Detects congestion zone
   - Applies pricing matrix
   - Applies stairs, VAT and minimum booking
   - Returns quote options

2. Booking engine
   - Converts accepted quote into booking
   - Collects customer details and full addresses
   - Stores move date/time
   - Blocks or warns if fleet/team availability is exceeded

3. Payment engine
   - Supports 25% deposit
   - Supports 100% payment
   - Handles Stripe webhooks
   - Updates payment status securely server-side

4. Admin dashboard
   - View bookings
   - Edit booking status
   - Manage rates
   - Manage van/team availability
   - View payment status and balance due
   - Export bookings

5. Notification system
   - Customer confirmation email
   - Admin notification email
   - Optional SMS/WhatsApp later

## Quote Formula Draft

```text
hours = max(customer_estimated_hours, 2)

base_hourly_rate = (50 * vans) + (15 * max(0, movers - vans))
labour_vehicle_subtotal = base_hourly_rate * hours

mileage_subtotal = route_miles * mileage_rate
stairs_subtotal = (pickup_stair_flights + delivery_stair_flights) * movers * stair_rate
congestion_subtotal = inside_congestion_zone ? congestion_fee : 0

subtotal_ex_vat = labour_vehicle_subtotal + mileage_subtotal + stairs_subtotal + congestion_subtotal
vat = subtotal_ex_vat * 0.20
total_inc_vat = subtotal_ex_vat + vat

deposit = total_inc_vat * 0.25
balance_due = total_inc_vat - deposit
```

## Database Tables

### customers

- id
- first_name
- last_name
- email
- phone
- created_at

### quotes

- id
- customer_id nullable
- move_type
- pickup_postcode
- delivery_postcode
- pickup_address nullable
- delivery_address nullable
- move_date nullable
- vans
- movers
- estimated_hours
- route_miles
- pickup_stair_flights
- delivery_stair_flights
- congestion_zone_applied
- subtotal_ex_vat
- vat
- total_inc_vat
- deposit_amount
- balance_amount
- status
- created_at

### bookings

- id
- quote_id
- customer_id
- move_date
- arrival_window
- pickup_address
- delivery_address
- item_notes
- packing_required
- dismantling_required
- payment_option
- payment_status
- booking_status
- created_at

### payments

- id
- booking_id
- provider
- provider_payment_id
- amount
- currency
- payment_type
- status
- created_at

### pricing_rules

- id
- vans
- movers
- hourly_rate_ex_vat
- active
- updated_at

### settings

- key
- value

Examples:

- vat_rate = 0.20
- minimum_hours = 2
- mileage_rate = 2.00
- stair_rate_per_floor_per_man = 15.00
- congestion_fee = 27.00
- max_bookable_vans_online = 5

## API Endpoints

### Public

- `POST /api/quotes`
  - Creates a quote calculation.
  - Uses `GOOGLE_DISTANCE_MATRIX_API_KEY` server-side to calculate distance from pickup and delivery postcodes/addresses.
  - Uses `GOOGLE_GEOCODING_API_KEY` server-side to detect whether pickup or delivery is inside the Congestion Charge zone.

- `POST /api/bookings`
  - Converts a quote into a pending booking.

- `POST /api/payments/create-session`
  - Creates Stripe checkout for deposit or full payment.

- `POST /api/webhooks/stripe`
  - Receives payment confirmation from Stripe.

### Admin

- `GET /admin/bookings`
- `GET /admin/bookings/:id`
- `PATCH /admin/bookings/:id`
- `GET /admin/pricing`
- `PATCH /admin/pricing`
- `GET /admin/settings`
- `PATCH /admin/settings`

## Security Standard

Minimum server and application security:

- HTTPS only
- Firewall allows only SSH, HTTP and HTTPS
- SSH key login only, disable password login once setup is complete
- No root password login long term
- Automatic security updates
- Daily database backups
- Stripe webhooks verified with signing secret
- Admin protected by strong authentication and two-factor authentication
- Rate limit quote and payment endpoints
- Input validation on all API requests
- Audit log for pricing and booking changes
- Environment secrets stored outside code
- Regular off-server backups

## Build Order

1. Build quote calculator API. Done: live at `/api/quote`.
2. Connect front-end quote form to API. Done: homepage returns guide quote, VAT, 25% deposit and balance.
3. Build booking details step.
4. Add final payment terms and availability confirmation rules.
5. Add Stripe deposit/full-payment checkout.
6. Add admin dashboard.
7. Add email confirmations.
8. Add server hardening and backups.
9. Add advanced features: availability, SMS/WhatsApp, CRM, analytics.

## Current Live Backend Slice

The first quote backend is live on the VPS:

- Service: `menwithvan-quote.service`
- App path: `/opt/menwithvan/backend/app.py`
- Environment: `/etc/menwithvan/quote.env`
- Database: `/var/lib/menwithvan/bookings.sqlite3`
- Public endpoint: `POST https://www.menwithvan.com/api/quote`
- Booking endpoint: `POST https://www.menwithvan.com/api/bookings`
- Admin dashboard: `https://www.menwithvan.com/admin`
- Private service bind: `127.0.0.1:3020`
- Reverse proxy: nginx `/api/` and `/admin`

Current quote behaviour:

- Uses Google Distance Matrix server-side for mileage.
- Calculates confirmed hourly rates using:
  - £50/hour per Luton van
  - £15/hour for each extra mover above one mover per Luton van
- Enforces a two-hour minimum.
- Applies mileage at the configured rate.
- Applies stairs at the configured per-floor-per-man rate.
- Applies the £27 + VAT congestion-zone charge when pickup or delivery geocodes inside the zone.
- Does not charge when the route merely passes through the zone.
- Calculates subtotal, VAT, total, 25% deposit and balance.
- Stores customer booking requests in SQLite.
- Provides an authenticated admin dashboard and CSV export.

## Open Questions

1. Confirm mileage rate: £1 or £2 plus VAT per mile?
2. Confirm payment provider preference: Stripe is recommended.
3. Confirm whether customers should get instant confirmed bookings or "request booking, admin confirms".
