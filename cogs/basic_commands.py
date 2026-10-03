"""
Basic commands cog for the Discord bot.
Includes essential commands like help, ping, info, etc.
"""

import discord
from discord.ext import commands
from discord import app_commands
from discord import ui
import platform
import psutil
import os
from datetime import datetime
import logging
import asyncio
import re
import requests
import time

from utils.slash_permissions import owner_only


class HelpDropdownView(ui.View):
    """Interactive dropdown view for help command."""
    
    def __init__(self, author):
        super().__init__(timeout=300)  # 5 minute timeout
        self.author = author
    
    async def interaction_check(self, interaction):
        """Ensure only the command invoker can use the dropdown."""
        return interaction.user == self.author
    
    @ui.select(
        placeholder="Choose a command permission level...",
        options=[
            discord.SelectOption(
                label="🌟 Regular Commands",
                description="Commands available to everyone",
                emoji="🌟",
                value="regular"
            ),
            discord.SelectOption(
                label="🔒 Trusted User Commands",
                description="Admin and setup commands (Requires trusted status)",
                emoji="🔒",
                value="trusted"
            ),
            discord.SelectOption(
                label="👑 Owner Only Commands",
                description="System control commands (Bot owner only)",
                emoji="👑",
                value="owner"
            ),
            discord.SelectOption(
                label="🔐 Special Permissions",
                description="Commands with specific Discord permissions",
                emoji="🔐",
                value="special"
            ),
            discord.SelectOption(
                label="🧪 Beta Features",
                description="Commands for server-enabled beta features",
                emoji="🧪",
                value="beta"
            )
        ]
    )
    async def help_dropdown(self, interaction: discord.Interaction, select: ui.Select):
        """Handle dropdown selection."""
        category = select.values[0]
        embed = self.get_category_embed(category)
        await interaction.response.edit_message(embed=embed, view=self)
    
    def get_category_embed(self, category: str) -> discord.Embed:
        """Get the embed for a specific category."""
        embeds = {
            "regular": discord.Embed(
                title="🌟 Regular Commands - Available to Everyone",
                description="These commands can be used by all server members (no special permissions required)",
                color=0x00ff00
            ).add_field(
                name="📊 Basic Information & Status",
                value="• `/ping` - Check bot response time and latency\n• `/info` - Bot statistics and system information\n• `/uptime` - How long the bot has been running\n• `/updates` - Recent bot changes and updates\n• `/status` - Check message forwarding status\n• `/suggest` - Submit a suggestion or idea for the bot",
                inline=False
            ).add_field(
                name="🚂 Train Participation",
                value="• `!createschedule` - Create custom train schedule (interactive)\n• `!cancelschedule` - Cancel ongoing schedule creation\n• `!forwardstatus` - Check current forwarding status\n• `/twitchcord` - Show TwitchCord features preview",
                inline=False
            ).add_field(
                name="📈 Database & Statistics",
                value="• `!dbstats` - Database statistics and metrics\n• `!topusers` - Most active users across servers\n• `!guildinfo` - Server database information",
                inline=False
            ).add_field(
                name="🔗 Support & Invites",
                value="• `/help` - Interactive help system with categories\n• `/support` - Get help with bot issues\n• `!botinvite` - Get basic bot invite link\n• `/invite` - Get standard bot invite link\n• `!donate` - Support bot hosting costs\n• `!dash` - Access web dashboard",
                inline=False
            ).add_field(
                name="🔄 Backup Applications",
                value="• `/backups` - Post backup streamer applications\n• `!viewbackups` - View all backup applications",
                inline=False
            ),

            "trusted": discord.Embed(
                title="🔒 Trusted User Commands - Admin/Trusted Only",
                description="Commands requiring @is_owner_or_trusted decorator (Verified trusted users & bot owner)",
                color=0xffa500
            ).add_field(
                name="⚙️ Bot Administration",
                value="• `!shutdown` - Shutdown bot and enter maintenance mode\n• `!restart` - Restart the bot\n• `!reload <cog>` - Reload specific cog or all cogs\n• `!sysinfo` - Display detailed system information\n• `!viewlogs <lines>` - Display recent bot logs (max 50)\n• `!clearcache` - Clear bot cache",
                inline=False
            ).add_field(
                name="🎛️ Bot Settings & Control",
                value="• `!setactivity <type> <name>` - Set bot activity\n• `!setstatus <status>` - Set bot status (online/idle/dnd)\n• `!leaveserver <guild_id>` - Leave specific server\n• `!admininvite` - Generate admin bot invite link",
                inline=False
            ).add_field(
                name="📨 Message Forwarding Management",
                value="• `!addforward <source_guild> <source_channel> [target]` - Add forwarding\n• `!removeforward <source_guild> <source_channel>` - Remove forwarding\n• `!listforwards` - List all forwarding configurations\n• `!selectforward` - Interactive configuration selector\n• `!activateforward <number>` - Activate specific configuration\n• `!deactivateforward [number]` - Deactivate forwarding\n• `!start <target_guild> [target_channel]` - Start forwarding from current channel",
                inline=False
            ).add_field(
                name="🎮 TwitchCord Management",
                value="• `!linkusertwitch @user <username>` - Link user's Twitch account\n• `!unlinkusertwitch @user` - Unlink user's Twitch account\n• `!forcelinktwitchbypass @user <username>` - Force link (bypass consent)\n• `!linkedusers` - Show all users with linked accounts\n• `!testattendance @user` - Test attendance notification",
                inline=False
            ).add_field(
                name="📊 Database & Updates",
                value="• `!dbclean [days]` - Clean old database entries (default 30 days)\n• `!clearalltrainschedules` - Clear ALL train schedules (OWNER ONLY)\n• `/clearparticipants` or `!clearparticipants` - Clear all participants (keeps schedules)\n• `!addupdate [category] <info>` - Add bot changelog update\n• `!manageupdates [action] [id]` - Manage existing updates\n• `!setupupdates [channel] [role]` - Configure weekly updates\n• `!updateconfig` - Show update configuration\n• `!updateschedule [day] [time]` - Set posting schedule\n• `!enableupdates` / `!disableupdates` - Toggle weekly updates",
                inline=False
            ).add_field(
                name="📅 Weekly Scheduler",
                value="• `!forceweeklypost [dry_run]` - Force weekly schedule post\n• `!schedulerstatus` - Check scheduler status\n• `!togglescheduler [enabled]` - Toggle scheduler on/off\n• `/setattendancechannel [channel]` - Set attendance channel",
                inline=False
            ),

            "owner": discord.Embed(
                title="👑 Owner Only Commands - Bot Owner Exclusive",
                description="Commands restricted to the bot owner only (@is_owner decorator)",
                color=0xff0000
            ).add_field(
                name="👥 Trusted User Management",
                value="• `!trustuser @user [notes]` - Add user to trusted list\n• `!untrustuser @user` - Remove user from trusted list\n• `!trustedusers` - List all trusted users with details",
                inline=False
            ),

            "special": discord.Embed(
                title="🔐 Special Permission Commands",
                description="Commands requiring specific Discord permissions (not role-based)",
                color=0x8b00ff
            ).add_field(
                name="💬 Manage Messages Permission",
                value="• `!startforward` - Start message forwarding (requires Manage Messages)\n• `!stopforward` - Stop message forwarding (requires Manage Messages)",
                inline=False
            ),

            "beta": discord.Embed(
                title="🧪 Beta Features — Commands",
                description="Beta features are **off by default** and enabled per-server by an admin using `/managefeatures`. Each feature must be toggled on before its commands work.",
                color=0xffaa00
            ).add_field(
                name="⚙️ Enabling & Viewing",
                value="• `/managefeatures` - Toggle beta features on/off for your server\n• `/viewserverfeatures` - See which features are currently enabled\n• `/betacommands` - Quick reference for all beta commands",
                inline=False
            ).add_field(
                name="🎟️ Slot Waitlist",
                value="• `/mywaitlists` - View, accept, decline, or leave your waitlist spots\n• `!setwaitlistpanel #channel` - Post the join/leave waitlist panel *(admin)*\n• `!removewaitlistpanel` - Remove the panel *(admin)*\n• `!setwaitlistlog #channel` - Set the seat-filled alert channel *(admin)*",
                inline=False
            ).add_field(
                name="⏰ Train Reminder DMs",
                value="• `/trainreminder on [minutes]` - Opt in (default 30 min lead time)\n• `/trainreminder off` - Opt out\n• `/trainreminder status` - Check your current setting",
                inline=False
            ).add_field(
                name="📊 Participant Stats",
                value="• `/trainstats` - Your attended sessions, current & longest streak, server top-10",
                inline=False
            )
        }
        
        embed = embeds.get(category)
        if embed:
            embed.set_footer(
                text="Game Lounge Train Bot v3.0 • Verified permissions • Use /help command:<name> for details"
            )
        return embed

    async def on_timeout(self):
        """Disable all components when the view times out."""
        for child in self.children:
            child.disabled = True


