# Integration guides
## AI receptionist
1. Create an API key with an OpenAI-compatible provider that supports tool calling.
2. Set `LLM_API_KEY`, `LLM_MODEL` and (if not OpenAI) `LLM_BASE_URL` in `.env`; restart.
3. Test in the chat widget: ask for a room, confirm, then check Admin > Bookings (source "ai") and Admin > AI conversations.
Without `LLM_API_KEY`, Kabita uses a limited local fallback: it can ask for missing stay dates, search live availability for ISO dates or simple requests such as “tomorrow for 2 nights”, and route event enquiries to the event form. It is not model-powered and cannot complete multi-step booking or general receptionist tasks. Set the key in the local `.env` file and restart Flask to enable provider-backed tool calling. Never commit the `.env` file or paste the key into chat. Conversations are stored (privacy policy mentions this).
Safety rules enforced in code (not only in the prompt): bookings need an earlier availability search in the same chat and an explicit "yes" as the guest's latest message.
## Razorpay
1. Dashboard > Settings > API Keys: use **Test mode** keys first. Set `RAZORPAY_KEY_ID` and `RAZORPAY_KEY_SECRET`.
2. Webhooks: add `https://YOURDOMAIN/api/payments/webhook`, events `payment.captured` and `payment.failed`, set a secret and put it in `RAZORPAY_WEBHOOK_SECRET`.
3. Book a room choosing "Pay online". A booking is marked paid only after the server verifies the signature (or the signed webhook). Test card details are in Razorpay's docs.
4. Refunds: Admin > booking > Payments > Refund (calls the Razorpay refund API for online payments).
5. Go live: swap to live keys and rerun a small real payment.
## Email (SMTP)
Set `SMTP_HOST`, `SMTP_PORT` (587 STARTTLS), `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`. Results appear per booking under Notifications (sent / failed / skipped).
## WhatsApp
Use the WhatsApp Business Cloud API: set `WHATSAPP_TOKEN` and `WHATSAPP_PHONE_ID`. Messages outside the 24-hour window need approved templates (extend `notify.py` for your template names).
## SMS
Set `SMS_WEBHOOK_URL` (+ `SMS_WEBHOOK_TOKEN`) to an endpoint of your SMS provider or gateway that accepts JSON `{"to","message"}`. Adapt `notify.py` if your provider needs a different format. In India DLT template registration is required.
## Reminders
Cron daily: `0 9 * * * cd /path && .venv/bin/python scripts/send_reminders.py`
