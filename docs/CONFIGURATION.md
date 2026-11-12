# Hotel configuration management

The Admin Dashboard's **Configuration** area stores customer-facing settings in the database, separate from application source code. Open **Admin → Configuration → Hotel setup wizard** to configure identity, colors, fonts, the canonical website URL, and the primary domain.

## What is configurable

- **Brand and presentation:** name, tagline, logo/favicon paths, a curated theme/font set, and validated palette colors.
- **Website:** homepage, about and event copy; per-page SEO title/description; hotel policies; configured offers and banquet enquiry packages.
- **Operations:** existing Settings, Categories and rates, Rooms, Dynamic pricing, and Images and gallery screens remain the source of truth for property contact details, inventory, tariffs, tax, availability, and media approvals.
- **Integrations:** AI provider/model/instructions/knowledge and notification templates are stored as non-secret configuration. Email sender metadata is configurable. Provider credentials remain deployment environment variables.
- **SEO and deployment:** canonical site URL, primary domain, SEO text and Open Graph image path.

The current booking and payment engine supports **INR** and **Razorpay** only. The site can select English or Hindi as its document/default chat language; selecting Hindi does not automatically translate all public page copy.

## Import and export

Use **Export JSON** to download a UTF-8 `vijay-hospitality-configuration` bundle containing the validated configuration plus property, room-category, tariff/occupancy and room records. The format has `schema_version: 1`; import rejects unknown fields, invalid values, unsafe asset paths and secret-like field names. Logo, favicon and Open Graph assets must already be under `/static/`.

The import is transactional. Property and inventory records can be created only on an empty installation. On an installation with hotel data, imported property/category/room data must match exactly; otherwise import stops and points to the existing admin screens. This prevents a configuration import from rewriting bookings, payments or transaction history. Catalog changes are never applied as updates or deletions by import.

The three-step onboarding wizard saves identity first, links to the existing verified property/inventory workflows, then presents a launch-readiness checklist. Checklist completion is an administrative status, not proof that DNS, HTTPS, payments, backups or external services have been tested.

## Credentials and deployment

Do **not** put provider secrets in this JSON. `LLM_API_KEY`, Razorpay keys, SMTP passwords, WhatsApp tokens and SMS webhook tokens must be configured on the server in environment variables (or a deployment secret manager). They are not read from or written to an export. Updating a domain in Admin records the intended URL; it does not change GoDaddy DNS, configure the web server, or issue a TLS certificate.

## Tenant boundary

This implementation remains single-tenant. Tenant data isolation and property-level user access controls were explicitly deferred; do not host unrelated hotel customers in the same live database. Existing multi-property records belong to the same current hotel account.

## Sample second-hotel configuration

[`examples/sample-second-hotel-config.json`](../examples/sample-second-hotel-config.json) is a clearly marked, inactive demonstration export. It contains example-only identity and inventory placeholders, no real prices and no secrets. Import it only into a disposable empty installation for a walkthrough; do not publish or use it as hotel data.