class BasicCommands(commands.Cog):
    """Basic commands for the Discord bot."""

    def __init__(self, bot):
        """Initialize the BasicCommands cog."""
        self.bot = bot
        self.logger = logging.getLogger('discord_bot.basic_commands')
        from collections import deque
        self._latency_history: deque = deque(maxlen=10)  # last 10 readings

        # ── Comprehensive knowledge base ────────────────────────────────────
        # Each entry: keywords (phrase, weight), title, color, explanation, solutions, commands
        self.KNOWLEDGE_BASE = {
            'twitch_oauth': {
                'keywords': [
                    ('oauth', 10), ('token', 8), ('expired', 10), ('expire', 9),
                    ('twitchoauth', 10), ('reauth', 10), ('re-auth', 10),
                    ('authorize', 8), ('authorise', 8), ('not authorized', 10),
                    ('not authorised', 10), ('invalid token', 10), ('token invalid', 10),
                    ('twitch auth', 9), ('auth failed', 9), ('revoke', 7),
                    ('refresh token', 9), ('access token', 8), ('scope', 6),
                ],
                'title': '🔑 Twitch OAuth / Token Issue',
                'color': 0xFF4444,
                'what_it_means': (
                    "The bot's Twitch OAuth token is likely expired or invalid. This token is needed "
                    "to fetch chatters for attendance tracking. The bot auto-refreshes tokens every "
                    "hour, but if Twitch revokes the refresh token (e.g. you changed your Twitch "
                    "password or manually revoked access), you need to re-authorise manually."
                ),
                'solutions': [
                    "1. Run `/twitchoauth` in Discord — the bot will DM you an authorisation link",
                    "2. Click the link, log in to Twitch if needed, then click **Authorize**",
                    "3. The bot will confirm the new token has been saved",
                    "4. Run `!checktwitch @YourName` to verify the token is now valid",
                    "5. If it expires again quickly, check whether your Twitch password changed recently",
                ],
                'commands': '`/twitchoauth` · `/oauthstatus` · `!checktwitch @user`',
                'diagnostic': '_diag_twitch_oauth',
                'autofix': '_autofix_token_refresh',
            },
            'twitch_link': {
                'keywords': [
                    ('not linked', 9), ('twitch username', 9), ('twitch account', 8),
                    ('link twitch', 8), ('twitch link', 8), ('linkusertwitch', 10),
                    ('twitch connection', 9), ('twitch name', 7), ('connect twitch', 8),
                    ('unlinked', 8), ('no twitch', 7), ('wrong twitch', 8),
                    ('twitch id', 7), ('twitch login', 7), ('checktwitch', 9),
                ],
                'title': '🔗 Twitch Account Linking',
                'color': 0x9146FF,
                'what_it_means': (
                    "A Discord user isn't linked to their Twitch account, or the wrong Twitch username "
                    "is stored. The bot needs this link to assign the live role when they go live, "
                    "track attendance, and send auto-shoutouts."
                ),
                'solutions': [
                    "1. Run `!linkusertwitch @DiscordUser twitch_username` to manually link them",
                    "2. Run `!checktwitch @DiscordUser` to verify the link is stored correctly",
                    "3. If the wrong Twitch name is stored: run `!linkusertwitch @User correct_name` again to overwrite",
                    "4. Run `!checktwitch twitch:their_twitch_name` to search by Twitch username instead",
                    "5. Ensure the Twitch username is spelled exactly as it appears on twitch.tv",
                ],
                'commands': '`!linkusertwitch @user twitch_name` · `!checktwitch @user`',
                'diagnostic': '_diag_twitch_link',
                'autofix': None,
            },
            'attendance': {
                'keywords': [
                    ('attendance', 10), ('tracking', 8), ('not tracking', 10),
                    ('attendance report', 10), ('who attended', 9), ('chatters', 8),
                    ('viewers', 7), ('missed attendance', 9), ('attendance session', 9),
                    ('start attendance', 9), ('stop attendance', 9), ('attendance not', 9),
                    ('not being tracked', 9), ('track viewers', 8), ('sendattendancenow', 9),
                    ('setupattendance', 9), ('attendance channel', 8),
                ],
                'title': '📊 Attendance Tracking Issue',
                'color': 0x28a745,
                'what_it_means': (
                    "Attendance tracking monitors who is in a streamer's Twitch chat during a raid train. "
                    "It requires: (a) a valid Twitch OAuth token, (b) an attendance channel configured, "
                    "and (c) the bot joined to the Twitch channel via IRC. Common causes: expired token, "
                    "attendance channel not set up, or the bot missed the stream start."
                ),
                'solutions': [
                    "1. Check your Twitch token is valid: run `!checktwitch @YourName` → look for 'Token valid'",
                    "2. If token is expired: run `/twitchoauth` to re-authorise",
                    "3. Check attendance channel is set: run `/setupattendance` in your server",
                    "4. If the session didn't start automatically: run `/starttest` to manually kick off a session",
                    "5. Force an attendance report right now: run `/sendattendancenow`",
                    "6. Check bot has 'Send Messages' permission in your attendance channel",
                ],
                'commands': '`/setupattendance` · `/sendattendancenow` · `/starttest` · `/oauthstatus`',
                'diagnostic': '_diag_attendance',
                'autofix': None,
            },
            'schedule': {
                'keywords': [
                    ('schedule', 10), ('notification', 9), ('not notified', 10),
                    ('ping', 8), ('not pinging', 9), ('raid notification', 9),
                    ('train time', 8), ('remind', 7), ('alert', 6),
                    ('stage 1', 8), ('stage 2', 8), ('stage 3', 8),
                    ('t-60', 8), ('t-30', 8), ('t-5', 8), ('60 minute', 7),
                    ('30 minute', 7), ('createschedule', 10), ('no notification', 9),
                    ('missed notification', 9), ('wrong time', 8), ('notification channel', 8),
                    ('raid channel', 7), ('setup channel', 7),
                ],
                'title': '📅 Schedule / Notifications Issue',
                'color': 0x5865F2,
                'what_it_means': (
                    "The bot sends three notification stages before a scheduled raid train: "
                    "Stage 1 at T-60 min, Stage 2 at T-30 min, and Stage 3 at T-5 min. "
                    "If notifications aren't arriving it's usually a channel config issue, "
                    "a timing mismatch (schedules use UK wall clock time), or the schedule "
                    "wasn't saved correctly."
                ),
                'solutions': [
                    "1. View current schedules: run `/nexttrain` or check the admin dashboard",
                    "2. Make sure a notification channel is set: run `/setnotificationchannel #channel`",
                    "3. All times are **UK wall clock (BST/GMT)** — double-check yours are correct",
                    "4. Create a new schedule: run `/createschedule` and fill in the details",
                    "5. Test notifications immediately: run `/testraidnow`",
                    "6. If the channel is wrong: update with `/setnotificationchannel #correct-channel`",
                    "7. Backup escalation fires 30 min before if Stage 1 went unconfirmed",
                ],
                'commands': '`/createschedule` · `/nexttrain` · `/testraidnow` · `/setnotificationchannel`',
                'diagnostic': '_diag_schedule',
                'autofix': None,
            },
            'live_role': {
                'keywords': [
                    ('live role', 10), ('streaming role', 9), ('not getting role', 10),
                    ('role not assigned', 10), ('live notification', 8), ('role not added', 9),
                    ('role not removed', 9), ('still has role', 8), ('stuck with role', 8),
                    ('live check', 8), ('liverole', 9), ('went live', 7), ('going live', 7),
                    ('stream live', 6), ('not showing live', 8),
                ],
                'title': '🔴 Live Role Issue',
                'color': 0xFF0000,
                'what_it_means': (
                    "The bot checks Twitch every few minutes and assigns/removes a 'live' role "
                    "automatically. For this to work: the user must be Twitch-linked, a live role "
                    "must be configured in the server, and the bot needs the 'Manage Roles' Discord "
                    "permission with its role ranked above the live role."
                ),
                'solutions': [
                    "1. Verify the user is Twitch-linked: `!checktwitch @User`",
                    "2. Check the bot has 'Manage Roles' permission in server settings",
                    "3. Make sure the bot's role is ranked **above** the live role in Server Settings → Roles",
                    "4. Verify a live role is configured: check your server's bot setup",
                    "5. The bot polls Twitch every 5 min — wait a full cycle after going live",
                    "6. If the role is stuck ON after going offline: wait one poll cycle, then report if still stuck",
                ],
                'commands': '`!checktwitch @user` · `/oauthstatus`',
                'diagnostic': '_diag_live_role',
                'autofix': None,
            },
            'chat_monitor': {
                'keywords': [
                    ('chat monitor', 10), ('irc', 9), ('twitch chat', 8),
                    ('auto shoutout', 9), ('shoutout', 7), ('chat message', 7),
                    ('not joining', 9), ('not in channel', 9), ('not monitoring', 9),
                    ('channel not joined', 9), ('chatmonitor', 9), ('stopchatmonitor', 8),
                    ('testchatmonitor', 9), ('irc disconnect', 9), ('irc reconnect', 8),
                    ('monitor chat', 8), ('watching chat', 7),
                ],
                'title': '💬 Twitch Chat Monitor Issue',
                'color': 0x9146FF,
                'what_it_means': (
                    "The bot connects to Twitch IRC to monitor chat in real time — this powers "
                    "auto-shoutouts and live chat presence for attendance. If the bot isn't in a "
                    "channel's chat it can't track messages or trigger shoutouts."
                ),
                'solutions': [
                    "1. Check which channels the bot is currently in: run `/chatmonitorstatus`",
                    "2. Test the connection to a specific channel: run `/testchatmonitor channel:twitch_name`",
                    "3. If the bot disconnected from IRC, restart auto-join: run `/stopchatmonitor` then the bot auto-reconnects",
                    "4. Confirm the Twitch username is correct: run `!checktwitch @User`",
                    "5. The bot auto-joins all linked channels on startup — a restart will re-connect all channels",
                    "6. Check bot logs for IRC authentication errors (usually a token issue)",
                ],
                'commands': '`/chatmonitorstatus` · `/testchatmonitor` · `/stopchatmonitor`',
                'diagnostic': '_diag_chat_monitor',
                'autofix': '_autofix_rejoin_irc',
            },
            'raid_train': {
                'keywords': [
                    ('raid', 8), ('train', 7), ('auto raid', 9), ('raiding', 8),
                    ('next raid', 8), ('raid order', 8), ('who next', 7), ('raid queue', 8),
                    ('raid not', 8), ('not raiding', 9), ('raid failed', 9),
                    ('raid stuck', 8), ('testraidnow', 9), ('listnextrider', 9),
                    ('raid command', 7), ('autoraid', 9),
                ],
                'title': '🚂 Raid Train Issue',
                'color': 0xFF6B35,
                'what_it_means': (
                    "Raid train automation handles the order of raids during a stream. "
                    "Auto-raid fires the Twitch /raid command at stream end. Issues are usually "
                    "caused by: no participants signed up, the next rider being offline, or "
                    "the bot not having channel editor/mod status on Twitch."
                ),
                'solutions': [
                    "1. Check who's next in the raid queue: run `/listnextrider`",
                    "2. Test a raid right now: run `/testraidnow`",
                    "3. For auto-raid to work the bot must be a **channel editor** on Twitch",
                    "4. Participants must be signed up for the schedule in advance",
                    "5. View the full schedule: run `/nexttrain`",
                    "6. If the next rider is offline the bot skips to the next available one",
                ],
                'commands': '`/listnextrider` · `/nexttrain` · `/testraidnow`',
                'diagnostic': '_diag_schedule',
                'autofix': None,
            },
            'permissions': {
                'keywords': [
                    ('permission', 9), ('forbidden', 9), ("can't send", 9), ('cannot send', 9),
                    ('missing access', 9), ('missing permission', 9), ('denied', 8),
                    ('manage roles', 8), ('send messages', 8), ('403', 8), ('401', 7),
                    ('no access', 7), ('role permission', 7),
                ],
                'title': '🔒 Discord Permission Issue',
                'color': 0xFFA500,
                'what_it_means': (
                    "The bot is missing a Discord permission it needs in your server. "
                    "Different features require different permissions."
                ),
                'solutions': [
                    "1. Go to **Server Settings → Roles** and click the bot's role",
                    "2. Required permissions: Send Messages, Read Message History, Embed Links, Manage Roles, Add Reactions",
                    "3. Also check channel-level overrides: the bot role may be allowed server-wide but denied in specific channels",
                    "4. For live roles: bot's role must be **ranked above** the live role in the role list",
                    "5. For cross-server features: bot needs the same permissions in both servers",
                ],
                'commands': '`/ping` to check bot is responding · `/help` for feature list',
                'diagnostic': None,
                'autofix': None,
            },
            'bot_down': {
                'keywords': [
                    ('offline', 9), ('not responding', 9), ('bot down', 10), ('not working', 8),
                    ('broken', 8), ('error', 6), ('crash', 9), ('restarted', 7),
                    ('slow', 6), ('latency', 7), ('not responding', 9), ('timed out', 7),
                    ('bot not', 7), ("doesn't work", 8), ('stopped working', 9),
                ],
                'title': '⚙️ Bot Not Responding',
                'color': 0x6c757d,
                'what_it_means': (
                    "The bot appears to be down, slow, or not responding to commands. "
                    "This may be a temporary blip, a Replit restart, or a code issue."
                ),
                'solutions': [
                    "1. Run `/ping` — if it responds the bot is up and the issue is likely command-specific",
                    "2. Wait 60 seconds — the bot auto-restarts and takes ~30 sec to reconnect",
                    "3. If `/ping` doesn't respond: the bot may be restarting; try again in 2 minutes",
                    "4. Check if Twitch commands work separately from Discord commands to narrow down the issue",
                    "5. If it's been down more than 5 minutes, contact the bot owner",
                ],
                'commands': '`/ping` · `!dash` for status',
                'diagnostic': None,
                'autofix': None,
            },
            'forwarding': {
                'keywords': [
                    ('forward', 9), ('forwarding', 9), ('not forwarding', 10),
                    ('addforward', 9), ('removeforward', 9), ('message forward', 9),
                    ('cross server', 8), ('mirror', 7), ('copy message', 7),
                ],
                'title': '📨 Message Forwarding Issue',
                'color': 0x667eea,
                'what_it_means': (
                    "Message forwarding copies messages from a source channel to a target channel, "
                    "across servers if needed. Common issues: missing permissions, wrong channel IDs, "
                    "or the forwarding rule wasn't saved."
                ),
                'solutions': [
                    "1. View all active forwarding rules: run `!forwards`",
                    "2. Add a new rule: `!addforward #source-channel #target-channel`",
                    "3. Remove a rule: `!removeforward` and follow the prompts",
                    "4. Bot needs Send Messages + Read Message History in **both** channels",
                    "5. For cross-server: the bot must be a member of both servers",
                ],
                'commands': '`!forwards` · `!addforward` · `!removeforward`',
                'diagnostic': None,
                'autofix': None,
            },
        }
        # Keep legacy field for anything that still references it
        self.support_solutions = {}

    async def respond(self, interaction, *args, **kwargs):
        """Helper method to safely respond to an interaction."""
        # followup.send rejects view=None; strip it out before sending via webhook
        if interaction.response.is_done():
            if 'view' in kwargs and kwargs['view'] is None:
                del kwargs['view']
            return await interaction.followup.send(*args, **kwargs)
        return await interaction.response.send_message(*args, **kwargs)

    @app_commands.command(name='ping', description='Check bot latency and toggle maintenance mode (admin only)')
    async def ping(self, interaction: discord.Interaction):
        """Check bot latency and toggle maintenance mode if active."""
        from utils.maintenance_manager import maintenance_manager
        from datetime import datetime
        
        self.logger.debug(f"🏓 Ping command invoked by {interaction.user} ({interaction.user.id})")
        
        # Calculate latency first
        latency = round(self.bot.latency * 1000)
        
        # Determine latency quality
        if latency < 100:
            quality = "🟢 Excellent"
        elif latency < 200:
            quality = "🟡 Good"
        elif latency < 300:
            quality = "🟠 Fair"
        else:
            quality = "🔴 Poor"
        
        # Check for maintenance mode
        in_maintenance = await maintenance_manager.get()
        
        if in_maintenance:
            # Check if user is authorized to clear maintenance mode
            if await self.bot.is_owner_or_trusted(interaction.user):
                # Clear maintenance mode
                success = await maintenance_manager.set(False)
                
                if success:
                    self.logger.info(f"Maintenance mode disabled by {interaction.user}")
                    
                    # Send exit maintenance message
                    embed = discord.Embed(
                        title="🟢 Bot Online!",
                        description=f"**Maintenance mode disabled!**\n\n**Latency:** {latency}ms\n**Quality:** {quality}",
                        color=discord.Color.green()
                    )
                    embed.add_field(
                        name="ℹ️ Status",
                        value=f"All commands are now available.\nDisabled by {interaction.user.mention}",
                        inline=False
                    )
                    embed.set_footer(text=f"Exited maintenance at {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC")
                    
                    await self.respond(interaction, embed=embed)
                    return
                else:
                    embed = discord.Embed(
                        title="❌ Error",
                        description="Failed to disable maintenance mode. Check database connection.",
                        color=discord.Color.red()
                    )
                    await self.respond(interaction, embed=embed)
                    return
            else:
                # User not authorized
                embed = discord.Embed(
                    title="🔧 Bot in Maintenance",
                    description=f"**Latency:** {latency}ms\n**Quality:** {quality}",
                    color=discord.Color.orange()
                )
                embed.add_field(
                    name="⚠️ Limited Access",
                    value="Bot is in maintenance mode. Only admins can disable it.",
                    inline=False
                )
                await self.respond(interaction, embed=embed)
                return
        
        # Record this reading in the rolling history
        from datetime import datetime as _dt
        self._latency_history.append((latency, _dt.utcnow()))

        # Build latency trend context
        if len(self._latency_history) >= 2:
            readings = [r[0] for r in self._latency_history]
            avg = round(sum(readings) / len(readings))
            lo = min(readings)
            hi = max(readings)
            # Show last 5 readings as dots with color
            def _dot(ms):
                if ms < 100: return f"🟢{ms}"
                if ms < 200: return f"🟡{ms}"
                if ms < 300: return f"🟠{ms}"
                return f"🔴{ms}"
            recent = " → ".join(_dot(r) for r in list(readings)[-5:])
            trend_text = (
                f"{recent}\n"
                f"**Avg:** {avg}ms  **Low:** {lo}ms  **High:** {hi}ms "
                f"*(last {len(readings)} checks)*"
            )
            # Determine if this is a spike vs baseline
            if len(readings) >= 3 and latency > avg * 1.5 and latency > 120:
                trend_note = "⚠️ This reading is higher than your recent average."
            elif len(readings) >= 3 and latency < avg * 0.7:
                trend_note = "⚡ This reading is lower than usual — looking good!"
            else:
                trend_note = ""
        else:
            trend_text = f"🟢 First reading — run `/ping` a few times to build a trend."
            trend_note = ""

        # Embed colour based on quality
        if latency < 100:
            color = discord.Color.green()
        elif latency < 200:
            color = discord.Color.yellow()
        elif latency < 300:
            color = discord.Color.orange()
        else:
            color = discord.Color.red()

        # Send standard ping response (not in maintenance)
        embed = discord.Embed(
            title="🏓 Pong!",
            description=(
                f"**Latency:** {latency}ms  ·  **Quality:** {quality}"
                + (f"\n{trend_note}" if trend_note else "")
            ),
            color=color
        )
        embed.add_field(name="📈 Latency History", value=trend_text, inline=False)
        embed.set_footer(
            text=f"Requested by {interaction.user.display_name}",
            icon_url=interaction.user.display_avatar.url if interaction.user.display_avatar else None
        )

        self.logger.debug(f"🏓 Sending ping response to {interaction.user} ({latency}ms)")
        await self.respond(interaction, embed=embed)

    async def _perform_maintenance_exit_tasks(self, interaction, latency, quality):
        """Comprehensive maintenance exit with Twitch approval detection, schedule refresh, and operational checks."""
        from database import DatabaseSession
        from models import TwitchLinkRequest, TrainParticipant
        from datetime import datetime, timedelta
        
        self.logger.info("🔄 Starting enhanced maintenance exit procedures...")
        
        # CRITICAL: Acknowledge interaction immediately (Discord requires response within 3 seconds)
        try:
            await interaction.response.defer(thinking=True)
        except:
            # Already responded - use followup instead
            pass
        
        # Send initial response
        embed = discord.Embed(
            title="🟢 Bot Coming Online!",
            description=f"**Maintenance mode disabled!**\n\n**Latency:** {latency}ms\n**Quality:** {quality}",
            color=discord.Color.green())
        embed.add_field(
            name="🔄 System Checks",
            value="Running comprehensive operational checks...",
            inline=False
        )
        
        # Send response (use followup since we deferred)
        try:
            await interaction.followup.send(embed=embed)
        except:
            # Fallback if followup fails
            try:
                await interaction.response.send_message(embed=embed)
            except:
                self.logger.error("Failed to send maintenance exit response")
        
        # Initialize results tracking
        results = {
            'twitch_approvals': 0,
            'schedule_refreshed': False,
            'operational_checks': {},
            'errors': []
        }
        
        try:
            # 1. Check for new Twitch linking approvals during maintenance
            self.logger.info("🔍 Checking for new Twitch approvals during maintenance...")
            with DatabaseSession() as session:
                # Get maintenance start time (when it was last enabled)
                maintenance_start = datetime.utcnow() - timedelta(hours=24)  # Default to 24h ago
                
                # Find recently approved Twitch links
                recent_approvals = session.query(TwitchLinkRequest).filter(
                    TwitchLinkRequest.status == 'approved',
                    TwitchLinkRequest.responded_at >= maintenance_start
                ).all()
                
                if recent_approvals:
                    results['twitch_approvals'] = len(recent_approvals)
                    self.logger.info(f"✅ Found {len(recent_approvals)} new Twitch approvals to process")
                    
                    # Update train participants with approved Twitch usernames
                    for approval in recent_approvals:
                        participant = session.query(TrainParticipant).filter_by(
                            user_id=approval.target_user_id,
                            is_active=True
                        ).first()
                        
                        if participant and (participant.twitch_username is None or participant.twitch_username == ''):
                            participant.twitch_username = approval.requested_twitch_username
                            self.logger.info(f"🔗 Linked {approval.target_username} → {approval.requested_twitch_username}")
                    
                    session.commit()
                else:
                    self.logger.info("📋 No new Twitch approvals found")
            
            # 2. Refresh persistent displays if Twitch links were updated
            if results['twitch_approvals'] > 0:
                self.logger.info("🔄 Refreshing persistent displays with new Twitch links...")
                try:
                    # Trigger persistent display refresh
                    train_cog = self.bot.get_cog('TrainParticipantCommands')
                    if train_cog:
                        await train_cog._update_all_persistent_displays()
                        results['schedule_refreshed'] = True
                        self.logger.info("✅ Persistent displays refreshed successfully")
                    else:
                        results['errors'].append("Train participant commands cog not found")
                except Exception as e:
                    results['errors'].append(f"Failed to refresh displays: {str(e)}")
                    self.logger.error(f"Error refreshing displays: {e}")
            
            # 3. Run comprehensive operational checks
            self.logger.info("🔍 Running operational checks...")
            results['operational_checks'] = await self._run_operational_checks()
            
        except Exception as e:
            results['errors'].append(f"Maintenance exit error: {str(e)}")
            self.logger.error(f"Error during maintenance exit: {e}")
        
        # 4. Send comprehensive status report
        await self._send_maintenance_exit_report(interaction, results)

    async def _run_operational_checks(self):
        """Run comprehensive operational checks and return results."""
        checks = {}
        
        try:
            # Check database connection
            from database import DatabaseSession
            with DatabaseSession() as session:
                from sqlalchemy import text
                session.execute(text("SELECT 1"))
                checks['database'] = "✅ Connected"
        except Exception as e:
            checks['database'] = f"❌ Error: {str(e)}"
        
        try:
            # Check Discord connection
            if self.bot.is_ready():
                checks['discord'] = f"✅ Connected ({len(self.bot.guilds)} guilds)"
            else:
                checks['discord'] = "❌ Not ready"
        except Exception as e:
            checks['discord'] = f"❌ Error: {str(e)}"
        
        try:
            # Check notification scheduler
            notif_cog = self.bot.get_cog('NotificationCommands')
            if notif_cog:
                if hasattr(notif_cog, 'notification_scheduler'):
                    is_running = notif_cog.notification_scheduler.is_running()
                    self.logger.debug(f"Notification scheduler is_running check: {is_running}")
                    if is_running:
                        checks['notifications'] = "✅ Scheduler running"
                    else:
                        checks['notifications'] = "❌ Scheduler not running"
                else:
                    checks['notifications'] = "❌ Scheduler attribute missing"
            else:
                checks['notifications'] = "❌ Cog not loaded"
        except Exception as e:
            self.logger.error(f"Notification check error: {e}")
            checks['notifications'] = f"❌ Error: {str(e)}"
        
        try:
            # Check persistent display updater
            train_cog = self.bot.get_cog('TrainParticipantCommands')
            if train_cog:
                if hasattr(train_cog, 'persistent_updater'):
                    is_running = train_cog.persistent_updater.is_running()
                    self.logger.debug(f"Persistent updater is_running check: {is_running}")
                    if is_running:
                        checks['persistent_displays'] = "✅ Updater running"
                    else:
                        checks['persistent_displays'] = "❌ Updater not running"
                else:
                    checks['persistent_displays'] = "❌ Updater attribute missing"
            else:
                checks['persistent_displays'] = "❌ Cog not loaded"
        except Exception as e:
            self.logger.error(f"Persistent display check error: {e}")
            checks['persistent_displays'] = f"❌ Error: {str(e)}"
        
        return checks

    async def _send_maintenance_exit_report(self, interaction, results):
        """Send comprehensive status report after maintenance exit."""
        
        # Determine overall status
        has_errors = len(results['errors']) > 0
        failed_checks = [k for k, v in results['operational_checks'].items() if v.startswith('❌')]
        
        if has_errors or failed_checks:
            # Error report
            embed = discord.Embed(
                title="⚠️ Bot Online with Issues",
                description="Bot has exited maintenance mode but some issues were detected.",
                color=discord.Color.orange(),
                timestamp=datetime.utcnow()
            )
            
            if results['errors']:
                embed.add_field(
                    name="❌ Errors Encountered",
                    value="\n".join([f"• {error}" for error in results['errors']]),
                    inline=False
                )
            
            if failed_checks:
                embed.add_field(
                    name="⚠️ Failed System Checks",
                    value="\n".join([f"• {check}: {status}" for check, status in results['operational_checks'].items() if status.startswith('❌')]),
                    inline=False
                )
            
        else:
            # Success report
            embed = discord.Embed(
                title="✅ Bot Fully Operational!",
                description="All systems are running perfectly after maintenance exit.",
                color=discord.Color.green(),
                timestamp=datetime.utcnow()
            )
        
        # Add Twitch approval results
        if results['twitch_approvals'] > 0:
            embed.add_field(
                name="🔗 Twitch Approvals Processed",
                value=f"✅ {results['twitch_approvals']} new Twitch account(s) linked",
                inline=True
            )
        
        # Add schedule refresh status
        if results['schedule_refreshed']:
            embed.add_field(
                name="🔄 Schedule Refreshed",
                value="✅ Persistent displays updated with new Twitch links",
                inline=True
            )
        
        # Add operational checks
        if results['operational_checks']:
            working_checks = [f"• {check}: {status}" for check, status in results['operational_checks'].items() if status.startswith('✅')]
            if working_checks:
                embed.add_field(
                    name="✅ System Status",
                    value="\n".join(working_checks),
                    inline=False
                )
        
        embed.set_footer(
            text=f"Maintenance exit completed by {interaction.user}",
            icon_url=interaction.user.avatar.url if interaction.user.avatar else None
        )
        
        # Send follow-up message
        try:
            await interaction.followup.send(embed=embed)
            self.logger.info("✅ Maintenance exit report sent successfully")
        except Exception as e:
            self.logger.error(f"Failed to send maintenance exit report: {e}")


    @app_commands.command(name='twitchcord', description='Show active TwitchCord features and system status')
    async def twitchcord_preview(self, interaction: discord.Interaction):
        """Show all active TwitchCord features and current system status."""
        
        embed = discord.Embed(
            title="🎮 Game Lounge Train - TwitchCord Active!",
            description="**Comprehensive Twitch-Discord Integration System**\n*✅ Fully integrated and operational*",
            color=0x9146ff  # Twitch purple
        )
        
        # Active Features
        embed.add_field(
            name="🎯 Raid Train Management - ✅ ACTIVE",
            value=(
                "• **@mention Ping System** - ✅ Fully operational\n"
                "• **Emoji Reaction Responses** - ✅ Working with all commands\n" 
                "• **15-Min Backup System** - ✅ Active monitoring\n"
                "• **Automatic Attendance Updates** - ✅ Database tracking\n"
                "• **Smart Position Tracking** - ✅ Real-time coordination"
            ),
            inline=False
        )
        
        embed.add_field(
            name="🤖 Intelligent Automation - ✅ ACTIVE",
            value=(
                "• **Auto Ping Next Streamer** - ✅ Ready for raid trains\n"
                "• **Backup Streamer Assignment** - ✅ Application system live\n"
                "• **Timeout Management** - ✅ 15-minute response tracking\n"
                "• **Real-time Status Updates** - ✅ Live monitoring active\n"
                "• **Cross-platform Sync** - ⚠️ Requires Twitch API setup"
            ),
            inline=False
        )
        
        embed.add_field(
            name="📊 TwitchCord Commands - ✅ ACTIVE",
            value=(
                "• **!twitchhelp** - Complete command guide\n"
                "• **!streams, !analytics, !raidstats** - Performance tracking\n"
                "• **!attendance, !streak** - Participation monitoring\n"
                "• **!poll, !leaderboard** - Community engagement\n"
                "• **!addstream, !timeslots** - Stream management"
            ),
            inline=False
        )
        
        embed.add_field(
            name="⚡ Core Features - ✅ ACTIVE",
            value=(
                "• **Message Forwarding** - ✅ Cross-server communication\n"
                "• **Web Dashboard** - ✅ Real-time management interface\n"
                "• **Database Tracking** - ✅ Persistent data storage\n"
                "• **Admin Commands** - ✅ Full bot management\n"
                "• **24/7 Uptime** - ✅ Reserved VM deployment"
            ),
            inline=False
        )
        
        embed.add_field(
            name="🔧 Quick Commands",
            value=(
                "• **!twitchhelp** - View all TwitchCord commands\n"
                "• **!help** - Complete bot command guide\n"
                "• **!dashboard** - Access web dashboard interface\n"
                "• **!addforward** - Setup message forwarding\n"
                "• **!status** - Check current configurations"
            ),
            inline=False
        )
        
        embed.add_field(
            name="💰 Support Game Lounge Train",
            value=(
                "**$25/month hosting costs** - Keep the raid train running 24/7!\n"
                "• Dedicated Reserved VM hosting\n"
                "• Professional Discord bot infrastructure\n"
                "• Continuous feature development\n"
                "• Community support & feature requests"
            ),
            inline=False
        )
        
        embed.set_footer(
            text="🚂 Game Lounge Train Bot • Full TwitchCord Integration • Live Attendance • Analytics",
            icon_url=self.bot.user.avatar.url if self.bot.user.avatar else None
        )
        
        embed.set_thumbnail(url="https://static-cdn.jtvnw.net/jtv_user_pictures/8a6381c7-d0c0-4576-b179-38bd5ce1d6af-profile_image-300x300.png")
        
        await self.respond(interaction, embed=embed)

    @commands.command(name='dash', help='Access the modern web dashboard interface')
    async def dashboard_command(self, ctx: commands.Context):
        """Access the comprehensive web dashboard interface."""
        try:
            # Direct link to the modern dashboard
            domain = os.environ.get('REPLIT_DEV_DOMAIN') or os.environ.get('REPLIT_DOMAINS', '').split(',')[0]
            dashboard_url = f"https://{domain}" if domain else "Dashboard temporarily unavailable"
            
            embed = discord.Embed(
                title="🎯 Modern Bot Dashboard",
                description="**Access your tabbed web interface with real-time bot management**",
                color=0x667eea
            )
            
            embed.add_field(
                name="🔗 Dashboard Access",
                value=f"**[Open Modern Dashboard]({dashboard_url})**\n\n"
                      "Your new responsive web interface with 8 organized tabs:",
                inline=False
            )
            
            embed.add_field(
                name="📊 Dashboard Tabs",
                value=(
                    "• **📊 Overview** - Bot stats, connected servers, quick actions\n"
                    "• **📅 Train Schedules** - View all active train schedules\n"
                    "• **🎥 Live Roles** - Manage automatic live role assignments\n"
                    "• **↗️ Forwarding** - Message forwarding rules management\n"
                    "• **🎮 Twitch** - Linked accounts and integration settings\n"
                    "• **👥 Users** - Trusted users, roles, and banned users\n"
                    "• **📈 Analytics** - Bot usage and performance metrics\n"
                    "• **⚙️ Settings** - Bot configuration and maintenance mode"
                ),
                inline=False
            )
            
            embed.add_field(
                name="✨ Key Features",
                value=(
                    "• Real-time bot statistics and server information\n"
                    "• Manage forwarding rules with server/channel names\n"
                    "• View Twitch linked accounts and integration status\n"
                    "• Control maintenance mode and bot settings\n"
                    "• Mobile-responsive design with dark theme\n"
                    "• Live data updates via REST API"
                ),
                inline=False
            )
            
            embed.set_footer(
                text="🚂 Game Lounge Train • Modern Dashboard • Tabbed Interface",
                icon_url=self.bot.user.avatar.url if self.bot.user.avatar else None
            )
            
            await ctx.send(embed=embed)
            
        except Exception as e:
            self.logger.error(f"Error in dashboard command: {e}")
            # Fallback message if embed fails
            try:
                await ctx.send("❌ Dashboard command error - please try again")
            except:
                pass  # Prevent cascade errors

    @app_commands.command(name='info', description='Display bot information')
    async def info(self, interaction: discord.Interaction):
        """Display bot information."""
        # Calculate uptime
        if self.bot.start_time:
            uptime = datetime.utcnow() - self.bot.start_time
            uptime_str = str(uptime).split('.')[0]  # Remove microseconds
        else:
            uptime_str = "Unknown"

        # Get system info
        try:
            cpu_usage = psutil.cpu_percent(interval=1)
            memory = psutil.virtual_memory()
            memory_usage = f"{memory.percent}%"
        except:
            cpu_usage = "N/A"
            memory_usage = "N/A"

        embed = discord.Embed(title=f"🤖 {self.bot.user.name} Information",
                              color=discord.Color.blue())

        # Bot info
        embed.add_field(
            name="📊 Bot Statistics",
            value=f"**Servers:** {len(self.bot.guilds)}\n"
            f"**Users:** {len(self.bot.users):,}\n"
            f"**Commands Used:** {getattr(self.bot, 'commands_executed', 0)}\n"
            f"**Messages Seen:** {getattr(self.bot, 'messages_seen', 0):,}",
            inline=True)

        # System info
        embed.add_field(name="⚙️ System Information",
                        value=f"**Platform:** {platform.system()}\n"
                        f"**Python:** {platform.python_version()}\n"
                        f"**Discord.py:** {discord.__version__}\n"
                        f"**CPU Usage:** {cpu_usage}%\n"
                        f"**Memory Usage:** {memory_usage}",
                        inline=True)

        # Runtime info
        embed.add_field(name="🕐 Runtime Information",
                        value=f"**Uptime:** {uptime_str}\n"
                        f"**Latency:** {round(self.bot.latency * 1000)}ms\n"
                        f"**Hosted on:** Replit\n"
                        f"**Status:** 🟢 Online",
                        inline=True)

        embed.set_thumbnail(
            url=self.bot.user.avatar.url if self.bot.user.avatar else None)
        embed.set_footer(
            text="🚂 Game Lounge Train Bot • Train Scheduling • Twitch Integration • Live Attendance Tracking",
            icon_url=self.bot.user.avatar.url if self.bot.user.avatar else None)
        embed.timestamp = datetime.utcnow()

        await self.respond(interaction, embed=embed)

    @commands.command(name='about', help='Learn about Game Lounge Train Bot')
    async def about(self, ctx: commands.Context):
        """Display comprehensive information about the bot."""
        embed = discord.Embed(
            title="🚂 Welcome to Game Lounge Train Bot!",
            description="Hey there! I'm your friendly neighborhood bot that helps streamers grow together through raid trains. Think of me as your automated train conductor - I handle the boring stuff so you can focus on creating great content! 🎮",
            color=0x9146ff,
            timestamp=datetime.utcnow()
        )
        
        embed.add_field(
            name="🤔 What's a Raid Train?",
            value=(
                "Imagine a bunch of streamers taking turns going live, raiding each other in sequence like a train! "
                "Everyone gets their spotlight, communities grow, and it's just a super fun way to support each other. "
                "I automate the whole thing so nobody has to stress about the details!"
            ),
            inline=False
        )
        
        embed.add_field(
            name="✨ What I Can Do For You",
            value=(
                "**🎯 Train Management**\n"
                "I'll handle scheduling your trains, send reminders, and track who shows up. No spreadsheets needed!\n\n"
                "**💬 Twitch Chat Assistant**\n"
                "I jump into your Twitch chat during streams to welcome new people, help coordinate raids, and keep things running smoothly.\n\n"
                "**🔥 Auto-Shoutouts**\n"
                "When someone new chats for the first time, I'll give them a warm welcome with your custom message - perfect when you're gaming and can't type!\n\n"
                "**📊 Smart Analytics**\n"
                "I track attendance, participation, and send you detailed reports so you know exactly how your trains are performing."
            ),
            inline=False
        )
        
        embed.add_field(
            name="🎮 Cool Extras",
            value=(
                "I also support **YouTube Gaming** and **Kick**, notify you when your friends post new **clips or VODs**, "
                "protect your server from **spam**, let people set **AFK status**, sync with **Google Sheets**, "
                "and even have a modern **web dashboard** for easy management. Pretty neat, right?"
            ),
            inline=False
        )
        
        embed.add_field(
            name="🚀 How to Get Started",
            value=(
                "Just use `/help` to see everything I can do, or type `!dash` to open my web interface where you can manage everything with a few clicks. "
                "New to trains? Your server admin can set things up, and then you just sign up and go live at your time!"
            ),
            inline=False
        )
        
        embed.add_field(
            name="💜 Why I Exist",
            value=(
                "Raid trains are amazing for small streamers, but organizing them can be a headache. "
                "I'm here to automate all the tedious stuff so you can focus on what matters: "
                "**building your community, having fun, and supporting each other.** Let's grow together! 🌱"
            ),
            inline=False
        )
        
        embed.set_thumbnail(
            url=self.bot.user.avatar.url if self.bot.user.avatar else None
        )
        
        embed.set_footer(
            text="🚂 All aboard! Let's build amazing communities together 💜",
            icon_url=self.bot.user.avatar.url if self.bot.user.avatar else None
        )
        
        await ctx.send(embed=embed)

    @commands.command(name='botinvite', help='Get bot invite link')
    async def public_invite(self, ctx: commands.Context):
        """Generate public bot invite link."""
        # Basic permissions for public use
        permissions = discord.Permissions(read_messages=True,
                                          send_messages=True,
                                          embed_links=True,
                                          read_message_history=True,
                                          use_external_emojis=True,
                                          add_reactions=True,
                                          view_channel=True)

        invite_url = discord.utils.oauth_url(self.bot.user.id,
                                             permissions=permissions,
                                             scopes=('bot', ))

        embed = discord.Embed(
            title="🤖 Invite Me to Your Server!",
            description=
            "Click the link below to add me to your Discord server:",
            color=discord.Color.blue())

        embed.add_field(
            name="🔗 Invite Link",
            value=f"[Add {self.bot.user.name} to your server]({invite_url})",
            inline=False)

        embed.add_field(
            name="✨ What I can do:",
            value=
            "• Forward messages between channels\n• Track server statistics\n• Provide bot information\n• Monitor server activity",
            inline=False)

        embed.add_field(
            name="🛡️ Permissions I need:",
            value=
            "• Read and send messages\n• View channels\n• Use external emojis\n• Add reactions",
            inline=False)

        embed.set_footer(
            text=f"Requested by {ctx.author}",
            icon_url=ctx.author.avatar.url if ctx.author.avatar else None)

        await ctx.send(embed=embed)

    @app_commands.command(name='help', description='Interactive command guide with dropdown menu')
    async def help_command(self, interaction: discord.Interaction, command_name: str | None = None):
        """Interactive help command with dropdown menu."""
        prefix = self.bot.command_prefix

        if command_name:
            # Help for specific command - check both prefix and slash commands
            command = self.bot.get_command(command_name.lower())
            slash_command = None
            
            # Also check slash commands
            if hasattr(self.bot, 'tree'):
                for cmd in self.bot.tree.walk_commands():
                    if cmd.name.lower() == command_name.lower():
                        slash_command = cmd
                        break
            
            if command or slash_command:
                # Prefer slash command if both exist
                target_cmd = slash_command if slash_command else command
                cmd_name = target_cmd.name
                
                embed = discord.Embed(title=f"📖 Command Help: {cmd_name}",
                                      description=getattr(target_cmd, 'description', getattr(target_cmd, 'help', 'No description available')),
                                      color=discord.Color.blue())

                # Show usage based on command type
                if slash_command:
                    embed.add_field(name="Usage", value=f"`/{cmd_name}` (Slash command)", inline=False)
                    embed.add_field(name="Type", value="Discord Slash Command", inline=True)
                else:
                    if command.aliases:
                        embed.add_field(name="Aliases", value=", ".join(command.aliases), inline=False)
                    embed.add_field(name="Usage", value=f"`{prefix}{command.name} {command.signature}`", inline=False)
                    embed.add_field(name="Type", value="Prefix Command", inline=True)
                
                await self.respond(interaction, embed=embed)
                return  # Important: Return early to prevent showing interactive menu
            else:
                embed = discord.Embed(title="❌ Command Not Found",
                                    description=f"No command named `{command_name}` found.\n\nTry using the dropdown menu above to browse commands by category.",
                                    color=discord.Color.red())
                await self.respond(interaction, embed=embed)
                return  # Important: Return early to prevent showing interactive menu
        else:
            # Interactive help menu
            view = HelpDropdownView(interaction.user)
            embed = discord.Embed(
                title="🚂 Game Lounge Train Bot - Command Reference",
                description="**Commands are organized by permission requirements. Select a category from the dropdown below:**\n\nUse `/help command:<name>` for specific command details.",
                color=0x9146ff
            )
            
            embed.add_field(
                name="🔒 Permission-Based Categories",
                value="• 🌟 **Regular Commands** - Available to everyone (25+ commands)\n• 🔒 **Trusted Commands** - Admin & setup commands (30+ commands)\n• 👑 **Owner Only Commands** - Trust management (3 commands)\n• 🔐 **Special Permissions** - Discord permission-based (2 commands)\n• 🧪 **Beta Features** - Commands for server-enabled beta features",
                inline=False
            )
            
            embed.add_field(
                name="📈 Command Statistics",
                value="**60+ Total Commands** verified and categorized by actual permissions\n• **Prefix commands:** `!command` (traditional)\n• **Slash commands:** `/command` (Discord's new system)\n• **Hybrid commands:** Both prefix and slash available",
                inline=False
            )
            
            embed.add_field(
                name="📝 How to Use This Help System",
                value="• Select a permission level from the dropdown above\n• All commands are verified against actual code permissions\n• Permission requirements are clearly stated in each category\n• Use `/help command:<name>` for detailed usage info",
                inline=False
            )
            
            embed.set_footer(
                text="Game Lounge Train Bot v3.0 • TwitchCord Integration • Multi-Server Support",
                icon_url=interaction.user.avatar.url if interaction.user.avatar else None
            )
            
            await self.respond(interaction, embed=embed, view=view)


    @app_commands.command(name='betacommands', description='Quick reference for all beta feature commands')
    async def betacommands(self, interaction: discord.Interaction):
        """Shows all beta feature commands in one place."""
        embed = discord.Embed(
            title="🧪 Beta Feature Commands",
            description=(
                "Beta features are **off by default** and must be enabled per-server by an admin "
                "using `/managefeatures`. Use `/viewserverfeatures` to see what's currently active."
            ),
            color=0xffaa00
        )
        embed.add_field(
            name="🎟️ Slot Waitlist",
            value=(
                "`/mywaitlists` — View, accept, decline, or leave your waitlist spots\n"
                "`!setwaitlistpanel #channel` — Post the join/leave panel *(admin)*\n"
                "`!removewaitlistpanel` — Remove the panel *(admin)*\n"
                "`!setwaitlistlog #channel` — Set the seat-filled alert channel *(admin)*"
            ),
            inline=False
        )
        embed.add_field(
            name="⏰ Train Reminder DMs",
            value=(
                "`/trainreminder on [minutes]` — Opt in (default 30 min lead time)\n"
                "`/trainreminder off` — Opt out\n"
                "`/trainreminder status` — Check your current setting"
            ),
            inline=False
        )
        embed.add_field(
            name="📊 Participant Stats",
            value="`/trainstats` — Your stats + server top-10 leaderboard",
            inline=False
        )
        embed.add_field(
            name="⚙️ Admin Controls",
            value=(
                "`/managefeatures` — Enable/disable any beta feature for your server\n"
                "`/viewserverfeatures` — See which features are active"
            ),
            inline=False
        )
        embed.set_footer(text="Beta Program • Enable features with /managefeatures")
        await self.respond(interaction, embed=embed, ephemeral=True)

    @app_commands.command(name='uptime', description='Check bot uptime')
    async def uptime(self, interaction: discord.Interaction):
        """Check bot uptime."""
        if self.bot.start_time:
            uptime = datetime.utcnow() - self.bot.start_time

            days = uptime.days
            hours, remainder = divmod(uptime.seconds, 3600)
            minutes, seconds = divmod(remainder, 60)

            uptime_str = f"{days}d {hours}h {minutes}m {seconds}s"

            embed = discord.Embed(
                title="⏰ Bot Uptime",
                description=f"I've been running for: **{uptime_str}**",
                color=discord.Color.green())
            embed.add_field(
                name="Started at",
                value=self.bot.start_time.strftime("%Y-%m-%d %H:%M:%S UTC"),
                inline=False)
        else:
            embed = discord.Embed(
                title="⏰ Bot Uptime",
                description="Uptime information not available",
                color=discord.Color.red())

        embed.set_footer(
            text=f"Requested by {interaction.user}",
            icon_url=interaction.user.avatar.url if interaction.user.avatar else None)
        await self.respond(interaction, embed=embed)

    @app_commands.command(name='invite', description='Get bot invite link')
    async def invite(self, interaction: discord.Interaction):
        """Generate bot invite link."""
        # Calculate required permissions
        permissions = discord.Permissions(read_messages=True,
                                          send_messages=True,
                                          embed_links=True,
                                          attach_files=True,
                                          read_message_history=True,
                                          add_reactions=True,
                                          use_external_emojis=True,
                                          connect=True,
                                          speak=True,
                                          manage_messages=True)

        invite_url = discord.utils.oauth_url(self.bot.user.id,
                                             permissions=permissions,
                                             scopes=('bot',
                                                     'applications.commands'))

        embed = discord.Embed(
            title="🔗 Invite Me to Your Server!",
            description=
            f"Click [here]({invite_url}) to invite me to your server!",
            color=discord.Color.blurple())
        embed.add_field(
            name="📋 What I can do:",
            value=
            "• Respond to commands\n• Send embedded messages\n• Manage messages\n• And much more!",
            inline=False)
        embed.set_footer(text="Thank you for using our bot!")

        await self.respond(interaction, embed=embed)

    @app_commands.command(name='status', description='Check message forwarding status')
    async def forwarding_status(self, interaction: discord.Interaction):
        """Check the current message forwarding status."""
        status_emoji = "✅" if self.bot.bot_active else "⛔"
        status_text = "Active" if self.bot.bot_active else "Inactive"

        embed = discord.Embed(title="📡 Message Forwarding Status",
                              color=discord.Color.green()
                              if self.bot.bot_active else discord.Color.red())

        # Legacy forwarding status
        embed.add_field(name="🔧 Legacy Forwarding",
                        value=f"{status_emoji} {status_text}",
                        inline=True)

        # Check database forwarding configurations
        active_configs = 0
        try:
            from database import DatabaseSession
            from models import ForwardingConfig
            
            with DatabaseSession() as session:
                configs = session.query(ForwardingConfig).filter(
                    ForwardingConfig.is_active == True
                ).all()
                active_configs = len(configs)
        except Exception as e:
            self.logger.error(f"Error checking forwarding configs: {e}")

        embed.add_field(name="🌐 Database Configs",
                        value=f"{active_configs} active",
                        inline=True)

        # Show legacy channel info only if active
        if self.bot.bot_active:
            source_channel = self.bot.get_channel(self.bot.source_channel_id)
            target_channel = self.bot.get_channel(self.bot.target_channel_id)

            source_name = source_channel.name if source_channel else f"ID: {self.bot.source_channel_id}"
            target_name = target_channel.name if target_channel else f"ID: {self.bot.target_channel_id}"

            embed.add_field(name="📤 Legacy Source",
                            value=f"#{source_name}",
                            inline=True)

            embed.add_field(name="📥 Legacy Target", 
                            value=f"#{target_name}",
                            inline=True)

        embed.add_field(name="🛡️ Authorized Role",
                        value=self.bot.authorized_role_name,
                        inline=True)

        embed.add_field(
            name="📋 Available Commands",
            value="`!start` / `!stop` - Control legacy forwarding\n`!forwards` - List database configs\n`!addforward` - Add new config",
            inline=False)

        embed.set_footer(
            text=f"Requested by {ctx.author}",
            icon_url=ctx.author.avatar.url if ctx.author.avatar else None)

        await ctx.send(embed=embed)

