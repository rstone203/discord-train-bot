---
name: Adding a new beta feature flag (Discord train bot)
description: Checklist of every file that must be touched to add a new per-guild beta feature flag; missing one leaves the toggle UI or gating silently inconsistent.
---

# Adding a new beta feature flag

This repo gates experimental features per-guild via `ServerFeaturePermissions` beta
columns (see `reminders_enabled`, `waitlist_enabled`, `stats_enabled`,
`self_signup_enabled`), defaulting OFF everywhere until explicitly enabled for a
specific guild.

**Why:** the schema has no Alembic migration files — new columns are added via
an idempotent `ADD COLUMN IF NOT EXISTS` list in `database.py`, and the flag is
read/written in several independent places that don't share a single source of
truth. Skipping one spot leaves the toggle command lying about state, or the
feature check silently defaulting the wrong way.

**How to apply — a new beta flag touches all of these:**
1. `models.py` — add the `Boolean, default=False` column to `ServerFeaturePermissions`.
2. `database.py` — add `("server_feature_permissions", "<col>", "BOOLEAN DEFAULT FALSE")` to `_ADDITIVE_COLUMNS`.
3. `utils/server_permissions.py` — add the name to `BETA_FEATURES`, plus the attribute-loading tuples, `feature_map` in `is_feature_enabled`, and both the update/create branches (+ param) of `set_server_permissions`.
4. `cogs/server_permissions_commands.py` — `FeatureToggleView.get_current_permissions` (both branches), `update_buttons`, the `set_server_permissions(...)` call in `toggle_feature`, the `feature_names` dict, `create_embed`'s beta list, and `viewserverfeatures`'s beta list.
5. To scope a feature to one guild only (e.g. a beta/testing server) without touching production behavior: leave the column default False, gate the feature's command/logic behind `permissions_manager.is_feature_enabled(guild_id, '<name>')`, and enable it for that one guild via a direct DB write (not a code default) — every other guild stays off by construction.

Restart the bot workflow after model/database.py changes so the additive-column
migration runs before any code path reads the new column.
