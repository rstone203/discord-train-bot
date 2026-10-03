---
name: Twitch device-code OAuth needs `scopes` (plural)
description: Twitch's device-authorization endpoint silently drops scopes if you send `scope` instead of `scopes`, yielding tokens with no chat:read.
---

# Twitch device-code flow: parameter is `scopes`, not `scope`

When starting Twitch's Device Code Grant Flow (`POST https://id.twitch.tv/oauth2/device`), the scope list must be sent in a field named **`scopes`** (plural). This differs from the standard authorization-code endpoint, which uses `scope` (singular).

**Why this matters:**
- If you POST `scope=...` to the device endpoint, Twitch ignores it and issues a device code with **no scopes**. The resulting access token validates with empty/`null` scopes, so it has no `chat:read` and the bot's Twitch IRC (attendance tracking) cannot connect — even though the user completed authorization successfully (`/twitchoauth`, "Device auth complete", `scope=None`).
- This produces a silent failure: re-authorizing repeatedly never fixes it, because the bug is in how the device code is requested.

**How to apply:**
- For any device-code request, send `'scopes': '<space-separated scopes>'`.
- After fixing, the change only takes effect once the deployment is re-published, AND the affected users must re-run `!resetmytwitch` then `/twitchoauth` to mint a fresh token that actually carries the scopes.
- Symptom to recognize in logs: `Token id=N (user) validated — scopes: []` / `lacks chat:read — has: []` right after a successful `/twitchoauth`.