# All slash commands removed to fix double messaging issue

# Slash command removed

# Multiple slash commands removed

    @app_commands.command(name='servers', description='List all servers the bot is in with invite links')
    @owner_only()
    async def servers(self, interaction: discord.Interaction):
        """List all servers the bot is in with invite links."""
        if len(self.bot.guilds) == 0:
            embed = discord.Embed(
                title="📭 No Servers",
                description="I'm not currently in any servers.",
                color=discord.Color.orange()
            )
            await self.respond(interaction, embed=embed)
            return

        embed = discord.Embed(
            title="🌐 Server List",
            description=f"I'm currently in **{len(self.bot.guilds)}** server{'s' if len(self.bot.guilds) != 1 else ''}:",
            color=discord.Color.blue()
        )

        # Sort servers by member count (descending)
        sorted_guilds = sorted(self.bot.guilds, key=lambda g: g.member_count, reverse=True)
        
        for i, guild in enumerate(sorted_guilds[:10], 1):  # Show max 10 servers
            # Try to get a valid invite
            invite_link = "No invite available"
            try:
                # Try to find an existing invite first
                invites = await guild.invites()
                if invites:
                    # Use the first permanent invite or any invite
                    for inv in invites:
                        if inv.max_age == 0:  # Permanent invite
                            invite_link = f"[Join Server]({inv.url})"
                            break
                    else:
                        # If no permanent invite, use the first one
                        invite_link = f"[Join Server]({invites[0].url})"
                else:
                    # Create a new invite if possible
                    text_channel = None
                    for channel in guild.text_channels:
                        if channel.permissions_for(guild.me).create_instant_invite:
                            text_channel = channel
                            break
                    
                    if text_channel:
                        invite = await text_channel.create_invite(
                            max_age=0,  # Never expires
                            max_uses=0,  # Unlimited uses
                            reason="Server list command"
                        )
                        invite_link = f"[Join Server]({invite.url})"
            except discord.Forbidden:
                invite_link = "No permission to create invites"
            except discord.HTTPException:
                invite_link = "Failed to create invite"
            except Exception:
                invite_link = "Invite unavailable"

            # Get owner info
            owner_name = "Unknown"
            if guild.owner:
                owner_name = guild.owner.display_name

            server_info = (
                f"**Members:** {guild.member_count:,}\n"
                f"**Owner:** {owner_name}\n"
                f"**Created:** {guild.created_at.strftime('%Y-%m-%d')}\n"
                f"**Invite:** {invite_link}"
            )

            embed.add_field(
                name=f"#{i} {guild.name}",
                value=server_info,
                inline=True
            )

        if len(self.bot.guilds) > 10:
            embed.set_footer(text=f"Showing 10 of {len(self.bot.guilds)} servers")
        
        embed.set_footer(
            text=f"Requested by {interaction.user} • Total: {len(self.bot.guilds)} servers",
            icon_url=interaction.user.avatar.url if interaction.user.avatar else None
        )

        await self.respond(interaction, embed=embed)

    # Slash command removed

    @app_commands.command(name='updates', description='Show recent bot updates and current system status')
    async def updates_command(self, interaction: discord.Interaction):
        """Show recent bot updates and current system status."""
        try:
            from datetime import datetime, timedelta
            from database import DatabaseSession
            from models import TrainSchedule, ForwardingConfig, BotUpdate, get_est_time
            from sqlalchemy import desc, or_
            
            # Get current date for context
            today = datetime.now().strftime("%B %d, %Y")
            server_name = interaction.guild.name if interaction.guild else "Discord Bot"
            
            # Get recent updates directly from database with fresh session
            recent_updates = []
            cutoff_date = get_est_time() - timedelta(days=7)
            
            with DatabaseSession() as session:
                guild_id = interaction.guild.id if interaction.guild else None
                query = session.query(BotUpdate).filter(
                    BotUpdate.created_at >= cutoff_date,
                    BotUpdate.is_published == True
                )
                
                if guild_id is not None:
                    query = query.filter(
                        or_(BotUpdate.guild_id == guild_id, BotUpdate.guild_id.is_(None))
                    )
                
                updates = query.order_by(desc(BotUpdate.created_at)).all()
                
                # Convert to simple dictionaries to avoid session issues
                for update in updates:
                    recent_updates.append({
                        'id': update.id,
                        'title': update.title,
                        'description': update.description,
                        'category': update.category,
                        'created_at': update.created_at,
                        'version': update.version,
                        'author_id': update.author_id
                    })
            
            # Get system statistics
            with DatabaseSession() as session:
                active_schedules = session.query(TrainSchedule).filter_by(is_active=True).count()
                forwarding_configs = session.query(ForwardingConfig).filter_by(is_active=True).count()
            
            if recent_updates:
                # Create embed directly with our updates
                embed = discord.Embed(
                    title=f"🚂 {server_name} Bot Updates - Recent Week",
                    description=f"Latest updates and improvements (as of {today})",
                    color=0x667eea,
                    timestamp=datetime.utcnow()
                )
                
                # Category emojis
                category_emojis = {
                    'feature': '✨',
                    'bugfix': '🐛',
                    'enhancement': '⚡',
                    'performance': '🚀',
                    'security': '🔒',
                    'ui': '🎨'
                }
                
                # Add recent updates (show full descriptions up to Discord's 1024 char limit)
                for update in recent_updates[:5]:  # Show first 5 updates
                    emoji = category_emojis.get(update['category'], '📝')
                    created_date = update['created_at'].strftime('%b %d')
                    
                    # Discord field value limit is 1024 characters
                    description = update['description']
                    if len(description) > 1000:
                        description = description[:1000] + '...'
                    
                    embed.add_field(
                        name=f"{emoji} {update['title']}",
                        value=f"{description}\n*{created_date}*",
                        inline=False
                    )
                
                if len(recent_updates) > 5:
                    embed.add_field(
                        name="📋 More Updates",
                        value=f"+ {len(recent_updates) - 5} more updates from the past week",
                        inline=False
                    )
            
            else:
                # No updates found - create a fallback embed
                embed = discord.Embed(
                    title=f"🚂 {server_name} Bot Updates",
                    description=f"Bot is **up and running!** Here's what's new (as of {today})\n\n**🔔 v3.5 — Attendance Reliability**",
                    color=0x667eea
                )

                embed.add_field(
                    name="🆕 Latest Updates (June 2026)",
                    value=(
                        "• **📊 Every Rider Now Tracked** - Attendance reports used to only cover the schedule host's leg, so most riders showed nothing. The bot now tracks attendance for every rider across the whole train\n"
                        "• **📨 Day-After Summary Now Sends** - The end-of-train attendance summary had no scheduler running it, so it never went out. It now generates and delivers automatically the day after each train\n"
                        "• **📊 Attendance Survives Restarts** - Chat attendance reports merge live in-memory data with the database, so a bot restart mid-session no longer wipes collected attendance\n"
                        "• **🔴 Go-Live Reminder** - Bot checks if the scheduled rider is live 15 minutes before their slot starts and pings them in #pings if not detected\n"
                        "• **🛡️ Crash Stability Fix** - Resolved a self-destruct bug where the health monitor killed the bot during slow scheduler ticks"
                    ),
                    inline=False
                )

            
            # Enhanced current system status
            uptime = getattr(self.bot, 'start_time', None)
            uptime_str = ""
            if uptime:
                from datetime import datetime
                delta = datetime.now() - uptime
                days = delta.days
                hours = delta.seconds // 3600
                uptime_str = f"\n• **Uptime:** {days}d {hours}h"
            
            status_info = (
                f"• **Servers:** {len(self.bot.guilds)} Discord servers connected\n"
                f"• **Commands:** {len([cmd for cmd in self.bot.tree.get_commands()])} slash commands available\n"
                f"• **Train Schedules:** {active_schedules} active training schedules\n"
                f"• **Message Forwarding:** {forwarding_configs} active forwarding rules\n"
                f"• **Notification System:** ✅ Running reliably{uptime_str}"
            )
            embed.add_field(
                name="📈 Current System Status",
                value=status_info,
                inline=False
            )
            
            # Add feature highlights
            features_info = (
                "• **Train Management** - Schedule and manage train sessions with automatic notifications\n"
                "• **TwitchCord Integration** - Attendance tracking, chat bot, and raid coordination\n"
                "• **Auto-Shoutouts** - Automatically welcomes new chatters with customizable messages (`!autoso`, `!sosetmessage`)\n"
                "• **Multi-Platform Streaming** - YouTube, Kick, and Twitch support with live notifications\n"
                "• **AFK Status** - Auto-reply system with custom messages and cooldown protection\n"
                "• **Spam Protection** - Suspicious join detection with configurable alerts\n"
                "• **Message Forwarding** - Cross-server forwarding with @everyone support\n"
                "• **💡 Suggestion Box** - `/suggest` to submit ideas directly to the developer\n"
                "• **Web Dashboard** - Live UI for monitoring and management (`!dash`)\n"
                "• **Privacy Tools** - GDPR-compliant data viewing and deletion"
            )
            embed.add_field(
                name="🚀 Key Features",
                value=features_info,
                inline=False
            )
            
            # Update footer to match existing style
            embed.set_footer(text="Use !dash to access the web interface • Use /help for command list")
            
            await self.respond(interaction, embed=embed)
            
        except Exception as e:
            self.logger.error(f"Error in updates command: {e}")
            
            # Fallback error embed
            embed = discord.Embed(
                title="🚂 Game Lounge Train Updates",
                description="Unable to fetch recent updates from database. The bot is still running normally.",
                color=0xff6b6b
            )
            
            embed.add_field(
                name="📊 System Status",
                value=f"• Bot is online and connected to {len(self.bot.guilds)} servers\n• All core features remain active\n• Try again later or use !dashboard for status",
                inline=False
            )
            
            embed.set_footer(text="Error retrieving updates • Bot functionality remains unaffected")
            await self.respond(interaction, embed=embed, ephemeral=True)

    # Slash command removed

    @commands.command(name='donate', help='Support bot hosting costs')
    async def donate(self, ctx: commands.Context):
        """Support bot hosting costs."""
        embed = discord.Embed(
            title="💝 Support Train Bot",
            description="Help keep the bot running 24/7!",
            color=0x28a745
        )
        
        embed.add_field(
            name="💰 Monthly Costs",
            value="• **$40/month** for Replit Reserved VM\n• Your donations help keep the bot free for everyone!",
            inline=False
        )
        
        embed.add_field(
            name="🔗 Donate via PayPal",
            value="**PayPal.me/ReganLStone**\n\nhttps://paypal.me/ReganLStone\n\n💳 Secure PayPal checkout • 🔒 Safe & Easy",
            inline=False
        )
        
        embed.add_field(
            name="🎯 Suggested Donations",
            value="• $3 - Coffee fund ☕\n• $5 - Popular choice 👍\n• $10 - Generous support 🎉\n• $15 - Cover one month! 📅\n• Custom amount available",
            inline=False
        )
        
        embed.set_footer(text="Thank you for supporting Train Bot! 💙")
        
        await ctx.send(embed=embed)

    # Slash command removed

