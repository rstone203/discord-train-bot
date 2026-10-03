# Threat Model

## Project Overview

This project is a production Discord bot platform with an integrated Flask web server and PostgreSQL database. The main process is `main.py`, which starts `DiscordBot` from `consolidated_train_bot.py`; that file also exposes the production HTTP surface used for health checks, dashboard pages, Twitch OAuth callbacks, and management APIs. The system manages Discord server configuration, train schedules and participants, message forwarding rules, trusted-user permissions, Twitch account links and OAuth tokens, and notification settings.

Current production wiring confirmed by this scan:
- The only production-reachable web app is the Flask server inside `consolidated_train_bot.py`.
- `dashboard.py`, `simple_dashboard.py`, `keep_alive.py`, and `keep_alive_broken.py` are legacy or alternate paths that are not started by the deployed entrypoint under the current configs.
- The Flask dashboard/API now distinguishes owner-only routes from guild-scoped routes. `require_owner_key()` protects global endpoints, while most guild data routes pair `require_api_key()` with `_require_guild_access()` and HMAC-derived guild tokens.
- One production auth exception remains important for future scans: `/oauth/twitch/status` accepts any valid dashboard token via `require_api_key()` but does not apply `_require_guild_access()`, so any guild-scoped dashboard token can read global Twitch OAuth metadata.
- Repository exposure is currently concrete, not just theoretical: `backups/backup_20260427_214908.json` is a committed production data export containing Discord IDs, usernames, Twitch usernames, schedules, and participation history. `.dockerignore` keeps `backups/` out of container images, but repo and artifact sharing still expose the file.

Production assumptions for future scans:
- Only production-reachable code should be reported.
- Mockup or sandbox-only code is out of scope unless production reachability is shown.
- TLS is handled by the platform for deployed traffic.
- `NODE_ENV=production` is assumed when relevant, though this repo is primarily Python.

## Assets

- **Discord administrative control** — forwarding rules, maintenance mode, schedule management, live-role settings, and trusted-user/trusted-role data. Unauthorized changes here let an attacker disrupt multiple Discord communities or gain privileged operational control.
- **User and community data** — Discord user IDs, display names, Twitch usernames, train participant lists, message logs, guild metadata, and banned-user/trusted-user records. This is sensitive operational and personal data.
- **OAuth and service credentials** — Twitch OAuth access/refresh tokens, Discord bot token, database credentials, Stripe keys, Google connector tokens, and Flask secret material. Compromise could enable account takeover, bot impersonation, or direct service access.
- **Database integrity** — the PostgreSQL data backing schedules, permissions, forwarding configs, and OAuth tokens. Corruption or unauthorized writes directly alter live bot behavior.
- **Bot availability** — maintenance mode, schedule state, notification settings, and forwarding behavior all affect the bot’s ability to serve multiple communities reliably.
- **Repository and build artifacts** — committed backups, copied build context, and produced container images can expose sensitive operational data even without a live web route if those artifacts are shared, pulled, or read outside the app runtime.

## Trust Boundaries

- **Public internet to Flask server** — the browser and any HTTP client can reach the Flask routes in `consolidated_train_bot.py`. This is the main web attack surface.
- **Discord users to bot commands** — Discord slash/prefix commands are untrusted input and must be authorized per user, role, and server.
- **Discord DM context to slash commands** — DM-invoked slash commands have no guild context. Any permission helper that depends on guild state must fail closed in DMs unless the command is intentionally global.
- **Flask server to PostgreSQL** — the server can read and write the full application database; any injection or missing authorization at the HTTP layer can directly impact stored state.
- **Flask server to external services** — Twitch OAuth/token exchange, Google Sheets connector calls, Stripe integration, and outbound webhooks cross into third-party trust boundaries.
- **Owner/trusted users vs regular server admins vs regular users** — the application distinguishes bot owner, globally trusted users, server-specific trusted roles, server admins, and normal members. Those boundaries must be enforced server-side.
- **Per-guild isolation boundary** — configuration and schedules for one Discord guild must not be readable or writable by unrelated users from another guild.
- **Guild-scoped trusted access boundary** — trusted-user and trusted-role access is intended to depend on `ServerFeaturePermissions.trusted_access_enabled` for the current guild. Prefix-command or DM paths that lose guild context are in scope as authorization bugs.

