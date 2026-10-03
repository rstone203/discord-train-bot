---
name: One-time operations gated by SystemSettings flag
description: Convention for converting per-restart maintenance loops (e.g. per-guild API cleanup) into one-time operations using a SystemSettings boolean flag.
---

Expensive per-guild/per-restart maintenance work (e.g. Discord API cleanup loops) should run once and record completion in a `SystemSettings` row (`setting_key=<name>_done`, `is_enabled=True`), not re-run on every bot restart.

**Why:** As the bot scales past a fixed guild count (e.g. after Discord verification lifts a 100-guild cap), loops that make N API calls per guild on every restart burn increasing rate-limit budget and startup time for no benefit once the underlying state is already correct.

**How to apply:** Before adding a startup/restart loop that mutates external state per-guild or per-entity, check whether it's idempotent one-time cleanup vs. something that must re-verify each time. If one-time, gate it behind a `SystemSettings` flag checked via `DatabaseSession`, and log clearly whether it ran or was skipped so restarts are easy to audit.
