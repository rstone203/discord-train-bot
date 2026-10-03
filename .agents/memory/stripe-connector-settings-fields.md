---
name: Stripe connector settings field names
description: The actual field names returned by the Replit Stripe connector's settings object, for building custom (non-Node) Stripe integrations.
---

When fetching Stripe credentials manually (e.g. in Python, via the
`REPLIT_CONNECTORS_HOSTNAME` + `REPL_IDENTITY`/`WEB_REPL_RENEWAL` connector
proxy) rather than using the Node `stripe-replit-sync` helper, the connection
`settings` object uses these field names:

- `secret` — the Stripe secret key (NOT `secret_key`)
- `publishable` — the Stripe publishable key (NOT `publishable_key`)
- `webhook_secret` — Stripe webhook signing secret (present once a webhook is configured)
- `account_id`, `mcp`, `claim_url`, `sandbox_id` — other metadata fields

**Why:** The Node/monetization skill docs describe the Node connector sync
pattern, which uses different field names than the raw connector API
response. Guessing `secret_key` (matching Stripe's own naming) silently
returns `None` and produces a confusing "Stripe connection is missing a
secret key" error even though the connection is healthy.

**How to apply:** When building a custom (Python or other non-Node) Stripe
credential fetcher against the raw `/api/v2/connection` connector endpoint,
verify actual field names via `listConnections('stripe')` in the code
execution sandbox before assuming a name — don't guess based on Stripe's own
API naming conventions.
