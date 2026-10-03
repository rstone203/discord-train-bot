import discord
from discord import app_commands
from discord.ext import commands
import logging

logger = logging.getLogger('discord_bot.tutorial_command')

class TutorialCommand(commands.Cog):
    """Comprehensive bot tutorial command for all users."""
    
    def __init__(self, bot):
        self.bot = bot
        logger.info("Tutorial command cog loaded")
    
    @app_commands.command(name="botguide", description="Learn how to use all the bot's features")
    async def show_guide(self, interaction: discord.Interaction):
        """Comprehensive guide to using the bot - available to everyone."""
        
        try:
            
            await interaction.response.defer(ephemeral=True)
        except Exception as e:
            logger.error(f"Error in botguide command setup: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "An error occurred while loading the guide. Please try again.",
                    ephemeral=True
                )
            return
        
        embeds = []
        
        embed1 = discord.Embed(
            title="Bot Guide - Welcome & Quick Start (1/13)",
            description=(
                "Welcome to the comprehensive bot guide! This tutorial covers every "
                "feature so you can get the most out of the bot.\n\n"
                "**Use the navigation buttons below to browse each section.**"
            ),
            color=0x5865F2
        )
        embed1.add_field(
            name="Quick Start",
            value=(
                "1. Link your Twitch: `/linktwitch`\n"
                "2. Check bot status: `/status`\n"
                "3. View your trains: `/mytrains`\n"
                "4. View time slots: `/timeslots`\n"
                "5. Get help: `/help`"
            ),
            inline=False
        )
        embed1.add_field(
            name="Sections in this Guide",
            value=(
                "2. Train Schedules\n"
                "3. Twitch Integration\n"
                "4. Multi-Platform Streaming\n"
                "5. Auto-Shoutout & Live Chat\n"
                "6. Live Roles & Notifications\n"
                "7. Attendance & Reports\n"
                "8. Utility Features\n"
                "9. Privacy & Data\n"
                "10. Spam Protection\n"
                "11. Admin & Server Setup\n"
                "12. Prefix Commands & Help\n"
                "13. Beta Features"
            ),
            inline=False
        )
        embeds.append(embed1)
        
        embed2 = discord.Embed(
            title="Train Schedules (2/12)",
            description="Commands for managing train schedules and participation.",
            color=0x57F287
        )
        embed2.add_field(
            name="Joining & Viewing",
            value=(
                "`/jointrain` - Join a train schedule\n"
                "`/jointraindropdown` - Join a train via dropdown menu\n"
                "`/leavetrain` - Leave a train you've joined\n"
                "`/leavetraindropdown` - Leave a train via dropdown\n"
                "`/mytrains` - View trains you're signed up for\n"
                "`/timeslots` - View available time slots\n"
                "`/nexttrain` - See the next upcoming train\n"
                "`/trainroster` - View full roster for a train"
            ),
            inline=False
        )
        embed2.add_field(
            name="Admin Schedule Management",
            value=(
                "`/createtrain` - Create a new train schedule\n"
                "`/addtotrain` - Add someone to a train\n"
                "`/removefromtrain` - Remove someone from a train\n"
                "`/addslot` - Add a time slot to a schedule\n"
                "`/removeslot` - Remove a time slot\n"
                "`/clearparticipants` - Clear all participants\n"
                "`/setuppersistenttimeslots` - Create auto-updating schedule display\n"
                "`/removepersistenttimeslots` - Remove persistent display\n"
                "`/refreshpersistent` - Manually refresh persistent display"
            ),
            inline=False
        )
        embeds.append(embed2)
        
        embed3 = discord.Embed(
            title="Twitch Integration (3/12)",
            description="Connect your Twitch account and access stream features.",
            color=0x9146FF
        )
        embed3.add_field(
            name="Account Linking",
            value=(
                "`/linktwitch` - Link your Twitch account (self-service)\n"
                "`/twitchstatus` - Check your Twitch connection status\n"
                "`/twitchoauth` - Get Twitch OAuth link (admin)\n"
                "`/oauthstatus` - Check Twitch OAuth status"
            ),
            inline=False
        )
        embed3.add_field(
            name="Stream Info & Analytics",
            value=(
                "`/streams` - See who's currently live\n"
                "`/analytics` - View stream analytics\n"
                "`/twitchsettings` - Configure Twitch features\n"
                "`/sessionsummary` - View train session summary\n"
                "`/topsupporters` - See top supporters\n"
                "`/raidstats` - View raid statistics\n"
                "`/leaderboard` - Community leaderboard"
            ),
            inline=False
        )
        embed3.add_field(
            name="TwitchCord Features",
            value=(
                "`/twitchcord` - Full TwitchCord information\n"
                "`/twitchhelp` - Twitch-specific help\n"
                "`/chatbotsettings` - Configure Twitch chat bot features\n"
                "`/poll` - Create community polls"
            ),
            inline=False
        )
        embeds.append(embed3)
        
        embed4 = discord.Embed(
            title="Multi-Platform Streaming (4/12)",
            description=(
                "Stream support beyond Twitch! The bot also works with "
                "YouTube Gaming and Kick.com for live detection and notifications."
            ),
            color=0xFF0000
        )
        embed4.add_field(
            name="Stream Commands",
            value=(
                "`/stream link` - Link a streaming account (Twitch/YouTube/Kick)\n"
                "`/stream unlink` - Unlink a streaming account\n"
                "`/stream settings` - Configure per-platform settings\n"
                "`/stream list` - View all linked streaming accounts\n"
                "`/stream schedule` - View Twitch stream schedules"
            ),
            inline=False
        )
        embed4.add_field(
            name="Features",
            value=(
                "- Automatic live status detection across platforms\n"
                "- Customizable live notifications per user/server\n"
                "- Twitch clips & VODs auto-detection and posting\n"
                "- Stream schedule integration from Twitch"
            ),
            inline=False
        )
        embeds.append(embed4)
        
        embed5 = discord.Embed(
            title="Auto-Shoutout & Live Chat Announcements (5/12)",
            description="Automatic engagement tools for your streams.",
            color=0xFF6B6B
        )
        embed5.add_field(
            name="Auto-Shoutout",
            value=(
                "When enabled, the bot monitors your Twitch chat while you're live. "
                "New chatters automatically get a customizable welcome message!\n\n"
                "**Features:**\n"
                "- 3-second delay to feel natural\n"
                "- 60-minute cooldown per user\n"
                "- Bot filtering (ignores Nightbot, StreamElements, etc.)\n"
                "- Custom message templates\n\n"
                "`/autoshoutoutinfo` - View your auto-shoutout settings"
            ),
            inline=False
        )
        embed5.add_field(
            name="Live Chat Announcements",
            value=(
                "Announce when friends go live directly in their Twitch chat!\n\n"
                "Prefix commands for setup:\n"
                "`!setliveannouncement` - Set a custom live announcement\n"
                "`!viewliveannouncement` - View current announcement\n"
                "`!removeliveannouncement` - Remove announcement"
            ),
            inline=False
        )
        embeds.append(embed5)
        
        embed6 = discord.Embed(
            title="Live Roles & Notifications (6/12)",
            description="Automatic role assignment and train alerts.",
            color=0xFEE75C
        )
        embed6.add_field(
            name="Live Role System",
            value=(
                "When you go live on Twitch, you automatically get a 'Live Now' role "
                "so others know you're streaming. The role is removed when you go offline.\n\n"
                "The bot checks live status every 60 seconds across all linked accounts."
            ),
            inline=False
        )
        embed6.add_field(
            name="Train Notifications",
            value=(
                "- **1-hour warning** - Ping when your train starts in 1 hour\n"
                "- **10-minute warning** - Final reminder before train time\n"
                "- **Next rider alerts** - Announces when the next person goes live\n"
                "- **Auto-cancellation** - Under-filled trains can be auto-cancelled\n\n"
                "`/togglenotifications` - Enable/disable your notifications\n"
                "`/setuptrainpings` - Configure notification channel & role (admin)"
            ),
            inline=False
        )
        embeds.append(embed6)
        
        embed7 = discord.Embed(
            title="Attendance & Reports (7/12)",
            description="Automatic participation tracking and comprehensive reports.",
            color=0xEB459E
        )
        embed7.add_field(
            name="How Attendance Works",
            value=(
                "The bot monitors Twitch chat via IRC during active trains. "
                "When a train rider is live, it tracks who chats in their stream. "
                "This data is compiled into daily and weekend reports.\n\n"
                "Reports are automatically sent at **12pm UTC** the day after trains complete."
            ),
            inline=False
        )
        embed7.add_field(
            name="Commands",
            value=(
                "`/attendancestats` - View your attendance statistics\n"
                "`/sendattendancenow` - Manually send attendance report\n"
                "`/comprehensivereports` - Configure report settings\n"
                "`/viewpendingreports` - See reports waiting to be sent\n"
                "`/sessionsummary` - View session summary\n"
                "`/setattendancechannel` - Set where reports are posted (admin)\n"
                "`/setattendanceinterval` - Configure report frequency (admin)\n"
                "`/postattendanceinfo` - Post attendance explanation (admin)"
            ),
            inline=False
        )
        embeds.append(embed7)
        
        embed8 = discord.Embed(
            title="Utility Features (8/12)",
            description="Helpful everyday commands.",
            color=0x3498DB
        )
        embed8.add_field(
            name="AFK System",
            value=(
                "`/afk [message]` - Set yourself as away with an optional message\n"
                "- When someone mentions you while AFK, the bot auto-replies with your message\n"
                "- Shows how long you've been away\n"
                "- AFK clears automatically when you send a message\n"
                "- Rate limited to prevent spam"
            ),
            inline=False
        )
        embed8.add_field(
            name="General Commands",
            value=(
                "`/ping` - Check bot responsiveness\n"
                "`/uptime` - See how long the bot has been running\n"
                "`/status` - Full bot status & system info\n"
                "`/info` - Bot information\n"
                "`/servers` - View servers the bot is in\n"
                "`/updates` - Check recent bot updates\n"
                "`/invite` - Get bot invite link\n"
                "`/backups` - View database backup info"
            ),
            inline=False
        )
        embeds.append(embed8)
        
        embed9 = discord.Embed(
            title="Privacy & Your Data (9/12)",
            description="You have full control over your personal data.",
            color=0x2ECC71
        )
        embed9.add_field(
            name="Privacy Commands",
            value=(
                "`/mydata` - View all data the bot stores about you\n"
                "`/deletemydata` - Request deletion of your personal data\n\n"
                "The bot only stores data necessary for features you use, "
                "like Twitch links, train signups, and attendance records."
            ),
            inline=False
        )
        embeds.append(embed9)
        
        embed10 = discord.Embed(
            title="Spam Protection (10/12)",
            description="Automatic and manual spam detection tools.",
            color=0xE67E22
        )
        embed10.add_field(
            name="For Everyone",
            value=(
                "`/reportspam` - Report suspicious accounts to admins\n\n"
                "The bot automatically detects suspicious joins using 15+ detection "
                "patterns and can alert admins via DM or a designated channel."
            ),
            inline=False
        )
        embed10.add_field(
            name="Admin Spam Commands",
            value=(
                "`!setspamchannel` - Set channel for spam alerts\n"
                "`!setspamuser` - Set user for DM spam alerts\n"
                "`!toggleautokick` - Toggle auto-kick for spam accounts\n"
                "`!togglelogalljoins` - Toggle logging all server joins\n"
                "`!togglespamdm` - Toggle DM alerts for spam\n"
                "`!spamstatus` - View spam protection settings\n"
                "`!spamreports` - View member-reported spam"
            ),
            inline=False
        )
        embeds.append(embed10)
        
        embed11 = discord.Embed(
            title="Admin & Server Setup (11/12)",
            description="Commands for server administrators and bot owners.",
            color=0xE74C3C
        )
        embed11.add_field(
            name="Permission Management",
            value=(
                "`/trustrole` / `/untrustrole` - Add/remove trusted roles\n"
                "`/addtrustedrole` / `/removetrustedrole` / `/listtrustedroles`\n"
                "`/managefeatures` - Interactive feature toggle per server\n"
                "`/viewserverfeatures` - See enabled features\n"
                "`/resetserverfeatures` - Reset to defaults (owner)\n"
                "`/enabletrustedaccess` / `/disabletrustedaccess`\n"
                "`/cleanuptrusted` - Clean up invalid trusted entries"
            ),
            inline=False
        )
        embed11.add_field(
            name="Server Configuration",
            value=(
                "`/setserverdescription` - Set server description\n"
                "`/setuptrainpings` - Configure train notification channel/role\n"
                "`/setattendancechannel` - Set attendance report channel\n"
                "`/setattendanceinterval` - Set report frequency\n"
                "`/togglenotifications` - Toggle notifications\n"
                "`/chatbotsettings` - Configure Twitch chat bot"
            ),
            inline=False
        )
        embed11.add_field(
            name="Message Forwarding",
            value=(
                "`/addforward` - Set up message forwarding between channels\n"
                "`/listforwards` - View all forwarding rules\n"
                "`/removeforward` - Remove a forwarding rule\n"
                "`/removeforwardbyid` - Remove by specific ID"
            ),
            inline=False
        )
        embed11.add_field(
            name="Moderation",
            value=(
                "`/banuser` - Ban a user from train schedules\n"
                "`/unbanuser` - Unban a user\n"
                "`/listbans` - View all bans"
            ),
            inline=False
        )
        embeds.append(embed11)
        
        embed12 = discord.Embed(
            title="Prefix Commands & Getting Help (12/13)",
            description="Owner prefix commands and additional resources.",
            color=0x95A5A6
        )
        embed12.add_field(
            name="Owner Prefix Commands (!)",
            value=(
                "`!set_activity` - Set bot's activity (playing, watching, etc.)\n"
                "`!clear_activity` - Clear custom activity\n"
                "`!set_status` - Set online/idle/dnd/invisible\n"
                "`!view_logs` - View recent bot logs\n"
                "`!announce_signups` - Announce train signups\n"
                "`!viewbackups` - View database backups\n"
                "`!dash` - Get web dashboard link\n"
                "`!botinvite` - Get bot invite link\n"
                "`!donate` - Donation info\n"
                "`!about` - Bot info"
            ),
            inline=False
        )
        embed12.add_field(
            name="Twitch Chat Commands (in Twitch chat)",
            value=(
                "`!raidnext` - Raid the next person in the train (mods only)\n"
                "Auto-announcements when next rider goes live\n"
                "1-hour and 10-minute warnings in chat"
            ),
            inline=False
        )
        embed12.add_field(
            name="Getting Help",
            value=(
                "`/help` - General help command\n"
                "`/support` - Get support information\n"
                "`/invite` - Get bot invite link\n"
                "`/botguide` - This guide!\n\n"
                "**Tips:**\n"
                "- Most commands use slash (`/`) - start typing to see options\n"
                "- Commands with choices show dropdown menus\n"
                "- Some responses are only visible to you (ephemeral)\n"
                "- The web dashboard at `/dash` gives a full overview"
            ),
            inline=False
        )
        embed12.set_footer(text="Thank you for using the bot!")
        embeds.append(embed12)

        embed13 = discord.Embed(
            title="Beta Features (13/13)",
            description=(
                "These features are in **beta testing** and must be enabled per-server by an admin "
                "using `/managefeatures`. Everything is **off by default** — nothing changes for "
                "your server until it's switched on."
            ),
            color=0xffaa00
        )
        embed13.add_field(
            name="⚙️ Enabling Beta Features",
            value=(
                "`/managefeatures` — Interactive toggle for each beta feature\n"
                "`/viewserverfeatures` — See which features are currently active\n"
                "`/betacommands` — Quick single-page reference for all beta commands"
            ),
            inline=False
        )
        embed13.add_field(
            name="🎟️ Slot Waitlist",
            value=(
                "When a slot is full, members join a waitlist. When a seat opens the bot DMs the "
                "next person an Accept/Decline offer — no button races.\n\n"
                "`/mywaitlists` — View, accept, decline, or leave your waitlist spots\n"
                "`!setwaitlistpanel #channel` — Post the join/leave panel *(admin)*\n"
                "`!removewaitlistpanel` — Remove the panel *(admin)*\n"
                "`!setwaitlistlog #channel` — Set where seat-filled alerts are posted *(admin)*"
            ),
            inline=False
        )
        embed13.add_field(
            name="⏰ Train Reminder DMs",
            value=(
                "Opt-in personal DM reminders before your scheduled slot.\n\n"
                "`/trainreminder on [minutes]` — Opt in (default 30 min lead time)\n"
                "`/trainreminder off` — Opt out\n"
                "`/trainreminder status` — Check your current setting"
            ),
            inline=False
        )
        embed13.add_field(
            name="📊 Participant Stats",
            value=(
                "`/trainstats` — View your attended sessions, current & longest streak, "
                "and the server top-10 leaderboard"
            ),
            inline=False
        )
        embed13.set_footer(text="Beta Program • Enable features with /managefeatures • Feedback welcome")
        embeds.append(embed13)

        try:
            view = TutorialPaginator(embeds, interaction.user.id)
            await interaction.followup.send(embed=embeds[0], view=view, ephemeral=True)
            logger.info(f"Bot guide accessed by {interaction.user.name} ({interaction.user.id})")
        except Exception as e:
            logger.error(f"Error sending botguide response: {e}")
            try:
                await interaction.followup.send(
                    "An error occurred while displaying the guide. Please try again.",
                    ephemeral=True
                )
            except:
                pass


