# Vijay Hospitality: hotel booking, reception and AI receptionist platform

Python (Flask + SQLite) application for Vijay Utsav and Vijay Garden, Manpur, Gaya. It contains the public website, a real room-availability and
booking engine, an AI receptionist that books through the same engine, Razorpay payments with server-side verification, staff dashboards,
reports and exports, notifications, audit logs and automated tests.

> There was no existing Python project in the uploaded files, so this is a new project. It reuses your site's design (CSS) and real photos.
> Read **"Known limits"** before going live.

## 1. Install
```bash
python3 -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```
## 2. Configure
```bash
cp .env.example .env     # then edit: SECRET_KEY, SITE_URL, TAX_PERCENT, and any integrations
```
Generate a secret: `python -c "import secrets;print(secrets.token_urlsafe(48))"`. With `APP_ENV=production` the app refuses to start without a strong `SECRET_KEY`.
## 3. Database and migrations
SQLite is used (`DATABASE_PATH`, default `instance/hotel.db`). Migrations in `migrations/*.sql` are applied automatically at start-up and with
`python -c "from app.db import migrate;print(migrate('instance/hotel.db'))"`. Add new schema changes as `002_*.sql`.
```bash
python scripts/seed.py          # properties, AC / Non-AC categories, amenities (no rooms, no prices, no fake bookings)
python scripts/create_admin.py  # first owner account (password: 10+ chars, mixed case, a number)
```
For local UI/report review only, create clearly labeled demo rooms, bookings, receipts and finance entries with `python scripts/seed_demo_data.py --confirm`. Remove that dataset with `python scripts/seed_demo_data.py --remove --confirm`. The command refuses to run when `APP_ENV=production`.
To preview public availability search with clearly labeled sample rooms and existing bundled room photos, run `python scripts/seed_search_demo.py --confirm`; remove them with `python scripts/seed_search_demo.py --remove --confirm`. This is disabled in production. Room pages rotate through available photos for a multi-angle preview; a continuous 360° tour is shown after management uploads and approves a real 2:1 equirectangular panorama in Admin → Rooms.
Optional site photos can be imported with `python scripts/seed_media.py`. Put exactly four landscape images in
`photos/slides/`, gallery images in `photos/gallery/`, and room-category images in
`photos/rooms/<category name>/` (folder name matched case-insensitively). Re-running the importer skips source files already imported.
## 4. Run
```bash
flask --app wsgi run                         # development: http://localhost:5000
gunicorn -w 2 -b 127.0.0.1:8000 wsgi:app     # production behind nginx; set TRUSTED_PROXY=1
```
Then sign in at `/admin/login` and: **Settings** (verify both addresses, phone, WhatsApp, map links) → **Categories and rates** (set a price; rooms with no price are not
bookable) → **Rooms** (add real rooms, photos, amenities) → test a booking on the website.
Search content is database-backed: Settings controls property/contact details; Categories and rates controls room type, category description, occupancy, rates, tax and whether the category is published; Rooms controls individual room number, floor, description, amenities, optional price, photos and 360° panorama. Save and approve/publish required category details before they appear publicly. Availability is computed from active rooms, reservations and room blocks. Admins and owners also get management links on search results when signed in. Search-page labels and layout are application UI, not editable property content.
## 5. Tests
```bash
python -m unittest discover -s tests -t . -v      # results in docs/TEST-RESULTS.txt
```
## 6. Integrations (see docs/INTEGRATIONS.md)
Razorpay, AI receptionist (any OpenAI-compatible API), SMTP email, WhatsApp Cloud API and SMS webhook are configured only through environment variables.
Unconfigured channels are logged as **skipped**; nothing is ever shown as sent or paid unless the provider confirmed it.
## 7. Deploy and operate
See docs/DEPLOYMENT.md (server, HTTPS, cron reminders, backups) and docs/API.md.
Admin → **Configuration** provides the hotel identity wizard, validated JSON import/export, reusable page and notification configuration, and a launch checklist. See [docs/CONFIGURATION.md](docs/CONFIGURATION.md) for the schema, import safeguards, secret handling and current single-tenant boundary.

## What is implemented
- Availability engine from real records; double booking is blocked by a database primary key on (room, night) plus a locked transaction; tested with 8 simultaneous requests.
- Public site: home, properties, rooms, room detail, availability search, booking, confirmation, booking lookup (reference + mobile), modify, cancel, PDF receipt, gallery, facilities, contact, FAQ, privacy, sitemap.xml, robots.txt, canonical URLs, Open Graph, Hotel and FAQ structured data, responsive images (AVIF/WebP/JPEG, lazy loading).
- AI receptionist (chat widget + `/api/chat`): tool-calling to search availability, create/look up/modify/cancel bookings, hand over to a human. The server refuses to create a booking unless the room was offered in that conversation and the guest's latest message is an explicit "yes".
- Payments: Razorpay order, signature verification on the server, webhook, failed payments, late-payment handling, refunds, manual cash/UPI/card/bank records, receipts.
- Admin and reception: dashboard, rooms (add, edit, deactivate, block, photos, amenities), categories and seasonal rates, bookings (search, filters, modify, cancel, reassign, check-in/out, no-show, payments, refunds), walk-in/phone booking, owner reports with SVG charts and recorded expense/investment ROI, guest enquiries (room/event/general) with audited follow-up status, Excel/PDF export, users and roles (owner, admin, receptionist), settings, audit log, AI conversation review. Event enquiries are not event bookings; capacity, packages, catering, availability and prices require direct property confirmation.
- Dynamic pricing (opt-in): additive weekend, occupancy, lead-time, stay-length, and event rules with global increase/discount caps, rounding, peak markers, price preview, and a public nightly-price calendar.
- Site media: multi-image room-category photos, homepage slides, and gallery items; uploads are verified and stored as resized JPEGs with thumbnails. Existing room-specific photo uploads remain available.
- Security: hashed passwords, 5-attempt lockout, role permissions, CSRF on every form and API write, rate limits, parameterised SQL, escaped output, CSP and security headers, secure cookies in production, no secrets in code or logs.

## Known limits (be aware before launch)
- **Not tested against live services** (this was built without internet access): Razorpay, the LLM provider, SMTP, WhatsApp and SMS are coded to their public APIs and tested with stand-ins only. Test each in sandbox/test mode first.
- **Not tested in a real browser**: layouts are responsive by design and the pages render, but check phone and desktop views yourself.
- **Banquet and event booking is not part of this application.** Vijay Garden rooms are bookable here; events remain enquiry-based.
- SQLite suits one server and moderate traffic. The rate limiter is in memory (per process). For several servers use PostgreSQL and Redis (the SQL is standard but would need a driver change).
- Tax is one configurable percentage (`TAX_PERCENT`), not slab-based GST. Confirm the correct rate with your accountant.
- WhatsApp free-form messages only reach guests who messaged you in the last 24 hours; otherwise approved templates are required.
- Sample prices and room numbers are not included; addresses come from your messages and must be verified.