## Scan Anchors

- **Production entry points:** `main.py`, `app.py`, `consolidated_train_bot.py`
- **Highest-risk code areas:** Flask routes in `consolidated_train_bot.py`, Discord permission helpers in `utils/slash_permissions.py`, admin/trusted command cogs, OAuth/token flows in `cogs/oauth_commands.py` and `utils/*twitch*`
- **Privilege-boundary hot spots:** `cogs/forwarding_commands.py` for DM execution and cross-guild routing, `utils/slash_permissions.py` for guild-scoped trusted access, dashboard routes that use `require_api_key()` without `_require_guild_access()`, and owner-only token issuance in `consolidated_train_bot.py`
- **Public surfaces:** unauthenticated HTTP routes, Twitch OAuth endpoints, Discord commands available to regular members
- **Authenticated/admin surfaces:** trusted-user/trusted-role commands, per-guild configuration routes, maintenance controls, forwarding management, schedule mutation APIs
- **Repository exposure surface:** tracked artifacts under `attached_assets/` and `backups/` are in scope when they contain real operational exports, personal data, or secrets; `Dockerfile` build-context patterns like `COPY . .` can turn committed artifacts into production image exposure as well; untracked `.cache/replit/env/*` snapshots should only be reported if production reachability or distribution is demonstrated
- **Confirmed legacy/non-production surfaces for current deployment:** `dashboard.py`, `simple_dashboard.py`, `keep_alive.py`, and `keep_alive_broken.py`

## Threat Categories

### Spoofing

The application treats Discord identity, trusted-user status, trusted-role membership, and Twitch OAuth identity as authoritative inputs. Production routes and commands must verify the acting Discord user on every privileged action, and OAuth callbacks must bind authorization results to the intended Discord account. If Twitch usernames supplied through Discord are later used for raids, announcements, or exports, the system must either verify ownership or clearly treat those values as untrusted profile text. Public HTTP callers must never be able to impersonate trusted dashboard users or privileged guild operators.

### Tampering

This project exposes state-changing operations for schedules, participants, forwarding rules, guild settings, and maintenance mode. All such mutations must require server-side authentication and must enforce per-guild authorization, not just global trust. Client-controlled fields like guild IDs, channel IDs, schedule IDs, role IDs, and notification settings must be validated before they reach the database or bot actions.

### Information Disclosure

The application stores operational data about Discord guilds, linked Twitch accounts, train participants, message logs, trusted users, banned users, and service credentials. Public web routes must not expose internal configuration or user lists, and secrets or live operational exports must never be committed to the repo, logged, or returned through debug/status endpoints. OAuth error handling must avoid reflecting attacker-controlled content into HTML responses.

### Denial of Service

Unauthenticated or weakly authenticated routes that toggle maintenance mode, create large numbers of participants/configs, or trigger expensive DB or Discord lookups can disrupt service. Publicly reachable endpoints must be narrow, authenticated where appropriate, and resistant to repeated state-changing requests. External calls should use bounded timeouts, which this codebase often does, but critical mutating routes still need access control.

### Elevation of Privilege

The most important privilege boundaries are public vs trusted HTTP callers, bot owner vs trusted users, and one guild’s admins vs another guild’s data. The system must ensure that being globally trusted does not grant unrestricted cross-guild access unless intentionally designed, and that no public HTTP route can perform owner/trusted actions. Any fallback or debug path that bypasses these checks is effectively an elevation-of-privilege vulnerability.
