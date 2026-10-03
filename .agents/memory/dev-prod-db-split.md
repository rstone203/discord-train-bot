---
name: Dev vs prod database split (Discord train bot)
description: The published deployment reads a different PostgreSQL database than the editor; editor DB writes never reach the live bot.
---

# Dev vs prod database split

The published Replit Deployment of this Discord bot uses its **own production database**, which is **separate** from the editor/development database that `DatabaseSession` (and `executeSql environment:"development"`) connect to.

**Evidence (how this was confirmed):**
- Same OAuth token primary keys map to different Twitch usernames in each DB (e.g. editor DB had gl_stoney at row id=6; the live deployment's own logs and `executeSql environment:"production"` show gl_stoney at row id=8).
- The deployment's runtime logs match the `production` replica, not the editor DB.
- Editor `DATABASE_URL` host was `helium/heliumdb`; the deployment's prod DB is a distinct Replit-managed database.

**Why this matters:**
- Edits made directly to the database from the editor (schedules, participant swaps, Twitch links, token cleanup) do **NOT** propagate to the live bot. Earlier work assumed a shared DB and was written to the wrong database.
- `executeSql environment:"production"` is READ-ONLY, so prod data cannot be fixed by direct DB writes from here.

**How to apply:**
- To change what the LIVE bot sees, go through the bot's own interfaces against prod: Discord slash/prefix commands, or the live web dashboard's API — not editor `DatabaseSession` scripts.
- Interactive Twitch OAuth (`/twitchoauth`, `!resetmytwitch`) can only be completed by the user in Discord; the agent cannot do it.
- When diagnosing "is the live bot OK", trust deployment logs + `executeSql environment:"production"`, never the editor DB.
