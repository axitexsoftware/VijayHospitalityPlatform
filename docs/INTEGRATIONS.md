# Integration guides
## AI receptionist
1. An OpenAI-compatible API key is optional for room availability and guided room reservations. To enable provider-backed general questions, set `LLM_API_KEY`, `LLM_MODEL` and (if not OpenAI) `LLM_BASE_URL` in `.env`; restart.
2. The chat microphone and speech playback use browser-native Web Speech APIs. Set `VIRA_VOICE_ENABLED=0` to disable speech input, `VIRA_TTS_ENABLED=0` to disable spoken responses, and `VIRA_SPEECH_LANG` to choose the default recognition locale. Voice availability depends on the browser and a secure context; text chat remains available.
3. Configure `SMTP_HOST`, `SMTP_FROM`, and optionally `SMTP_USER`/`SMTP_PASSWORD` for email, and `WHATSAPP_TOKEN` plus `WHATSAPP_PHONE_ID` for WhatsApp. Booking notification attempts and skipped/failed providers are recorded under Admin > Notifications.
4. Test in the chat widget: provide stay details, select an available room, confirm the summary, and check Admin > Bookings (source "ai") and Admin > AI conversations. VIRA uses the existing availability and reservation services; confirmed AI reservations use Pay at Hotel.
Safety rules enforced in code (not only in the prompt): bookings require a fresh backend availability search in the same chat and explicit confirmation as the guest's latest message. Conversations are stored (privacy policy mentions this). Never commit `.env` or paste secrets into chat.
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
