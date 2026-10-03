# Discord Bot - 24/7 Replit Hosting

## Overview

This project is a Discord bot application designed for 24/7 hosting on Replit, offering continuous operation through a Discord bot, a web-based monitoring interface, and a keep-alive mechanism. It provides core bot functionalities, robust logging, error handling, and a modern web interface for status monitoring. Key features include TwitchCord integration for stream management and chat bot functionalities, advanced message forwarding, Google Sheets two-way sync for train schedules, and an interactive web dashboard for bot management and analytics. The project aims to deliver a reliable, always-on Discord bot solution with easy deployment and management on the Replit platform, supporting community engagement and administrative tasks.

## User Preferences

Preferred communication style: Simple, everyday language.

## System Architecture

The application employs a modular architecture, emphasizing clear separation of concerns and extensibility, optimized for Replit deployment.

### UI/UX Decisions
- **Web Interface**: Modern tabbed dashboard built with Bootstrap 5, featuring a dark theme and responsive mobile-first design. It includes 8 organized tabs (Overview, Train Schedules, Live Roles, Forwarding, Twitch, Users, Analytics, Settings) with real-time bot statistics and secure authentication.
- **Mobile Optimization**: Touch-friendly buttons, responsive breakpoints, and proper viewport settings.

### Technical Implementations
- **Backend**: Python-based Discord bot using `discord.py` and a Flask web server for keep-alive functionality and health checks.
- **Modular Design**: Commands are organized into cogs; configuration uses environment variables.
- **Database**: PostgreSQL with SQLAlchemy ORM for persistent data, managed with Alembic for migrations.
- **Keep-Alive System**: Flask server provides multiple health check endpoints in a separate thread.
- **Command System**: Supports both prefix (`!`) and slash (`/`) commands, with a comprehensive role-based permission system and global ban functionality. Owner-only commands use prefix-only to avoid Discord's 100 slash command limit (91/100 slots used). Prefix commands: `!set_activity`, `!clear_activity`, `!set_status`, `!view_logs`, `!mark_complete`, `!announce_signups`, `!botinvite`, `!donate`, `!viewbackups`, `!dash`, `!about`, `!deleteschedule`, `!setstatuschannel` (admin_commands.py, basic_commands.py).
- **Discord Heartbeat**: `!setstatuschannel #channel` sets a channel where the bot posts a status embed every 4 hours showing uptime, Twitch IRC connection state, and next upcoming train. Use `!setstatuschannel off` to disable. Stored in SystemSettings key `heartbeat_channel_id`.
- **Suggestion System**: `/suggest` slash command opens an interactive modal for users to submit feature requests/feedback. Includes category, description, and priority fields. Submissions are DM'd to the bot owner with full user and server context. Modal has 5-minute timeout.
- **Comprehensive Bot Tutorial**: `/botguide` command provides a 10-page interactive tutorial covering all bot features. Restricted to trusted users/roles only for security. Features paginated navigation with first/prev/next/last buttons and covers train schedules, Twitch integration, auto-shoutout, live roles, attendance, utilities, and admin commands.
- **TwitchCord Integration**: Features stream management, analytics, community polls, leaderboards, real-time Twitch chat monitoring, automatic Twitch OAuth token refresh, and a "Live Role" system for Discord role assignment based on streaming status. The Twitch Chat Bot provides automated train coordination including auto-announcements, 1-hour and 10-minute warnings, and a `!raidnext` command for initiating raids to the next scheduled person. It includes server-specific settings, test commands, robust attendance tracking with automatic stream offline detection, and comprehensive daily/weekend attendance reports automatically sent at 12pm UTC the day after trains complete.
- **Message Forwarding**: Database-driven system with duplicate prevention, manageable via the web dashboard.
- **Google Sheets Two-Way Sync**: Bidirectional synchronization between Discord train schedules and Google Sheets.
- **Timezone Handling**: Uses `pytz` for accurate DST handling in displayed times and schedule creation.
- **Stability Improvements**: Implemented API call timeouts, auto-restarting error handlers for critical background tasks, improved audit/debug logging, and deferred responses for slash commands with database queries to prevent timeouts.
- **Scheduling**: Supports creating train schedules weeks in advance and includes an automatic train cancellation system for under-filled trains.
- **Notification System**: Refined timing and logic to prevent duplicate pings and ensure accurate participant tracking. Includes reaction-based readiness tracking and server context in notifications, with error notifications to trusted roles.
- **Runtime Maintenance Mode**: Allows the bot to stay connected during maintenance with authorized user control via a ping command and global command gating.
- **Performance Optimization**: Schedule caching system with automatic invalidation and command cooldowns.
- **Automated Backups**: Daily database backups with 7-day retention and JSON export for disaster recovery.
- **Analytics Dashboard**: Provides comprehensive statistics and insights for train sessions and user participation.
- **Data Export Capabilities**: Commands to export schedules and participant data to CSV.
- **Self-Service Twitch Linking**: `/linktwitch` command allows users to link their Twitch accounts without admin intervention.
- **Self-Service Leave for Admin-Added Riders**: When an admin/trusted user manually adds someone to a train via `!addtotrain`, the bot DMs that user an embed with a "Leave this train" button, letting them back out themselves without needing an admin. The button is a restart-safe persistent component keyed on schedule/user IDs.
- **Proactive Twitch Link Outreach**: Automated DM system that contacts train participants who haven't linked their Twitch accounts. Asks for their Twitch username (users just reply to the DM) and offers OAuth authorization for auto-raids. Tracks outreach with 7-day cooldown to prevent spam. Admin command: `!outreachunlinked [schedule_id]`. DM replies are automatically processed to link Twitch accounts and update all active train participations.
- **Modern Web Dashboard**: A tabbed interface with 8 sections providing centralized bot management and real-time data via REST API endpoints for status, guild management, train schedules, and settings. Supports editable fields and server-specific filtering with token-based authentication.
- **Server-Specific Feature Restrictions**: Database-driven permissions system allowing selective feature enablement per server via an interactive `/managefeatures` command.
- **Customizable Attendance Intervals**: Server-specific configuration for attendance report frequency.
- **Enhanced Permission System**: Permission decorators recognize "Administrator" and "Manage Server" Discord permissions for admin command access, with consistent permission checking across all slash commands.
- **Critical Scheduler Fix**: Notification scheduler and auto-cancel checker background tasks now start only after the bot is ready, resolving blocking issues.
- **DST Fix**: Implemented smart timezone conversion using next occurrence dates to ensure train schedule times remain stable across Daylight Saving Time transitions.
- **AFK Status System**: Automatic away status with custom messages. When mentioned while AFK, the bot auto-replies with the user's AFK message and time away. Status automatically clears when the user sends a message. Includes rate limiting to prevent spam. Command: `/afk [message]`.
- **Multi-Platform Streaming**: Extended streaming support beyond Twitch to include YouTube Gaming and Kick.com. Features live status detection, automatic notifications when streamers go live, and platform-specific settings per user/guild. Managed via `/stream` command group (link, settings, list, unlink, schedule).
- **Twitch Clips & VODs**: Automatic detection and notification system for new Twitch clips and VODs. Posts to designated channels with embed formatting, thumbnail previews, duration info, and view counts. Duplicate prevention via database tracking. Configurable per-user via `/stream settings`.
- **Stream Schedule Integration**: Pulls and displays Twitch stream schedules directly in Discord. Shows upcoming scheduled streams with start times, titles, and game categories. Accessible via `/stream schedule` command.
- **Advanced Spam Protection**: Multi-recipient DM alert system for suspicious joins with 15+ detection patterns. Member-reported spam tracking via `/reportspam` command. Configurable auto-kick, join logging, and alert destinations (channel or multi-user DMs). Prefix commands: `!setspamchannel`, `!toggleautokick`, `!togglelogalljoins`, `!togglespamdm`, `!spamstatus`, `!setspamuser`, `!spamreports`.

## External Dependencies

- **Required Libraries**:
    - `discord.py`: Discord API interaction.
    - `Flask`: Web server and keep-alive functionality.
    - `psutil`: System monitoring.
    - `asyncio`: Asynchronous programming.
    - `sqlalchemy`: Object Relational Mapping (ORM).
    - `psycopg2-binary`: PostgreSQL adapter.
    - `alembic`: Database migration management.
    - `pytz`: Timezone handling.
- **Environment Variables**:
    - `DISCORD_BOT_TOKEN`: Discord bot authentication.
    - `COMMAND_PREFIX`: Bot's command prefix.
    - `BOT_NAME`: Bot's display name.
    - `LOG_LEVEL`: Logging verbosity.
    - `DATABASE_URL`: PostgreSQL connection string.
    - `TWITCH_CLIENT_ID`: Twitch API client ID.
    - `TWITCH_CLIENT_SECRET`: Twitch API client secret.
    - Feature flags (e.g., `ENABLE_MUSIC`).
    - API keys (e.g., `WEATHER_API_KEY`).