class TutorialPaginator(discord.ui.View):
    """Paginated view for the tutorial embeds."""
    
    def __init__(self, embeds: list, user_id: int):
        super().__init__(timeout=300)
        self.embeds = embeds
        self.user_id = user_id
        self.current_page = 0
        self.update_buttons()
    
    def update_buttons(self):
        self.first_page.disabled = self.current_page == 0
        self.prev_page.disabled = self.current_page == 0
        self.next_page.disabled = self.current_page == len(self.embeds) - 1
        self.last_page.disabled = self.current_page == len(self.embeds) - 1
        self.page_counter.label = f"{self.current_page + 1}/{len(self.embeds)}"
    
    @discord.ui.button(label="<<", style=discord.ButtonStyle.secondary)
    async def first_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This tutorial is for someone else!", ephemeral=True)
            return
        self.current_page = 0
        self.update_buttons()
        await interaction.response.edit_message(embed=self.embeds[self.current_page], view=self)
    
    @discord.ui.button(label="<", style=discord.ButtonStyle.primary)
    async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This tutorial is for someone else!", ephemeral=True)
            return
        self.current_page = max(0, self.current_page - 1)
        self.update_buttons()
        await interaction.response.edit_message(embed=self.embeds[self.current_page], view=self)
    
    @discord.ui.button(label="1/13", style=discord.ButtonStyle.secondary, disabled=True)
    async def page_counter(self, interaction: discord.Interaction, button: discord.ui.Button):
        pass
    
    @discord.ui.button(label=">", style=discord.ButtonStyle.primary)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This tutorial is for someone else!", ephemeral=True)
            return
        self.current_page = min(len(self.embeds) - 1, self.current_page + 1)
        self.update_buttons()
        await interaction.response.edit_message(embed=self.embeds[self.current_page], view=self)
    
    @discord.ui.button(label=">>", style=discord.ButtonStyle.secondary)
    async def last_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This tutorial is for someone else!", ephemeral=True)
            return
        self.current_page = len(self.embeds) - 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.embeds[self.current_page], view=self)


async def setup(bot):
    await bot.add_cog(TutorialCommand(bot))
