---
name: Verifying which code version is live in the published bot
description: How to tell whether a code change is actually deployed to the live Discord bot, given the editor/prod split.
---

# Confirming what code the live bot is running

The published Discord-bot deployment runs a separate process + database from the
editor (see dev-prod-db-split.md). Editor code changes do NOT reach the live bot
until the user **republishes**. To verify whether a specific change is live:

## Technique: log-string fingerprinting
Pick a log line whose text changed between old and new code, then search
production deployment logs for the old vs new wording. Whichever string appears
tells you which code the live process is running. Example: the no-signup→host
behavior changed the skip log from `no participants signed up yet` (old, silently
skipped) to `no participants signed up and no host set` / `sending reminder to
host so the slot can be claimed` (new, pings host). Seeing the old string in prod
logs proves the live bot predates the change.

## Technique: detect redeploys/restarts via a periodic task's cadence
The `twitch_sync` task runs every ~6 hours. Its timestamps creep by a few seconds
each cycle (e.g. :53 → :57 → :03 → :11) when the process runs continuously. A
restart/redeploy resets that drift and leaves a gap. Continuous, drifting
cadence across the window = no redeploy happened in that window.

**Why:** verification tasks ("confirm X works in the live bot") can give a false
pass if you only read the editor code — the code can be correct while the live
deployment is stale. These two checks distinguish "code is correct" from "change
is actually live."

**How to apply:** when asked to confirm runtime behavior in production, find a
qualifying record in the prod DB (read-only), then check prod logs around when it
last fired for the fingerprint string, and confirm no redeploy reset the periodic
cadence. If the change isn't live, the user must republish, and re-verification
must wait for the next real firing of a qualifying event.

## Technique: prod information_schema = fastest proof a startup migration shipped
Schema changes added by the startup additive-migration (`_ensure_additive_columns`
in database.py) only reach prod on republish. The quickest definitive check of
"did the new code/schema deploy?" is to query prod `information_schema.columns`
(read-only `executeSql environment:"production"`) for the new columns. If they are
absent, the republish has NOT happened and any "confirm it works live" task is
blocked — a task agent cannot republish (only the main agent/user) and cannot
drive Discord slash commands, so live e2e confirmation is inherently owner action.
