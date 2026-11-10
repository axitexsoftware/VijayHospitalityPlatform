# API (JSON). Writes need the `X-CSRF-Token` header (value from `<meta name="csrf">`) and the same session cookie.
| Method | Path | Purpose |
|---|---|---|
| GET | /api/health | liveness + database check |
| GET | /api/availability?check_in&check_out&adults&children&category_id&property_id | available rooms with price, tax, total (paise); reports `management_confirmation_pending` until room numbers, rates, and occupancy are confirmed |
| GET | /api/rooms/availability | alias for `/api/availability` |
| GET | /api/rooms/search?check_in&check_out&adults&children&rooms&category_id&property_id | availability grouped by room category with actual inventory counts; `can_accommodate` reflects the requested room count |
| GET | /api/room-categories | published categories with confirmed occupancy, rates, taxes, and available inventory |
| GET | /api/rooms/{category_id} | published room-category details; 404 if the category is not published |
| GET | /api/price-calendar?category_id&start&months | nightly `price_paise`, `peak`, and `notify_me` per date; months 1–12; returns 409 while rates are unconfirmed |
| GET | /api/price-quote?category_id&check_in&check_out | nightly dynamic-price breakdown and stay total (paise); returns 409 while rates are unconfirmed |
| GET | /api/rooms/rates?category_id&check_in&check_out | alias for `/api/price-quote` |
| GET | /api/media?kind&property_id&category_id | active slide, gallery, or room-category media ordered by sort; unapproved room photos are not published |
| POST | /api/bookings | `{room_id,check_in,check_out,adults,children,name,phone,email,notes}` → 201 `{reference}`; 409 if unavailable; 400 with `fields` |
| POST | /api/payments/order | `{reference}` → Razorpay order (only for your own pending booking and after tax approval) |
| POST | /api/payments/verify | Razorpay checkout response; server verifies signature |
| POST | /api/payments/failed | `{order_id,reason}` |
| POST | /api/payments/webhook | Razorpay webhook (signature verified, CSRF-exempt) |
| GET | /api/chat | `{messages,status}` for the conversation bound to the current session; tool arguments and results are not exposed |
| POST | /api/chat | `{message,language?}` → `{reply,status,booking}` (conversation kept in the session; client-supplied conversation tokens are ignored) |
Room categories are configured at `/admin/categories`; category publication approvals are submitted to `/admin/categories/{category_id}/approval`. The guided inventory review uses `/admin/inventory-setup`, `/admin/inventory-setup/review`, and `/admin/inventory-setup/apply`. These signed-in admin pages are role-checked and CSRF-protected. Rate limits: bookings 10 per 10 min, chat messages 25 per minute, chat history and read-only public pricing/media endpoints 60 per minute, per IP.