# Slash command help removed - using only the interactive dropdown version

    # ── Support: topic scoring ─────────────────────────────────────────────

    def _score_topics(self, issue_text: str) -> list[tuple[str, int]]:
        """Return topics sorted by weighted keyword score (highest first)."""
        text = issue_text.lower()
        scores = {}
        for topic, data in self.KNOWLEDGE_BASE.items():
            score = 0
            for phrase, weight in data['keywords']:
                if phrase in text:
                    score += weight
            if score > 0:
                scores[topic] = score
        return sorted(scores.items(), key=lambda x: x[1], reverse=True)

    # ── Support: live diagnostics ──────────────────────────────────────────

    async def _diag_twitch_oauth(self, guild_id, user_id) -> dict:
        """Check OAuth token status for users in this guild."""
        result = {'found': False, 'lines': [], 'action_needed': False}
        try:
            from database import DatabaseSession
            from models import TwitchOAuthToken, TwitchConnection, GuildMember
            import pytz
            uk = pytz.timezone('Europe/London')
            now_utc = datetime.utcnow()
            with DatabaseSession() as db:
                # Find linked users in this guild
                connections = db.query(TwitchConnection).all()
                checked = 0
                for conn in connections:
                    token = (
                        db.query(TwitchOAuthToken)
                        .filter_by(twitch_user_id=conn.twitch_user_id, is_active=True)
                        .order_by(TwitchOAuthToken.expires_at.desc())
                        .first()
                    )
                    checked += 1
                    if token is None:
                        result['lines'].append(f"• **{conn.twitch_login or conn.twitch_user_id}** — ❌ No active token found")
                        result['action_needed'] = True
                    else:
                        expires = token.expires_at
                        if expires and expires < now_utc:
                            delta = now_utc - expires
                            result['lines'].append(
                                f"• **{conn.twitch_login or conn.twitch_user_id}** — ❌ Token expired "
                                f"{int(delta.total_seconds()//60)} min ago"
                            )
                            result['action_needed'] = True
                        elif expires:
                            delta = expires - now_utc
                            hrs = int(delta.total_seconds() // 3600)
                            mins = int((delta.total_seconds() % 3600) // 60)
                            result['lines'].append(
                                f"• **{conn.twitch_login or conn.twitch_user_id}** — ✅ Token valid, "
                                f"expires in {hrs}h {mins}m"
                            )
                        else:
                            result['lines'].append(
                                f"• **{conn.twitch_login or conn.twitch_user_id}** — ✅ Token active (no expiry date stored)"
                            )
                if checked == 0:
                    result['lines'].append("• No Twitch connections found in database")
                    result['action_needed'] = True
                result['found'] = True
        except Exception as e:
            result['lines'].append(f"• Could not query token status: {e}")
        return result

    async def _diag_twitch_link(self, guild_id, user_id) -> dict:
        """Check if the requesting user has a Twitch connection."""
        result = {'found': False, 'lines': [], 'action_needed': False}
        try:
            from database import DatabaseSession
            from models import TwitchConnection
            with DatabaseSession() as db:
                conn = db.query(TwitchConnection).filter_by(discord_user_id=str(user_id)).first()
                if conn:
                    result['lines'].append(
                        f"• Your Discord account is linked to **{conn.twitch_login or conn.twitch_user_id}** "
                        f"(linked via {conn.link_method or 'admin'})"
                    )
                    result['found'] = True
                else:
                    result['lines'].append("• Your Discord account is **not linked** to any Twitch account")
                    result['action_needed'] = True
                    result['found'] = True
        except Exception as e:
            result['lines'].append(f"• Could not query Twitch connections: {e}")
        return result

    async def _diag_attendance(self, guild_id, user_id) -> dict:
        """Check attendance session status and channel config."""
        result = {'found': False, 'lines': [], 'action_needed': False}
        try:
            from database import DatabaseSession
            from models import GuildConfig
            with DatabaseSession() as db:
                config = db.query(GuildConfig).filter_by(guild_id=str(guild_id)).first()
                if config:
                    att_ch = config.attendance_channel_id
                    notif_ch = config.raid_notification_channel_id
                    result['lines'].append(
                        f"• Attendance channel: {'<#' + att_ch + '>' if att_ch else '❌ Not configured'}"
                    )
                    result['lines'].append(
                        f"• Notification channel: {'<#' + notif_ch + '>' if notif_ch else '❌ Not configured'}"
                    )
                    if not att_ch:
                        result['action_needed'] = True
                else:
                    result['lines'].append("• No guild config found — run `/setupattendance` to configure")
                    result['action_needed'] = True
            # Check active monitor sessions
            monitor = getattr(self.bot, 'twitch_chat_monitor', None)
            if monitor and hasattr(monitor, 'active_train_monitors'):
                sessions = monitor.active_train_monitors
                if sessions:
                    result['lines'].append(f"• Active tracking sessions: {len(sessions)} running")
                    for sid, data in list(sessions.items())[:3]:
                        ch = data.get('channel', 'unknown')
                        result['lines'].append(f"  – Session #{sid}: monitoring #{ch}")
                else:
                    result['lines'].append("• No active attendance sessions running right now")
            result['found'] = True
        except Exception as e:
            result['lines'].append(f"• Could not check attendance status: {e}")
        return result

    async def _diag_schedule(self, guild_id, user_id) -> dict:
        """Check upcoming schedules for this guild."""
        result = {'found': False, 'lines': [], 'action_needed': False}
        try:
            from database import DatabaseSession
            from models import TrainSchedule
            import pytz
            uk = pytz.timezone('Europe/London')
            now_uk = datetime.now(uk)
            with DatabaseSession() as db:
                schedules = (
                    db.query(TrainSchedule)
                    .filter_by(guild_id=str(guild_id), is_active=True)
                    .limit(5).all()
                )
                if schedules:
                    result['lines'].append(f"• Found **{len(schedules)}** active schedule(s):")
                    for s in schedules:
                        result['lines'].append(f"  – **{s.name}** at {s.start_time} UK time")
                else:
                    result['lines'].append("• No active schedules found for this server")
                    result['action_needed'] = True
            result['found'] = True
        except Exception as e:
            result['lines'].append(f"• Could not query schedules: {e}")
        return result

    async def _diag_live_role(self, guild_id, user_id) -> dict:
        """Check live role configuration."""
        result = {'found': False, 'lines': [], 'action_needed': False}
        try:
            from database import DatabaseSession
            from models import GuildConfig
            with DatabaseSession() as db:
                config = db.query(GuildConfig).filter_by(guild_id=str(guild_id)).first()
                if config and hasattr(config, 'live_role_id') and config.live_role_id:
                    guild = self.bot.get_guild(guild_id)
                    role = guild.get_role(int(config.live_role_id)) if guild else None
                    result['lines'].append(
                        f"• Live role configured: **{role.name if role else config.live_role_id}**"
                    )
                    if guild and role:
                        bot_member = guild.get_member(self.bot.user.id)
                        if bot_member and bot_member.top_role <= role:
                            result['lines'].append(
                                "• ⚠️ Bot's highest role is **below or equal** to the live role — "
                                "it cannot assign it. Move the bot's role higher in Server Settings → Roles."
                            )
                            result['action_needed'] = True
                        else:
                            result['lines'].append("• Bot role rank: ✅ above live role")
                else:
                    result['lines'].append("• No live role configured for this server")
                    result['action_needed'] = True
            result['found'] = True
        except Exception as e:
            result['lines'].append(f"• Could not check live role config: {e}")
        return result

    async def _diag_chat_monitor(self, guild_id, user_id) -> dict:
        """Check Twitch IRC chat monitor status."""
        result = {'found': False, 'lines': [], 'action_needed': False}
        try:
            monitor = getattr(self.bot, 'twitch_chat_monitor', None)
            if monitor is None:
                result['lines'].append("• ❌ Chat monitor module not loaded")
                result['action_needed'] = True
            else:
                connected = getattr(monitor, 'is_connected', False)
                result['lines'].append(f"• IRC connection: {'✅ Connected' if connected else '❌ Disconnected'}")
                joined = getattr(monitor, 'joined_channels', set())
                if joined:
                    result['lines'].append(f"• Channels currently joined ({len(joined)}): {', '.join(['#' + c for c in list(joined)[:8]])}")
                else:
                    result['lines'].append("• No channels currently joined via IRC")
                    result['action_needed'] = True
                if not connected:
                    result['action_needed'] = True
            result['found'] = True
        except Exception as e:
            result['lines'].append(f"• Could not check chat monitor: {e}")
        return result

    # ── Support: auto-fix attempts ─────────────────────────────────────────

    async def _autofix_token_refresh(self, guild_id, user_id) -> dict:
        """Try to refresh expired OAuth tokens."""
        result = {'attempted': True, 'lines': [], 'success': False}
        try:
            from database import DatabaseSession
            from models import TwitchOAuthToken, TwitchConnection
            import aiohttp
            client_id = os.getenv('TWITCH_CLIENT_ID')
            client_secret = os.getenv('TWITCH_CLIENT_SECRET')
            if not client_id or not client_secret:
                result['lines'].append("• Cannot attempt auto-refresh: Twitch credentials not configured")
                return result
            with DatabaseSession() as db:
                connections = db.query(TwitchConnection).all()
                refreshed = 0
                failed = 0
                for conn in connections:
                    token = (
                        db.query(TwitchOAuthToken)
                        .filter_by(twitch_user_id=conn.twitch_user_id, is_active=True)
                        .order_by(TwitchOAuthToken.expires_at.desc())
                        .first()
                    )
                    if token and token.refresh_token:
                        try:
                            async with aiohttp.ClientSession() as session:
                                async with session.post(
                                    'https://id.twitch.tv/oauth2/token',
                                    data={
                                        'grant_type': 'refresh_token',
                                        'refresh_token': token.refresh_token,
                                        'client_id': client_id,
                                        'client_secret': client_secret,
                                    }
                                ) as resp:
                                    if resp.status == 200:
                                        data = await resp.json()
                                        token.access_token = data['access_token']
                                        token.refresh_token = data.get('refresh_token', token.refresh_token)
                                        expires_in = data.get('expires_in', 14400)
                                        token.expires_at = datetime.utcnow() + __import__('datetime').timedelta(seconds=expires_in)
                                        db.commit()
                                        refreshed += 1
                                    else:
                                        failed += 1
                        except Exception:
                            failed += 1
                if refreshed > 0:
                    result['lines'].append(f"• ✅ Auto-refreshed {refreshed} token(s) successfully")
                    result['success'] = True
                if failed > 0:
                    result['lines'].append(
                        f"• ❌ Failed to refresh {failed} token(s) — these need manual `/twitchoauth`"
                    )
                if refreshed == 0 and failed == 0:
                    result['lines'].append("• No refresh tokens available to attempt auto-refresh")
        except Exception as e:
            result['lines'].append(f"• Auto-refresh error: {e}")
        return result

    async def _autofix_rejoin_irc(self, guild_id, user_id) -> dict:
        """Try to reconnect bot to Twitch IRC and rejoin channels."""
        result = {'attempted': True, 'lines': [], 'success': False}
        try:
            monitor = getattr(self.bot, 'twitch_chat_monitor', None)
            if monitor is None:
                result['lines'].append("• Chat monitor module not available")
                return result
            connected = getattr(monitor, 'is_connected', False)
            if connected:
                result['lines'].append("• IRC already connected — no reconnect needed")
                result['success'] = True
            else:
                result['lines'].append("• Attempted IRC reconnect (will complete in background)")
                # Trigger reconnect task if method exists
                if hasattr(monitor, 'connect_to_twitch_irc'):
                    asyncio.create_task(monitor.connect_to_twitch_irc())
                result['success'] = True
        except Exception as e:
            result['lines'].append(f"• IRC reconnect error: {e}")
        return result

    # ── Support: main smart analysis engine ───────────────────────────────

    async def _smart_analyze(self, issue_text: str, guild, user, channel):
        """Score topics, run live diagnostics, attempt fixes, send a rich embed response."""
        issue_lower = issue_text.lower()
        scored = self._score_topics(issue_lower)
        top_topics = scored[:3] if scored else []

        # Build the response embed
        if not top_topics:
            # Nothing matched — fallback to escalation
            embed = discord.Embed(
                title="🤔 Couldn't Match Your Issue",
                description=(
                    f"I couldn't find a known solution for: *\"{issue_text[:120]}\"*\n\n"
                    "I've flagged this for the bot owner to review."
                ),
                color=0xFFA500
            )
            embed.add_field(
                name="💡 Things to try first",
                value=(
                    "• Run `/ping` to confirm the bot is responding\n"
                    "• Run `/help` to browse all available commands\n"
                    "• Run `!checktwitch @YourName` for Twitch connection issues\n"
                    "• Run `/oauthstatus` to check Twitch token health"
                ),
                inline=False
            )
            await channel.send(embed=embed)
            await self.send_escalation_message(user, issue_text, guild)
            return

        # Use the top-scoring topic as the primary
        primary_topic_key, primary_score = top_topics[0]
        primary = self.KNOWLEDGE_BASE[primary_topic_key]

        embed = discord.Embed(
            title=primary['title'],
            description=(
                f"> *\"{issue_text[:150]}\"*\n\n"
                f"{primary['what_it_means']}"
            ),
            color=primary['color'],
            timestamp=datetime.utcnow()
        )

        # ── Live diagnostics ──
        diag_func_name = primary.get('diagnostic')
        diag_result = None
        if diag_func_name and guild:
            diag_func = getattr(self, diag_func_name, None)
            if diag_func:
                try:
                    diag_result = await diag_func(guild.id if guild else 0, user.id)
                except Exception as e:
                    diag_result = {'found': False, 'lines': [f'Diagnostic error: {e}'], 'action_needed': False}

        if diag_result and diag_result.get('lines'):
            action_icon = "⚠️" if diag_result.get('action_needed') else "✅"
            embed.add_field(
                name=f"{action_icon} Live Status Check",
                value="\n".join(diag_result['lines'][:8]),
                inline=False
            )

        # ── Auto-fix attempt ──
        autofix_func_name = primary.get('autofix')
        autofix_result = None
        if autofix_func_name and (diag_result and diag_result.get('action_needed')):
            autofix_func = getattr(self, autofix_func_name, None)
            if autofix_func:
                try:
                    autofix_result = await autofix_func(guild.id if guild else 0, user.id)
                except Exception as e:
                    autofix_result = {'attempted': True, 'lines': [f'Fix attempt error: {e}'], 'success': False}

        if autofix_result and autofix_result.get('attempted'):
            fix_icon = "✅" if autofix_result.get('success') else "⚠️"
            embed.add_field(
                name=f"{fix_icon} Auto-Fix Attempted",
                value="\n".join(autofix_result['lines'][:5]),
                inline=False
            )

        # ── Step-by-step solutions ──
        solutions = primary['solutions']
        embed.add_field(
            name="🔧 Step-by-Step Fix",
            value="\n".join(solutions),
            inline=False
        )

        # ── Relevant commands ──
        embed.add_field(
            name="📋 Relevant Commands",
            value=primary['commands'],
            inline=False
        )

        # ── Secondary topics (if any scored) ──
        if len(top_topics) > 1:
            secondary_names = []
            for t_key, _ in top_topics[1:]:
                secondary_names.append(self.KNOWLEDGE_BASE[t_key]['title'])
            embed.add_field(
                name="🔍 Also possibly related",
                value="\n".join(secondary_names),
                inline=False
            )

        # ── Footer ──
        still_broken_msg = "Still not working? Reply **no** and I'll escalate to the bot owner."
        embed.set_footer(text=still_broken_msg)

        await channel.send(embed=embed)

        # Ask if resolved
        def check(m):
            return m.author == user and m.channel == channel

        try:
            feedback = await self.bot.wait_for('message', check=check, timeout=180.0)
            fb = feedback.content.lower().strip()
            positive = {'yes', 'y', 'solved', 'fixed', 'worked', 'good', 'thanks', 'thank you', 'great', 'perfect'}
            negative = {'no', 'n', 'nope', 'still', 'not working', "didn't", 'broken', 'not fixed', 'nothing', 'nah'}
            if any(p in fb for p in positive):
                done = discord.Embed(
                    title="✅ Glad that helped!",
                    description="Feel free to run `/support` any time you need help.",
                    color=0x28a745
                )
                await channel.send(embed=done)
            elif any(n in fb for n in negative):
                esc = discord.Embed(
                    title="📞 Escalating to Bot Owner",
                    description=(
                        "I've sent your issue details to the bot owner. "
                        "They'll follow up as soon as possible."
                    ),
                    color=0xFFA500
                )
                await channel.send(embed=esc)
                await self.send_escalation_message(
                    user,
                    f"Issue: {issue_text}\n\nDiag: {diag_result}\n\nFix attempted: {autofix_result}",
                    guild
                )
            else:
                # They said something else — treat as additional context, escalate
                await self.send_escalation_message(user, f"Issue: {issue_text}\nAdditional: {feedback.content}", guild)
        except asyncio.TimeoutError:
            pass  # No reply — that's fine, no action needed

    def analyze_issue(self, issue_text):
        """Legacy wrapper — returns solutions from the new knowledge base."""
        issue_lower = issue_text.lower()
        scored = self._score_topics(issue_lower)
        if not scored:
            return None
        solutions = []
        for topic_key, _ in scored[:2]:
            solutions.extend(self.KNOWLEDGE_BASE[topic_key]['solutions'])
        return solutions if solutions else None

    async def send_escalation_message(self, user, issue, guild):
        """Send escalation message to you and trusted users."""
        try:
            # Import database dependencies
            from database import DatabaseSession
            from models import TrustedUser
            
            # Create the escalation embed
            embed = discord.Embed(
                title="🚨 Support Escalation Required",
                description="A user needs additional help after automated support solutions.",
                color=0xff4444
            )
            
            embed.add_field(
                name="👤 User Information",
                value=f"**Username:** {user.name}#{user.discriminator}\n**User ID:** {user.id}\n**Mention:** {user.mention}",
                inline=False
            )
            
            embed.add_field(
                name="🌐 Server Information", 
                value=f"**Server Name:** {guild.name if guild else 'Direct Message'}\n**Server ID:** {guild.id if guild else 'N/A'}",
                inline=False
            )
            
            embed.add_field(
                name="❓ Reported Issue",
                value=f"```{issue[:1000]}```",  # Limit to 1000 chars
                inline=False
            )
            
            embed.add_field(
                name="⏰ Timestamp",
                value=f"<t:{int(datetime.utcnow().timestamp())}:F>",
                inline=False
            )
            
            embed.set_footer(text="Support system escalation • Please assist when possible")
            
            # List of users to send escalation to
            target_users = [int(os.getenv('OWNER_ID_DISCORD', '887354716751810560'))]  # Bot owner ID
            
            # Get trusted users from database
            try:
                with DatabaseSession() as session:
                    trusted_users = session.query(TrustedUser).filter(
                        TrustedUser.is_active == True
                    ).all()
                    
                    for trusted_user in trusted_users:
                        target_users.append(int(trusted_user.user_id))
                        
            except Exception as db_error:
                self.logger.error(f"Failed to fetch trusted users: {db_error}")
            
            # Send DM to each target user
            sent_count = 0
            for user_id in target_users:
                try:
                    target_user = await self.bot.fetch_user(user_id)
                    await target_user.send(embed=embed)
                    sent_count += 1
                    self.logger.info(f"Support escalation sent to {target_user} (ID: {user_id})")
                except Exception as send_error:
                    self.logger.warning(f"Failed to send escalation to user {user_id}: {send_error}")
            
            if sent_count > 0:
                guild_name = guild.name if guild else 'DM'
                self.logger.info(f"Support escalation sent to {sent_count} users for {user} (ID: {user.id}) in {guild_name}")
            else:
                self.logger.error("Failed to send escalation to any users")
            
        except Exception as e:
            self.logger.error(f"Failed to send escalation message: {e}")

    @app_commands.command(name='support', description='Describe your problem and I\'ll diagnose it, run live checks, and try to fix it')
    @app_commands.describe(issue='What\'s not working? e.g. "twitch token expired", "attendance not tracking", "live role not assigning"')
    async def support(self, interaction: discord.Interaction, issue: str = None):
        """Smart support command — diagnoses and attempts fixes for known issues."""
        guild_name = interaction.guild.name if interaction.guild else "DM"
        self.logger.info(f"Support command by {interaction.user} (ID: {interaction.user.id}) in {guild_name}: {issue!r}")
        try:
            if issue:
                # Acknowledge immediately so we have time to run diagnostics
                await interaction.response.defer(ephemeral=False)
                await self._smart_analyze(issue, interaction.guild, interaction.user, interaction.channel)
            else:
                await self.run_support_flow(interaction, interaction.user, interaction.channel, interaction.guild, is_slash=True)
        except Exception as e:
            self.logger.error(f"Support command error: {e}", exc_info=True)
            try:
                await self.respond(interaction, "❌ Support system error. Please try again or contact the developer.", ephemeral=True)
            except Exception:
                pass

    # OAuth-based Twitch linking commands removed
    # Use admin consent system instead: !linkusertwitch @user twitch_name

    # Slash command removed
# Slash command function removed

    async def run_support_flow(self, ctx_or_interaction, user, channel, guild, is_slash=False):
        """Main support flow logic."""
        try:
            # Initial support message
            embed = discord.Embed(
                title="🛠️ Train Bot Support",
                description="I'm here to help! Please describe the issue you're experiencing.",
                color=0x667eea
            )
            
            embed.add_field(
                name="💬 How to Respond",
                value="Simply type your issue in the next message. I'll analyze it and provide relevant solutions.",
                inline=False
            )
            
            embed.add_field(
                name="🔍 Common Issues I Can Help With",
                value=(
                    "• Twitch OAuth / token expired\n"
                    "• Twitch account not linked\n"
                    "• Attendance not tracking\n"
                    "• Notifications / schedules\n"
                    "• Live role not assigning\n"
                    "• Twitch chat monitor / IRC\n"
                    "• Raid train issues\n"
                    "• Discord permissions"
                ),
                inline=False
            )

            embed.set_footer(text="Waiting for your response... (90 second timeout)")

            # Always send to channel for consistency
            await channel.send(embed=embed)

            # Wait for user response
            def check(message):
                return message.author == user and message.channel == channel

            try:
                response = await self.bot.wait_for('message', check=check, timeout=90.0)
                issue_text = response.content

                # Route through the smart analysis engine
                await self._smart_analyze(issue_text, guild, user, channel)

                # _smart_analyze handles the full analysis and feedback loop

            except asyncio.TimeoutError:
                timeout_embed = discord.Embed(
                    title="⏰ Support Session Timeout",
                    description="You didn't respond in time. Feel free to run `/support issue:your problem here` again when you're ready!",
                    color=0x6c757d
                )
                await channel.send(embed=timeout_embed)
                
        except Exception as e:
            error_embed = discord.Embed(
                title="❌ Support System Error",
                description="Something went wrong with the support system. The developer has been notified.",
                color=0xff4444
            )
            await channel.send(embed=error_embed)
            self.logger.error(f"Support system error: {e}")
            
            # Try to escalate the error itself
            try:
                await self.send_escalation_message(user, f"Support system error: {str(e)}", guild)
            except:
                pass  # Fail silently if escalation also fails

    # Beta mode command removed - no longer needed
    
    # Slash command removed
# Slash command function removed

    @app_commands.command(name='backups', description='Post backup streamer application with reactions')
    async def backups_command(self, interaction: discord.Interaction):
        """Post a message for backup streamer applications with reaction emojis."""
        from models import BackupApplication
        from database import DatabaseSession
        
        embed = discord.Embed(
            title="🚂 Backup Streamer Applications",
            description="React to this message to apply as a backup streamer for raid trains!\n\n**How it works:**\n• React with 🎮 to apply as a backup streamer\n• Remove your reaction to withdraw your application\n• Backup streamers help keep trains running when someone misses their turn\n• You'll be pinged when needed as a backup",
            color=0x28a745
        )
        
        embed.add_field(
            name="📋 Requirements",
            value="• Must be an active streamer on Twitch\n• Should be available during common raid train hours\n• Willing to receive backup pings when needed\n• Committed to keeping trains moving smoothly",
            inline=False
        )
        
        embed.add_field(
            name="⚡ Benefits",
            value="• Get priority spots in future raid trains\n• Help build the streaming community\n• Network with other streamers\n• Support the raid train ecosystem",
            inline=False
        )
        
        embed.add_field(
            name="🎯 How to Apply",
            value="**React with 🎮 below to apply!**\n\n*Current backup applications will be shown here*",
            inline=False
        )
        
        embed.set_footer(text="React with 🎮 to apply • Remove reaction to withdraw")
        
        # Send the message
        await self.respond(interaction, embed=embed)
        message = await interaction.original_response()
        
        # Add the reaction emoji for users to click
        await message.add_reaction("🎮")
        
        # Log the backup post creation
        self.logger.info(f"Backup application post created by {interaction.user} (ID: {message.id})")
        
        # Store message ID for tracking and configure as backup signup message
        try:
            from models import NotificationSettings
            from database import DatabaseSession
            
            with DatabaseSession() as session:
                # Get or create notification settings for this guild
                settings = session.query(NotificationSettings).filter_by(
                    guild_id=interaction.guild.id
                ).first()
                
                if not settings:
                    settings = NotificationSettings(guild_id=interaction.guild.id)
                    session.add(settings)
                
                # Configure this message as the backup signup message
                settings.backup_signup_message_id = message.id
                settings.backup_signup_emoji = "🎮"
                
                session.commit()
                self.logger.info(f"Configured message {message.id} as backup signup message for guild {interaction.guild.id}")
                
        except Exception as e:
            self.logger.error(f"Failed to configure backup signup message: {e}")

    @commands.command(name='viewbackups', help='View all backup streamer applications')
    async def view_backups_command(self, ctx: commands.Context):
        """View all current backup streamers (users with Backups role)."""
        from models import User as DBUser
        from database import DatabaseSession
        
        # Check if command is run in a guild
        if not ctx.guild:
            await ctx.send("❌ This command can only be used in a server, not in DMs!")
            return
        
        try:
            # Find the "Backups" role
            backup_role = ctx.guild.get_role(1408535262379905097)
            
            if not backup_role:
                embed = discord.Embed(
                    title="❌ Backups Role Not Found",
                    description="The Backups role could not be found in this server.",
                    color=0xff0000
                )
                await ctx.send(embed=embed)
                return
            
            # Get all members with the Backups role
            backup_members = backup_role.members
            
            if not backup_members:
                embed = discord.Embed(
                    title="📋 Backup Streamer Applications",
                    description=f"No users currently have the {backup_role.mention} role.\n\nUsers can react to the backup signup message to get this role!",
                    color=0xffc107
                )
                await ctx.send(embed=embed)
                return
            
            # Check Twitch status for each backup member
            with DatabaseSession() as session:
                backup_users = []
                for member in backup_members:
                    db_user = session.query(DBUser).filter(DBUser.id == member.id).first()
                    twitch_username = db_user.twitch_login if db_user and db_user.twitch_login else None
                    backup_users.append({
                        'user': member,
                        'twitch': twitch_username
                    })
            
            embed = discord.Embed(
                title="📋 Active Backup Streamers",
                description=f"Found {len(backup_users)} backup streamers with the {backup_role.mention} role!",
                color=0x28a745
            )
            
            # Show all backup users
            for backup in backup_users:
                user = backup['user']
                twitch_info = f"\n🎮 Twitch: `{backup['twitch']}`" if backup['twitch'] else "\n⚠️ No Twitch linked"
                
                embed.add_field(
                    name=f"{user.display_name}",
                    value=f"{user.mention}{twitch_info}",
                    inline=True
                )
            
            embed.set_footer(text=f"Total: {len(backup_users)} backup streamers")
            
            await ctx.send(embed=embed)
                
        except Exception as e:
            self.logger.error(f"Failed to fetch backup applications: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            await ctx.send("❌ Failed to fetch backup applications. Please try again.")

    @app_commands.command(name='linktwitch', description='View your linked Twitch account or request linking')
    async def link_twitch(self, interaction: discord.Interaction, twitch_username: str = None):
        """View current Twitch link or request linking if not already linked."""
        await interaction.response.defer(ephemeral=True)
        try:
            from models import User, TwitchLinkRequest
            from database import DatabaseSession
            from datetime import datetime
            
            with DatabaseSession() as session:
                # Check if user already has a Twitch account linked
                existing_user = session.query(User).filter(
                    User.id == interaction.user.id,
                    User.twitch_login.isnot(None)
                ).first()
                
                # If already linked, show current link status — also sync Twitch data
                # to the current guild's user record so go-live alerts work here too
                if existing_user:
                    if interaction.guild:
                        try:
                            guild_user = session.query(User).filter_by(
                                id=interaction.user.id,
                                guild_id=interaction.guild.id
                            ).first()
                            if guild_user and not guild_user.twitch_id:
                                guild_user.twitch_id           = existing_user.twitch_id
                                guild_user.twitch_login        = existing_user.twitch_login
                                guild_user.twitch_display_name = existing_user.twitch_display_name
                                guild_user.twitch_linked_at    = existing_user.twitch_linked_at
                                guild_user.twitch_source       = existing_user.twitch_source
                                guild_user.twitch_consent      = True
                                session.commit()
                        except Exception:
                            pass

                    embed = discord.Embed(
                        title="✅ Twitch Account Already Linked",
                        description="Your Discord account is already linked to a Twitch account!",
                        color=0x00ff00,
                        timestamp=datetime.utcnow()
                    )
                    
                    embed.add_field(
                        name="📋 Current Link",
                        value=f"**Discord:** {interaction.user.mention}\n**Twitch:** `{existing_user.twitch_login}` ({existing_user.twitch_display_name})",
                        inline=False
                    )
                    
                    embed.add_field(
                        name="🎯 Active Features",
                        value=(
                            "✅ **Automatic attendance tracking** during raid trains\n"
                            "✅ **Real-time chat monitoring** - Your presence in Twitch chat is tracked\n"
                            "✅ **Enhanced reports** - Shows your actual participation\n"
                            "✅ **Train notifications** display your Twitch username"
                        ),
                        inline=False
                    )
                    
                    from models import TwitchOAuthToken
                    has_token = session.query(TwitchOAuthToken).filter_by(
                        twitch_username=existing_user.twitch_login,
                        is_active=True
                    ).first()
                    
                    if has_token:
                        embed.add_field(
                            name="🎮 Auto-Raid Status",
                            value="✅ Your Twitch account is authorized for auto-raids!",
                            inline=False
                        )
                    else:
                        embed.add_field(
                            name="🎮 Enable Auto-Raid (Recommended)",
                            value="Run **/twitchoauth** in this server to authorize auto-raids from your channel.",
                            inline=False
                        )
                    embed.add_field(
                        name="🔧 Need to Update?",
                        value="Contact an admin if you need to unlink your Twitch account. To link a different account, unlink first then use `/linktwitch` again.",
                        inline=False
                    )
                    
                    embed.set_footer(text=f"Linked via admin command")
                    await self.respond(interaction, embed=embed, ephemeral=True)
                    return
                
                # Not linked — start device code flow so they can verify ownership
                # and link in one step, no role restriction required.

                # Ensure a User row exists so the token save can update it.
                db_user = session.query(User).filter_by(
                    id=interaction.user.id, guild_id=interaction.guild.id
                ).first()
                if not db_user:
                    db_user = session.query(User).filter_by(id=interaction.user.id).first()
                if not db_user:
                    db_user = User(
                        id=interaction.user.id,
                        username=interaction.user.name,
                        display_name=interaction.user.display_name,
                        guild_id=interaction.guild.id,
                        first_seen=datetime.utcnow(),
                        last_seen=datetime.utcnow()
                    )
                    session.add(db_user)
                session.commit()

            # --- start device code flow (outside the DB session) ---
            client_id = os.getenv('TWITCH_CLIENT_ID')
            client_secret = os.getenv('TWITCH_CLIENT_SECRET')
            if not client_id or not client_secret:
                await self.respond(
                    interaction,
                    "❌ Twitch credentials are not configured. Contact the bot owner.",
                    ephemeral=True
                )
                return

            def _start_device():
                return requests.post(
                    "https://id.twitch.tv/oauth2/device",
                    data={
                        'client_id': client_id,
                        'scopes': (
                            "chat:read chat:edit user:read:email "
                            "moderator:read:chatters user:write:chat channel:manage:raids"
                        ),
                    },
                    timeout=10
                )

            resp = await asyncio.to_thread(_start_device)
            if resp.status_code != 200:
                await self.respond(
                    interaction,
                    f"❌ Failed to start Twitch authorization (HTTP {resp.status_code}). Try again later.",
                    ephemeral=True
                )
                return

            dev_data = resp.json()
            device_code      = dev_data['device_code']
            user_code        = dev_data['user_code']
            verification_uri = dev_data.get('verification_uri', 'https://www.twitch.tv/activate')
            expires_in       = dev_data.get('expires_in', 1800)
            interval         = dev_data.get('interval', 5)

            embed = discord.Embed(
                title="🔗 Link Your Twitch Account",
                description="Follow these two steps to link your Twitch account to the bot.",
                color=0x9146ff,
                timestamp=datetime.utcnow()
            )
            embed.add_field(
                name="Step 1 — Open Twitch",
                value=f"Go to **[twitch.tv/activate]({verification_uri})**",
                inline=False
            )
            embed.add_field(
                name="Step 2 — Enter Your Code",
                value=f"```\n{user_code}\n```",
                inline=False
            )
            embed.add_field(
                name="⏰ Code Expires In",
                value=f"{expires_in // 60} minutes",
                inline=False
            )
            embed.set_footer(text="The bot will confirm automatically once you've authorized.")
            await self.respond(interaction, embed=embed, ephemeral=True)

            asyncio.create_task(self._poll_linktwitch_device_auth(
                interaction=interaction,
                device_code=device_code,
                interval=interval,
                expires_in=expires_in,
                client_id=client_id,
                client_secret=client_secret,
                discord_user_id=str(interaction.user.id),
                guild_id=str(interaction.guild.id),
            ))
            return

        except Exception as e:
            self.logger.error(f"Error in link_twitch command: {e}", exc_info=True)
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(
                        "❌ **Error Processing Request**\n"
                        f"An error occurred while processing your linking request: {str(e)}",
                        ephemeral=True
                    )
                else:
                    await interaction.response.send_message(
                        "❌ **Error Processing Request**\n"
                        f"An error occurred while processing your linking request: {str(e)}",
                        ephemeral=True
                    )
            except Exception:
                pass

    async def _poll_linktwitch_device_auth(
        self, interaction, device_code, interval, expires_in,
        client_id, client_secret, discord_user_id, guild_id
    ):
        """Poll Twitch until the user completes the device code flow or it expires."""
        deadline = time.time() + expires_in
        while time.time() < deadline:
            await asyncio.sleep(interval)

            def _poll():
                return requests.post(
                    "https://id.twitch.tv/oauth2/token",
                    data={
                        'client_id': client_id,
                        'client_secret': client_secret,
                        'device_code': device_code,
                        'grant_type': 'urn:ietf:params:oauth:grant-type:device_code',
                    },
                    timeout=10
                )

            try:
                resp = await asyncio.to_thread(_poll)
            except Exception as e:
                self.logger.warning(f"linktwitch device poll error: {e}")
                continue

            token_data = resp.json()
            if resp.status_code == 200 and 'access_token' in token_data:
                await self._handle_linktwitch_token_success(
                    token_data, client_id, discord_user_id, guild_id, interaction
                )
                return

            error = token_data.get('message') or token_data.get('error', '')
            if 'authorization_pending' in error:
                continue
            elif 'slow_down' in error:
                interval = min(interval + 5, 30)
                continue
            elif 'expired' in error or 'access_denied' in error:
                self.logger.info(f"linktwitch device auth ended for {discord_user_id}: {error}")
                break
            else:
                self.logger.warning(f"linktwitch device auth unexpected response: {token_data}")
                break

        self.logger.info(f"linktwitch device auth timed out for Discord user {discord_user_id}")

    async def _handle_linktwitch_token_success(
        self, token_data, client_id, discord_user_id, guild_id, interaction
    ):
        """Save the token and link the User record after successful device auth via /linktwitch."""
        from models import TwitchOAuthToken, User
        from database import DatabaseSession

        access_token  = token_data['access_token']
        refresh_token = token_data.get('refresh_token')
        token_expires = token_data.get('expires_in')

        def _get_twitch_user():
            return requests.get(
                "https://api.twitch.tv/helix/users",
                headers={
                    'Authorization': f'Bearer {access_token}',
                    'Client-Id': client_id,
                },
                timeout=10
            )

        try:
            u_resp = await asyncio.to_thread(_get_twitch_user)
            u_data = u_resp.json()['data'][0]
            twitch_user_id      = u_data['id']
            twitch_username     = u_data['login']
            twitch_display_name = u_data.get('display_name', twitch_username)
        except Exception as e:
            self.logger.error(f"linktwitch: failed to fetch Twitch user info: {e}")
            return

        from datetime import timedelta

        def _save():
            with DatabaseSession() as session:
                expires_at = (
                    datetime.now() + timedelta(seconds=token_expires)
                    if token_expires else None
                )

                # Twitch's device-code flow sometimes omits 'scope' from the token
                # response even when scopes were granted. Fall back to the requested
                # scopes so we never store empty metadata for a valid token.
                raw_scope = token_data.get('scope')
                if raw_scope:
                    saved_scopes = raw_scope if isinstance(raw_scope, list) else raw_scope.split()
                else:
                    saved_scopes = (
                        "chat:read chat:edit user:read:email "
                        "moderator:read:chatters user:write:chat channel:manage:raids"
                    ).split()

                existing = session.query(TwitchOAuthToken).filter_by(
                    twitch_user_id=twitch_user_id
                ).first()
                if existing:
                    existing.access_token    = access_token
                    existing.refresh_token   = refresh_token
                    existing.twitch_username = twitch_username
                    existing.scopes          = saved_scopes
                    existing.expires_at      = expires_at
                    existing.is_active       = True
                    existing.last_used_at    = datetime.now()
                    if discord_user_id:
                        existing.user_id = discord_user_id
                    if guild_id and not existing.guild_id:
                        existing.guild_id = guild_id
                else:
                    session.add(TwitchOAuthToken(
                        user_id=discord_user_id,
                        guild_id=guild_id,
                        access_token=access_token,
                        refresh_token=refresh_token,
                        twitch_user_id=twitch_user_id,
                        twitch_username=twitch_username,
                        scopes=saved_scopes,
                        expires_at=expires_at,
                        is_active=True,
                        last_used_at=datetime.now(),
                    ))

                if discord_user_id:
                    user_record = session.query(User).filter(
                        User.id == int(discord_user_id),
                        User.guild_id == int(guild_id)
                    ).first()
                    if not user_record:
                        user_record = session.query(User).filter(
                            User.id == int(discord_user_id)
                        ).order_by(User.last_seen.desc()).first()
                    if user_record:
                        user_record.twitch_id           = twitch_user_id
                        user_record.twitch_login        = twitch_username
                        user_record.twitch_display_name = twitch_display_name
                        user_record.twitch_linked_at    = datetime.now()
                        user_record.twitch_source       = 'twitch_oauth'
                        user_record.twitch_consent      = True
                        self.logger.info(
                            f"✅ linktwitch: Discord {discord_user_id} → Twitch {twitch_username}"
                        )

        try:
            await asyncio.to_thread(_save)
        except Exception as e:
            self.logger.error(f"linktwitch: failed to save token: {e}")
            return

        try:
            await interaction.followup.send(
                f"✅ **{twitch_display_name}** linked! The bot can now track your attendance and auto-raid from your channel.",
                ephemeral=True
            )
        except Exception:
            pass

        try:
            user = await self.bot.fetch_user(int(discord_user_id))
            await user.send(
                f"✅ **Twitch account linked!**\n\n"
                f"Your Twitch account **{twitch_username}** has been successfully connected to the bot. "
                f"Auto-raids and chat monitoring are now active for your channel."
            )
        except Exception as e:
            self.logger.warning(f"linktwitch: could not DM user {discord_user_id}: {e}")

        try:
            monitor = self.bot.cogs.get('TwitchChatMonitor')
            if monitor and hasattr(monitor, 'join_channel_for_user'):
                await monitor.join_channel_for_user(twitch_username)
        except Exception as e:
            self.logger.debug(f"linktwitch: could not auto-join Twitch channel: {e}")

    @app_commands.command(name='suggest', description='Submit a suggestion for the bot - tell us what you\'d like to see!')
    async def suggest_command(self, interaction: discord.Interaction):
        """Open an interactive suggestion form for users to submit ideas."""
        modal = SuggestionModal(self.bot)
        await interaction.response.send_modal(modal)


class SuggestionModal(discord.ui.Modal, title="💡 Bot Suggestion"):
    category = discord.ui.TextInput(
        label="Category",
        placeholder="e.g. New Feature, Improvement, Bug Fix, Train System, Twitch, etc.",
        max_length=100,
        required=True
    )
    suggestion = discord.ui.TextInput(
        label="Your Suggestion",
        style=discord.TextStyle.paragraph,
        placeholder="Describe what you'd like to see added or changed...",
        max_length=1000,
        required=True
    )
    priority = discord.ui.TextInput(
        label="How important is this to you? (1-5)",
        placeholder="1 = Nice to have, 5 = Really need this",
        max_length=1,
        required=False
    )

    def __init__(self, bot):
        super().__init__(timeout=300)
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction):
        try:
            owner_id = int(os.getenv('OWNER_ID_DISCORD', '887354716751810560'))
            owner = self.bot.get_user(owner_id)
            if not owner:
                owner = await self.bot.fetch_user(owner_id)

            priority_val = self.priority.value.strip() if self.priority.value else "Not rated"
            priority_stars = ""
            if priority_val.isdigit() and 1 <= int(priority_val) <= 5:
                priority_stars = "⭐" * int(priority_val) + f" ({priority_val}/5)"
            else:
                priority_stars = priority_val

            dm_embed = discord.Embed(
                title="💡 New Bot Suggestion Received!",
                color=0x667eea,
                timestamp=datetime.utcnow()
            )
            dm_embed.add_field(
                name="👤 From",
                value=f"**{interaction.user.display_name}** ({interaction.user.name})\nID: `{interaction.user.id}`",
                inline=True
            )
            dm_embed.add_field(
                name="🏠 Server",
                value=f"**{interaction.guild.name}**" if interaction.guild else "DM",
                inline=True
            )
            dm_embed.add_field(
                name="📂 Category",
                value=f"**{self.category.value}**",
                inline=True
            )
            dm_embed.add_field(
                name="📝 Suggestion",
                value=self.suggestion.value,
                inline=False
            )
            dm_embed.add_field(
                name="📊 Priority",
                value=priority_stars,
                inline=True
            )
            dm_embed.set_thumbnail(url=interaction.user.display_avatar.url)
            dm_embed.set_footer(text=f"Suggestion from {interaction.guild.name if interaction.guild else 'DM'}")

            dm_channel = await owner.create_dm()
            await dm_channel.send(embed=dm_embed)

            confirm_embed = discord.Embed(
                title="✅ Suggestion Submitted!",
                description=(
                    "Thank you for your feedback! Your suggestion has been sent to the bot developer.\n\n"
                    f"**Category:** {self.category.value}\n"
                    f"**Suggestion:** {self.suggestion.value[:200]}{'...' if len(self.suggestion.value) > 200 else ''}"
                ),
                color=0x00ff00
            )
            confirm_embed.set_footer(text="Your input helps make the bot better for everyone!")
            await interaction.response.send_message(embed=confirm_embed, ephemeral=True)

        except Exception as e:
            logging.getLogger('discord_bot.basic_commands').error(f"Error sending suggestion DM: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "✅ Your suggestion was recorded, but there was a small issue delivering it. Don't worry — it's been logged!",
                    ephemeral=True
                )
            else:
                await interaction.followup.send(
                    "✅ Your suggestion was recorded, but there was a small issue delivering it. Don't worry — it's been logged!",
                    ephemeral=True
                )

    async def on_timeout(self):
        pass


async def setup(bot):
    """Setup function to add the cog to the bot."""
    await bot.add_cog(BasicCommands(bot))
