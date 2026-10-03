"""
Admin-only commands for the Discord bot owner.
"""

import discord
from discord.ext import commands
from discord import app_commands, ui
from datetime import datetime, timedelta
import pytz
import psutil
import os
import logging
import secrets
import hashlib
import asyncio
from database import DatabaseSession
from models import Guild, User, Message, CommandLog, BotStats, BotUpdate, UpdateChannelConfig, SystemSettings, TrainSchedule, TrainParticipant, TrainNotification, TwitchLinkRequest, TrustedRole, StreamChatAnnouncement, TwitchOAuthToken, TwitchLinkOutreach, NotificationSettings, get_est_time
from sqlalchemy import text
from utils.update_manager import update_manager, VALID_CATEGORIES, CATEGORY_EMOJIS
from utils.slash_permissions import owner_only, owner_or_trusted, admin_or_trusted, server_admin_only


async def send_oauth_dm(bot, discord_user_id: int, twitch_login: str, reason: str = 'linked') -> bool:
    """DM a newly-linked user their personal Twitch OAuth authorisation link.

    Args:
        bot: the Discord bot instance
        discord_user_id: Discord user ID to DM
        twitch_login: their Twitch username (for display)
        reason: 'linked' (just linked) or 'reminder' (existing link, no token)

    Returns True if the DM was delivered, False if DMs are closed / user not found.
    """
    logger = logging.getLogger('discord_bot.admin.oauth_dm')
    try:
        user = bot.get_user(discord_user_id)
        if not user:
            try:
                user = await bot.fetch_user(discord_user_id)
            except (discord.NotFound, discord.HTTPException):
                logger.warning(f"Could not fetch user {discord_user_id} for OAuth DM")
                return False

        if reason == 'verify':
            title = "🔗 Verify your Twitch account to complete linking"
            intro = (
                f"An admin has approved a request to link **{twitch_login}** to your Discord account. "
                "To complete the process, run **/twitchoauth** in the Discord server to prove you own that channel. "
                "Your account will be linked only after verification."
            )
        elif reason == 'linked':
            title = "🎉 One more step — authorize the raid bot!"
            intro = (
                f"Your Twitch account **{twitch_login}** has just been linked to the raid train bot. "
                "To unlock full features, you need to authorize the bot with Twitch once. "
                "This is quick and only needs to be done once."
            )
        else:
            title = "🔔 Raid Train Bot — Twitch authorization needed"
            intro = (
                f"Your Twitch account **{twitch_login}** is linked to the raid train bot, "
                "but the bot hasn't been authorized with Twitch yet. "
                "Run **/twitchoauth** in the Discord server to fix this in under a minute."
            )

        embed = discord.Embed(
            title=title,
            description=intro,
            color=0x9146ff,
            timestamp=datetime.utcnow()
        )
        embed.add_field(
            name="🔗 How to Authorize",
            value="Run **/twitchoauth** in the Discord server → you'll get a short code to enter at **twitch.tv/activate** — no browser redirects, no passwords shared.",
            inline=False
        )
        embed.add_field(
            name="✅ What this unlocks",
            value=(
                "• **Auto-raids** — the bot can start raids from your channel during trains\n"
                "• **Attendance tracking** — your chat activity is recorded so you appear in train stats\n"
                "• **Auto-shoutouts** — the bot can give shoutouts in your chat during raids\n"
                "• **Live role** — your Discord role updates automatically when you go live"
            ),
            inline=False
        )
        embed.add_field(
            name="🔒 Privacy",
            value=(
                "The bot only requests the permissions it needs (chat read/write, raids). "
                "You can revoke access at any time from your [Twitch connections page](https://www.twitch.tv/settings/connections)."
            ),
            inline=False
        )
        embed.set_footer(text="This is a one-time setup — tokens refresh automatically after that.")

        await user.send(embed=embed)
        logger.info(f"OAuth DM sent to {user} ({discord_user_id}) for twitch:{twitch_login}")
        return True

    except discord.Forbidden:
        logger.info(f"Could not DM {discord_user_id} (DMs closed) for OAuth prompt")
        return False
    except Exception as e:
        logger.error(f"Unexpected error sending OAuth DM to {discord_user_id}: {e}")
        return False


class TwitchLinkConsentView(ui.View):
    """Interactive consent view for Twitch account linking requests."""
    
    def __init__(self, request_id: int, target_user_id: int, bot):
        super().__init__(timeout=86400.0)  # 24 hour timeout
        self.request_id = request_id
        self.target_user_id = target_user_id
        self.bot = bot
        self.logger = logging.getLogger('discord_bot.admin.consent')
    
    async def respond(self, interaction, *args, **kwargs):
        """Helper method to safely respond to an interaction."""
        if interaction.response.is_done():
            if 'view' in kwargs and kwargs['view'] is None:
                del kwargs['view']
            return await interaction.followup.send(*args, **kwargs)
        return await interaction.response.send_message(*args, **kwargs)
    
    async def interaction_check(self, interaction) -> bool:
        """Ensure only the target user can respond to their own request."""
        if interaction.user.id != self.target_user_id:
            await self.respond(interaction,
                "❌ This request is not for you. Only the target user can respond.",
                ephemeral=True
            )
            return False
        return True
    
    @ui.button(label="Allow Access", style=discord.ButtonStyle.green, emoji="✅")
    async def allow_access(self, interaction: discord.Interaction, button: ui.Button):
        """Handle the Allow Access button click."""
        try:
            with DatabaseSession() as session:
                # Get the pending request
                request = session.query(TwitchLinkRequest).filter_by(
                    id=self.request_id,
                    target_user_id=self.target_user_id,
                    status='pending'
                ).first()
                
                if not request:
                    await self.respond(
                        interaction,
                        "❌ This request has expired or is no longer valid.",
                        ephemeral=True
                    )
                    return
                
                # Check if request has expired
                if datetime.utcnow() > request.expires_at:
                    request.status = 'expired'
                    session.commit()
                    await self.respond(
                        interaction,
                        "❌ This request has expired. Please ask the admin to send a new request.",
                        ephemeral=True
                    )
                    return
                
                # Update request status
                request.status = 'approved'
                request.responded_at = datetime.utcnow()
                request.response_message = f"User approved access via Discord interaction"
                
                # Proceed with the actual linking logic
                await self._perform_twitch_linking(session, request, interaction)
                
                # Send verification-pending response to user
                embed = discord.Embed(
                    title="🔗 Almost Done — Verify Your Twitch Account",
                    description=(
                        f"The admin request to link **{request.requested_twitch_username}** to your Discord account "
                        f"has been approved. **Check your DMs** for a verification link — you must sign in with "
                        f"Twitch to prove you own that channel. Your account will be fully linked after that step."
                    ),
                    color=0x9146ff,
                    timestamp=datetime.utcnow()
                )
                embed.add_field(
                    name="🎯 What Happens After Verification",
                    value=(
                        "• Your attendance for raid trains will be tracked automatically\n"
                        "• You'll appear in raid train notifications with your Twitch username\n"
                        "• Admins can see your participation in train statistics"
                    ),
                    inline=False
                )

                await interaction.response.edit_message(embed=embed, view=None)
                
                # Notify the admin
                await self._notify_admin(request, 'approved', interaction.user)

        except Exception as e:
            self.logger.error(f"Error handling allow access: {e}")
            await self.respond(
                interaction,
                f"❌ An error occurred while processing your response: {str(e)}",
                ephemeral=True
            )
    
    @ui.button(label="Deny Access", style=discord.ButtonStyle.red, emoji="❌")
    async def deny_access(self, interaction: discord.Interaction, button: ui.Button):
        """Handle the Deny Access button click."""
        try:
            with DatabaseSession() as session:
                # Get the pending request
                request = session.query(TwitchLinkRequest).filter_by(
                    id=self.request_id,
                    target_user_id=self.target_user_id,
                    status='pending'
                ).first()
                
                if not request:
                    await self.respond(
                        interaction,
                        "❌ This request has expired or is no longer valid.",
                        ephemeral=True
                    )
                    return
                
                # Update request status
                request.status = 'denied'
                request.responded_at = datetime.utcnow()
                request.response_message = f"User denied access via Discord interaction"
                session.commit()
                
                # Send denial confirmation to user
                embed = discord.Embed(
                    title="❌ Access Denied",
                    description="You have successfully denied the Twitch account linking request.",
                    color=0xff0000,
                    timestamp=datetime.utcnow()
                )
                embed.add_field(
                    name="🔒 Your Privacy Protected",
                    value="• No Twitch account has been linked to your Discord\n• Your information remains private\n• You can still participate in raid trains without automatic tracking",
                    inline=False
                )
                
                await interaction.response.edit_message(embed=embed, view=None)
                
                # Notify the admin
                await self._notify_admin(request, 'denied', interaction.user)
                
        except Exception as e:
            self.logger.error(f"Error handling deny access: {e}")
            await self.respond(
                interaction,
                f"❌ An error occurred while processing your response: {str(e)}",
                ephemeral=True
            )
    
    async def _perform_twitch_linking(self, session, request: TwitchLinkRequest, interaction: discord.Interaction):
        """Gate Twitch account binding behind OAuth verification.

        The previous implementation wrote User.twitch_login directly from the
        admin-requested username without any Twitch-side proof of ownership.
        Instead, we now ensure a User record exists and then send the target
        user a Twitch OAuth verification link.  User.twitch_login is only set
        by the OAuth callback once Twitch authenticates the user's identity.
        """
        try:
            # Ensure a User record exists so the OAuth callback can find it
            db_user = session.query(User).filter_by(id=request.target_user_id).first()
            if not db_user:
                db_user = User(
                    id=request.target_user_id,
                    username=request.target_username,
                    display_name=request.target_display_name,
                    guild_id=request.guild_id,
                    first_seen=datetime.utcnow(),
                    last_seen=datetime.utcnow()
                )
                session.add(db_user)

            session.commit()
            self.logger.info(
                f"Admin consent approved for user {request.target_user_id} to link Twitch "
                f"'{request.requested_twitch_username}' — sending OAuth verification DM"
            )

            # Send OAuth verification link so the user can prove channel ownership
            asyncio.create_task(send_oauth_dm(
                self.bot,
                request.target_user_id,
                request.requested_twitch_username,
                reason='verify'
            ))

        except Exception as e:
            session.rollback()
            self.logger.error(f"Error in _perform_twitch_linking: {e}")
            raise e
    
    async def _notify_admin(self, request: TwitchLinkRequest, decision: str, user: discord.User):
        """Notify the admin about the user's decision."""
        try:
            # Check if this is a self-initiated request
            is_self_service = (request.admin_user_id == request.target_user_id)
            
            # If self-service, notify the bot owner; otherwise notify the requesting admin
            if is_self_service:
                # Get bot owner ID from environment
                owner_id = int(os.getenv('OWNER_ID_DISCORD', '887354716751810560'))
                notification_user_id = owner_id
            else:
                notification_user_id = request.admin_user_id
            
            # Try to get user from cache first, then fetch from API
            admin_user = self.bot.get_user(notification_user_id)
            if not admin_user:
                try:
                    admin_user = await self.bot.fetch_user(notification_user_id)
                except discord.NotFound:
                    self.logger.warning(f"Admin user {notification_user_id} not found")
                    return
                except Exception as e:
                    self.logger.warning(f"Could not fetch admin user {notification_user_id}: {e}")
                    return
            
            if decision == 'approved':
                if is_self_service:
                    # Self-service notification — linking is now pending OAuth verification
                    embed = discord.Embed(
                        title="🔗 User Approved Twitch Link (Verification Pending)",
                        description=f"**{user.display_name}** (`{user.name}`) approved Twitch linking and has been sent an OAuth verification link.",
                        color=0x9146ff,
                        timestamp=datetime.utcnow()
                    )
                    embed.add_field(
                        name="🔗 Link Details",
                        value=f"**Discord User:** {user.mention}\n**Claimed Twitch Username:** `{request.requested_twitch_username}`\n**Status:** Awaiting Twitch OAuth verification",
                        inline=False
                    )
                    embed.add_field(
                        name="ℹ️ What Happens Next",
                        value="• The user must sign in with Twitch to prove ownership\n• Attendance tracking activates after they complete verification\n• The link is not yet active",
                        inline=False
                    )
                    embed.set_thumbnail(url=user.display_avatar.url)
                    embed.set_footer(text=f"Verification link sent • {datetime.now(pytz.timezone('Europe/London')).strftime('%I:%M %p UK')}")
                else:
                    # Admin-initiated notification — linking is now pending OAuth verification
                    embed = discord.Embed(
                        title="🔗 Twitch Link Request APPROVED (Verification Pending)",
                        description=f"**{user.display_name}** (`{user.name}`) approved the request. They have been sent a Twitch OAuth link to verify channel ownership.",
                        color=0x9146ff,
                        timestamp=datetime.utcnow()
                    )
                    embed.add_field(
                        name="⏳ Pending Verification",
                        value=f"**Discord User:** {user.mention}\n**Claimed Twitch Username:** `{request.requested_twitch_username}`\n**Request ID:** `{request.id}`",
                        inline=False
                    )
                    embed.add_field(
                        name="ℹ️ What Happens Now",
                        value="• A verification link has been DM'd to the user\n• They must sign in with Twitch to prove they own the channel\n• Attendance tracking and train features activate after verification",
                        inline=False
                    )
                    embed.set_thumbnail(url=user.display_avatar.url)
                    embed.set_footer(text=f"Request approved at {datetime.now(pytz.timezone('Europe/London')).strftime('%I:%M %p UK on %B %d, %Y')}")
            else:  # denied
                embed = discord.Embed(
                    title="❌ Twitch Link Request Denied",
                    description=f"{user.mention} has **denied** the Twitch account linking request.",
                    color=0xff0000,
                    timestamp=datetime.utcnow()
                )
                embed.add_field(
                    name="📋 Details",
                    value=f"**User:** {user.mention} (`{user.name}`)\n**Requested Twitch:** `{request.requested_twitch_username}`\n**Status:** Access denied by user",
                    inline=False
                )
                embed.add_field(
                    name="🔒 User Privacy",
                    value="The user has chosen to keep their Twitch account private. No linking has been performed.",
                    inline=False
                )
            
            await admin_user.send(embed=embed)
            self.logger.info(f"🔔 SUCCESS: Admin {admin_user.name} ({request.admin_user_id}) notified via DM of {decision.upper()} decision by {user.name} ({user.id})")
            
            # Additional confirmation in console for immediate visibility
            if decision == 'approved':
                self.logger.info(f"🎉 TWITCH LINK APPROVED: {user.name} accepted linking to {request.requested_twitch_username} - Admin {admin_user.name} has been notified!")
            
            # Duplicate Sippy Cup co-owner notification disabled per owner request
            # (was always sending a second DM to Sippy Cup on top of the primary
            # admin notification above, which read as a double-send).
            
        except discord.Forbidden:
            self.logger.warning(f"Could not send DM to admin {request.admin_user_id} - DMs disabled")
            # Try to log this for debugging
            self.logger.error(f"IMPORTANT: Admin {request.admin_user_id} has DMs disabled - they won't receive {decision} notification for user {user.id}")
        except Exception as e:
            self.logger.error(f"Error notifying admin {request.admin_user_id} about {decision} decision: {e}")
            # Additional logging for debugging
            self.logger.error(f"Admin user object: {admin_user}, Request ID: {request.id}, Decision: {decision}")
    
    async def on_timeout(self):
        """Handle view timeout after 24 hours."""
        try:
            with DatabaseSession() as session:
                request = session.query(TwitchLinkRequest).filter_by(
                    id=self.request_id,
                    status='pending'
                ).first()
                
                if request:
                    request.status = 'expired'
                    request.updated_at = datetime.utcnow()
                    session.commit()
                    self.logger.info(f"Twitch link request {self.request_id} expired due to timeout")
                    
        except Exception as e:
            self.logger.error(f"Error handling view timeout: {e}")


class ReplaceLinkRequestView(ui.View):
    """View shown when a pending link request already exists — allows replacing it."""

    def __init__(self, existing_request_id: int, user: discord.Member, new_twitch_username: str, ctx, bot):
        super().__init__(timeout=60.0)
        self.existing_request_id = existing_request_id
        self.user = user
        self.new_twitch_username = new_twitch_username
        self.ctx = ctx
        self.bot = bot
        self.logger = logging.getLogger('discord_bot.admin.replace_link')

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("❌ Only the admin who ran the command can do this.", ephemeral=True)
            return False
        return True

    @ui.button(label="Replace Request", style=discord.ButtonStyle.danger, emoji="🔄")
    async def replace_request(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.defer()
        try:
            with DatabaseSession() as session:
                # Delete the old pending request
                old = session.query(TwitchLinkRequest).filter_by(id=self.existing_request_id, status='pending').first()
                if old:
                    session.delete(old)
                    session.flush()

                # Create the new request
                expires_at = datetime.utcnow() + timedelta(hours=24)
                link_request = TwitchLinkRequest(
                    target_user_id=self.user.id,
                    target_username=self.user.name,
                    target_display_name=self.user.display_name or self.user.name,
                    requested_twitch_username=self.new_twitch_username,
                    guild_id=self.ctx.guild.id if self.ctx.guild else None,
                    admin_user_id=self.ctx.author.id,
                    admin_username=self.ctx.author.name,
                    expires_at=expires_at,
                    status='pending'
                )
                session.add(link_request)
                session.commit()
                session.refresh(link_request)

                # Build and send DM
                consent_embed = discord.Embed(
                    title="🔗 Twitch Account Linking Request",
                    description=f"**{self.ctx.author.display_name}** would like to link your Discord account to your Twitch account for attendance tracking in raid trains.",
                    color=0x9146ff,
                    timestamp=datetime.utcnow()
                )
                consent_embed.add_field(
                    name="📋 Request Details",
                    value=f"**Admin:** {self.ctx.author.mention} (`{self.ctx.author.name}`)\n**Twitch Username:** `{self.new_twitch_username}`\n**Server:** {self.ctx.guild.name if self.ctx.guild else 'Direct Message'}",
                    inline=False
                )
                consent_embed.add_field(
                    name="🎯 What This Means",
                    value="• Your attendance for raid trains will be tracked automatically\n• You'll appear in raid train notifications with your Twitch username\n• Admins can see your participation in train statistics\n• Your Discord and Twitch accounts will be linked in our system",
                    inline=False
                )
                consent_embed.add_field(
                    name="🔒 Your Privacy",
                    value="• Only basic linking information is stored\n• You can deny this request if you prefer to keep accounts separate\n• This won't affect your ability to participate in Discord activities\n• You can request unlinking at any time",
                    inline=False
                )
                consent_embed.add_field(
                    name="🎮 Enable Auto-Raid (Optional)",
                    value="After accepting, run **/twitchoauth** in the Discord server so the bot can auto-start raids from your channel during trains.",
                    inline=False
                )
                consent_embed.add_field(
                    name="⏰ Response Time",
                    value=f"Please respond within 24 hours. This request expires <t:{int(expires_at.timestamp())}:R>.",
                    inline=False
                )
                consent_embed.set_footer(text="Only you can respond to this request")

                view = TwitchLinkConsentView(link_request.id, self.user.id, self.bot)

                try:
                    dm_message = await self.user.send(embed=consent_embed, view=view)
                    link_request.request_message_id = dm_message.id
                    link_request.request_channel_id = dm_message.channel.id
                    session.commit()

                    admin_embed = discord.Embed(
                        title="📨 Consent Request Sent",
                        description=f"Old request cancelled and a new Twitch linking consent request has been sent to {self.user.mention}.",
                        color=0x00ff00,
                        timestamp=datetime.utcnow()
                    )
                    admin_embed.add_field(
                        name="📋 Request Details",
                        value=f"**Target User:** {self.user.mention} (`{self.user.name}`)\n**Twitch Username:** `{self.new_twitch_username}`\n**Request ID:** `{link_request.id}`",
                        inline=False
                    )
                    admin_embed.add_field(
                        name="⏳ Next Steps",
                        value=f"• The user has 24 hours to respond\n• You'll be notified when they make a decision\n• The request expires <t:{int(expires_at.timestamp())}:R>",
                        inline=False
                    )
                    for child in self.children:
                        child.disabled = True
                    await interaction.edit_original_response(view=self)
                    await interaction.followup.send(embed=admin_embed)

                except discord.Forbidden:
                    link_request.status = 'failed'
                    link_request.dm_failed = True
                    link_request.error_message = "User has disabled direct messages"
                    session.commit()
                    await interaction.followup.send(
                        embed=discord.Embed(
                            title="❌ Cannot Send Direct Message",
                            description=f"Old request was cancelled, but {self.user.mention} has DMs disabled — couldn't send the new request.",
                            color=0xff0000
                        )
                    )
        except Exception as e:
            self.logger.error(f"Error replacing link request: {e}")
            await interaction.followup.send(f"❌ Error: {e}")

    @ui.button(label="Keep Existing", style=discord.ButtonStyle.secondary, emoji="✖️")
    async def keep_existing(self, interaction: discord.Interaction, button: ui.Button):
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)
        await interaction.followup.send("↩️ Kept the existing pending request. No changes made.", ephemeral=True)


class LinkedUsersPaginationView(ui.View):
    """Pagination view for linked users list."""
    
    def __init__(self, user_data: list, author: discord.Member, users_per_page: int = 10):
        super().__init__(timeout=300.0)  # 5 minute timeout
        self.user_data = user_data
        self.author = author
        self.users_per_page = users_per_page
        self.current_page = 0
        self.total_pages = (len(user_data) + users_per_page - 1) // users_per_page
        
        # Update button states
        self.update_buttons()
    
    def update_buttons(self):
        """Update button states based on current page."""
        self.previous_button.disabled = (self.current_page == 0)
        self.next_button.disabled = (self.current_page >= self.total_pages - 1)
    
    def create_embed(self) -> discord.Embed:
        """Create embed for current page."""
        start_idx = self.current_page * self.users_per_page
        end_idx = min(start_idx + self.users_per_page, len(self.user_data))
        
        embed = discord.Embed(
            title="📋 Linked Twitch Users",
            description=f"Showing {start_idx + 1}-{end_idx} of {len(self.user_data)} linked user(s)",
            color=0x667eea,
            timestamp=get_est_time()
        )
        
        # Add users for current page
        users_text = []
        for i in range(start_idx, end_idx):
            user = self.user_data[i]
            users_text.append(
                f"{user['mention']}\n"
                f"**Discord:** {user['discord_name']}\n"
                f"**Twitch:** {user['twitch_name']}\n"
                f"**Linked:** {user['linked_date']} ({user['source']})\n"
            )
        
        embed.add_field(
            name=f"👥 Users (Page {self.current_page + 1}/{self.total_pages})",
            value='\n'.join(users_text) if users_text else "No users on this page",
            inline=False
        )
        
        embed.set_footer(text=f"Total: {len(self.user_data)} linked user(s) • Page {self.current_page + 1}/{self.total_pages}")
        
        return embed
    
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Ensure only the command author can use the buttons."""
        if interaction.user.id != self.author.id:
            await interaction.response.send_message(
                "❌ Only the command author can use these buttons.",
                ephemeral=True
            )
            return False
        return True
    
    @ui.button(label="◀ Previous", style=discord.ButtonStyle.primary, disabled=True)
    async def previous_button(self, interaction: discord.Interaction, button: ui.Button):
        """Go to previous page."""
        self.current_page = max(0, self.current_page - 1)
        self.update_buttons()
        
        embed = self.create_embed()
        await interaction.response.edit_message(embed=embed, view=self)
    
    @ui.button(label="Next ▶", style=discord.ButtonStyle.primary)
    async def next_button(self, interaction: discord.Interaction, button: ui.Button):
        """Go to next page."""
        self.current_page = min(self.total_pages - 1, self.current_page + 1)
        self.update_buttons()
        
        embed = self.create_embed()
        await interaction.response.edit_message(embed=embed, view=self)
    
    async def on_timeout(self):
        """Handle view timeout."""
        # Disable all buttons when timeout occurs
        for item in self.children:
            item.disabled = True


class AdminCommands(commands.Cog):
    """Administrator-only commands for bot management."""
    
    def __init__(self, bot):
        self.bot = bot
        self.logger = logging.getLogger('discord_bot.admin')
    
    async def respond(self, interaction, *args, **kwargs):
        """Helper method to safely respond to an interaction."""
        if interaction.response.is_done():
            if 'view' in kwargs and kwargs['view'] is None:
                del kwargs['view']
            return await interaction.followup.send(*args, **kwargs)
        return await interaction.response.send_message(*args, **kwargs)
        
    def is_owner_or_trusted():
        """Check if user is the bot owner or a trusted user."""
        async def predicate(ctx):
            return await ctx.bot.is_owner_or_trusted(ctx.author)
        return commands.check(predicate)
    
    @commands.command(name='shutdown', aliases=['stop'])
    @is_owner_or_trusted()
    async def shutdown_bot(self, ctx):
        """Shutdown the bot and enter maintenance mode (Owner only)."""
        try:
            # Set maintenance mode flag in database
            with DatabaseSession() as session:
                maintenance_setting = session.query(SystemSettings).filter_by(
                    setting_key='maintenance_mode'
                ).first()
                
                if not maintenance_setting:
                    maintenance_setting = SystemSettings(
                        setting_key='maintenance_mode',
                        is_enabled=True,
                        setting_value='true'
                    )
                    session.add(maintenance_setting)
                else:
                    maintenance_setting.is_enabled = True
                    maintenance_setting.setting_value = 'true'
                    maintenance_setting.updated_at = datetime.utcnow()
                
                session.commit()
                self.logger.info(f"Maintenance mode enabled by {ctx.author}")
            
            embed = discord.Embed(
                title="🔴 Entering Maintenance Mode",
                description="Bot is shutting down and entering maintenance mode.\n\n**To bring the bot back online, use:** `!ping`",
                color=0xff0000,
                timestamp=datetime.utcnow()
            )
            embed.add_field(
                name="ℹ️ What This Means",
                value="• Bot will restart automatically but stay dormant\n• No commands will work until you use `!ping`\n• This prevents accidental activity during downtime",
                inline=False
            )
            await ctx.send(embed=embed)
            self.logger.info(f"Bot shutdown with maintenance mode initiated by {ctx.author}")
            await self.bot.close()
            
        except Exception as e:
            self.logger.error(f"Error during shutdown: {e}")
            # Fallback to regular shutdown if database fails
            embed = discord.Embed(
                title="🔴 Shutting Down",
                description="Bot is shutting down...",
                color=0xff0000,
                timestamp=datetime.utcnow()
            )
            await ctx.send(embed=embed)
            await self.bot.close()
    
    @commands.command(name='resetmytwitch')
    @is_owner_or_trusted()
    async def reset_my_twitch(self, ctx):
        """Mark all gl_stoney Twitch tokens inactive so /twitchoauth creates a clean new grant with correct scopes."""
        try:
            from models import TwitchOAuthToken
            with DatabaseSession() as session:
                tokens = session.query(TwitchOAuthToken).filter(
                    TwitchOAuthToken.twitch_username == 'gl_stoney',
                    TwitchOAuthToken.is_active == True
                ).all()
                count = len(tokens)
                for t in tokens:
                    t.is_active = False
                session.commit()

            embed = discord.Embed(
                title="🔄 Twitch Tokens Reset",
                color=0xff9900,
                timestamp=datetime.utcnow()
            )
            embed.add_field(
                name="✅ Done",
                value=f"Marked **{count}** gl_stoney token(s) inactive.",
                inline=False
            )
            embed.add_field(
                name="📋 Next Steps",
                value=(
                    "1. Go to **[Twitch Connected Apps](https://www.twitch.tv/settings/connections)** "
                    "and **Disconnect** this bot app (so Twitch shows the full permissions screen again)\n"
                    "2. Run `/twitchoauth` in Discord and approve all the permissions\n"
                    "3. The bot will reconnect to IRC as **gl_stoney** automatically"
                ),
                inline=False
            )
            await ctx.send(embed=embed)
            self.logger.info(f"!resetmytwitch: marked {count} gl_stoney token(s) inactive by {ctx.author}")
        except Exception as e:
            self.logger.error(f"Error in !resetmytwitch: {e}")
            await ctx.send(f"❌ Error: {e}")

    @commands.command(name='maintenance')
    @is_owner_or_trusted()
    async def maintenance_toggle(self, ctx, state: str = ""):
        """Enable or disable maintenance mode. Usage: !maintenance on/off"""
        state = state.strip().lower()
        if state not in ('on', 'off', 'enable', 'disable', 'true', 'false'):
            await ctx.send("Usage: `!maintenance on` or `!maintenance off`")
            return
        
        enabled = state in ('on', 'enable', 'true')
        try:
            with DatabaseSession() as session:
                setting = session.query(SystemSettings).filter_by(
                    setting_key='maintenance_mode'
                ).first()
                if setting:
                    setting.is_enabled = enabled
                    setting.setting_value = 'true' if enabled else 'false'
                else:
                    setting = SystemSettings(
                        setting_key='maintenance_mode',
                        is_enabled=enabled,
                        setting_value='true' if enabled else 'false'
                    )
                    session.add(setting)
                session.commit()
            
            # Also update the in-memory maintenance manager cache
            from utils.maintenance_manager import maintenance_manager
            await maintenance_manager.set(enabled)
            
            status = "🔴 enabled" if enabled else "🟢 disabled"
            await ctx.send(f"✅ Maintenance mode {status}.")
            self.logger.info(f"Maintenance mode {'enabled' if enabled else 'disabled'} by {ctx.author}")
        except Exception as e:
            self.logger.error(f"maintenance toggle error: {e}", exc_info=True)
            await ctx.send(f"❌ Error: {e}")

    @commands.command(name='restart')
    @is_owner_or_trusted()
    async def restart_bot(self, ctx):
        """Restart the bot (Owner only)."""
        embed = discord.Embed(
            title="🔄 Restarting",
            description="Bot is restarting... Please wait.",
            color=0xffa500,
            timestamp=datetime.utcnow()
        )
        await ctx.send(embed=embed)
        self.logger.info(f"Bot restart initiated by {ctx.author}")
        # Note: In Replit, this will cause the workflow to restart automatically
        await self.bot.close()
    
    @commands.command(name='reload')
    @is_owner_or_trusted()
    async def reload_cog(self, ctx, cog_name: str = ""):
        """Reload a specific cog or all cogs (Owner only)."""
        if not cog_name:
            # Reload all cogs
            cogs = list(self.bot.extensions.keys())
            reloaded = []
            failed = []
            
            for cog in cogs:
                try:
                    await self.bot.reload_extension(cog)
                    reloaded.append(cog)
                except Exception as e:
                    failed.append(f"{cog}: {str(e)}")
            
            embed = discord.Embed(
                title="🔄 Cog Reload Results",
                color=0x00ff00 if not failed else 0xffa500,
                timestamp=datetime.utcnow()
            )
            
            if reloaded:
                embed.add_field(
                    name="✅ Successfully Reloaded",
                    value="\n".join(reloaded),
                    inline=False
                )
            
            if failed:
                embed.add_field(
                    name="❌ Failed to Reload",
                    value="\n".join(failed),
                    inline=False
                )
            
            await ctx.send(embed=embed)
        else:
            # Reload specific cog
            try:
                await self.bot.reload_extension(f'cogs.{cog_name}')
                embed = discord.Embed(
                    title="✅ Cog Reloaded",
                    description=f"Successfully reloaded `{cog_name}`",
                    color=0x00ff00
                )
            except Exception as e:
                embed = discord.Embed(
                    title="❌ Reload Failed",
                    description=f"Failed to reload `{cog_name}`: {str(e)}",
                    color=0xff0000
                )
            
            await ctx.send(embed=embed)
    
    @commands.command(name='sysinfo', aliases=['system'])
    @is_owner_or_trusted()
    async def system_info(self, ctx):
        """Display detailed system information (Owner only)."""
        # Get system stats
        cpu_percent = psutil.cpu_percent(interval=1)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        
        # Get process info
        process = psutil.Process()
        bot_memory = process.memory_info().rss / 1024 / 1024  # MB
        
        embed = discord.Embed(
            title="🖥️ System Information",
            color=0x00ff00,
            timestamp=datetime.utcnow()
        )
        
        embed.add_field(
            name="💾 Memory",
            value=f"**Total:** {memory.total // (1024**3)} GB\n"
                  f"**Used:** {memory.used // (1024**3)} GB ({memory.percent}%)\n"
                  f"**Bot Usage:** {bot_memory:.1f} MB",
            inline=True
        )
        
        embed.add_field(
            name="💿 CPU",
            value=f"**Usage:** {cpu_percent}%\n"
                  f"**Cores:** {psutil.cpu_count()}",
            inline=True
        )
        
        embed.add_field(
            name="💽 Disk",
            value=f"**Total:** {disk.total // (1024**3)} GB\n"
                  f"**Used:** {disk.used // (1024**3)} GB ({disk.percent}%)",
            inline=True
        )
        
        await ctx.send(embed=embed)
    
    @commands.command(name='viewlogs', aliases=['logs'])
    @is_owner_or_trusted()
    async def view_logs(self, ctx, lines: int = 20):
        """Display recent bot logs (Owner only)."""
        if lines > 50:
            lines = 50
        elif lines < 1:
            lines = 20
        
        try:
            # This would read from log files if they exist
            embed = discord.Embed(
                title="📋 Bot Logs",
                description=f"Recent {lines} log entries would be displayed here.\n"
                           "Log file reading not implemented in current setup.",
                color=0x00ff00,
                timestamp=datetime.utcnow()
            )
            await ctx.send(embed=embed)
        except Exception as e:
            await ctx.send(f"❌ Error retrieving logs: {str(e)}")
    
    @commands.command(name='setactivity')
    @is_owner_or_trusted()
    async def set_activity(self, ctx, activity_type: str, *, activity_name: str):
        """Set bot activity (Owner only) - Persists across restarts."""
        activity_types = {
            'playing': discord.ActivityType.playing,
            'watching': discord.ActivityType.watching,
            'listening': discord.ActivityType.listening,
            'streaming': discord.ActivityType.streaming
        }
        
        if activity_type.lower() not in activity_types:
            await ctx.send(f"❌ Invalid activity type. Use: {', '.join(activity_types.keys())}")
            return
        
        try:
            # Save to database for persistence
            from models import SystemSettings
            from database import DatabaseSession
            
            with DatabaseSession() as session:
                # Find or create custom_status setting
                custom_status_setting = session.query(SystemSettings).filter_by(
                    setting_key='custom_status'
                ).first()
                
                # Find or create activity_type setting
                activity_type_setting = session.query(SystemSettings).filter_by(
                    setting_key='activity_type'
                ).first()
                
                if not custom_status_setting:
                    custom_status_setting = SystemSettings(
                        setting_key='custom_status',
                        setting_value=activity_name,
                        is_enabled=True
                    )
                    session.add(custom_status_setting)
                else:
                    custom_status_setting.setting_value = activity_name
                    custom_status_setting.is_enabled = True
                
                if not activity_type_setting:
                    activity_type_setting = SystemSettings(
                        setting_key='activity_type',
                        setting_value=activity_type.lower(),
                        is_enabled=True
                    )
                    session.add(activity_type_setting)
                else:
                    activity_type_setting.setting_value = activity_type.lower()
                    activity_type_setting.is_enabled = True
                
                session.commit()
            
            # Apply the activity change
            activity = discord.Activity(
                type=activity_types[activity_type.lower()],
                name=activity_name
            )
            await self.bot.change_presence(activity=activity)
            
            embed = discord.Embed(
                title="✅ Activity Updated",
                description=f"Bot activity set to: **{activity_type.title()} {activity_name}**\n\n✨ **This will persist across restarts!**",
                color=0x00ff00
            )
            await ctx.send(embed=embed)
            self.logger.info(f"Activity changed by {ctx.author}: {activity_type} {activity_name} (saved to database)")
        except Exception as e:
            await ctx.send(f"❌ Failed to set activity: {str(e)}")
    
    @commands.command(name='clearactivity')
    @is_owner_or_trusted()
    async def clear_activity(self, ctx):
        """Clear custom bot activity and restore default (Owner only)."""
        try:
            from models import SystemSettings
            from database import DatabaseSession
            
            with DatabaseSession() as session:
                # Disable custom status settings
                custom_status_setting = session.query(SystemSettings).filter_by(
                    setting_key='custom_status'
                ).first()
                activity_type_setting = session.query(SystemSettings).filter_by(
                    setting_key='activity_type'
                ).first()
                
                if custom_status_setting:
                    custom_status_setting.is_enabled = False
                if activity_type_setting:
                    activity_type_setting.is_enabled = False
                
                session.commit()
            
            # Set back to default (member count)
            guild_count = len(self.bot.guilds)
            member_count = sum(len(guild.members) for guild in self.bot.guilds)
            
            await self.bot.change_presence(
                activity=discord.Activity(
                    type=discord.ActivityType.watching,
                    name=f"{member_count:,} members | {self.bot.config.COMMAND_PREFIX}help"
                ),
                status=discord.Status.online
            )
            
            embed = discord.Embed(
                title="✅ Activity Cleared",
                description=f"Bot activity reset to default:\n**Watching {member_count:,} members**",
                color=0x00ff00
            )
            await ctx.send(embed=embed)
            self.logger.info(f"Custom activity cleared by {ctx.author}, reverted to default")
        except Exception as e:
            await ctx.send(f"❌ Failed to clear activity: {str(e)}")
    
    @commands.command(name='setstatus')
    @is_owner_or_trusted()
    async def set_status(self, ctx, status: str):
        """Set bot status (Owner only)."""
        status_types = {
            'online': discord.Status.online,
            'idle': discord.Status.idle,
            'dnd': discord.Status.dnd,
            'invisible': discord.Status.invisible
        }
        
        if status.lower() not in status_types:
            await ctx.send(f"❌ Invalid status. Use: {', '.join(status_types.keys())}")
            return
        
        try:
            await self.bot.change_presence(status=status_types[status.lower()])
            
            embed = discord.Embed(
                title="✅ Status Updated",
                description=f"Bot status set to: **{status.title()}**",
                color=0x00ff00
            )
            await ctx.send(embed=embed)
            self.logger.info(f"Status changed by {ctx.author}: {status}")
        except Exception as e:
            await ctx.send(f"❌ Failed to set status: {str(e)}")
    
    @commands.command(name='setstatuschannel')
    @is_owner_or_trusted()
    async def set_status_channel(self, ctx, channel: discord.TextChannel = None):
        """Set (or clear) the channel where the bot posts a heartbeat every 4 hours.
        
        Usage:
          !setstatuschannel #channel   — enable heartbeat posts in that channel
          !setstatuschannel off        — disable heartbeat posts
        """
        try:
            with DatabaseSession() as session:
                setting = session.query(SystemSettings).filter_by(
                    setting_key='heartbeat_channel_id'
                ).first()

                # Handle "off" as text arg when no channel mention is given
                raw = ctx.message.content.split()
                if len(raw) > 1 and raw[1].lower() == 'off':
                    if setting:
                        setting.is_enabled = False
                        setting.setting_value = None
                        session.commit()
                    embed = discord.Embed(
                        title="💤 Heartbeat Disabled",
                        description="Bot will no longer post periodic status messages.",
                        color=0xff9900
                    )
                    await ctx.send(embed=embed)
                    return

                if not channel:
                    await ctx.send("❌ Mention a channel: `!setstatuschannel #channel` or use `!setstatuschannel off` to disable.")
                    return

                if setting:
                    setting.setting_value = str(channel.id)
                    setting.is_enabled = True
                else:
                    session.add(SystemSettings(
                        setting_key='heartbeat_channel_id',
                        setting_value=str(channel.id),
                        is_enabled=True
                    ))
                session.commit()

            embed = discord.Embed(
                title="💓 Heartbeat Channel Set",
                description=f"Bot will post a status update in {channel.mention} every **4 hours**.",
                color=0x00cc66
            )
            embed.add_field(
                name="What it shows",
                value="• Bot uptime\n• Twitch IRC connection status\n• Next scheduled train",
                inline=False
            )
            embed.set_footer(text="Use !setstatuschannel off to disable")
            await ctx.send(embed=embed)

        except Exception as e:
            self.logger.error(f"setstatuschannel error: {e}", exc_info=True)
            await ctx.send(f"❌ Error: {e}")

    @commands.command(name='setshoutoutchannel')
    @is_owner_or_trusted()
    async def set_shoutout_channel(self, ctx, channel: discord.TextChannel = None):
        """Set (or clear) the channel where Discord shoutout and raid alert embeds are posted.

        Usage:
          !setshoutoutchannel #channel   — enable Discord shoutout/raid posts in that channel
          !setshoutoutchannel off        — disable Discord shoutout/raid posts
        """
        try:
            raw = ctx.message.content.split()
            turning_off = len(raw) > 1 and raw[1].lower() == 'off'

            with DatabaseSession() as session:
                settings = session.query(NotificationSettings).filter_by(
                    guild_id=ctx.guild.id
                ).first()

                if turning_off:
                    if settings:
                        settings.shoutout_discord_channel_id = None
                        session.commit()
                    embed = discord.Embed(
                        title="🔕 Discord Shoutouts Disabled",
                        description="The bot will no longer post shoutout or raid alert embeds to Discord.",
                        color=0xff9900
                    )
                    await ctx.send(embed=embed)
                    return

                if not channel:
                    await ctx.send(
                        "❌ Mention a channel: `!setshoutoutchannel #channel` "
                        "or use `!setshoutoutchannel off` to disable."
                    )
                    return

                if settings:
                    settings.shoutout_discord_channel_id = channel.id
                else:
                    session.add(NotificationSettings(
                        guild_id=ctx.guild.id,
                        shoutout_discord_channel_id=channel.id
                    ))
                session.commit()

            embed = discord.Embed(
                title="📢 Discord Shoutout Channel Set",
                description=f"Shoutout and raid alert embeds will be posted in {channel.mention}.",
                color=0x00cc66
            )
            embed.add_field(
                name="What gets posted there",
                value=(
                    "• **👏 Auto-shoutout fired** — whenever the bot shouts someone out in Twitch chat\n"
                    "• **🏘️ Community member spotted** — when a linked Discord member appears in chat\n"
                    "• **🚀 Incoming raid alert** — when someone raids your stream"
                ),
                inline=False
            )
            embed.set_footer(text="Use !setshoutoutchannel off to disable")
            await ctx.send(embed=embed)

        except Exception as e:
            self.logger.error(f"setshoutoutchannel error: {e}", exc_info=True)
            await ctx.send(f"❌ Error: {e}")

    @commands.command(name='addupdate')
    @is_owner_or_trusted()
    async def add_update(self, ctx, category: str = None, *, update_info: str = None):
        """
        Add an update to the bot changelog (Admin only).
        Usage: !addupdate [category] [title | description]
        Categories: feature, bugfix, enhancement, performance, security, ui
        """
        try:
            # If no parameters provided, show interactive interface
            if not category:
                embed = discord.Embed(
                    title="📝 Add Bot Update",
                    description="Use the command with parameters or follow this format:",
                    color=0x9146ff
                )
                
                embed.add_field(
                    name="📋 Usage",
                    value="`!addupdate <category> <title> | <description>`",
                    inline=False
                )
                
                embed.add_field(
                    name="📂 Valid Categories", 
                    value="\n".join([f"{CATEGORY_EMOJIS[cat]} `{cat}`" for cat in VALID_CATEGORIES]),
                    inline=False
                )
                
                embed.add_field(
                    name="💡 Example",
                    value="`!addupdate feature New admin commands | Added comprehensive update management tools`",
                    inline=False
                )
                
                await ctx.send(embed=embed)
                return
            
            # Validate category
            category = category.lower()
            if category not in VALID_CATEGORIES:
                embed = discord.Embed(
                    title="❌ Invalid Category",
                    description=f"Category `{category}` is not valid.",
                    color=0xff0000
                )
                embed.add_field(
                    name="Valid Categories",
                    value="\n".join([f"{CATEGORY_EMOJIS[cat]} `{cat}`" for cat in VALID_CATEGORIES]),
                    inline=False
                )
                await ctx.send(embed=embed)
                return
            
            # Parse title and description
            if not update_info or '|' not in update_info:
                await ctx.send("❌ Please provide both title and description separated by `|`\nExample: `!addupdate feature New feature | Description of the feature`")
                return
            
            parts = update_info.split('|', 1)
            title = parts[0].strip()
            description = parts[1].strip() if len(parts) > 1 else ""
            
            if not title or not description:
                await ctx.send("❌ Both title and description are required.")
                return
            
            if len(title) > 100:
                await ctx.send("❌ Title must be 100 characters or less.")
                return
            
            if len(description) > 1000:
                await ctx.send("❌ Description must be 1000 characters or less.")
                return
            
            # Add the update
            new_update = await update_manager.add_update(
                title=title,
                description=description,
                category=category,
                guild_id=ctx.guild.id if ctx.guild else None,
                author_id=ctx.author.id,
                is_published=True
            )
            
            if new_update:
                embed = discord.Embed(
                    title="✅ Update Added Successfully",
                    color=0x00ff00,
                    timestamp=datetime.utcnow()
                )
                
                embed.add_field(
                    name="📋 Update Details",
                    value=f"**ID:** {new_update.id}\n"
                          f"**Category:** {CATEGORY_EMOJIS[category]} {category.title()}\n"
                          f"**Title:** {title}\n"
                          f"**Description:** {description}\n"
                          f"**Author:** {ctx.author.mention}\n"
                          f"**Published:** Yes",
                    inline=False
                )
                
                await ctx.send(embed=embed)
                self.logger.info(f"Update added by {ctx.author}: {title} (ID: {new_update.id})")
            else:
                await ctx.send("❌ Failed to add update. Please check the logs for details.")
                
        except Exception as e:
            self.logger.error(f"Error adding update: {e}")
            await ctx.send(f"❌ An error occurred while adding the update: {str(e)}")
    
    @commands.command(name='manageupdates')
    @is_owner_or_trusted()
    async def manage_updates(self, ctx, action: str = "view", update_id: int = None):
        """
        Manage existing bot updates (Admin only).
        Actions: view, publish, unpublish, delete, stats
        Usage: !manageupdates [action] [update_id]
        """
        try:
            action = action.lower()
            
            if action == "view":
                # Show recent unpublished updates
                unpublished = await update_manager.get_unpublished_updates()
                recent_published = await update_manager.get_recent_updates(days=7, published_only=True)
                
                embed = discord.Embed(
                    title="📋 Update Management",
                    description="Recent updates and management options",
                    color=0x9146ff,
                    timestamp=datetime.utcnow()
                )
                
                if unpublished:
                    unpublished_text = "\n".join([
                        f"**{update.id}:** {CATEGORY_EMOJIS[update.category]} {update.title[:50]}{'...' if len(update.title) > 50 else ''}"
                        for update in unpublished[:10]
                    ])
                    embed.add_field(
                        name="📝 Unpublished Updates",
                        value=unpublished_text,
                        inline=False
                    )
                else:
                    embed.add_field(
                        name="📝 Unpublished Updates",
                        value="No unpublished updates found.",
                        inline=False
                    )
                
                if recent_published:
                    published_text = "\n".join([
                        f"**{update.id}:** {CATEGORY_EMOJIS[update.category]} {update.title[:50]}{'...' if len(update.title) > 50 else ''}"
                        for update in recent_published[:5]
                    ])
                    embed.add_field(
                        name="✅ Recent Published (Last 7 days)",
                        value=published_text,
                        inline=False
                    )
                
                embed.add_field(
                    name="🔧 Management Commands",
                    value="• `!manageupdates stats` - View statistics\n"
                          "• `!manageupdates publish <id>` - Publish update\n"
                          "• `!manageupdates unpublish <id>` - Unpublish update\n"
                          "• `!manageupdates delete <id>` - Delete update",
                    inline=False
                )
                
                await ctx.send(embed=embed)
                
            elif action == "stats":
                # Show update statistics
                all_updates = await update_manager.get_recent_updates(days=30, published_only=False)
                published_updates = [u for u in all_updates if u.is_published]
                
                # Count by category
                category_counts = {}
                for update in all_updates:
                    category_counts[update.category] = category_counts.get(update.category, 0) + 1
                
                embed = discord.Embed(
                    title="📊 Update Statistics (Last 30 days)",
                    color=0x9146ff,
                    timestamp=datetime.utcnow()
                )
                
                embed.add_field(
                    name="📈 Overview",
                    value=f"**Total Updates:** {len(all_updates)}\n"
                          f"**Published:** {len(published_updates)}\n"
                          f"**Unpublished:** {len(all_updates) - len(published_updates)}",
                    inline=True
                )
                
                if category_counts:
                    category_text = "\n".join([
                        f"{CATEGORY_EMOJIS[cat]} **{cat.title()}:** {count}"
                        for cat, count in category_counts.items()
                    ])
                    embed.add_field(
                        name="📂 By Category",
                        value=category_text,
                        inline=True
                    )
                
                await ctx.send(embed=embed)
                
            elif action in ["publish", "unpublish", "delete"]:
                if not update_id:
                    await ctx.send(f"❌ Please provide an update ID for the {action} action.")
                    return
                
                # Get the update
                with DatabaseSession() as session:
                    update = session.query(BotUpdate).filter(BotUpdate.id == update_id).first()
                    if not update:
                        await ctx.send(f"❌ Update with ID {update_id} not found.")
                        return
                    
                    if action == "publish":
                        update.is_published = True
                        action_text = "published"
                        color = 0x00ff00
                    elif action == "unpublish":
                        update.is_published = False
                        action_text = "unpublished"
                        color = 0xffa500
                    elif action == "delete":
                        session.delete(update)
                        action_text = "deleted"
                        color = 0xff0000
                    
                    session.commit()
                
                embed = discord.Embed(
                    title=f"✅ Update {action_text.title()}",
                    description=f"Update **{update_id}** has been {action_text}.",
                    color=color
                )
                
                if action != "delete":
                    embed.add_field(
                        name="Update Details",
                        value=f"**Title:** {update.title}\n"
                              f"**Category:** {CATEGORY_EMOJIS[update.category]} {update.category.title()}",
                        inline=False
                    )
                
                await ctx.send(embed=embed)
                self.logger.info(f"Update {update_id} {action_text} by {ctx.author}")
                
            else:
                await ctx.send("❌ Invalid action. Use: view, publish, unpublish, delete, or stats")
                
        except Exception as e:
            self.logger.error(f"Error managing updates: {e}")
            await ctx.send(f"❌ An error occurred: {str(e)}")
    
    
    @commands.command(name='dbclean')
    @is_owner_or_trusted()
    async def clean_database(self, ctx, days: int = 30):
        """Clean old database entries (Owner only)."""
        if not self.bot.db_manager:
            await ctx.send("❌ Database is not connected")
            return
        
        if days < 1 or days > 365:
            await ctx.send("❌ Days must be between 1 and 365")
            return
        
        try:
            cutoff_date = datetime.utcnow() - timedelta(days=days)
            
            with DatabaseSession() as session:
                # Clean old messages
                old_messages = session.query(Message).filter(Message.timestamp < cutoff_date).count()
                session.query(Message).filter(Message.timestamp < cutoff_date).delete()
                
                # Clean old command logs
                old_commands = session.query(CommandLog).filter(CommandLog.timestamp < cutoff_date).count()
                session.query(CommandLog).filter(CommandLog.timestamp < cutoff_date).delete()
                
                session.commit()
            
            embed = discord.Embed(
                title="🧹 Database Cleaned",
                description=f"Removed entries older than {days} days:",
                color=0x00ff00,
                timestamp=datetime.utcnow()
            )
            
            embed.add_field(
                name="📊 Cleaned Data",
                value=f"**Messages:** {old_messages:,}\n**Commands:** {old_commands:,}",
                inline=False
            )
            
            await ctx.send(embed=embed)
            self.logger.info(f"Database cleaned by {ctx.author}: {old_messages} messages, {old_commands} commands")
            
        except Exception as e:
            await ctx.send(f"❌ Database cleaning failed: {str(e)}")
            self.logger.error(f"Database cleaning error: {e}")

    @commands.command(name='clearalltrainschedules', aliases=['cleartrains', 'removealltrains', 'clear_all_trains', 'nuclear_clear_all_trains'])
    @commands.guild_only()
    @commands.is_owner()
    async def clear_all_train_schedules(self, ctx):
        """Clear ALL train schedules for this server (Bot Owner only - DESTRUCTIVE)."""
        try:
            # Debug logging
            self.logger.info(f"🔍 Clear trains command - Guild: {ctx.guild.name} (ID: {ctx.guild.id})")
            
            with DatabaseSession() as session:
                # Count schedules first
                schedule_count = session.query(TrainSchedule).filter_by(guild_id=ctx.guild.id).count()
                self.logger.info(f"🔍 Found {schedule_count} schedules for guild {ctx.guild.id}")
                
                if schedule_count == 0:
                    embed = discord.Embed(
                        title="ℹ️ No Schedules Found",
                        description="There are no train schedules to remove for this server.",
                        color=0x3498db
                    )
                    await ctx.send(embed=embed)
                    return
                
                # Delete related data first to maintain referential integrity
                # 1. Delete Google Sheet syncs (they reference schedules)
                google_sync_count = 0
                try:
                    from models import GoogleSheetSync
                    google_sync_count = session.query(GoogleSheetSync).filter_by(guild_id=ctx.guild.id).delete()
                except ImportError:
                    pass
                
                # 2. Delete participants and notifications
                participant_count = session.query(TrainParticipant).filter_by(guild_id=ctx.guild.id).delete()
                notification_count = session.query(TrainNotification).filter_by(guild_id=ctx.guild.id).delete()
                
                # Delete train slot completions (if they exist)
                completion_count = 0
                try:
                    from models import TrainSlotCompletion
                    completion_count = session.query(TrainSlotCompletion).filter_by(guild_id=ctx.guild.id).delete()
                except ImportError:
                    # Table doesn't exist yet, skip
                    pass
                
                # Delete train schedules
                deleted_schedules = session.query(TrainSchedule).filter_by(guild_id=ctx.guild.id).delete()
                
                # Reset the sequence to start from 1 if all schedules are cleared
                all_schedules_count = session.query(TrainSchedule).count()
                if all_schedules_count == 0:
                    # Reset the ID sequence to start from 1
                    session.execute(text("ALTER SEQUENCE train_schedules_id_seq RESTART WITH 1;"))
                    self.logger.info("🔄 Reset train schedule ID sequence to start from 1")
                
                session.commit()
            
            embed = discord.Embed(
                title="🗑️ All Train Schedules Cleared!",
                description="Successfully removed all train schedules and related data for this server.",
                color=0x00ff00,
                timestamp=datetime.utcnow()
            )
            
            embed.add_field(
                name="📊 Removed Items",
                value=f"**Schedules:** {deleted_schedules}\n**Participants:** {participant_count}\n**Notifications:** {notification_count}\n**Completions:** {completion_count}\n**Google Sheet Syncs:** {google_sync_count}",
                inline=False
            )
            
            embed.add_field(
                name="✨ What's Next?",
                value="You can now create new train schedules using:\n• `!setuptrainschedule` for quick setup\n• `!createschedule` for interactive creation\n\n🔄 **ID Reset:** New schedules will start from ID 1",
                inline=False
            )
            
            await ctx.send(embed=embed)
            self.logger.info(f"ALL train schedules cleared by {ctx.author} in {ctx.guild.name}: {deleted_schedules} schedules, {participant_count} participants, {notification_count} notifications, {completion_count} completions, {google_sync_count} Google syncs")
            
        except Exception as e:
            await ctx.send(f"❌ Failed to clear train schedules: {str(e)}")
            self.logger.error(f"Error clearing train schedules: {e}")
    
    @app_commands.command(name='clearparticipants', description='[Server Admin or Trusted] Clear all participants from all train schedules (keeps schedules intact)')
    @admin_or_trusted()
    async def clear_participants_slash(self, interaction: discord.Interaction):
        """Clear all participants from all train schedules while keeping the schedules intact."""
        await interaction.response.defer()
        
        try:
            with DatabaseSession() as session:
                # Get all schedules in this guild
                schedules = session.query(TrainSchedule).filter_by(
                    guild_id=interaction.guild.id
                ).all()
                
                if not schedules:
                    await interaction.followup.send(
                        "ℹ️ No train schedules found in this server.",
                        ephemeral=True
                    )
                    return
                
                # Get total participant count before clearing
                total_participants = session.query(TrainParticipant).filter_by(
                    guild_id=interaction.guild.id
                ).count()
                
                if total_participants == 0:
                    await interaction.followup.send(
                        "ℹ️ No participants to clear.",
                        ephemeral=True
                    )
                    return
                
                # Clear all participants for this guild
                session.query(TrainParticipant).filter_by(
                    guild_id=interaction.guild.id
                ).delete()
                
                # Clear all pending notifications for this guild
                notification_count = session.query(TrainNotification).filter_by(
                    guild_id=interaction.guild.id
                ).delete()
                
                # Reset participant count on all schedules
                for schedule in schedules:
                    schedule.participant_count = 0
                
                session.commit()
                
                # Invalidate cache
                try:
                    from utils.schedule_cache import get_cache
                    cache = get_cache()
                    cache.invalidate_timeslots(interaction.guild.id)
                    for schedule in schedules:
                        cache.invalidate_roster(schedule.id)
                except ImportError:
                    pass
            
            embed = discord.Embed(
                title="🔄 All Participants Cleared!",
                description=f"All participants have been removed from **{len(schedules)} schedule(s)**",
                color=0x00ff00,
                timestamp=datetime.utcnow()
            )
            
            embed.add_field(
                name="📊 Cleared",
                value=f"**Participants:** {total_participants}\n**Notifications:** {notification_count}\n**Schedules:** {len(schedules)}",
                inline=False
            )
            
            embed.add_field(
                name="✅ Schedule Status",
                value="All schedules are still active and ready for new participants to join!",
                inline=False
            )
            
            embed.set_footer(text=f"Cleared by {interaction.user.display_name}")
            
            await interaction.followup.send(embed=embed)
            self.logger.info(f"All participants cleared by {interaction.user} in {interaction.guild.name}: {total_participants} participants, {notification_count} notifications from {len(schedules)} schedules")
            
        except Exception as e:
            await interaction.followup.send(f"❌ Failed to clear participants: {str(e)}", ephemeral=True)
            self.logger.error(f"Error clearing participants: {e}")
    
    @commands.command(name='clearparticipants', aliases=['clearparts', 'resetparticipants', 'resetparts'])
    @commands.guild_only()
    @is_owner_or_trusted()
    async def clear_participants_prefix(self, ctx):
        """Clear all participants from all train schedules (keeps schedules intact)."""
        try:
            with DatabaseSession() as session:
                # Get all schedules in this guild
                schedules = session.query(TrainSchedule).filter_by(
                    guild_id=ctx.guild.id
                ).all()
                
                if not schedules:
                    await ctx.send("ℹ️ No train schedules found in this server.")
                    return
                
                # Get total participant count before clearing
                total_participants = session.query(TrainParticipant).filter_by(
                    guild_id=ctx.guild.id
                ).count()
                
                if total_participants == 0:
                    await ctx.send("ℹ️ No participants to clear.")
                    return
                
                # Clear all participants for this guild
                session.query(TrainParticipant).filter_by(
                    guild_id=ctx.guild.id
                ).delete()
                
                # Clear all pending notifications for this guild
                notification_count = session.query(TrainNotification).filter_by(
                    guild_id=ctx.guild.id
                ).delete()
                
                # Reset participant count on all schedules
                for schedule in schedules:
                    schedule.participant_count = 0
                
                session.commit()
                
                # Invalidate cache
                try:
                    from utils.schedule_cache import get_cache
                    cache = get_cache()
                    cache.invalidate_timeslots(ctx.guild.id)
                    for schedule in schedules:
                        cache.invalidate_roster(schedule.id)
                except ImportError:
                    pass
            
            embed = discord.Embed(
                title="🔄 All Participants Cleared!",
                description=f"All participants have been removed from **{len(schedules)} schedule(s)**",
                color=0x00ff00,
                timestamp=datetime.utcnow()
            )
            
            embed.add_field(
                name="📊 Cleared",
                value=f"**Participants:** {total_participants}\n**Notifications:** {notification_count}\n**Schedules:** {len(schedules)}",
                inline=False
            )
            
            embed.add_field(
                name="✅ Schedule Status",
                value="All schedules are still active and ready for new participants to join!",
                inline=False
            )
            
            embed.set_footer(text=f"Cleared by {ctx.author.display_name}")
            
            await ctx.send(embed=embed)
            self.logger.info(f"All participants cleared by {ctx.author} in {ctx.guild.name}: {total_participants} participants, {notification_count} notifications from {len(schedules)} schedules")
            
        except Exception as e:
            await ctx.send(f"❌ Failed to clear participants: {str(e)}")
            self.logger.error(f"Error clearing participants: {e}")
    
    @commands.command(name='adminInvite', aliases=['ainvite'])
    @is_owner_or_trusted()
    async def create_invite(self, ctx):
        """Generate bot invite link (Owner only)."""
        # Required permissions for the bot
        permissions = discord.Permissions(
            read_messages=True,
            send_messages=True,
            embed_links=True,
            attach_files=True,
            read_message_history=True,
            use_external_emojis=True,
            add_reactions=True,
            manage_messages=True,  # For message forwarding
            view_channel=True
        )
        
        invite_url = discord.utils.oauth_url(
            self.bot.user.id,
            permissions=permissions,
            scopes=('bot', 'applications.commands')
        )
        
        embed = discord.Embed(
            title="🔗 Bot Invite Link",
            description="Use this link to add the bot to other servers:",
            color=0x00ff00,
            timestamp=datetime.utcnow()
        )
        
        embed.add_field(
            name="📋 Invite URL",
            value=f"[Click here to invite the bot]({invite_url})",
            inline=False
        )
        
        embed.add_field(
            name="🛡️ Required Permissions",
            value="• Read Messages\n• Send Messages\n• Embed Links\n• Attach Files\n• Read Message History\n• Use External Emojis\n• Add Reactions\n• Manage Messages\n• View Channel",
            inline=False
        )
        
        embed.add_field(
            name="⚙️ Setup Instructions",
            value="1. Click the invite link\n2. Select the server to add the bot\n3. Ensure all permissions are granted\n4. The bot will automatically start tracking the server",
            inline=False
        )
        
        embed.set_footer(text="Bot will automatically join and begin database tracking")
        
        await ctx.send(embed=embed)
        self.logger.info(f"Invite link generated by {ctx.author}")
    

    @commands.command(name='leaveserver')
    @is_owner_or_trusted()
    async def leave_server(self, ctx, guild_id: int):
        """Leave a specific server (Owner only)."""
        guild = self.bot.get_guild(guild_id)
        
        if not guild:
            embed = discord.Embed(
                title="❌ Server Not Found",
                description=f"Bot is not in a server with ID: {guild_id}",
                color=0xff0000
            )
            await ctx.send(embed=embed)
            return
        
        # Get server info before leaving
        guild_name = guild.name
        member_count = guild.member_count or 0
        
        try:
            # Update database to mark guild as inactive
            if self.bot.db_manager:
                with DatabaseSession() as session:
                    db_guild = session.query(Guild).filter(Guild.id == guild_id).first()
                    if db_guild:
                        db_guild.is_active = False
                        db_guild.left_at = datetime.utcnow()
                        session.commit()
            
            await guild.leave()
            
            embed = discord.Embed(
                title="✅ Left Server",
                description=f"Successfully left **{guild_name}**",
                color=0x00ff00,
                timestamp=datetime.utcnow()
            )
            
            embed.add_field(
                name="📊 Server Info",
                value=f"**Name:** {guild_name}\n**ID:** {guild_id}\n**Members:** {member_count:,}",
                inline=False
            )
            
            await ctx.send(embed=embed)
            self.logger.info(f"Left server {guild_name} ({guild_id}) by {ctx.author}")
            
        except Exception as e:
            embed = discord.Embed(
                title="❌ Error Leaving Server",
                description=f"Failed to leave server: {str(e)}",
                color=0xff0000
            )
            await ctx.send(embed=embed)
            self.logger.error(f"Error leaving server {guild_id}: {e}")

    # Update Channel Configuration Commands
    
    @commands.command(name='setupupdates')
    @is_owner_or_trusted()
    async def setup_updates(self, ctx, channel: discord.TextChannel = None, role: discord.Role = None):
        """
        Configure weekly update posting for this server (Admin only).
        Usage: !setupupdates <channel> [role]
        """
        if not ctx.guild:
            await ctx.send("❌ This command can only be used in a server.")
            return
        
        if not channel:
            # Show current configuration if no channel provided
            await self.show_update_config(ctx)
            return
        
        try:
            # Check bot permissions in the target channel
            bot_member = ctx.guild.get_member(self.bot.user.id)
            channel_perms = channel.permissions_for(bot_member)
            
            if not (channel_perms.send_messages and channel_perms.embed_links):
                await ctx.send(f"❌ I don't have permission to send messages and embeds in {channel.mention}.")
                return
            
            # Check if role is valid and pingable if provided
            if role and not role.mentionable:
                embed = discord.Embed(
                    title="⚠️ Role Warning",
                    description=f"The role {role.mention} is not mentionable. Updates will be posted without role pings.",
                    color=0xffa500
                )
                await ctx.send(embed=embed)
            
            # Save configuration to database
            with DatabaseSession() as session:
                # Check for existing config
                config = session.query(UpdateChannelConfig).filter(
                    UpdateChannelConfig.guild_id == ctx.guild.id
                ).first()
                
                if config:
                    # Update existing config
                    config.update_channel_id = channel.id
                    config.notify_role_id = role.id if role else None
                    config.configured_by = ctx.author.id
                    config.is_enabled = True
                    from models import get_est_time
                    config.updated_at = get_est_time()
                else:
                    # Create new config
                    from models import get_est_time
                    config = UpdateChannelConfig(
                        guild_id=ctx.guild.id,
                        update_channel_id=channel.id,
                        notify_role_id=role.id if role else None,
                        configured_by=ctx.author.id,
                        is_enabled=True
                    )
                    session.add(config)
                
                session.commit()
            
            # Success response
            embed = discord.Embed(
                title="✅ Weekly Updates Configured",
                description="Update posting has been set up successfully!",
                color=0x00ff00,
                timestamp=datetime.utcnow()
            )
            
            embed.add_field(
                name="📢 Update Channel",
                value=channel.mention,
                inline=True
            )
            
            embed.add_field(
                name="📅 Schedule",
                value="Sundays at 6:00 PM UK",
                inline=True
            )
            
            if role:
                embed.add_field(
                    name="🔔 Mention Role",
                    value=role.mention if role.mentionable else f"{role.mention} (not mentionable)",
                    inline=True
                )
            
            embed.add_field(
                name="⚙️ Additional Configuration",
                value="• Use `!updateschedule` to change posting time\n• Use `!disableupdates` to disable posting\n• Use `!updateconfig` to view current settings",
                inline=False
            )
            
            await ctx.send(embed=embed)
            self.logger.info(f"Update config set by {ctx.author} for guild {ctx.guild.id}: channel={channel.id}, role={role.id if role else None}")
            
        except Exception as e:
            self.logger.error(f"Error setting up updates: {e}")
            await ctx.send(f"❌ Failed to configure updates: {str(e)}")
    
    @commands.command(name='updateconfig')
    @is_owner_or_trusted()
    async def show_update_config(self, ctx):
        """Show current weekly update configuration for this server (Admin only)."""
        if not ctx.guild:
            await ctx.send("❌ This command can only be used in a server.")
            return
        
        try:
            with DatabaseSession() as session:
                config = session.query(UpdateChannelConfig).filter(
                    UpdateChannelConfig.guild_id == ctx.guild.id
                ).first()
                
                embed = discord.Embed(
                    title="⚙️ Weekly Update Configuration",
                    description=f"Configuration for **{ctx.guild.name}**",
                    color=0x9146ff,
                    timestamp=datetime.utcnow()
                )
                
                if not config:
                    embed.add_field(
                        name="📋 Status",
                        value="❌ Not configured",
                        inline=False
                    )
                    embed.add_field(
                        name="🛠️ Setup Instructions",
                        value="Use `!setupupdates #channel` to configure weekly update posting.",
                        inline=False
                    )
                else:
                    # Get channel and role objects
                    channel = self.bot.get_channel(config.update_channel_id)
                    role = ctx.guild.get_role(config.notify_role_id) if config.notify_role_id else None
                    
                    status = "✅ Enabled" if config.is_enabled else "❌ Disabled"
                    embed.add_field(name="📋 Status", value=status, inline=True)
                    
                    # Posting schedule
                    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
                    day_name = days[config.posting_day]
                    time_str = f"{config.posting_hour:02d}:00 UK"
                    embed.add_field(
                        name="📅 Schedule", 
                        value=f"{day_name}s at {time_str}",
                        inline=True
                    )
                    
                    # Channel info
                    if channel:
                        embed.add_field(
                            name="📢 Update Channel",
                            value=channel.mention,
                            inline=True
                        )
                    else:
                        embed.add_field(
                            name="📢 Update Channel",
                            value="❌ Channel not found",
                            inline=True
                        )
                    
                    # Role info
                    if role:
                        mentionable_status = "✅" if role.mentionable else "⚠️ (not mentionable)"
                        embed.add_field(
                            name="🔔 Mention Role",
                            value=f"{role.mention} {mentionable_status}",
                            inline=True
                        )
                    elif config.notify_role_id:
                        embed.add_field(
                            name="🔔 Mention Role",
                            value="❌ Role not found",
                            inline=True
                        )
                    else:
                        embed.add_field(
                            name="🔔 Mention Role",
                            value="None set",
                            inline=True
                        )
                    
                    # Additional settings
                    global_updates = "✅ Included" if config.include_global_updates else "❌ Excluded"
                    embed.add_field(
                        name="🌐 Global Updates",
                        value=global_updates,
                        inline=True
                    )
                    
                    # Configuration details
                    configured_user = self.bot.get_user(config.configured_by)
                    configured_by_text = configured_user.mention if configured_user else f"User ID: {config.configured_by}"
                    embed.add_field(
                        name="👤 Configured By",
                        value=configured_by_text,
                        inline=False
                    )
                    
                    embed.add_field(
                        name="📅 Last Updated",
                        value=f"<t:{int(config.updated_at.timestamp())}:R>",
                        inline=True
                    )
                
                embed.add_field(
                    name="🔧 Management Commands",
                    value="• `!setupupdates #channel [role]` - Configure channel and role\n"
                          "• `!updateschedule <day> <time>` - Change posting schedule\n"
                          "• `!enableupdates` / `!disableupdates` - Toggle posting\n"
                          "• `!testupdates weekly` - Preview weekly summary",
                    inline=False
                )
                
                await ctx.send(embed=embed)
                
        except Exception as e:
            self.logger.error(f"Error showing update config: {e}")
            await ctx.send(f"❌ Failed to retrieve configuration: {str(e)}")
    
    @commands.command(name='updateschedule')
    @is_owner_or_trusted()
    async def update_schedule(self, ctx, day: str = None, time: str = None):
        """
        Set custom posting schedule for weekly updates (Admin only).
        Usage: !updateschedule <day> <time>
        Example: !updateschedule Sunday 18:00
        """
        if not ctx.guild:
            await ctx.send("❌ This command can only be used in a server.")
            return
        
        if not day or not time:
            embed = discord.Embed(
                title="📅 Update Scheduling",
                description="Set when weekly updates should be posted.",
                color=0x9146ff
            )
            embed.add_field(
                name="📋 Usage",
                value="`!updateschedule <day> <time>`",
                inline=False
            )
            embed.add_field(
                name="📅 Valid Days",
                value="Monday, Tuesday, Wednesday, Thursday, Friday, Saturday, Sunday",
                inline=False
            )
            embed.add_field(
                name="🕐 Time Format",
                value="Use 24-hour format (HH:MM) in UK timezone\nExamples: `18:00`, `09:30`, `23:00`",
                inline=False
            )
            embed.add_field(
                name="💡 Example",
                value="`!updateschedule Sunday 18:00` - Every Sunday at 6:00 PM UK",
                inline=False
            )
            await ctx.send(embed=embed)
            return
        
        try:
            # Validate day
            days = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
            day_lower = day.lower()
            if day_lower not in days:
                await ctx.send("❌ Invalid day. Please use: Monday, Tuesday, Wednesday, Thursday, Friday, Saturday, or Sunday")
                return
            
            day_index = days.index(day_lower)
            
            # Validate time format
            try:
                hour, minute = map(int, time.split(':'))
                if not (0 <= hour <= 23) or not (0 <= minute <= 59):
                    raise ValueError("Invalid time range")
            except ValueError:
                await ctx.send("❌ Invalid time format. Use HH:MM in 24-hour format (00:00 to 23:59)")
                return
            
            # Check if configuration exists
            with DatabaseSession() as session:
                config = session.query(UpdateChannelConfig).filter(
                    UpdateChannelConfig.guild_id == ctx.guild.id
                ).first()
                
                if not config:
                    embed = discord.Embed(
                        title="❌ Configuration Not Found",
                        description="You need to set up update posting first.",
                        color=0xff0000
                    )
                    embed.add_field(
                        name="🛠️ Setup Required",
                        value="Use `!setupupdates #channel` to configure update posting before setting a schedule.",
                        inline=False
                    )
                    await ctx.send(embed=embed)
                    return
                
                # Update schedule
                config.posting_day = day_index
                config.posting_hour = hour
                from models import get_est_time
                config.updated_at = get_est_time()
                session.commit()
            
            # Success response
            day_name = days[day_index].title()
            time_str = f"{hour:02d}:{minute:02d}"
            
            embed = discord.Embed(
                title="✅ Schedule Updated",
                description=f"Weekly updates will now be posted every **{day_name}** at **{time_str} UK**.",
                color=0x00ff00,
                timestamp=datetime.utcnow()
            )
            
            embed.add_field(
                name="📅 New Schedule",
                value=f"**Day:** {day_name}\n**Time:** {time_str} UK",
                inline=True
            )
            
            embed.add_field(
                name="⏰ Next Post",
                value="The new schedule will take effect for the next weekly update.",
                inline=True
            )
            
            await ctx.send(embed=embed)
            self.logger.info(f"Update schedule changed by {ctx.author} for guild {ctx.guild.id}: {day_name} {time_str}")
            
        except Exception as e:
            self.logger.error(f"Error updating schedule: {e}")
            await ctx.send(f"❌ Failed to update schedule: {str(e)}")
    
    @commands.command(name='enableupdates')
    @is_owner_or_trusted()
    async def enable_updates(self, ctx):
        """Enable weekly update posting for this server (Admin only)."""
        if not ctx.guild:
            await ctx.send("❌ This command can only be used in a server.")
            return
        
        try:
            with DatabaseSession() as session:
                config = session.query(UpdateChannelConfig).filter(
                    UpdateChannelConfig.guild_id == ctx.guild.id
                ).first()
                
                if not config:
                    embed = discord.Embed(
                        title="❌ Configuration Not Found",
                        description="You need to set up update posting first.",
                        color=0xff0000
                    )
                    embed.add_field(
                        name="🛠️ Setup Required",
                        value="Use `!setupupdates #channel` to configure update posting.",
                        inline=False
                    )
                    await ctx.send(embed=embed)
                    return
                
                if config.is_enabled:
                    embed = discord.Embed(
                        title="ℹ️ Already Enabled",
                        description="Weekly update posting is already enabled for this server.",
                        color=0x9146ff
                    )
                    await ctx.send(embed=embed)
                    return
                
                # Enable updates
                config.is_enabled = True
                from models import get_est_time
                config.updated_at = get_est_time()
                session.commit()
            
            embed = discord.Embed(
                title="✅ Updates Enabled",
                description="Weekly update posting has been enabled for this server.",
                color=0x00ff00,
                timestamp=datetime.utcnow()
            )
            
            embed.add_field(
                name="📢 Channel",
                value=f"<#{config.update_channel_id}>",
                inline=True
            )
            
            days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
            day_name = days[config.posting_day]
            embed.add_field(
                name="📅 Schedule",
                value=f"{day_name}s at {config.posting_hour:02d}:00 UK",
                inline=True
            )
            
            await ctx.send(embed=embed)
            self.logger.info(f"Updates enabled by {ctx.author} for guild {ctx.guild.id}")
            
        except Exception as e:
            self.logger.error(f"Error enabling updates: {e}")
            await ctx.send(f"❌ Failed to enable updates: {str(e)}")
    
    @commands.command(name='disableupdates')
    @is_owner_or_trusted()
    async def disable_updates(self, ctx):
        """Disable weekly update posting for this server (Admin only)."""
        if not ctx.guild:
            await ctx.send("❌ This command can only be used in a server.")
            return
        
        try:
            with DatabaseSession() as session:
                config = session.query(UpdateChannelConfig).filter(
                    UpdateChannelConfig.guild_id == ctx.guild.id
                ).first()
                
                if not config:
                    embed = discord.Embed(
                        title="ℹ️ No Configuration",
                        description="Weekly update posting is not configured for this server.",
                        color=0x9146ff
                    )
                    await ctx.send(embed=embed)
                    return
                
                if not config.is_enabled:
                    embed = discord.Embed(
                        title="ℹ️ Already Disabled",
                        description="Weekly update posting is already disabled for this server.",
                        color=0x9146ff
                    )
                    await ctx.send(embed=embed)
                    return
                
                # Disable updates
                config.is_enabled = False
                from models import get_est_time
                config.updated_at = get_est_time()
                session.commit()
            
            embed = discord.Embed(
                title="❌ Updates Disabled",
                description="Weekly update posting has been disabled for this server.",
                color=0xffa500,
                timestamp=datetime.utcnow()
            )
            
            embed.add_field(
                name="🔄 Re-enable",
                value="Use `!enableupdates` to re-enable posting when ready.",
                inline=True
            )
            
            embed.add_field(
                name="⚙️ Configuration",
                value="Your settings are preserved and will be used when re-enabled.",
                inline=True
            )
            
            await ctx.send(embed=embed)
            self.logger.info(f"Updates disabled by {ctx.author} for guild {ctx.guild.id}")
            
        except Exception as e:
            self.logger.error(f"Error disabling updates: {e}")
            await ctx.send(f"❌ Failed to disable updates: {str(e)}")

    @app_commands.command(name='setuptrainpings', description='[Server Admin or Trusted] Set up train notification channel and ping role')
    @app_commands.describe(
        channel='The text channel where train notifications will be posted',
        role='The role to ping for train notifications (optional)'
    )
    @admin_or_trusted()
    async def setup_train_pings(self, interaction: discord.Interaction, channel: discord.TextChannel = None, role: discord.Role = None):
        """
        Set up channel and role for train ping notifications (Admin only).
        Usage: /setuptrainpings <channel> [role]
        """
        # Defer immediately to prevent timeout
        await interaction.response.defer(ephemeral=False)
        
        if not interaction.guild:
            embed = discord.Embed(
                title="❌ Server Only Command",
                description="This command can only be used in a server.",
                color=0xff0000
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return
            
        if not channel:
            # Show current configuration if no channel provided
            try:
                from models import NotificationSettings
                with DatabaseSession() as session:
                    settings = session.query(NotificationSettings).filter(
                        NotificationSettings.guild_id == interaction.guild.id
                    ).first()
                    
                    embed = discord.Embed(
                        title="🔔 Train Ping Configuration",
                        description="Current train notification ping settings",
                        color=0x9146ff,
                        timestamp=datetime.utcnow()
                    )
                    
                    if settings and settings.raid_notification_channel_id:
                        notification_channel = interaction.guild.get_channel(settings.raid_notification_channel_id)
                        ping_role = interaction.guild.get_role(settings.raid_ping_role_id) if settings.raid_ping_role_id else None
                        
                        if notification_channel:
                            embed.add_field(
                                name="📢 Notification Channel",
                                value=f"{notification_channel.mention}",
                                inline=True
                            )
                            embed.add_field(
                                name="✅ Status",
                                value="Configured and ready",
                                inline=True
                            )
                            
                            if ping_role:
                                embed.add_field(
                                    name="🔔 Ping Role",
                                    value=f"{ping_role.mention}",
                                    inline=True
                                )
                            else:
                                embed.add_field(
                                    name="🔔 Ping Role",
                                    value="None configured",
                                    inline=True
                                )
                        else:
                            embed.add_field(
                                name="⚠️ Notification Channel",
                                value="Channel not found (may have been deleted)",
                                inline=True
                            )
                    else:
                        embed.add_field(
                            name="❌ Configuration Status",
                            value="Not configured",
                            inline=False
                        )
                        embed.add_field(
                            name="🛠️ Setup Instructions",
                            value="Use `/setuptrainpings #channel-name @role` to configure train ping notifications.",
                            inline=False
                        )
                    
                    embed.add_field(
                        name="ℹ️ Usage Examples",
                        value="• `/setuptrainpings #train-notifications` - Set channel only\n"
                              "• `/setuptrainpings #train-notifications @Raiders` - Set channel and ping role\n"
                              "• `/setuptrainpings` - View current configuration",
                        inline=False
                    )
                    
                    embed.set_footer(
                        text=f"Requested by {interaction.user}",
                        icon_url=interaction.user.avatar.url if interaction.user.avatar else None
                    )
                    
                    await interaction.followup.send(embed=embed)
                    return
                    
            except Exception as e:
                self.logger.error(f"Error showing train ping config: {e}")
                embed = discord.Embed(
                    title="❌ Configuration Error",
                    description=f"Failed to retrieve current configuration: {str(e)}",
                    color=0xff0000
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
                return
        
        # Configure the channel and role
        try:
            # Check bot permissions in the target channel
            bot_member = interaction.guild.get_member(self.bot.user.id)
            channel_perms = channel.permissions_for(bot_member)
            
            if not (channel_perms.send_messages and channel_perms.embed_links):
                embed = discord.Embed(
                    title="❌ Permission Error",
                    description=f"I don't have permission to send messages and embeds in {channel.mention}.",
                    color=0xff0000
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
                return
            
            # Check if role is valid and mentionable if provided
            role_warning = ""
            if role and not role.mentionable:
                role_warning = "\n⚠️ **Warning:** The selected role is not mentionable. Notifications will be posted without role pings."
            
            # Save configuration to database
            from models import NotificationSettings
            with DatabaseSession() as session:
                # Check for existing settings
                settings = session.query(NotificationSettings).filter(
                    NotificationSettings.guild_id == interaction.guild.id
                ).first()
                
                if settings:
                    # Update existing settings
                    settings.raid_notification_channel_id = channel.id
                    settings.raid_ping_role_id = role.id if role else None
                    settings.updated_at = datetime.utcnow()
                else:
                    # Create new settings
                    settings = NotificationSettings(
                        guild_id=interaction.guild.id,
                        raid_notification_channel_id=channel.id,
                        raid_ping_role_id=role.id if role else None,
                        created_at=datetime.utcnow(),
                        updated_at=datetime.utcnow()
                    )
                    session.add(settings)
                
                session.commit()
            
            # Success response
            embed = discord.Embed(
                title="✅ Train Pings Configured",
                description=f"Train notification pings have been set up successfully!{role_warning}",
                color=0x00ff00,
                timestamp=datetime.utcnow()
            )
            
            embed.add_field(
                name="📢 Notification Channel",
                value=channel.mention,
                inline=True
            )
            
            if role:
                embed.add_field(
                    name="🔔 Ping Role",
                    value=role.mention if role.mentionable else f"{role.mention} (not mentionable)",
                    inline=True
                )
            else:
                embed.add_field(
                    name="🔔 Ping Role",
                    value="None - notifications without pings",
                    inline=True
                )
            
            embed.add_field(
                name="📋 What This Does",
                value="• Configures where train notifications are sent\n"
                      "• Sets which role gets pinged for train events\n"
                      "• Used by train signup and notification systems",
                inline=False
            )
            
            embed.add_field(
                name="⚙️ Additional Configuration",
                value="• Use `/setuptrainpings` again to view or change settings\n"
                      "• Use `/setattendancechannel` for attendance notifications\n"
                      "• Use `!setupupdates` for weekly bot updates",
                inline=False
            )
            
            embed.set_footer(
                text=f"Configured by {interaction.user}",
                icon_url=interaction.user.avatar.url if interaction.user.avatar else None
            )
            
            await interaction.followup.send(embed=embed)
            self.logger.info(f"Train ping configuration updated by {interaction.user} in {interaction.guild.name}: Channel={channel.name}, Role={role.name if role else 'None'}")
            
        except Exception as e:
            self.logger.error(f"Error setting up train pings: {e}")
            embed = discord.Embed(
                title="❌ Configuration Failed",
                description=f"Failed to set up train ping configuration: {str(e)}",
                color=0xff0000
            )
            await interaction.followup.send(embed=embed, ephemeral=True)



    # EVAL COMMAND REMOVED FOR SECURITY
    # The eval command was removed due to critical security vulnerability
    # It allowed arbitrary code execution which poses extreme risk if accounts are compromised
    
    def has_start_stop_permission(self, user):
        """Check if user has permission to use start/stop commands."""
        # Owner always has permission
        if user.id == int(os.getenv('OWNER_ID_DISCORD', '887354716751810560')):
            return True
            
        # Check if user has "Manage messages" permission
        if hasattr(user, 'guild_permissions') and user.guild_permissions.manage_messages:
            return True
        
        # Check if user is in authorized user IDs (if the list exists)
        if hasattr(self.bot, 'authorized_user_ids') and user.id in self.bot.authorized_user_ids:
            return True
            
        return False
    

    @commands.command(name='linkusertwitch')
    @commands.guild_only()
    @is_owner_or_trusted()
    async def link_user_twitch(self, ctx, user: discord.Member, *, twitch_username: str):
        """
        Request user consent to link their Discord account to a Twitch account (Admin only).
        Usage: !linkusertwitch @user twitch_username
        """
        try:
            # Validate Twitch username format (basic validation)
            if not twitch_username.replace('_', '').isalnum():
                embed = discord.Embed(
                    title="❌ Invalid Twitch Username",
                    description="Twitch usernames can only contain letters, numbers, and underscores.",
                    color=0xff0000
                )
                await ctx.send(embed=embed)
                return
            
            if len(twitch_username) > 25:  # Twitch username limit
                embed = discord.Embed(
                    title="❌ Invalid Twitch Username",
                    description="Twitch usernames must be 25 characters or less.",
                    color=0xff0000
                )
                await ctx.send(embed=embed)
                return
            
            with DatabaseSession() as session:
                # Check if this Twitch username is already linked to another Discord user
                existing_link = session.query(User).filter(
                    User.twitch_login == twitch_username.lower(),
                    User.id != user.id  # Exclude current user if re-linking
                ).first()
                
                if existing_link:
                    embed = discord.Embed(
                        title="❌ Twitch Username Already Linked",
                        description=f"The Twitch username `{twitch_username}` is already linked to another Discord user.",
                        color=0xff0000
                    )
                    embed.add_field(
                        name="Linked User",
                        value=f"<@{existing_link.id}> ({existing_link.username})",
                        inline=False
                    )
                    await ctx.send(embed=embed)
                    return
                
                # Check if there's already a pending request for this user
                existing_request = session.query(TwitchLinkRequest).filter_by(
                    target_user_id=user.id,
                    status='pending'
                ).first()
                
                if existing_request:
                    embed = discord.Embed(
                        title="⏳ Pending Request Exists",
                        description=f"{user.mention} already has a pending Twitch linking request. Would you like to replace it with the new one?",
                        color=0xffa500
                    )
                    embed.add_field(
                        name="Existing Request",
                        value=f"**Twitch Username:** `{existing_request.requested_twitch_username}`\n**Requested:** <t:{int(existing_request.request_sent_at.timestamp())}:R>\n**Expires:** <t:{int(existing_request.expires_at.timestamp())}:R>",
                        inline=False
                    )
                    embed.add_field(
                        name="New Request",
                        value=f"**Twitch Username:** `{twitch_username}`",
                        inline=False
                    )
                    view = ReplaceLinkRequestView(existing_request.id, user, twitch_username, ctx, self.bot)
                    await ctx.send(embed=embed, view=view)
                    return
                
                # Create new consent request
                expires_at = datetime.utcnow() + timedelta(hours=24)  # 24 hour expiry
                link_request = TwitchLinkRequest(
                    target_user_id=user.id,
                    target_username=user.name,
                    target_display_name=user.display_name or user.name,
                    requested_twitch_username=twitch_username,
                    guild_id=ctx.guild.id if ctx.guild else None,
                    admin_user_id=ctx.author.id,
                    admin_username=ctx.author.name,
                    expires_at=expires_at,
                    status='pending'
                )
                
                session.add(link_request)
                session.commit()
                session.refresh(link_request)  # Get the ID
                
                # Send consent request DM to user
                consent_embed = discord.Embed(
                    title="🔗 Twitch Account Linking Request",
                    description=f"**{ctx.author.display_name}** would like to link your Discord account to your Twitch account for attendance tracking in raid trains.",
                    color=0x9146ff,
                    timestamp=datetime.utcnow()
                )
                
                consent_embed.add_field(
                    name="📋 Request Details",
                    value=f"**Admin:** {ctx.author.mention} (`{ctx.author.name}`)\n**Twitch Username:** `{twitch_username}`\n**Server:** {ctx.guild.name if ctx.guild else 'Direct Message'}",
                    inline=False
                )
                
                consent_embed.add_field(
                    name="🎯 What This Means",
                    value="• Your attendance for raid trains will be tracked automatically\n• You'll appear in raid train notifications with your Twitch username\n• Admins can see your participation in train statistics\n• Your Discord and Twitch accounts will be linked in our system",
                    inline=False
                )
                
                consent_embed.add_field(
                    name="🔒 Your Privacy",
                    value="• Only basic linking information is stored\n• You can deny this request if you prefer to keep accounts separate\n• This won't affect your ability to participate in Discord activities\n• You can request unlinking at any time",
                    inline=False
                )
                
                consent_embed.add_field(
                    name="🎮 Enable Auto-Raid (Optional)",
                    value="After accepting, run **/twitchoauth** in the Discord server so the bot can auto-start raids from your channel during trains.",
                    inline=False
                )
                
                consent_embed.add_field(
                    name="⏰ Response Time",
                    value=f"Please respond within 24 hours. This request expires <t:{int(expires_at.timestamp())}:R>.",
                    inline=False
                )
                
                consent_embed.set_footer(text="Only you can respond to this request")
                
                # Create the consent view with buttons
                view = TwitchLinkConsentView(link_request.id, user.id, self.bot)
                
                try:
                    # Send DM to user
                    dm_message = await user.send(embed=consent_embed, view=view)
                    
                    # Update request with message details
                    link_request.request_message_id = dm_message.id
                    link_request.request_channel_id = dm_message.channel.id
                    session.commit()
                    
                    # Send confirmation to admin
                    admin_embed = discord.Embed(
                        title="📨 Consent Request Sent",
                        description=f"A Twitch linking consent request has been sent to {user.mention}.",
                        color=0x00ff00,
                        timestamp=datetime.utcnow()
                    )
                    
                    admin_embed.add_field(
                        name="📋 Request Details",
                        value=f"**Target User:** {user.mention} (`{user.name}`)\n**Twitch Username:** `{twitch_username}`\n**Request ID:** `{link_request.id}`",
                        inline=False
                    )
                    
                    admin_embed.add_field(
                        name="⏳ Next Steps",
                        value=f"• The user has 24 hours to respond\n• You'll be notified when they make a decision\n• The request expires <t:{int(expires_at.timestamp())}:R>",
                        inline=False
                    )
                    
                    admin_embed.add_field(
                        name="ℹ️ Note",
                        value="The user will receive a private message with options to Allow or Deny the request. Linking will only occur if they explicitly approve.",
                        inline=False
                    )
                    
                    await ctx.send(embed=admin_embed)
                    self.logger.info(f"Twitch linking consent request sent to user {user.id} for account {twitch_username} by admin {ctx.author.id}")
                    
                except discord.Forbidden:
                    # User has DMs disabled - mark request as failed
                    link_request.status = 'failed'
                    link_request.dm_failed = True
                    link_request.error_message = "User has disabled direct messages"
                    session.commit()
                    
                    error_embed = discord.Embed(
                        title="❌ Cannot Send Direct Message",
                        description=f"Unable to send consent request to {user.mention} - they have disabled direct messages.",
                        color=0xff0000
                    )
                    error_embed.add_field(
                        name="🔧 Solutions",
                        value="• Ask the user to temporarily enable DMs from server members\n• Have the user contact you directly to confirm consent\n• Try again after they adjust their privacy settings",
                        inline=False
                    )
                    await ctx.send(embed=error_embed)
                    self.logger.warning(f"Could not send consent DM to user {user.id} - DMs disabled")
                    
        except Exception as e:
            self.logger.error(f"Error creating Twitch linking consent request: {e}")
            embed = discord.Embed(
                title="❌ Error Creating Consent Request",
                description=f"An error occurred while creating the consent request: {str(e)}",
                color=0xff0000
            )
            await ctx.send(embed=embed)
    
    @commands.command(name='notifyoauth', aliases=['sendraidauth', 'oauthblast'])
    @commands.guild_only()
    @is_owner_or_trusted()
    async def notify_oauth(self, ctx, *, flags: str = ''):
        """
        DM everyone with a linked Twitch account asking them to authorize with the bot.
        By default only DMs users with missing or expired tokens.
        Add --all to include users who already have a valid token.
        Usage: !notifyoauth [--all]
        """
        send_to_all = '--all' in flags.lower()

        try:
            status_msg = await ctx.send(
                f"⏳ Looking up linked users... ({'including already-authorized' if send_to_all else 'missing/expired tokens only'})"
            )

            # Fetch all data in a thread so we don't block
            def _fetch_users():
                from datetime import datetime as _dt
                now = _dt.utcnow()
                with DatabaseSession() as session:
                    linked = session.query(User).filter(
                        User.twitch_login.isnot(None),
                        User.twitch_login != ''
                    ).all()

                    # Build a map: twitch_login → token state
                    tokens = session.query(TwitchOAuthToken).filter_by(is_active=True).all()
                    token_map = {}
                    for t in tokens:
                        login = (t.twitch_username or '').lower()
                        if login:
                            expired = bool(t.expires_at and t.expires_at < now)
                            # Keep the "best" token per login (valid beats expired)
                            if login not in token_map or token_map[login] == 'expired':
                                token_map[login] = 'expired' if expired else 'valid'

                    results = []
                    seen_ids = set()
                    for u in linked:
                        if u.id in seen_ids:
                            continue
                        seen_ids.add(u.id)
                        login = (u.twitch_login or '').lower()
                        state = token_map.get(login, 'none')  # none / expired / valid
                        results.append((u.id, u.twitch_login, state))
                    return results

            users = await asyncio.to_thread(_fetch_users)

            # Filter based on flag
            to_dm = [
                (uid, login, state) for uid, login, state in users
                if send_to_all or state in ('none', 'expired')
            ]
            already_valid = [u for u in users if u[2] == 'valid']

            if not to_dm:
                await status_msg.edit(content=(
                    f"✅ All {len(already_valid)} linked users already have valid tokens! "
                    f"Use `!notifyoauth --all` to DM them anyway."
                ))
                return

            await status_msg.edit(content=(
                f"📨 Sending OAuth authorization DMs to **{len(to_dm)}** users "
                f"({'all linked' if send_to_all else 'missing/expired token'})...\n"
                f"Already authorized: {len(already_valid)} (skipped)"
            ))

            sent = 0
            failed_dm = 0
            failed_fetch = 0

            for discord_id, twitch_login, state in to_dm:
                reason = 'linked' if state == 'none' else 'reminder'
                ok = await send_oauth_dm(self.bot, discord_id, twitch_login, reason=reason)
                if ok:
                    sent += 1
                else:
                    # Distinguish between user not found vs DMs closed
                    user_obj = self.bot.get_user(discord_id)
                    if user_obj is None:
                        failed_fetch += 1
                    else:
                        failed_dm += 1
                # Rate-limit courtesy pause
                await asyncio.sleep(0.8)

            # Build summary
            breakdown_parts = []
            no_token = sum(1 for _, _, s in to_dm if s == 'none')
            expired = sum(1 for _, _, s in to_dm if s == 'expired')
            if no_token:
                breakdown_parts.append(f"**{no_token}** with no token")
            if expired:
                breakdown_parts.append(f"**{expired}** with expired token")
            if send_to_all and already_valid:
                breakdown_parts.append(f"**{len(already_valid)}** already valid (re-sent)")

            result_embed = discord.Embed(
                title="📊 OAuth Notification Results",
                color=0x00ff00 if failed_dm + failed_fetch == 0 else 0xffa500,
                timestamp=datetime.utcnow()
            )
            result_embed.add_field(name="✅ DMs Delivered", value=str(sent), inline=True)
            result_embed.add_field(name="❌ DMs Closed", value=str(failed_dm), inline=True)
            result_embed.add_field(name="👻 User Not Found", value=str(failed_fetch), inline=True)
            result_embed.add_field(name="⏭️ Skipped (valid token)", value=str(len(already_valid)) if not send_to_all else "0", inline=True)
            if breakdown_parts:
                result_embed.add_field(name="📋 Breakdown", value=" · ".join(breakdown_parts), inline=False)
            result_embed.set_footer(text=f"Run !notifyoauth --all to include already-authorized users")

            await ctx.send(embed=result_embed)

        except Exception as e:
            self.logger.error(f"Error in notifyoauth command: {e}", exc_info=True)
            await ctx.send(f"❌ Error: {str(e)}")

    @commands.command(name='sendlinkinfo')
    @commands.guild_only()
    @is_owner_or_trusted()
    async def send_link_info(self, ctx, channel: discord.TextChannel = None):
        """
        Send an informational embed about Twitch account linking to a channel.
        Usage: !sendlinkinfo [#channel]
        Defaults to raid train sign-ups channel (1183135069896966154) if no channel specified.
        """
        try:
            if channel is None:
                channel = self.bot.get_channel(1183135069896966154)
                if channel is None:
                    await ctx.send("❌ Could not find the raid train sign-ups channel.")
                    return

            embed = discord.Embed(
                title="🔗 Twitch Account Linking Guide",
                description=(
                    "To get the most out of raid trains, you'll want to link your Twitch account to Discord through the bot. "
                    "Here's everything you need to know!"
                ),
                color=0x9146ff,
                timestamp=datetime.utcnow()
            )

            embed.add_field(
                name="📋 What Is Account Linking?",
                value=(
                    "Linking connects your Discord account to your Twitch account in the bot's system. "
                    "This allows the bot to track your participation in raid trains, monitor your stream status, "
                    "and display your Twitch info in train notifications."
                ),
                inline=False
            )

            embed.add_field(
                name="✅ Required: Link Your Twitch Account",
                value=(
                    "**How:** Use the `/linktwitch` command\n"
                    "**Example:** `/linktwitch YourTwitchUsername`\n\n"
                    "This is **required** to participate in raid trains. "
                    "An admin will link your account, or you can self-link using the command above."
                ),
                inline=False
            )

            embed.add_field(
                name="🎯 What Happens When You Link",
                value=(
                    "✅ **Automatic attendance tracking** — The bot monitors Twitch chat to confirm you're present during trains\n"
                    "✅ **Real-time stream monitoring** — Your live status is tracked for raid coordination\n"
                    "✅ **Train notifications** — Your Twitch username appears in all train-related messages\n"
                    "✅ **Enhanced reports** — Attendance reports show your actual participation\n"
                    "✅ **Raid coordination** — `!raidnext` can direct raids to your channel"
                ),
                inline=False
            )

            embed.add_field(
                name="🎮 Optional: Authorize Auto-Raids",
                value=(
                    "After linking, you can **optionally** authorize the bot to automatically start raids from your channel.\n\n"
                    "**How:** Run **/twitchoauth** in the server — enter the code shown at **twitch.tv/activate**\n\n"
                    "**What it does:** When `!raidnext` is used in your Twitch chat during a train, "
                    "the bot automatically starts the raid to the next person — no manual clicking needed!\n\n"
                    "**Not required!** If you skip this, `!raidnext` will still announce who's next, "
                    "and you just start the raid manually."
                ),
                inline=False
            )

            embed.add_field(
                name="📝 Requirements Summary",
                value=(
                    "**Required:**\n"
                    "• A Twitch account\n"
                    "• Use `/linktwitch YourTwitchUsername` to link it\n\n"
                    "**Optional:**\n"
                    "• Authorize auto-raids via **/twitchoauth** (lets the bot raid for you automatically)"
                ),
                inline=False
            )

            embed.add_field(
                name="🔒 Privacy & Security",
                value=(
                    "• Linking only stores your Twitch username — no passwords or sensitive data\n"
                    "• Auto-raid authorization only grants raid permissions, nothing else\n"
                    "• You can revoke auto-raid access anytime via your [Twitch connections settings](https://www.twitch.tv/settings/connections)\n"
                    "• An admin can unlink your account at any time if needed"
                ),
                inline=False
            )

            embed.add_field(
                name="❓ Need Help?",
                value=(
                    "• **To link:** `/linktwitch YourTwitchUsername`\n"
                    "• **To check status:** `/linktwitch` (no username)\n"
                    "• **Problems?** Contact an admin for assistance"
                ),
                inline=False
            )

            embed.set_footer(text="Train Bot • Twitch Account Linking")
            embed.set_thumbnail(url="https://static-cdn.jtvnw.net/jtv_user_pictures/twitch-logo.png")

            await channel.send(embed=embed)
            await ctx.send(f"✅ Account linking info embed sent to {channel.mention}!")

        except Exception as e:
            self.logger.error(f"Error sending link info: {e}")
            await ctx.send(f"❌ Error: {str(e)}")

    @commands.command(name='outreachunlinked')
    @commands.guild_only()
    @is_owner_or_trusted()
    async def outreach_unlinked(self, ctx, schedule_id: int = None):
        """
        DM all train participants who haven't linked their Twitch account.
        Asks for their Twitch username and offers OAuth authorization.
        Usage: !outreachunlinked [schedule_id]
        If no schedule_id given, checks all active schedules for this server.
        """
        try:
            from utils.auto_link_helper import send_proactive_outreach_dm
            from models import TrainParticipant, TrainSchedule, User, TwitchLinkOutreach
            
            await ctx.send("Checking for unlinked participants...")
            
            guild_id = ctx.guild.id
            contacted = 0
            already_linked = 0
            already_contacted = 0
            failed = 0
            
            with DatabaseSession() as session:
                if schedule_id:
                    schedules = session.query(TrainSchedule).filter_by(
                        id=schedule_id, guild_id=guild_id, is_active=True
                    ).all()
                else:
                    schedules = session.query(TrainSchedule).filter_by(
                        guild_id=guild_id, is_active=True
                    ).all()
                
                if not schedules:
                    await ctx.send("No active schedules found.")
                    return
                
                users_to_contact = []
                
                for schedule in schedules:
                    participants = session.query(TrainParticipant).filter_by(
                        schedule_id=schedule.id, is_active=True
                    ).all()
                    
                    for p in participants:
                        user = session.query(User).filter(
                            User.id == p.user_id,
                            User.guild_id == guild_id
                        ).first()
                        
                        has_twitch = bool(p.twitch_username) or (user and user.twitch_login)
                        
                        if has_twitch:
                            already_linked += 1
                            continue
                        
                        recent = session.query(TwitchLinkOutreach).filter(
                            TwitchLinkOutreach.user_id == p.user_id,
                            TwitchLinkOutreach.guild_id == guild_id,
                            TwitchLinkOutreach.dm_sent_at > get_est_time() - timedelta(days=7)
                        ).first()
                        
                        if recent:
                            already_contacted += 1
                            continue
                        
                        seen_ids = {u['user_id'] for u in users_to_contact}
                        if p.user_id not in seen_ids:
                            users_to_contact.append({
                                'user_id': p.user_id,
                                'schedule_id': schedule.id
                            })
            
            for entry in users_to_contact:
                sent = await send_proactive_outreach_dm(
                    self.bot, entry['user_id'], guild_id, entry['schedule_id']
                )
                if sent:
                    contacted += 1
                else:
                    failed += 1
            
            embed = discord.Embed(
                title="Twitch Link Outreach Results",
                color=0x9146ff
            )
            embed.add_field(name="DMs Sent", value=str(contacted), inline=True)
            embed.add_field(name="Already Linked", value=str(already_linked), inline=True)
            embed.add_field(name="Recently Contacted", value=str(already_contacted), inline=True)
            if failed:
                embed.add_field(name="Failed (DMs closed)", value=str(failed), inline=True)
            
            await ctx.send(embed=embed)
            
        except Exception as e:
            self.logger.error(f"Error in outreach_unlinked: {e}", exc_info=True)
            await ctx.send(f"Error: {str(e)}")

    @commands.command(name='cancellinktwitch')
    @commands.guild_only()
    @is_owner_or_trusted()
    async def cancel_link_twitch(self, ctx, request_id: int):
        """
        Cancel a pending Twitch linking consent request by its request ID.
        Usage: !cancellinktwitch <request_id>
        """
        try:
            with DatabaseSession() as session:
                request = session.query(TwitchLinkRequest).filter_by(id=request_id, status='pending').first()
                if not request:
                    embed = discord.Embed(
                        title="❌ Request Not Found",
                        description=f"No pending Twitch link request found with ID `{request_id}`.",
                        color=0xff0000
                    )
                    await ctx.send(embed=embed)
                    return

                target_username = request.target_display_name or request.target_username
                twitch_username = request.requested_twitch_username
                session.delete(request)
                session.commit()

                embed = discord.Embed(
                    title="✅ Request Cancelled",
                    description=f"Pending Twitch link request **#{request_id}** has been deleted.",
                    color=0x00ff00
                )
                embed.add_field(name="Target User", value=target_username, inline=True)
                embed.add_field(name="Twitch Username", value=f"`{twitch_username}`", inline=True)
                await ctx.send(embed=embed)
        except Exception as e:
            self.logger.error(f"Error cancelling link request {request_id}: {e}")
            await ctx.send(f"❌ Error: {e}")

    @commands.command(name='unlinkusertwitch')
    @commands.guild_only()
    @is_owner_or_trusted()
    async def unlink_user_twitch(self, ctx, user: discord.Member):
        """
        Manually unlink a Discord user from their Twitch account (Admin only).
        Usage: !unlinkusertwitch @user
        """
        try:
            with DatabaseSession() as session:
                # Get User record
                db_user = session.query(User).filter_by(id=user.id).first()
                
                if not db_user or not db_user.twitch_login:
                    embed = discord.Embed(
                        title="❌ No Twitch Account Linked",
                        description=f"{user.mention} does not have a Twitch account linked.",
                        color=0xff0000
                    )
                    await ctx.send(embed=embed)
                    return
                
                # Store previous Twitch info for confirmation
                previous_twitch = db_user.twitch_login
                
                # Clear Twitch information
                db_user.twitch_id = None
                db_user.twitch_login = None
                db_user.twitch_display_name = None
                db_user.twitch_linked_at = None
                db_user.twitch_source = None
                db_user.twitch_consent = True  # Keep consent as True in case of re-linking
                db_user.last_seen = datetime.utcnow()
                
                # Auto-sync to clear Twitch username from train participant records
                from utils.twitch_sync import TwitchLinkSyncService
                participants_updated = TwitchLinkSyncService.sync_user_twitch(
                    session, user.id, ctx.guild.id, None
                )
                
                session.commit()
                
                # Create success embed
                embed = discord.Embed(
                    title="✅ Twitch Account Unlinked Successfully",
                    color=0x00ff00,
                    timestamp=datetime.utcnow()
                )
                
                embed.add_field(
                    name="👤 Discord User",
                    value=f"{user.mention} (`{user.name}`)",
                    inline=True
                )
                
                embed.add_field(
                    name="🎮 Unlinked Twitch",
                    value=f"`{previous_twitch}`",
                    inline=True
                )
                
                embed.add_field(
                    name="🔗 Action",
                    value="Admin Manual Unlink",
                    inline=True
                )
                
                if participants_updated > 0:
                    embed.add_field(
                        name="🎯 Train Participants Updated",
                        value=f"Cleared Twitch username from {participants_updated} existing train participant record(s)",
                        inline=False
                    )
                
                embed.add_field(
                    name="ℹ️ Note",
                    value="This user's Twitch account has been unlinked and will no longer appear in Twitch-related notifications.",
                    inline=False
                )
                
                await ctx.send(embed=embed)
                self.logger.info(f"Twitch account {previous_twitch} manually unlinked from Discord user {user.id} by {ctx.author}")
                
        except Exception as e:
            self.logger.error(f"Error unlinking Twitch account: {e}")
            embed = discord.Embed(
                title="❌ Error Unlinking Twitch Account",
                description=f"An error occurred while unlinking the Twitch account: {str(e)}",
                color=0xff0000
            )
            await ctx.send(embed=embed)
    
    @commands.command(name='forcelinktwitchbypass')
    @commands.guild_only()
    @is_owner_or_trusted()
    async def force_link_twitch_bypass(self, ctx, user: discord.Member, *, twitch_username: str):
        """
        Manually link a Twitch account when consent has been given but DMs are closed (Admin only).
        Use this when the user has given explicit consent through other means.
        Usage: !forcelinktwitchbypass @user twitch_username
        """
        try:
            # Validate Twitch username format (basic validation)
            if not twitch_username.replace('_', '').isalnum():
                embed = discord.Embed(
                    title="❌ Invalid Twitch Username",
                    description="Twitch usernames can only contain letters, numbers, and underscores.",
                    color=0xff0000
                )
                await ctx.send(embed=embed)
                return
            
            with DatabaseSession() as session:
                from models import TwitchLinkRequest
                
                # Check if there's a failed DM request for this user/username
                failed_request = session.query(TwitchLinkRequest).filter(
                    TwitchLinkRequest.target_user_id == user.id,
                    TwitchLinkRequest.requested_twitch_username.ilike(twitch_username),
                    TwitchLinkRequest.status == 'failed',
                    TwitchLinkRequest.dm_failed == True
                ).first()
                
                if not failed_request:
                    embed = discord.Embed(
                        title="⚠️ No Failed DM Request Found",
                        description=f"No failed DM request found for {user.mention} with Twitch username '{twitch_username}'.\n\nThis command is only for bypassing DM failures when consent has been obtained through other means.",
                        color=0xffa500
                    )
                    embed.add_field(
                        name="💡 Tip",
                        value="First use `!linkusertwitch @user twitch_username` to create the consent request. If it fails due to closed DMs, then use this bypass command.",
                        inline=False
                    )
                    await ctx.send(embed=embed)
                    return
                
                # Get or create the user record
                db_user = session.query(User).filter_by(id=user.id).first()
                if not db_user:
                    db_user = User(
                        id=user.id,
                        username=user.name,
                        display_name=user.display_name or user.name,
                        guild_id=ctx.guild.id
                    )
                    session.add(db_user)
                
                # Check if user already has a Twitch account linked
                if db_user.twitch_login:
                    embed = discord.Embed(
                        title="❌ Twitch Account Already Linked",
                        description=f"{user.mention} already has Twitch account **{db_user.twitch_login}** linked.",
                        color=0xff0000
                    )
                    embed.add_field(
                        name="🔧 Solution",
                        value=f"Use `!unlinkusertwitch {user.mention}` first to remove the existing link.",
                        inline=False
                    )
                    await ctx.send(embed=embed)
                    return
                
                # Check if Twitch username is already linked to someone else
                existing_twitch_user = session.query(User).filter_by(twitch_login=twitch_username.lower()).first()
                
                if existing_twitch_user:
                    existing_discord_user = ctx.guild.get_member(existing_twitch_user.id)
                    user_mention = existing_discord_user.mention if existing_discord_user else f"User ID {existing_twitch_user.id}"
                    
                    embed = discord.Embed(
                        title="❌ Twitch Username Already Linked",
                        description=f"Twitch account **{twitch_username}** is already linked to {user_mention}.",
                        color=0xff0000
                    )
                    await ctx.send(embed=embed)
                    return
                
                # Link the Twitch account to the user
                db_user.twitch_login = twitch_username.lower()
                db_user.twitch_display_name = twitch_username  # Use username as display name for now
                db_user.twitch_linked_at = get_est_time()
                db_user.twitch_source = 'admin_bypass'
                db_user.twitch_consent = True
                db_user.last_seen = get_est_time()
                
                # Update the failed request to approved with bypass note
                failed_request.status = 'approved'
                failed_request.responded_at = get_est_time()
                failed_request.response_message = f"Manually approved by admin {ctx.author} (DM bypass)"
                
                # Auto-sync Twitch username to train participant records
                from utils.twitch_sync import TwitchLinkSyncService
                participants_updated = TwitchLinkSyncService.sync_user_twitch(
                    session, user.id, ctx.guild.id, twitch_username.lower()
                )
                
                session.commit()
                
                # Send success confirmation
                embed = discord.Embed(
                    title="✅ Twitch Account Linked (Manual Bypass)",
                    description=f"Successfully linked {user.mention} to Twitch account **{twitch_username}**.",
                    color=0x00ff00,
                    timestamp=get_est_time()
                )
                
                embed.add_field(
                    name="👤 Discord User",
                    value=f"**Name:** {user.display_name}\n**ID:** {user.id}",
                    inline=True
                )
                
                embed.add_field(
                    name="🎮 Twitch Account",
                    value=f"**Username:** {twitch_username}\n**Status:** Active",
                    inline=True
                )
                
                embed.add_field(
                    name="⚠️ Manual Override",
                    value="This link was created manually bypassing the DM consent system. Ensure explicit user consent was obtained through other means.",
                    inline=False
                )
                
                embed.set_footer(text=f"Linked by {ctx.author}")
                await ctx.send(embed=embed)

                # DM the user their OAuth link so they can authorize immediately
                dm_sent = await send_oauth_dm(self.bot, user.id, twitch_username, reason='linked')
                if not dm_sent:
                    await ctx.send(
                        f"⚠️ Couldn't DM {user.mention} their OAuth link (DMs may be closed). "
                        f"Ask them to run `/twitchoauth` manually.",
                        delete_after=30
                    )

                self.logger.info(f"Twitch account {twitch_username} manually linked to Discord user {user.id} by admin {ctx.author.id} (DM bypass)")
                
        except Exception as e:
            self.logger.error(f"Error force linking Twitch account: {e}")
            embed = discord.Embed(
                title="❌ Error Force Linking Twitch Account",
                description=f"An error occurred while force linking the Twitch account: {str(e)}",
                color=0xff0000
            )
            await ctx.send(embed=embed)

    @commands.command(name='linkedusers')
    @commands.guild_only()
    @is_owner_or_trusted()
    async def list_linked_users(self, ctx):
        """
        Show a full list of all users with linked Twitch accounts (Admin only).
        Usage: !linkedusers
        """
        try:
            with DatabaseSession() as session:
                # Get all users with linked Twitch accounts
                linked_users = session.query(User).filter(
                    User.twitch_login.isnot(None),
                    User.twitch_login != ''
                ).order_by(User.display_name).all()
                
                if not linked_users:
                    embed = discord.Embed(
                        title="📋 Linked Users",
                        description="No users currently have Twitch accounts linked.",
                        color=0x667eea
                    )
                    await ctx.send(embed=embed)
                    return
                
                # Prepare user data for pagination
                user_data = []
                for user in linked_users:
                    # Get Discord user info if available
                    discord_user = ctx.guild.get_member(user.id)
                    if discord_user:
                        discord_name = discord_user.display_name
                        user_mention = discord_user.mention
                    else:
                        discord_name = user.display_name or user.username
                        user_mention = f"<@{user.id}>"
                    
                    # Format user entry
                    twitch_name = user.twitch_display_name or user.twitch_login
                    linked_date = user.twitch_linked_at.strftime('%m/%d/%y') if user.twitch_linked_at else 'Unknown'
                    source = user.twitch_source or 'unknown'
                    
                    user_data.append({
                        'mention': user_mention,
                        'discord_name': discord_name,
                        'twitch_name': twitch_name,
                        'linked_date': linked_date,
                        'source': source
                    })
                
                # Create pagination view
                view = LinkedUsersPaginationView(user_data, ctx.author)
                embed = view.create_embed()
                
                await ctx.send(embed=embed, view=view)
                self.logger.info(f"Listed {len(linked_users)} linked users for {ctx.author} in {ctx.guild.name}")
                
        except Exception as e:
            self.logger.error(f"Error listing linked users: {e}")
            embed = discord.Embed(
                title="❌ Error Listing Linked Users",
                description=f"An error occurred while retrieving linked users: {str(e)}",
                color=0xff0000
            )
            await ctx.send(embed=embed)

    @commands.command(name='testattendance')
    @commands.guild_only()
    @is_owner_or_trusted()
    async def test_attendance_notification(self, ctx, user: discord.Member = None):
        """
        Send a test attendance notification for a linked user (Admin only).
        Usage: !testattendance @user
        """
        try:
            if not user:
                embed = discord.Embed(
                    title="❌ Missing User",
                    description="Please specify a user to test attendance for.\n\nUsage: `!testattendance @user`",
                    color=0xff0000
                )
                await ctx.send(embed=embed)
                return
            
            with DatabaseSession() as session:
                from models import NotificationSettings
                
                # Check if user has linked Twitch account
                db_user = session.query(User).filter_by(id=user.id).first()
                if not db_user or not db_user.twitch_login:
                    embed = discord.Embed(
                        title="❌ No Linked Twitch Account",
                        description=f"{user.mention} doesn't have a linked Twitch account.",
                        color=0xff0000
                    )
                    await ctx.send(embed=embed)
                    return
                
                # Get attendance channel configuration
                settings = session.query(NotificationSettings).filter(
                    NotificationSettings.guild_id == ctx.guild.id
                ).first()
                
                if not settings or not settings.attendance_channel_id:
                    embed = discord.Embed(
                        title="❌ No Attendance Channel",
                        description="No attendance channel is configured for this server.",
                        color=0xff0000
                    )
                    await ctx.send(embed=embed)
                    return
                
                # Get the attendance channel
                channel = ctx.guild.get_channel(settings.attendance_channel_id)
                if not channel:
                    embed = discord.Embed(
                        title="❌ Channel Not Found",
                        description=f"Attendance channel (ID: {settings.attendance_channel_id}) not found.",
                        color=0xff0000
                    )
                    await ctx.send(embed=embed)
                    return
                
                # Check bot permissions
                bot_member = ctx.guild.get_member(self.bot.user.id)
                if not bot_member:
                    return
                    
                channel_perms = channel.permissions_for(bot_member)
                if not (channel_perms.send_messages and channel_perms.embed_links):
                    embed = discord.Embed(
                        title="❌ Insufficient Permissions",
                        description=f"Bot lacks permissions to send messages in {channel.mention}.",
                        color=0xff0000
                    )
                    await ctx.send(embed=embed)
                    return
                
                # Create test attendance notification
                attendance_embed = discord.Embed(
                    title="🚂 Train Attendance Report (TEST)",
                    description="**This shows how linked users appear in attendance reports**",
                    color=0x00ff00,
                    timestamp=get_est_time()
                )
                
                attendance_embed.add_field(
                    name="👤 Participant Type",
                    value=f"**Linked User:** {user.mention}\nDiscord: {user.display_name}\nTwitch: {db_user.twitch_login} ✅",
                    inline=False
                )
                
                attendance_embed.add_field(
                    name="🎮 Account Status",
                    value=f"✅ **Twitch Account Linked**\n🎯 Username: **{db_user.twitch_login}**\n📅 Linked: {db_user.twitch_linked_at.strftime('%m/%d/%Y') if db_user.twitch_linked_at else 'Unknown'}",
                    inline=True
                )
                
                attendance_embed.add_field(
                    name="🚂 Test Info",
                    value="**Schedule:** Test Schedule (#TEST)\n**Type:** Manual Test\n**Status:** Ready ✅",
                    inline=True
                )
                
                attendance_embed.add_field(
                    name="📊 How Attendance Works",
                    value="• **Linked Users** (like this): Show Discord + Twitch names ✅\n• **Chat Participants**: Show only Discord names\n• **Reactions**: Anyone can react, but only linked users get full tracking",
                    inline=False
                )
                
                attendance_embed.set_footer(text=f"TEST: Shows linked user format • Admin: {ctx.author}")
                
                # Send the test notification
                await channel.send(embed=attendance_embed)
                
                # Confirm to admin
                confirm_embed = discord.Embed(
                    title="✅ Test Attendance Sent",
                    description=f"Test attendance notification sent to {channel.mention} for {user.mention}.",
                    color=0x00ff00
                )
                await ctx.send(embed=confirm_embed)
                
                self.logger.info(f"Sent test attendance notification for {user} to {channel.name} in {ctx.guild.name}")
                
        except Exception as e:
            self.logger.error(f"Error sending test attendance notification: {e}")
            embed = discord.Embed(
                title="❌ Error Sending Test Attendance",
                description=f"An error occurred: {str(e)}",
                color=0xff0000
            )
            await ctx.send(embed=embed)

    @commands.command(name='checktwitch', aliases=['twitchcheck', 'checktw'])
    @commands.guild_only()
    async def check_twitch_connection(self, ctx, *, lookup: str = None):
        """
        Check Twitch connection status.
        No argument  → shows your own Twitch connection (anyone can use).
        With @mention / ID / twitch:<name> → detailed admin report (trusted users only).
        """
        # ── Helper: extract plain data from ORM objects inside the thread ───
        def _snap_user(u):
            if not u:
                return None
            return {
                'id': u.id,
                'username': u.username,
                'display_name': u.display_name,
                'twitch_login': u.twitch_login,
                'twitch_display_name': u.twitch_display_name,
                'twitch_id': getattr(u, 'twitch_id', None),
                'twitch_linked_at': u.twitch_linked_at,
                'twitch_source': getattr(u, 'twitch_source', None),
                'twitch_consent': bool(getattr(u, 'twitch_consent', False)),
                'twitch_consent_requested': bool(getattr(u, 'twitch_consent_requested', False)),
                'exclude_from_attendance': bool(getattr(u, 'exclude_from_attendance', False)),
                'first_seen': u.first_seen,
            }

        def _snap_token(t):
            if not t:
                return None
            return {
                'expires_at': t.expires_at,
                'scopes': list(t.scopes or []),
            }

        try:
            from models import User, TwitchOAuthToken
            import datetime as dt

            now = dt.datetime.now()

            # Show typing indicator immediately so the user knows the bot is working
            async with ctx.typing():

                # ── No argument: self-check (available to everyone) ──────────
                if not lookup:
                    def _fetch_self():
                        with DatabaseSession() as session:
                            # Try current guild first, then fall back to any guild
                            u = session.query(User).filter(
                                User.id == ctx.author.id,
                                User.guild_id == ctx.guild.id
                            ).first()
                            if not u:
                                u = session.query(User).filter(
                                    User.id == ctx.author.id
                                ).order_by(User.last_seen.desc()).first()
                            ud = _snap_user(u)
                            if ud:
                                t = session.query(TwitchOAuthToken).filter(
                                    TwitchOAuthToken.user_id == u.id,
                                    TwitchOAuthToken.is_active == True
                                ).order_by(TwitchOAuthToken.expires_at.desc()).first()
                                td = _snap_token(t)
                            else:
                                td = None
                            return ud, td

                    user_data, token_data = await asyncio.to_thread(_fetch_self)
                    await self._send_self_twitch_check(ctx, user_data, token_data, ctx.author, now)
                    return

                # ── With argument: detailed lookup (trusted/owner only) ──────
                is_privileged = await self.bot.is_owner_or_trusted(ctx.author)
                if not is_privileged:
                    await ctx.send(
                        "❌ You need trusted-user access to look up other people's Twitch connections.\n"
                        "Run `!checktwitch` without any arguments to check your own."
                    )
                    return

                target_member = None
                search_twitch_login = None

                if lookup.lower().startswith("twitch:"):
                    search_twitch_login = lookup[7:].strip().lower()
                else:
                    try:
                        target_member = await commands.MemberConverter().convert(ctx, lookup)
                    except commands.BadArgument:
                        try:
                            discord_id = int(lookup.strip())
                            target_member = ctx.guild.get_member(discord_id)
                            if not target_member:
                                target_member = await ctx.guild.fetch_member(discord_id)
                        except (ValueError, discord.NotFound, discord.HTTPException):
                            await ctx.send(
                                f"❌ Could not find a Discord member for `{lookup}`.\n"
                                "Tip: to search by Twitch username use `!checktwitch twitch:<username>`"
                            )
                            return

                # Fetch all needed DB data in a single thread call
                def _fetch_lookup():
                    with DatabaseSession() as session:
                        if search_twitch_login:
                            users = session.query(User).filter(
                                User.twitch_login.ilike(search_twitch_login)
                            ).all()
                            results = []
                            for u in users:
                                ud = _snap_user(u)
                                t = session.query(TwitchOAuthToken).filter(
                                    TwitchOAuthToken.user_id == u.id,
                                    TwitchOAuthToken.is_active == True
                                ).order_by(TwitchOAuthToken.expires_at.desc()).first()
                                results.append((ud, _snap_token(t)))
                            return results
                        else:
                            # Try current guild first, then fall back to any guild
                            u = session.query(User).filter(
                                User.id == target_member.id,
                                User.guild_id == ctx.guild.id
                            ).first()
                            found_in_other_guild = False
                            if not u:
                                u = session.query(User).filter(
                                    User.id == target_member.id
                                ).order_by(User.last_seen.desc()).first()
                                if u:
                                    found_in_other_guild = True
                            if not u:
                                return []
                            t = session.query(TwitchOAuthToken).filter(
                                TwitchOAuthToken.user_id == u.id,
                                TwitchOAuthToken.is_active == True
                            ).order_by(TwitchOAuthToken.expires_at.desc()).first()
                            ud = _snap_user(u)
                            if ud and found_in_other_guild:
                                ud['_found_in_other_guild'] = True
                                ud['_linked_guild_id'] = u.guild_id
                            return [(ud, _snap_token(t))]

                results = await asyncio.to_thread(_fetch_lookup)

                if not results:
                    label = search_twitch_login or (target_member.display_name if target_member else lookup)
                    embed = discord.Embed(
                        title="🔍 Twitch Account Check",
                        description=(
                            f"No bot user found with Twitch username **{label}**."
                            if search_twitch_login
                            else f"**{label}** has no record in this server's bot database."
                        ),
                        color=0xff4444
                    )
                    if target_member and not search_twitch_login:
                        embed.set_thumbnail(url=target_member.display_avatar.url)
                    await ctx.send(embed=embed)
                    return

                for user_data, token_data in results:
                    member = target_member if not search_twitch_login else None
                    await self._send_twitch_check_embed(ctx, user_data, token_data, now, member=member)

        except Exception as e:
            self.logger.error(f"Error in checktwitch command: {e}", exc_info=True)
            await ctx.send(f"❌ Error: {str(e)}")

    async def _send_self_twitch_check(self, ctx, user_data, token_data, member, now):
        """User-facing self-check embed — shows own Twitch connection status.
        Accepts plain data dicts (not ORM objects) so no DB session is needed here.
        """
        import datetime as dt

        if not user_data or not user_data.get('twitch_login'):
            embed = discord.Embed(
                title="🟣 Your Twitch Connection",
                description=(
                    "Your Discord account is **not linked** to a Twitch account on this bot.\n\n"
                    "To link your account, ask an admin to run:\n"
                    f"`!linkusertwitch @{member.display_name} your_twitch_username`"
                ),
                color=0xff4444,
                timestamp=dt.datetime.utcnow()
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            embed.set_footer(text="Use !checktwitch @user (admin only) to look up someone else")
            await ctx.send(embed=embed)
            return

        embed = discord.Embed(
            title="🟣 Your Twitch Connection",
            color=0x9146ff,
            timestamp=dt.datetime.utcnow()
        )
        embed.set_thumbnail(url=member.display_avatar.url)

        twitch_login = user_data['twitch_login']
        embed.add_field(
            name="✅ Twitch Account Linked",
            value=(
                f"**Twitch:** [{twitch_login}](https://twitch.tv/{twitch_login})\n"
                f"**Display name:** {user_data.get('twitch_display_name') or twitch_login}"
            ),
            inline=False
        )

        # Token check — plain language, no raw timestamps
        if token_data:
            expires_at = token_data.get('expires_at')
            expired = expires_at and expires_at < now
            if expired:
                token_line = "⚠️ Token **expired** — run `/twitchoauth` to refresh"
            elif expires_at:
                delta = expires_at - now
                hrs = int(delta.total_seconds() // 3600)
                token_line = f"✅ Token **valid** (expires in ~{hrs}h)"
            else:
                token_line = "✅ Token **active**"
        else:
            token_line = "❌ No OAuth token — attendance tracking won't work\nRun `/twitchoauth` to authorise"
            expires_at = None
            expired = True

        embed.add_field(name="🔑 OAuth Token", value=token_line, inline=False)

        # Features that depend on the link
        chat_monitor = getattr(self.bot, 'twitch_chat_monitor', None)
        irc_connected = chat_monitor and getattr(chat_monitor, 'is_connected', False)
        if chat_monitor:
            # Use either confirmed-joined OR expected (JOIN sent, awaiting server ack)
            confirmed = getattr(chat_monitor, 'joined_channels', set())
            expected  = getattr(chat_monitor, 'expected_channels', set())
            all_monitored = {c.lower() for c in (confirmed | expected)}
        else:
            all_monitored = set()
        in_chat = twitch_login.lower() in all_monitored
        token_ok = token_data and not (token_data.get('expires_at') and token_data['expires_at'] < now)

        # Distinguish between "IRC offline (temporary)" vs "genuinely not joined"
        if not chat_monitor:
            chat_line = "❌ Chat monitor not running"
        elif not irc_connected:
            chat_line = "⚠️ IRC temporarily offline — bot will rejoin your channel once reconnected"
        elif in_chat:
            chat_line = "✅ Bot is in your Twitch channel (always-on, not live-dependent)"
        else:
            chat_line = "❌ Bot is not in your Twitch channel"

        embed.add_field(
            name="⚙️ Active Features",
            value=(
                f"{chat_line}\n"
                f"{'✅' if token_ok else '❌'} Attendance tracking (active during trains)\n"
                "✅ Live role detection"
            ),
            inline=False
        )

        embed.set_footer(text="Admins: use !checktwitch @user for the full detailed report")
        await ctx.send(embed=embed)

    async def _send_twitch_check_embed(self, ctx, user_data, token_data, now, member=None):
        """Build and send the Twitch connection embed for a single user.
        Accepts plain data dicts (not ORM objects) so no DB session is needed here.
        """
        import datetime as dt

        is_linked = bool(user_data and user_data.get('twitch_login'))

        embed = discord.Embed(
            title="🔍 Twitch Account Check",
            color=0x9146ff if is_linked else 0xff4444,
            timestamp=dt.datetime.utcnow()
        )

        if member:
            embed.set_thumbnail(url=member.display_avatar.url)

        # Cross-guild note if the record was found in a different server
        if user_data.get('_found_in_other_guild'):
            linked_guild_id = user_data.get('_linked_guild_id')
            linked_guild = self.bot.get_guild(int(linked_guild_id)) if linked_guild_id else None
            guild_name = linked_guild.name if linked_guild else f"server ID {linked_guild_id}"
            embed.add_field(
                name="ℹ️ Found in another server",
                value=f"This user's record is linked in **{guild_name}**, not this server — showing their global profile.",
                inline=False
            )

        # Discord info
        embed.add_field(
            name="👤 Discord",
            value=(
                f"**Username:** {user_data.get('username', 'Unknown')}\n"
                f"**Display:** {user_data.get('display_name') or 'N/A'}\n"
                f"**ID:** `{user_data.get('id', '?')}`"
            ),
            inline=True
        )

        # Twitch connection
        if is_linked:
            twitch_login = user_data['twitch_login']
            linked_at = user_data.get('twitch_linked_at')
            linked_at_str = linked_at.strftime("%d %b %Y %H:%M") if linked_at else "Unknown"
            source_map = {
                'discord_oauth': 'Discord OAuth',
                'manual': 'Manually linked by admin',
                'consent': 'User consent flow',
                'twitch_oauth': 'Twitch OAuth',
            }
            source = source_map.get(user_data.get('twitch_source') or '', user_data.get('twitch_source') or 'Unknown')

            embed.add_field(
                name="🟣 Twitch — ✅ Connected",
                value=(
                    f"**Login:** [{twitch_login}](https://twitch.tv/{twitch_login})\n"
                    f"**Display:** {user_data.get('twitch_display_name') or twitch_login}\n"
                    f"**Twitch ID:** `{user_data.get('twitch_id') or 'Missing'}`\n"
                    f"**Linked:** {linked_at_str}\n"
                    f"**Method:** {source}"
                ),
                inline=True
            )

            # OAuth token from the pre-fetched data dict
            if token_data:
                expires_at = token_data.get('expires_at')
                is_expired = expires_at and expires_at < now
                expires_str = expires_at.strftime("%d %b %Y %H:%M") if expires_at else "No expiry stored"
                scopes = token_data.get('scopes') or []
                scopes_str = ", ".join(scopes[:5]) if scopes else "None stored"
                if len(scopes) > 5:
                    scopes_str += f" (+{len(scopes) - 5} more)"
                embed.add_field(
                    name=f"🔑 OAuth Token — {'⚠️ Expired' if is_expired else '✅ Valid'}",
                    value=f"**Expires:** {expires_str}\n**Scopes:** {scopes_str}",
                    inline=False
                )
            else:
                embed.add_field(
                    name="🔑 OAuth Token — ❌ None",
                    value="No active OAuth token. Chat monitoring may be limited.",
                    inline=False
                )
        else:
            embed.add_field(
                name="🟣 Twitch — ❌ Not Connected",
                value="This user has not linked a Twitch account.",
                inline=True
            )

        # Consent / request status
        consent_given = user_data.get('twitch_consent', False)
        consent_requested = user_data.get('twitch_consent_requested', False)
        if consent_given:
            consent_line = "✅ User has given consent to link their Twitch account"
        elif consent_requested:
            consent_line = "⏳ Consent **requested** — waiting for user to approve"
        else:
            consent_line = "❌ No consent given and none requested yet"

        # Live/IRC status — check both confirmed and expected (JOIN sent but not yet ack'd)
        chat_monitor = getattr(self.bot, 'twitch_chat_monitor', None)
        irc_connected = chat_monitor and getattr(chat_monitor, 'is_connected', False)
        if chat_monitor:
            confirmed = getattr(chat_monitor, 'joined_channels', set())
            expected  = getattr(chat_monitor, 'expected_channels', set())
            all_monitored = {c.lower() for c in (confirmed | expected)}
        else:
            all_monitored = set()
        twitch_login = user_data.get('twitch_login') or ''
        in_chat = bool(twitch_login) and twitch_login.lower() in all_monitored
        first_seen = user_data.get('first_seen')

        # Distinguish IRC offline from genuinely not joined
        if not chat_monitor:
            irc_status = "❌ Monitor not running"
        elif not irc_connected:
            irc_status = "⚠️ IRC offline (reconnecting)"
        elif in_chat:
            irc_status = "✅ Active"
        else:
            irc_status = "❌ Not joined"

        embed.add_field(
            name="📋 Status & Consent",
            value=(
                f"{consent_line}\n"
                f"**Exclude from attendance:** {'Yes' if user_data.get('exclude_from_attendance') else 'No'}\n"
                f"**IRC chat monitoring:** {irc_status}\n"
                f"**First seen:** {first_seen.strftime('%d %b %Y') if first_seen else 'Unknown'}"
            ),
            inline=False
        )

        await ctx.send(embed=embed)

    @commands.command(name='sendatreport')
    @commands.guild_only()
    @is_owner_or_trusted()
    async def send_attendance_report_test(self, ctx, schedule_id: int = None):
        """
        Manually trigger the attendance report for a specific schedule (Admin only).
        Usage: !sendatreport <schedule_id>
        Sends the post-session chat activity report to the attendance channel.
        """
        try:
            if not schedule_id:
                await ctx.send("❌ Please provide a schedule ID.\nUsage: `!sendatreport <schedule_id>`\n\nUse `/timeslots` to find schedule IDs.")
                return

            from models import TrainSchedule, TwitchChatAttendance
            import datetime as dt

            # Look up schedule and recent attendance date, then do everything in one session
            with DatabaseSession() as session:
                schedule = session.query(TrainSchedule).filter_by(id=schedule_id, guild_id=ctx.guild.id).first()
                if not schedule:
                    await ctx.send(f"❌ Schedule ID `{schedule_id}` not found in this server.")
                    return

                schedule_name = schedule.name

                # Find most recent attendance data date for this schedule
                recent_attendance = session.query(TwitchChatAttendance).filter_by(
                    schedule_id=schedule_id
                ).order_by(TwitchChatAttendance.train_date.desc()).first()

                test_date = recent_attendance.train_date if recent_attendance else dt.date.today()
                self.logger.info(f"!sendatreport: schedule={schedule_id} ({schedule_name}), using date={test_date}")

                status_msg = await ctx.send(f"⏳ Generating attendance report for **{schedule_name}** (date: {test_date})...")

                notification_cog = self.bot.cogs.get('NotificationCommands')
                if not notification_cog:
                    await status_msg.edit(content="❌ Notification system not available.")
                    return

                await notification_cog.post_session_attendance_summary(schedule, test_date, session)

            await status_msg.edit(content=f"✅ Done! Check your attendance channel for the report for **{schedule_name}**.")

        except Exception as e:
            self.logger.error(f"Error in sendatreport command: {e}", exc_info=True)
            await ctx.send(f"❌ Error: {str(e)}")

    @commands.command(name='quicktest')
    @commands.guild_only()
    @is_owner_or_trusted()
    async def quicktest_prefix(self, ctx, twitch_username: str = None, duration: int = 30):
        """
        Start attendance tracking on a Twitch channel for testing.
        Usage: !quicktest <twitch_username> [duration_minutes]
        Example: !quicktest rstone203 30
        """
        try:
            if not twitch_username:
                await ctx.send("❌ Please provide a Twitch username.\nUsage: `!quicktest <twitch_username> [duration_minutes]`\nExample: `!quicktest rstone203 30`")
                return

            if duration < 1 or duration > 120:
                await ctx.send("❌ Duration must be between 1 and 120 minutes.")
                return

            if not hasattr(self.bot, 'twitch_chat_monitor'):
                await ctx.send("❌ Twitch chat monitor is not available.")
                return

            monitor = self.bot.twitch_chat_monitor
            test_key = f"quicktest_{twitch_username.lower()}"

            if test_key in monitor.active_monitors:
                await ctx.send(f"❌ Already running a quick test for **{twitch_username}**. Stop it with `!stopquicktest {twitch_username}`.")
                return

            status_msg = await ctx.send(f"⏳ Starting attendance tracking on **{twitch_username}** for {duration} minutes...")

            guild_id = ctx.guild.id
            command_channel_id = ctx.channel.id

            async def _polling_loop():
                channel_name = twitch_username.lower()
                try:
                    await monitor.join_channel(channel_name)
                    monitor.chat_participants[channel_name] = set()
                    monitor.current_live_channels = [channel_name]

                    elapsed = 0
                    max_duration = duration * 60
                    poll_interval = 120

                    while elapsed < max_duration:
                        wait_time = min(poll_interval, max_duration - elapsed)
                        await asyncio.sleep(wait_time)
                        elapsed += wait_time

                        chatters = await monitor.get_chatters_from_api(channel_name, guild_id=guild_id)
                        if chatters:
                            monitor.chat_participants[channel_name].update(chatters)
                            monitor.current_cycle_chatters[channel_name] = chatters.copy()
                            self.logger.info(f"🧪 !quicktest poll: {len(chatters)} chatters in #{channel_name} (total: {len(monitor.chat_participants[channel_name])})")

                    # Build final report
                    all_chatters = monitor.chat_participants.get(channel_name, set())
                    viewer_count = await monitor.get_stream_viewer_count(channel_name)

                    embed = discord.Embed(
                        title="🏁 Quick Test — Final Attendance Report",
                        description=f"**Channel:** [{channel_name}](https://twitch.tv/{channel_name})\n**Duration:** {duration} minutes",
                        color=0x9146ff,
                        timestamp=datetime.utcnow()
                    )

                    if viewer_count and viewer_count > 0:
                        engagement = (len(all_chatters) / viewer_count * 100)
                        embed.add_field(
                            name="📊 Stats",
                            value=f"**Total Viewers:** {viewer_count:,}\n**Unique Chatters:** {len(all_chatters)}\n**Engagement:** {engagement:.1f}%",
                            inline=True
                        )
                    else:
                        embed.add_field(name="📊 Stats", value=f"**Unique Chatters:** {len(all_chatters)}", inline=True)

                    if all_chatters:
                        chatter_list = sorted(all_chatters, key=str.lower)
                        display = ', '.join(f'`{c}`' for c in chatter_list[:30])
                        if len(chatter_list) > 30:
                            display += f'\n_...and {len(chatter_list) - 30} more_'
                        embed.add_field(name=f"👥 Chatters ({len(all_chatters)})", value=display, inline=False)
                    else:
                        embed.add_field(name="👥 Chatters", value="None detected — stream may not have been live or no one chatted", inline=False)

                    embed.set_footer(text="Quick test complete")

                    # Send to attendance channel if configured, else command channel
                    sent = False
                    try:
                        from database import DatabaseSession
                        from models import NotificationSettings
                        with DatabaseSession() as session:
                            settings = session.query(NotificationSettings).filter_by(guild_id=guild_id).first()
                            if settings and settings.attendance_channel_id:
                                guild = self.bot.get_guild(guild_id)
                                att_ch = guild.get_channel(settings.attendance_channel_id) if guild else None
                                if att_ch:
                                    await att_ch.send(embed=embed)
                                    sent = True
                    except Exception as e:
                        self.logger.warning(f"Could not send to attendance channel: {e}")

                    if not sent:
                        guild = self.bot.get_guild(guild_id)
                        fallback = guild.get_channel(command_channel_id) if guild else None
                        if fallback:
                            await fallback.send(embed=embed)

                    self.logger.info(f"✅ !quicktest complete for #{channel_name} — {len(all_chatters)} unique chatters")

                except asyncio.CancelledError:
                    self.logger.info(f"⏹️ !quicktest for #{channel_name} cancelled")
                    guild = self.bot.get_guild(guild_id)
                    ch = guild.get_channel(command_channel_id) if guild else None
                    if ch:
                        await ch.send(f"🛑 Quick test for **{channel_name}** was stopped early.")
                except Exception as e:
                    self.logger.error(f"Error in !quicktest polling loop: {e}", exc_info=True)
                    guild = self.bot.get_guild(guild_id)
                    ch = guild.get_channel(command_channel_id) if guild else None
                    if ch:
                        await ch.send(f"❌ Error during quick test: {str(e)}")
                finally:
                    if test_key in monitor.active_monitors:
                        del monitor.active_monitors[test_key]

            task = asyncio.create_task(_polling_loop())
            monitor.active_monitors[test_key] = task

            await status_msg.edit(content=(
                f"✅ Tracking started on **{twitch_username}** for {duration} minutes!\n"
                f"• Polls chat every 2 minutes via Twitch API\n"
                f"• Report will post to your attendance channel when done\n"
                f"• Stop early with `!stopquicktest {twitch_username}`"
            ))

        except Exception as e:
            self.logger.error(f"Error in !quicktest command: {e}", exc_info=True)
            await ctx.send(f"❌ Error: {str(e)}")

    @commands.command(name='stopquicktest')
    @commands.guild_only()
    @is_owner_or_trusted()
    async def stopquicktest_prefix(self, ctx, twitch_username: str = None):
        """
        Stop a running !quicktest early.
        Usage: !stopquicktest <twitch_username>
        """
        try:
            if not twitch_username:
                await ctx.send("❌ Please provide a Twitch username.\nUsage: `!stopquicktest <twitch_username>`")
                return

            if not hasattr(self.bot, 'twitch_chat_monitor'):
                await ctx.send("❌ Twitch chat monitor is not available.")
                return

            monitor = self.bot.twitch_chat_monitor
            test_key = f"quicktest_{twitch_username.lower()}"

            if test_key not in monitor.active_monitors:
                await ctx.send(f"❌ No active quick test found for **{twitch_username}**.")
                return

            monitor.active_monitors[test_key].cancel()
            await ctx.send(f"🛑 Stopped quick test for **{twitch_username}**.")

        except Exception as e:
            self.logger.error(f"Error in !stopquicktest: {e}", exc_info=True)
            await ctx.send(f"❌ Error: {str(e)}")

    @commands.command(name='chatsnap')
    @commands.guild_only()
    @is_owner_or_trusted()
    async def chatsnap(self, ctx, twitch_username: str = None):
        """
        Instantly poll a Twitch channel's chatters and send a report right now.
        Usage: !chatsnap <twitch_username>
        Example: !chatsnap rstone203
        """
        try:
            if not twitch_username:
                await ctx.send("❌ Please provide a Twitch username.\nUsage: `!chatsnap <twitch_username>`\nExample: `!chatsnap rstone203`")
                return

            if not hasattr(self.bot, 'twitch_chat_monitor'):
                await ctx.send("❌ Twitch chat monitor is not available.")
                return

            monitor = self.bot.twitch_chat_monitor
            channel_name = twitch_username.lower()
            guild_id = ctx.guild.id

            status_msg = await ctx.send(f"📡 Polling **{twitch_username}** chat right now...")

            # Make sure we're in the channel
            await monitor.join_channel(channel_name)

            # Single API poll for current chatters
            chatters = await monitor.get_chatters_from_api(channel_name, guild_id=guild_id)
            viewer_count = await monitor.get_stream_viewer_count(channel_name)

            # Also include any in-memory accumulated participants
            accumulated = monitor.chat_participants.get(channel_name, set())
            all_seen = set(chatters or []) | accumulated

            embed = discord.Embed(
                title="📊 Chat Snapshot",
                description=f"**Channel:** [{channel_name}](https://twitch.tv/{channel_name})",
                color=0x9146ff,
                timestamp=datetime.utcnow()
            )

            if viewer_count and viewer_count > 0:
                in_chat = len(chatters) if chatters else 0
                engagement = (in_chat / viewer_count * 100)
                embed.add_field(
                    name="📈 Right Now",
                    value=f"**Viewers:** {viewer_count:,}\n**In Chat:** {in_chat}\n**Engagement:** {engagement:.1f}%",
                    inline=True
                )
            else:
                embed.add_field(
                    name="📈 Right Now",
                    value=f"**In Chat:** {len(chatters) if chatters else 0}\n_(Stream may be offline)_",
                    inline=True
                )

            if accumulated:
                embed.add_field(
                    name="🗃️ Session Total",
                    value=f"**Unique chatters seen:** {len(all_seen)}",
                    inline=True
                )

            if all_seen:
                chatter_list = sorted(all_seen, key=str.lower)
                display = ', '.join(f'`{c}`' for c in chatter_list[:40])
                if len(chatter_list) > 40:
                    display += f'\n_...and {len(chatter_list) - 40} more_'
                embed.add_field(name=f"👥 Chatters ({len(all_seen)})", value=display, inline=False)
            elif chatters is not None:
                embed.add_field(name="👥 Chatters", value="No chatters found — stream may be offline or chat is empty", inline=False)
            else:
                embed.add_field(name="👥 Chatters", value="⚠️ Could not reach Twitch API — check bot token", inline=False)

            embed.set_footer(text=f"Snapshot by {ctx.author}")
            await status_msg.edit(content=None, embed=embed)

        except Exception as e:
            self.logger.error(f"Error in !chatsnap: {e}", exc_info=True)
            await ctx.send(f"❌ Error: {str(e)}")

    @commands.command(name='testpermissions')
    @is_owner_or_trusted()
    async def test_permissions(self, ctx):
        """Test command to verify admin permissions are working."""
        await ctx.send("✅ Admin permissions are working! You can use admin commands.")
        self.logger.info(f"testpermissions command executed by {ctx.author} ({ctx.author.id})")

    @commands.command(name='testbasic')
    async def test_basic(self, ctx):
        """Basic test command without permissions to verify commands work."""
        await ctx.send("✅ Basic commands are working!")
        self.logger.info(f"testbasic command executed by {ctx.author} ({ctx.author.id})")

    @app_commands.command(name="refreshpersistent", description="[Server Admin or Trusted] Manually refresh persistent timeslots display")
    @admin_or_trusted()
    async def refresh_persistent(self, interaction: discord.Interaction):
        """Manually refresh the persistent timeslots display (Admin only)."""
        await interaction.response.defer(ephemeral=True)

        if not interaction.guild:
            await interaction.followup.send("❌ This command can only be used in a server.", ephemeral=True)
            return
        
        try:
            # Get the train participant commands cog to trigger the refresh
            train_cog = self.bot.get_cog('TrainParticipantCommands')
            if train_cog:
                await train_cog.trigger_persistent_updates(interaction.guild.id)
                
                embed = discord.Embed(
                    title="✅ Persistent Display Refreshed",
                    description=f"Successfully refreshed all persistent timeslots displays for {interaction.guild.name}.",
                    color=0x00ff00,
                    timestamp=datetime.utcnow()
                )
                embed.add_field(
                    name="📊 Updated",
                    value="• Participant information\n• Twitch link status\n• Discord usernames",
                    inline=False
                )
                
                await interaction.followup.send(embed=embed, ephemeral=True)
                self.logger.info(f"Persistent display manually refreshed by {interaction.user} in {interaction.guild.name}")
            else:
                await interaction.followup.send("❌ Train commands cog not found.", ephemeral=True)
                
        except Exception as e:
            self.logger.error(f"Error refreshing persistent display: {e}")
            embed = discord.Embed(
                title="❌ Refresh Failed",
                description=f"An error occurred while refreshing the display: {str(e)}",
                color=0xff0000
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

    # @app_commands.command(name="markcomplete", description="[Bot Owner or Trusted] Manually mark a train notification as complete")
    @app_commands.describe(
        train_name="Name of the train (e.g., 'Saturday Prime Time 1')",
        stage="Stage number (1, 2, 3, or 4). Leave blank to mark all stages complete."
    )
    @owner_or_trusted()
    async def _disabled_mark_notification_complete(self, interaction: discord.Interaction, train_name: str, stage: int = None):
        """Manually mark train notifications as complete when bot is down or for manual confirmations."""
        await interaction.response.defer(ephemeral=True)
        
        try:
            with DatabaseSession() as session:
                # Find the train schedule
                schedule = session.query(TrainSchedule).filter(
                    TrainSchedule.name.ilike(f"%{train_name}%"),
                    TrainSchedule.is_active == True
                ).first()
                
                if not schedule:
                    await interaction.followup.send(
                        f"❌ Could not find an active train matching '{train_name}'.\n"
                        f"Try using the exact train name (e.g., 'Saturday Prime Time 1').",
                        ephemeral=True
                    )
                    return
                
                # Get today's notifications for this train
                today = datetime.utcnow().date()
                query = session.query(TrainNotification).filter(
                    TrainNotification.schedule_id == schedule.id,
                    TrainNotification.notification_date >= today
                )
                
                # Filter by stage if specified
                if stage is not None:
                    query = query.filter(TrainNotification.stage == stage)
                
                notifications = query.all()
                
                if not notifications:
                    stage_text = f"stage {stage}" if stage else "any stage"
                    await interaction.followup.send(
                        f"❌ No active notifications found for '{schedule.name}' at {stage_text}.",
                        ephemeral=True
                    )
                    return
                
                # Mark notifications as complete
                updated_count = 0
                for notification in notifications:
                    if not notification.is_completed:
                        notification.is_completed = True
                        updated_count += 1
                
                session.commit()
                
                # Build confirmation embed
                embed = discord.Embed(
                    title="✅ Notifications Marked Complete",
                    description=f"Successfully marked notifications as complete for **{schedule.name}**",
                    color=0x00ff00,
                    timestamp=datetime.utcnow()
                )
                
                embed.add_field(
                    name="🚂 Train",
                    value=schedule.name,
                    inline=True
                )
                
                embed.add_field(
                    name="📊 Updated",
                    value=f"{updated_count} notification(s)",
                    inline=True
                )
                
                if stage is not None:
                    embed.add_field(
                        name="🎯 Stage",
                        value=f"Stage {stage}",
                        inline=True
                    )
                else:
                    embed.add_field(
                        name="🎯 Stages",
                        value="All stages",
                        inline=True
                    )
                
                embed.set_footer(text=f"Marked by {interaction.user.display_name}")
                
                await interaction.followup.send(embed=embed, ephemeral=True)
                self.logger.info(f"Manual notification completion: {schedule.name} - {updated_count} notifications marked complete by {interaction.user}")
                
        except Exception as e:
            self.logger.error(f"Error marking notification complete: {e}")
            embed = discord.Embed(
                title="❌ Error",
                description=f"An error occurred while marking notifications complete: {str(e)}",
                color=0xff0000
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

    @commands.command(name='trustrole', aliases=['addrole', 'trustedrole'])
    async def trust_role(self, ctx, role: discord.Role = None, *, notes: str = ""):
        """Add a role to the trusted roles list - members of this role get admin permissions (Owner only)."""
        self.logger.info(f"trustrole command invoked by {ctx.author} in {ctx.guild.name if ctx.guild else 'DM'}")
        
        if not await self.bot.is_owner(ctx.author):
            await ctx.send("❌ This command is restricted to the bot owner only.")
            self.logger.warning(f"trustrole denied - {ctx.author} is not owner")
            return
            
        if not ctx.guild:
            await ctx.send("❌ This command can only be used in a server.")
            return
            
        if not role:
            await ctx.send("❌ Please specify a role. Usage: `!trustrole @RoleName [notes]`")
            return
            
        try:
            with DatabaseSession() as session:
                # Check if role is already trusted
                existing = session.query(TrustedRole).filter(
                    TrustedRole.guild_id == ctx.guild.id,
                    TrustedRole.role_id == role.id,
                    TrustedRole.is_active == True
                ).first()
                
                if existing:
                    await ctx.send(f"❌ {role.mention} is already a trusted role.")
                    return
                
                # Add to trusted roles
                trusted_role = TrustedRole(
                    guild_id=ctx.guild.id,
                    role_id=role.id,
                    role_name=role.name,
                    granted_by=ctx.author.id,
                    granted_at=datetime.utcnow(),
                    is_active=True,
                    notes=notes if notes else f"Trusted by {ctx.author} via command"
                )
                
                session.add(trusted_role)
                session.commit()
            
            embed = discord.Embed(
                title="✅ Trusted Role Added",
                description=f"Role {role.mention} has been added to the trusted roles list.",
                color=0x00ff00,
                timestamp=datetime.utcnow()
            )
            
            embed.add_field(
                name="🎯 Role",
                value=f"{role.mention} ({role.name})",
                inline=True
            )
            
            embed.add_field(
                name="🎭 Role Info",
                value=f"Position: {role.position}\nColor: {role.color}",
                inline=True
            )
            
            embed.add_field(
                name="🔓 Permissions Granted",
                value="All members of this role now have admin-level bot access",
                inline=False
            )
            
            if notes:
                embed.add_field(
                    name="📝 Notes",
                    value=notes,
                    inline=False
                )
            
            await ctx.send(embed=embed)
            self.logger.info(f"Role {role.name} ({role.id}) trusted by {ctx.author} in guild {ctx.guild.name}")
            
        except Exception as e:
            self.logger.error(f"Error trusting role: {e}")
            embed = discord.Embed(
                title="❌ Error Adding Trusted Role",
                description=f"An error occurred: {str(e)}",
                color=0xff0000
            )
            await ctx.send(embed=embed)

    @commands.command(name='untrustrole', aliases=['removerole'])
    @is_owner_or_trusted()
    async def untrust_role(self, ctx, role: discord.Role):
        """Remove a role from the trusted roles list (Owner only)."""
        if not await self.bot.is_owner(ctx.author):
            await ctx.send("❌ This command is restricted to the bot owner only.")
            return
            
        if not ctx.guild:
            await ctx.send("❌ This command can only be used in a server.")
            return
            
        try:
            with DatabaseSession() as session:
                trusted_role = session.query(TrustedRole).filter(
                    TrustedRole.guild_id == ctx.guild.id,
                    TrustedRole.role_id == role.id,
                    TrustedRole.is_active == True
                ).first()
                
                if not trusted_role:
                    await ctx.send(f"❌ {role.mention} is not a trusted role.")
                    return
                
                # Deactivate the trusted role
                trusted_role.is_active = False
                trusted_role.updated_at = datetime.utcnow()
                session.commit()
            
            embed = discord.Embed(
                title="✅ Trusted Role Removed",
                description=f"Role {role.mention} has been removed from the trusted roles list.",
                color=0x00ff00,
                timestamp=datetime.utcnow()
            )
            
            embed.add_field(
                name="🔒 Access Revoked",
                value="Members of this role no longer have admin-level bot permissions.",
                inline=False
            )
            
            await ctx.send(embed=embed)
            self.logger.info(f"Role {role.name} ({role.id}) untrusted by {ctx.author} in guild {ctx.guild.name}")
            
        except Exception as e:
            self.logger.error(f"Error untrusting role: {e}")
            embed = discord.Embed(
                title="❌ Error Removing Trusted Role",
                description=f"An error occurred: {str(e)}",
                color=0xff0000
            )
            await ctx.send(embed=embed)

    @commands.command(name='trustedroles', aliases=['listroles'])
    @is_owner_or_trusted()
    async def list_trusted_roles(self, ctx):
        """List all trusted roles in this server (Owner only)."""
        if not await self.bot.is_owner(ctx.author):
            await ctx.send("❌ This command is restricted to the bot owner only.")
            return
            
        if not ctx.guild:
            await ctx.send("❌ This command can only be used in a server.")
            return
            
        try:
            with DatabaseSession() as session:
                trusted_roles = session.query(TrustedRole).filter(
                    TrustedRole.guild_id == ctx.guild.id,
                    TrustedRole.is_active == True
                ).all()
                
                # Extract data while still in session to avoid detached instance errors
                role_data = []
                for trusted_role in trusted_roles:
                    role_data.append({
                        'role_id': trusted_role.role_id,
                        'role_name': trusted_role.role_name
                    })
            
            # Now work with the extracted data outside the session
            embed = discord.Embed(
                title="🎭 Trusted Roles",
                description=f"Roles with admin-level bot permissions in **{ctx.guild.name}**",
                color=0x9146ff,
                timestamp=datetime.utcnow()
            )
            
            if not role_data:
                embed.add_field(
                    name="📋 No Trusted Roles",
                    value="No roles have been granted admin permissions yet.\n\nUse `!trustrole @RoleName` to add a trusted role.",
                    inline=False
                )
            else:
                role_list = []
                for role_info in role_data:
                    role_obj = ctx.guild.get_role(role_info['role_id'])
                    if role_obj:
                        role_list.append(f"• {role_obj.mention} - Active role")
                    else:
                        role_list.append(f"• `{role_info['role_name']}` (deleted role)")
                
                embed.add_field(
                    name=f"🔓 Active Trusted Roles ({len(role_data)})",
                    value="\n".join(role_list),
                    inline=False
                )
                
                embed.add_field(
                    name="✅ Status",
                    value=f"All members of these {len(role_data)} role(s) have admin-level bot permissions",
                    inline=True
                )
            
            embed.set_footer(text="Use !trustrole @role or !untrustrole @role to manage")
            await ctx.send(embed=embed)
            
        except Exception as e:
            self.logger.error(f"Error listing trusted roles: {e}")
            embed = discord.Embed(
                title="❌ Error Listing Trusted Roles",
                description=f"An error occurred: {str(e)}",
                color=0xff0000
            )
            await ctx.send(embed=embed)

    # Dashboard command removed - handled by cogs/basic_commands.py to prevent duplicates  
    # async def dashboard_access(self, interaction: discord.Interaction):
    #     """Dashboard access moved to basic_commands cog."""
    #     pass
    
    @commands.command(name='checkbackuptwitch')
    @commands.is_owner()
    async def check_backup_twitch(self, ctx):
        """Check backup role members and send Twitch linking DMs to users without Twitch."""
        try:
            from models import User as DBUser
            
            # Find the "Backups" role
            backup_role = ctx.guild.get_role(1408535262379905097)
            
            if not backup_role:
                await ctx.send("❌ Backups role not found in this server.")
                return
            
            # Get all members with the Backups role
            backup_members = backup_role.members
            
            if not backup_members:
                await ctx.send(f"📋 No users currently have the {backup_role.mention} role.")
                return
            
            await ctx.send(f"🔍 Checking {len(backup_members)} backup streamers for Twitch links...")
            
            with DatabaseSession() as session:
                results = []
                sent_count = 0
                
                # Check each backup member's Twitch status
                for member in backup_members:
                    # Check if user has Twitch linked
                    db_user = session.query(DBUser).filter(DBUser.id == member.id).first()
                    has_twitch = db_user and db_user.twitch_login
                    
                    if has_twitch:
                        results.append(f"✅ {member.mention} - Twitch: `{db_user.twitch_login}`")
                    else:
                        results.append(f"❌ {member.mention} - No Twitch linked (DM sent)")
                        
                        # Send DM
                        try:
                            embed = discord.Embed(
                                title="🎮 Link Your Twitch Account!",
                                description=f"Hi {member.mention}! You signed up as a backup streamer, but we don't have your Twitch account linked yet.\n\nLinking your Twitch helps us track your attendance during trains and give you credit for participation!",
                                color=0x9146FF
                            )
                            embed.add_field(
                                name="🔗 How to Link Your Twitch",
                                value=f"**Self-Service:** Use `/linktwitch YourTwitchUsername` to link your account instantly!\n\nOr ask an admin for assistance.",
                                inline=False
                            )
                            embed.add_field(
                                name="🎯 Benefits of Linking",
                                value="• Automatic attendance tracking during trains\n• Show up in train rosters with your Twitch name\n• Get credit for participating in raids",
                                inline=False
                            )
                            embed.add_field(
                                name="🔒 Privacy",
                                value="Your Twitch username will only be used for train participation tracking. You can request unlinking at any time.",
                                inline=False
                            )
                            
                            await member.send(embed=embed)
                            sent_count += 1
                        except discord.Forbidden:
                            results[-1] += " (DMs disabled)"
                        except Exception as e:
                            results[-1] += f" (Error: {e})"
                
                # Send summary
                summary_embed = discord.Embed(
                    title="🔍 Backup Streamers Twitch Check",
                    description=f"Found **{len(backup_members)}** user(s) with the {backup_role.mention} role",
                    color=0x00ff00,
                    timestamp=datetime.utcnow()
                )
                
                summary_embed.add_field(
                    name="Results",
                    value="\n".join(results) if results else "No backup members found",
                    inline=False
                )
                
                summary_embed.add_field(
                    name="Summary",
                    value=f"Sent {sent_count} DM(s) to users without Twitch linked",
                    inline=False
                )
                
                await ctx.send(embed=summary_embed)
                
        except Exception as e:
            self.logger.error(f"Error checking backup Twitch: {e}")
            await ctx.send(f"❌ Error: {str(e)}")

    @commands.command(name='createbackup', help='Manually create a database backup')
    @commands.check(lambda ctx: ctx.author.id == ctx.bot.owner_id)
    async def create_backup(self, ctx):
        """Manually create a database backup."""
        try:
            from utils.database_backup import get_backup_system
            
            await ctx.send("🔄 Creating database backup...")
            
            # Run backup
            backup_system = get_backup_system()
            stats = await asyncio.to_thread(backup_system.create_backup)
            
            embed = discord.Embed(
                title="✅ Database Backup Created",
                description=f"Backup completed successfully",
                color=0x00ff00,
                timestamp=datetime.utcnow()
            )
            
            embed.add_field(
                name="📁 File",
                value=f"`{stats['filename']}`",
                inline=False
            )
            
            embed.add_field(
                name="📊 Statistics",
                value=f"**Size:** {stats['size_mb']} MB\n"
                      f"**Schedules:** {stats['schedules']}\n"
                      f"**Participants:** {stats['participants']}\n"
                      f"**Users:** {stats['users']}\n"
                      f"**Banned Users:** {stats['banned_users']}",
                inline=False
            )
            
            await ctx.send(embed=embed)
            self.logger.info(f"Manual backup created by {ctx.author}")
            
        except Exception as e:
            self.logger.error(f"Error creating backup: {e}")
            await ctx.send(f"❌ Failed to create backup: {str(e)}")
    
    @commands.command(name='listbackups', help='List all available database backups')
    @commands.check(lambda ctx: ctx.author.id == ctx.bot.owner_id)
    async def list_backups(self, ctx):
        """List all available database backups."""
        try:
            from utils.database_backup import get_backup_system
            
            backup_system = get_backup_system()
            backups = await asyncio.to_thread(backup_system.list_backups)
            
            embed = discord.Embed(
                title="📦 Available Backups",
                description=f"Found {len(backups)} backup file(s)",
                color=0x3498db,
                timestamp=datetime.utcnow()
            )
            
            if backups:
                backup_list = []
                for backup in backups[:10]:  # Show last 10 backups
                    age_text = f"{backup['age_days']}d ago" if backup['age_days'] > 0 else "Today"
                    backup_list.append(
                        f"`{backup['filename']}` - {backup['size_mb']} MB ({age_text})"
                    )
                
                embed.add_field(
                    name="Recent Backups",
                    value="\n".join(backup_list),
                    inline=False
                )
                
                if len(backups) > 10:
                    embed.add_field(
                        name="Note",
                        value=f"Showing 10 most recent backups out of {len(backups)} total",
                        inline=False
                    )
            else:
                embed.add_field(
                    name="No Backups",
                    value="No backup files found. Use `!createbackup` to create one.",
                    inline=False
                )
            
            await ctx.send(embed=embed)
            
        except Exception as e:
            self.logger.error(f"Error listing backups: {e}")
            await ctx.send(f"❌ Failed to list backups: {str(e)}")

    @commands.command(name='cachestats', help='Show cache performance statistics')
    @commands.check(lambda ctx: ctx.author.id == ctx.bot.owner_id)
    async def cache_stats(self, ctx):
        """Display cache performance statistics."""
        try:
            from utils.schedule_cache import get_cache
            
            cache = get_cache()
            stats = cache.get_stats()
            
            embed = discord.Embed(
                title="📊 Cache Performance Statistics",
                description="Schedule caching system metrics",
                color=0x3498db,
                timestamp=datetime.utcnow()
            )
            
            embed.add_field(
                name="🎯 Performance",
                value=f"**Hit Rate:** {stats['hit_rate']}\n"
                      f"**Cache Hits:** {stats['hits']:,}\n"
                      f"**Cache Misses:** {stats['misses']:,}\n"
                      f"**Total Requests:** {stats['total_requests']:,}",
                inline=True
            )
            
            embed.add_field(
                name="💾 Storage",
                value=f"**Cached Entries:** {stats['entries']:,}\n"
                      f"**Default TTL:** 30 seconds",
                inline=True
            )
            
            # Add interpretation
            hit_rate_num = float(stats['hit_rate'].rstrip('%'))
            if hit_rate_num > 70:
                status = "🟢 Excellent - Cache is highly effective"
            elif hit_rate_num > 50:
                status = "🟡 Good - Cache is providing benefit"
            elif hit_rate_num > 30:
                status = "🟠 Fair - Some cache benefit"
            else:
                status = "🔴 Low - Cache may need tuning"
            
            embed.add_field(
                name="📈 Status",
                value=status,
                inline=False
            )
            
            embed.set_footer(text="Cache reduces database load for frequently accessed data")
            
            await ctx.send(embed=embed)
            
        except Exception as e:
            self.logger.error(f"Error getting cache stats: {e}")
            await ctx.send(f"❌ Failed to get cache stats: {str(e)}")
    
    @commands.command(name='clearcache', help='Clear the schedule cache')
    @commands.check(lambda ctx: ctx.author.id == ctx.bot.owner_id)
    async def clear_cache(self, ctx):
        """Clear all cached schedule data."""
        try:
            from utils.schedule_cache import get_cache
            
            cache = get_cache()
            old_stats = cache.get_stats()
            old_entries = old_stats['entries']
            
            cache.invalidate_all()
            
            embed = discord.Embed(
                title="🗑️ Cache Cleared",
                description=f"Removed {old_entries} cached entries",
                color=0x00ff00,
                timestamp=datetime.utcnow()
            )
            
            embed.add_field(
                name="ℹ️ Info",
                value="Cache will rebuild automatically as commands are used.\n"
                      "Fresh data will be fetched from the database.",
                inline=False
            )
            
            await ctx.send(embed=embed)
            self.logger.info(f"Cache cleared by {ctx.author} ({old_entries} entries removed)")
            
        except Exception as e:
            self.logger.error(f"Error clearing cache: {e}")
            await ctx.send(f"❌ Failed to clear cache: {str(e)}")

    @commands.command(name='restorebackup', help='Restore database from a backup file (DANGEROUS)')
    @commands.check(lambda ctx: ctx.author.id == ctx.bot.owner_id)
    async def restore_backup(self, ctx):
        """Restore database from a backup file with dropdown selection and safety confirmation."""
        try:
            from utils.database_backup import get_backup_system
            
            backup_system = get_backup_system()
            
            # Get list of available backups
            backups = await asyncio.to_thread(backup_system.list_backups)
            
            if not backups:
                await ctx.send("❌ No backup files found. Create a backup first using `!createbackup`.")
                return
            
            # Create backup selection view with dropdown
            class BackupSelectionView(ui.View):
                def __init__(self, backup_list: list, parent_ctx, parent_cog):
                    super().__init__(timeout=120.0)
                    self.backup_list = backup_list
                    self.parent_ctx = parent_ctx
                    self.parent_cog = parent_cog
                    
                    # Create select menu options (limit to 25 - Discord's max)
                    options = []
                    for backup in backup_list[:25]:
                        # Format: backup_2025-10-21_133000.json
                        label = backup['filename'][:100]  # Truncate if too long
                        description = f"{backup['size']} | {backup['created']}"[:100]
                        options.append(
                            discord.SelectOption(
                                label=label,
                                description=description,
                                value=backup['filename']
                            )
                        )
                    
                    self.select_menu = ui.Select(
                        placeholder="📦 Select a backup file to restore...",
                        options=options,
                        custom_id="backup_select"
                    )
                    self.select_menu.callback = self.on_backup_select
                    self.add_item(self.select_menu)
                
                async def on_backup_select(self, interaction: discord.Interaction):
                    """Handle backup file selection."""
                    if interaction.user.id != self.parent_ctx.author.id:
                        await interaction.response.send_message("❌ Only the command author can select a backup.", ephemeral=True)
                        return
                    
                    await interaction.response.defer()
                    
                    selected_filename = self.select_menu.values[0]
                    
                    # First, do a dry run to show what would be restored
                    try:
                        dry_run_stats = await asyncio.to_thread(
                            backup_system.restore_from_backup,
                            selected_filename,
                            dry_run=True
                        )
                    except FileNotFoundError:
                        await interaction.followup.send(f"❌ Backup file not found: `{selected_filename}`")
                        self.stop()
                        return
                    except Exception as e:
                        await interaction.followup.send(f"❌ Error reading backup file: {str(e)}")
                        self.stop()
                        return
                    
                    # Create confirmation view
                    class RestoreConfirmView(ui.View):
                        def __init__(self, backup_file: str, stats: dict, parent_cog):
                            super().__init__(timeout=60.0)
                            self.backup_file = backup_file
                            self.stats = stats
                            self.parent_cog = parent_cog
                            self.confirmed = False
                        
                        @ui.button(label="⚠️ CONFIRM RESTORE", style=discord.ButtonStyle.danger)
                        async def confirm_restore(self, interaction2: discord.Interaction, button: ui.Button):
                            """Handle restore confirmation."""
                            if interaction2.user.id != self.parent_ctx.author.id:
                                await interaction2.response.send_message("❌ Only the command author can confirm this.", ephemeral=True)
                                return
                            
                            await interaction2.response.defer()
                            
                            # Perform actual restore
                            try:
                                restore_stats = await asyncio.to_thread(
                                    backup_system.restore_from_backup,
                                    self.backup_file,
                                    dry_run=False
                                )
                                
                                embed = discord.Embed(
                                    title="✅ Backup Restore Completed",
                                    description=f"Successfully restored from `{self.backup_file}`",
                                    color=0x00ff00,
                                    timestamp=datetime.utcnow()
                                )
                                
                                tables_info = []
                                for table, info in restore_stats['tables_restored'].items():
                                    tables_info.append(f"**{table}:** {info['count']} records")
                                
                                embed.add_field(
                                    name="📊 Restored Tables",
                                    value="\n".join(tables_info[:10]),  # Limit to 10 tables
                                    inline=False
                                )
                                
                                embed.add_field(
                                    name="⚠️ Important",
                                    value="Note: Current restore is a dry-run simulation.\nFull implementation requires careful data merge logic.",
                                    inline=False
                                )
                                
                                await interaction2.followup.send(embed=embed)
                                self.stop()
                                
                            except Exception as e:
                                await interaction2.followup.send(f"❌ Restore failed: {str(e)}")
                                self.stop()
                        
                        @ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
                        async def cancel_restore(self, interaction2: discord.Interaction, button: ui.Button):
                            """Cancel the restore operation."""
                            if interaction2.user.id != self.parent_ctx.author.id:
                                await interaction2.response.send_message("❌ Only the command author can cancel this.", ephemeral=True)
                                return
                            
                            await interaction2.response.send_message("✅ Restore cancelled.", ephemeral=False)
                            self.stop()
                    
                    # Show what would be restored
                    embed = discord.Embed(
                        title="⚠️ RESTORE BACKUP CONFIRMATION",
                        description=f"**WARNING:** This will restore data from backup file:\n`{selected_filename}`",
                        color=0xff0000,
                        timestamp=datetime.utcnow()
                    )
                    
                    tables_info = []
                    for table, info in dry_run_stats['tables_restored'].items():
                        tables_info.append(f"**{table}:** {info['count']} records")
                    
                    embed.add_field(
                        name="📦 Backup Contents",
                        value="\n".join(tables_info),
                        inline=False
                    )
                    
                    embed.add_field(
                        name="📅 Backup Date",
                        value=dry_run_stats['backup_timestamp'],
                        inline=True
                    )
                    
                    embed.add_field(
                        name="⚠️ Warning",
                        value="**This operation cannot be undone!**\nMake sure you have a current backup before proceeding.",
                        inline=False
                    )
                    
                    confirm_view = RestoreConfirmView(selected_filename, dry_run_stats, self.parent_cog)
                    await interaction.followup.send(embed=embed, view=confirm_view)
                    self.stop()
            
            # Show initial backup selection embed
            embed = discord.Embed(
                title="📦 Restore Database from Backup",
                description=f"Found **{len(backups)}** backup file(s).\nSelect a backup to restore from the dropdown below.",
                color=0xff9900,
                timestamp=datetime.utcnow()
            )
            
            embed.add_field(
                name="⚠️ Warning",
                value="Restoring will overwrite current database data.\nMake sure you understand what you're doing!",
                inline=False
            )
            
            view = BackupSelectionView(backups, ctx, self)
            await ctx.send(embed=embed, view=view)
            
        except Exception as e:
            self.logger.error(f"Error in restore backup command: {e}")
            await ctx.send(f"❌ Error: {str(e)}")
    
    @commands.command(name='setserverdescription', aliases=['setdesc', 'serverdesc'])
    @is_owner_or_trusted()
    async def set_server_description(self, ctx, *, description: str = None):
        """Set a custom server description for train notifications."""
        try:
            if not description:
                await ctx.send("❌ Please provide a description.\nExample: `!setserverdescription Game Lounge Community Raid Train`")
                return
            
            with DatabaseSession() as session:
                # Get or create guild
                guild = session.query(Guild).filter_by(id=ctx.guild.id).first()
                if not guild:
                    guild = Guild(
                        id=ctx.guild.id,
                        name=ctx.guild.name,
                        owner_id=ctx.guild.owner_id,
                        member_count=ctx.guild.member_count
                    )
                    session.add(guild)
                
                # Update server description
                guild.server_description = description
                session.commit()
                
                embed = discord.Embed(
                    title="✅ Server Description Updated",
                    description=f"The server description has been set to:\n\n**{description}**\n\nThis will be included in all train notifications.",
                    color=0x00ff00,
                    timestamp=datetime.utcnow()
                )
                await ctx.send(embed=embed)
                
        except Exception as e:
            self.logger.error(f"Error setting server description: {e}")
            await ctx.send(f"❌ Error: {str(e)}")
    
    @app_commands.command(name='togglenotifications', description='Toggle automatic train notifications on/off (keeps attendance tracking)')
    @app_commands.checks.has_permissions(administrator=True)
    async def toggle_notifications(self, interaction: discord.Interaction):
        """Toggle automatic train notifications while keeping attendance tracking."""
        try:
            await interaction.response.defer(ephemeral=True)
            
            from models import NotificationSettings
            
            with DatabaseSession() as session:
                # Get or create notification settings
                settings = session.query(NotificationSettings).filter_by(
                    guild_id=interaction.guild.id
                ).first()
                
                if not settings:
                    settings = NotificationSettings(
                        guild_id=interaction.guild.id,
                        auto_ping_enabled=False  # Default to off when creating
                    )
                    session.add(settings)
                    session.commit()
                    new_status = False
                else:
                    # Toggle the setting
                    settings.auto_ping_enabled = not settings.auto_ping_enabled
                    new_status = settings.auto_ping_enabled
                    session.commit()
                
                # Send confirmation
                if new_status:
                    embed = discord.Embed(
                        title="🔔 Notifications Enabled",
                        description="Automatic train notifications are now **enabled**.\n\n"
                                   "Users who sign up for trains will be pinged when the train is about to start.",
                        color=0x00ff00,
                        timestamp=datetime.utcnow()
                    )
                    embed.add_field(
                        name="What This Means",
                        value="✅ Signed-up users will get pinged\n"
                              "✅ Attendance tracking is active\n"
                              "✅ Users can react to confirm readiness",
                        inline=False
                    )
                else:
                    embed = discord.Embed(
                        title="🔕 Notifications Disabled",
                        description="Automatic train notifications are now **disabled**.\n\n"
                                   "Users can still sign up and attendance will be tracked, but they won't be pinged.",
                        color=0xff9900,
                        timestamp=datetime.utcnow()
                    )
                    embed.add_field(
                        name="What This Means",
                        value="❌ No automatic pings for trains\n"
                              "✅ Attendance tracking still works\n"
                              "✅ Users can still sign up with `/jointrain`\n"
                              "✅ You can view rosters with `/trainroster`",
                        inline=False
                    )
                
                embed.set_footer(text="Use /togglenotifications again to change this setting")
                await interaction.followup.send(embed=embed)
                
        except Exception as e:
            self.logger.error(f"Error toggling notifications: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {str(e)}", ephemeral=True)

    # ============================================================
    # SLASH COMMAND VERSIONS - Activity & Status Management
    # ============================================================
    # CONVERTED TO PREFIX-ONLY: Use !set_activity instead (doesn't count toward 100 slash command limit)
    
    # @app_commands.command(name="setactivity", description="[Bot Owner or Trusted] Set bot's custom activity status (playing, watching, listening, streaming)")
    @app_commands.describe(
        activity_type="Type of activity",
        activity_name="What the bot should be doing"
    )
    @app_commands.choices(activity_type=[
        app_commands.Choice(name="Playing", value="playing"),
        app_commands.Choice(name="Watching", value="watching"),
        app_commands.Choice(name="Listening", value="listening"),
        app_commands.Choice(name="Streaming", value="streaming")
    ])
    @owner_or_trusted()
    async def _disabled_slash_set_activity(self, interaction: discord.Interaction, activity_type: str, activity_name: str):
        """Set bot activity (Owner only) - Persists across restarts."""
        await interaction.response.defer()
        
        activity_types = {
            'playing': discord.ActivityType.playing,
            'watching': discord.ActivityType.watching,
            'listening': discord.ActivityType.listening,
            'streaming': discord.ActivityType.streaming
        }
        
        try:
            # Save to database for persistence
            with DatabaseSession() as session:
                custom_status_setting = session.query(SystemSettings).filter_by(
                    setting_key='custom_status'
                ).first()
                
                activity_type_setting = session.query(SystemSettings).filter_by(
                    setting_key='activity_type'
                ).first()
                
                if not custom_status_setting:
                    custom_status_setting = SystemSettings(
                        setting_key='custom_status',
                        setting_value=activity_name,
                        is_enabled=True
                    )
                    session.add(custom_status_setting)
                else:
                    custom_status_setting.setting_value = activity_name
                    custom_status_setting.is_enabled = True
                
                if not activity_type_setting:
                    activity_type_setting = SystemSettings(
                        setting_key='activity_type',
                        setting_value=activity_type.lower(),
                        is_enabled=True
                    )
                    session.add(activity_type_setting)
                else:
                    activity_type_setting.setting_value = activity_type.lower()
                    activity_type_setting.is_enabled = True
                
                session.commit()
            
            # Apply the activity change
            activity = discord.Activity(
                type=activity_types[activity_type.lower()],
                name=activity_name
            )
            await self.bot.change_presence(activity=activity)
            
            embed = discord.Embed(
                title="✅ Activity Updated",
                description=f"Bot activity set to: **{activity_type.title()} {activity_name}**\n\n✨ **This will persist across restarts!**",
                color=0x00ff00
            )
            await interaction.followup.send(embed=embed)
            self.logger.info(f"Activity changed by {interaction.user}: {activity_type} {activity_name} (saved to database)")
        except Exception as e:
            await interaction.followup.send(f"❌ Failed to set activity: {str(e)}", ephemeral=True)
    
    # @app_commands.command(name="clearactivity", description="[Bot Owner or Trusted] Clear custom bot activity and restore default member count display")
    @owner_or_trusted()
    async def _disabled_slash_clear_activity(self, interaction: discord.Interaction):
        """Clear custom bot activity and restore default (Owner only)."""
        await interaction.response.defer()
        
        try:
            with DatabaseSession() as session:
                custom_status_setting = session.query(SystemSettings).filter_by(
                    setting_key='custom_status'
                ).first()
                activity_type_setting = session.query(SystemSettings).filter_by(
                    setting_key='activity_type'
                ).first()
                
                if custom_status_setting:
                    custom_status_setting.is_enabled = False
                if activity_type_setting:
                    activity_type_setting.is_enabled = False
                
                session.commit()
            
            # Set back to default (member count)
            member_count = sum(len(guild.members) for guild in self.bot.guilds)
            
            await self.bot.change_presence(
                activity=discord.Activity(
                    type=discord.ActivityType.watching,
                    name=f"{member_count:,} members | {self.bot.config.COMMAND_PREFIX}help"
                ),
                status=discord.Status.online
            )
            
            embed = discord.Embed(
                title="✅ Activity Cleared",
                description=f"Bot activity reset to default:\n**Watching {member_count:,} members**",
                color=0x00ff00
            )
            await interaction.followup.send(embed=embed)
            self.logger.info(f"Custom activity cleared by {interaction.user}, reverted to default")
        except Exception as e:
            await interaction.followup.send(f"❌ Failed to clear activity: {str(e)}", ephemeral=True)
    
    # @app_commands.command(name="setstatus", description="[Bot Owner or Trusted] Set bot's online status (online, idle, dnd, invisible)")
    @app_commands.describe(status="The status to set")
    @app_commands.choices(status=[
        app_commands.Choice(name="Online", value="online"),
        app_commands.Choice(name="Idle", value="idle"),
        app_commands.Choice(name="Do Not Disturb", value="dnd"),
        app_commands.Choice(name="Invisible", value="invisible")
    ])
    @owner_or_trusted()
    async def _disabled_slash_set_status(self, interaction: discord.Interaction, status: str):
        """Set bot status (Owner only)."""
        await interaction.response.defer()
        
        status_types = {
            'online': discord.Status.online,
            'idle': discord.Status.idle,
            'dnd': discord.Status.dnd,
            'invisible': discord.Status.invisible
        }
        
        try:
            await self.bot.change_presence(status=status_types[status.lower()])
            
            embed = discord.Embed(
                title="✅ Status Updated",
                description=f"Bot status set to: **{status.title()}**",
                color=0x00ff00
            )
            await interaction.followup.send(embed=embed)
            self.logger.info(f"Status changed by {interaction.user}: {status}")
        except Exception as e:
            await interaction.followup.send(f"❌ Failed to set status: {str(e)}", ephemeral=True)
    
    # ============================================================
    # SLASH COMMAND VERSIONS - Trusted Role Management
    # ============================================================
    
    @app_commands.command(name="trustrole", description="[Server Admin Required] Add a role to the trusted roles list for this server")
    @app_commands.describe(role="The role to add as trusted")
    @server_admin_only()
    async def slash_trust_role(self, interaction: discord.Interaction, role: discord.Role):
        """Add a role to the trusted roles list (Admin only)."""
        await interaction.response.defer()
        
        try:
            with DatabaseSession() as session:
                # Check if role is already trusted
                existing = session.query(TrustedRole).filter_by(
                    guild_id=interaction.guild.id,
                    role_id=role.id
                ).first()
                
                if existing:
                    if existing.is_active:
                        await interaction.followup.send(
                            f"❌ {role.mention} is already a trusted role.",
                            ephemeral=True
                        )
                        return
                    else:
                        # Reactivate
                        existing.is_active = True
                        existing.updated_at = datetime.utcnow()
                        session.commit()
                        action = "reactivated"
                else:
                    # Add new
                    trusted_role = TrustedRole(
                        guild_id=interaction.guild.id,
                        role_id=role.id,
                        role_name=role.name,
                        added_by_user_id=interaction.user.id,
                        is_active=True
                    )
                    session.add(trusted_role)
                    session.commit()
                    action = "added"
            
            embed = discord.Embed(
                title="✅ Trusted Role Added",
                description=f"{role.mention} has been {action} as a trusted role.\n\n"
                           f"Members with this role can use admin commands.",
                color=0x00ff00,
                timestamp=datetime.utcnow()
            )
            embed.set_footer(text=f"Added by {interaction.user}")
            await interaction.followup.send(embed=embed)
            self.logger.info(f"Trusted role {action}: {role.name} ({role.id}) in {interaction.guild.name} by {interaction.user}")
            
        except Exception as e:
            self.logger.error(f"Error adding trusted role: {e}")
            await interaction.followup.send(f"❌ Error: {str(e)}", ephemeral=True)
    
    @app_commands.command(name="untrustrole", description="[Server Admin Required] Remove a role from the trusted roles list")
    @app_commands.describe(role="The role to remove from trusted")
    @server_admin_only()
    async def slash_untrust_role(self, interaction: discord.Interaction, role: discord.Role):
        """Remove a role from the trusted roles list (Admin only)."""
        await interaction.response.defer()
        
        try:
            with DatabaseSession() as session:
                trusted_role = session.query(TrustedRole).filter_by(
                    guild_id=interaction.guild.id,
                    role_id=role.id,
                    is_active=True
                ).first()
                
                if not trusted_role:
                    await interaction.followup.send(
                        f"❌ {role.mention} is not a trusted role.",
                        ephemeral=True
                    )
                    return
                
                trusted_role.is_active = False
                trusted_role.updated_at = datetime.utcnow()
                session.commit()
            
            embed = discord.Embed(
                title="✅ Trusted Role Removed",
                description=f"{role.mention} has been removed from trusted roles.",
                color=0x00ff00,
                timestamp=datetime.utcnow()
            )
            embed.set_footer(text=f"Removed by {interaction.user}")
            await interaction.followup.send(embed=embed)
            self.logger.info(f"Trusted role removed: {role.name} ({role.id}) in {interaction.guild.name} by {interaction.user}")
            
        except Exception as e:
            self.logger.error(f"Error removing trusted role: {e}")
            await interaction.followup.send(f"❌ Error: {str(e)}", ephemeral=True)
    
    # @app_commands.command(name="viewlogs", description="Display recent bot logs for debugging")
    @app_commands.describe(lines="Number of log lines to display (max 50)")
    @owner_or_trusted()
    async def _disabled_slash_view_logs(self, interaction: discord.Interaction, lines: int = 20):
        """Display recent bot logs (Owner only)."""
        await interaction.response.defer()
        
        if lines > 50:
            lines = 50
        elif lines < 1:
            lines = 20
        
        try:
            embed = discord.Embed(
                title="📋 Bot Logs",
                description=f"Recent {lines} log entries would be displayed here.\n"
                           "Log file reading not implemented in current setup.\n\n"
                           "**Tip:** Check the Replit console for live logs.",
                color=0x00ff00,
                timestamp=datetime.utcnow()
            )
            await interaction.followup.send(embed=embed)
        except Exception as e:
            await interaction.followup.send(f"❌ Error retrieving logs: {str(e)}", ephemeral=True)

    # ============================================================
    # SLASH COMMAND VERSIONS - Server Settings
    # ============================================================
    
    @app_commands.command(name="setserverdescription", description="[Server Admin Required] Set a custom description for this server in the bot's database")
    @app_commands.describe(description="The description to set for this server")
    @admin_or_trusted()
    async def slash_set_server_description(self, interaction: discord.Interaction, description: str):
        """Set server description (Admin only)."""
        await interaction.response.defer()
        
        if len(description) > 500:
            await interaction.followup.send("❌ Description must be 500 characters or less.", ephemeral=True)
            return
        
        try:
            with DatabaseSession() as session:
                # Find or create guild record
                db_guild = session.query(Guild).filter(Guild.id == interaction.guild.id).first()
                
                if not db_guild:
                    db_guild = Guild(
                        guild_id=str(interaction.guild.id),
                        guild_name=interaction.guild.name,
                        description=description
                    )
                    session.add(db_guild)
                else:
                    db_guild.description = description
                
                session.commit()
            
            embed = discord.Embed(
                title="✅ Server Description Updated",
                description=f"**New Description:**\n{description}",
                color=0x00ff00,
                timestamp=datetime.utcnow()
            )
            embed.set_footer(text=f"Updated by {interaction.user}")
            await interaction.followup.send(embed=embed)
            self.logger.info(f"Server description updated by {interaction.user} in {interaction.guild.name}")
            
        except Exception as e:
            self.logger.error(f"Error setting server description: {e}")
            await interaction.followup.send(f"❌ Error: {str(e)}", ephemeral=True)

    # ============================================================
    # SLASH COMMAND VERSIONS - Train Schedule Setup
    # ============================================================
    
    @app_commands.command(name="createtrain", description="[Server Admin or Trusted] Create a custom train schedule for a specific day of the week")
    @app_commands.describe(
        day="Day of the week for the train",
        num_slots="Number of train slots to create (1-24)",
        duration="Duration of each slot in minutes (15-480)",
        start_time="Start time in UK time (e.g., '10:00am', '2:30pm')",
        weeks_ahead="Create schedule X weeks in future (0=this week, 1=next week)"
    )
    @app_commands.choices(day=[
        app_commands.Choice(name="Monday", value="monday"),
        app_commands.Choice(name="Tuesday", value="tuesday"),
        app_commands.Choice(name="Wednesday", value="wednesday"),
        app_commands.Choice(name="Thursday", value="thursday"),
        app_commands.Choice(name="Friday", value="friday"),
        app_commands.Choice(name="Saturday", value="saturday"),
        app_commands.Choice(name="Sunday", value="sunday")
    ])
    @admin_or_trusted()
    async def slash_create_train(
        self, 
        interaction: discord.Interaction, 
        day: str, 
        num_slots: int = 9, 
        duration: int = 90, 
        start_time: str = "10:00am", 
        weeks_ahead: int = 0
    ):
        """Create a customizable train schedule for a specific day."""
        await interaction.response.defer()
        
        # Import schedule setup helper functions
        from cogs.schedule_setup_commands import ScheduleSetupCommands
        setup_cog = self.bot.get_cog('ScheduleSetupCommands')
        
        if not setup_cog:
            await interaction.followup.send("❌ Schedule setup system is not available.", ephemeral=True)
            return
        
        # Convert day name to day_of_week number
        days_map = {
            'monday': 0, 'tuesday': 1, 'wednesday': 2, 'thursday': 3,
            'friday': 4, 'saturday': 5, 'sunday': 6
        }
        day_of_week = days_map[day.lower()]
        
        # Validate parameters
        if num_slots < 1 or num_slots > 24:
            await interaction.followup.send("❌ Number of slots must be between 1 and 24", ephemeral=True)
            return
            
        if duration < 15 or duration > 480:
            await interaction.followup.send("❌ Duration must be between 15 minutes and 8 hours (480 minutes)", ephemeral=True)
            return
        
        if weeks_ahead < 0 or weeks_ahead > 4:
            await interaction.followup.send("❌ weeks_ahead must be between 0 and 4", ephemeral=True)
            return
            
        # Parse start time
        parsed_start_time = setup_cog.parse_time_input(start_time, day_of_week)
        if not parsed_start_time:
            await interaction.followup.send("❌ Invalid start time format. Examples: `10:00am`, `2:30pm`, `14:30`, `7pm`", ephemeral=True)
            return
        
        try:
            with DatabaseSession() as session:
                # Remove existing schedules for this day
                existing = session.query(TrainSchedule).filter_by(
                    guild_id=interaction.guild.id,
                    day_of_week=day_of_week
                ).all()
                
                for schedule in existing:
                    setattr(schedule, 'is_active', False)
                
                notify_before = 60
                created_schedules = []
                current_time = parsed_start_time
                
                for i in range(num_slots):
                    # Generate slot name
                    slot_name = f"Train Slot {i+1}"
                    if i == 0:
                        slot_name = "Opening Train"
                    elif i == num_slots - 1:
                        slot_name = "Final Train"
                    
                    # Handle day rollover
                    actual_day = day_of_week
                    if current_time.hour < 5:
                        actual_day = (day_of_week + 1) % 7
                    
                    description_text = f"{duration}-minute raid train slot"
                    if weeks_ahead > 0:
                        description_text += f" [WEEK_OFFSET:{weeks_ahead}]"
                    
                    schedule = TrainSchedule(
                        guild_id=interaction.guild.id,
                        name=slot_name,
                        day_of_week=actual_day,
                        start_time=current_time,
                        duration_minutes=duration,
                        notify_before_minutes=notify_before,
                        is_active=True,
                        description=description_text,
                        max_participants=1
                    )
                    
                    session.add(schedule)
                    created_schedules.append(schedule)
                    
                    # Calculate next slot time
                    from datetime import datetime, timedelta, time
                    next_time = datetime.combine(datetime.today(), current_time) + timedelta(minutes=duration)
                    current_time = next_time.time()
                
                session.commit()
                
                # Invalidate cache
                from utils.schedule_cache import invalidate_schedule_cache
                invalidate_schedule_cache(interaction.guild.id)
                
                # Success message
                embed = discord.Embed(
                    title="✅ Train Schedule Created",
                    description=f"Created **{num_slots} train slots** for **{day.title()}**",
                    color=0x00ff00,
                    timestamp=datetime.utcnow()
                )
                
                week_text = ""
                if weeks_ahead == 0:
                    week_text = "this week"
                elif weeks_ahead == 1:
                    week_text = "next week"
                else:
                    week_text = f"{weeks_ahead} weeks ahead"
                
                embed.add_field(
                    name="📅 Schedule Details",
                    value=f"**Day:** {day.title()} ({week_text})\n**Slots:** {num_slots}\n**Duration:** {duration} minutes\n**Start Time:** {start_time} ET",
                    inline=False
                )
                
                embed.add_field(
                    name="📋 Next Steps",
                    value="• Users can now join with `/jointrain`\n• View schedule with `/timeslots`\n• Check participants with `/trainroster`",
                    inline=False
                )
                
                await interaction.followup.send(embed=embed)
                self.logger.info(f"Train schedule created by {interaction.user} for {day} in {interaction.guild.name}")
                
        except Exception as e:
            self.logger.error(f"Error creating train schedule: {e}")
            await interaction.followup.send(f"❌ Error creating schedule: {str(e)}", ephemeral=True)
    
    # ============================================================
    # SLASH COMMAND VERSIONS - Train Participant Management
    # ============================================================
    
    @app_commands.command(name="addtotrain", description="[Server Admin or Trusted] Add a user to a train schedule")
    @app_commands.describe(
        schedule_id="The train schedule ID",
        user="The user to add to the train",
        twitch_username="Optional: Twitch username if not linked via /linktwitch",
        notes="Optional notes about why this user was added"
    )
    @admin_or_trusted()
    async def slash_add_to_train(self, interaction: discord.Interaction, schedule_id: int, user: discord.Member, twitch_username: str = None, notes: str = "Added by admin"):
        """Add someone else to a train (admin command)."""
        await interaction.response.defer()
        
        try:
            with DatabaseSession() as session:
                schedule = session.query(TrainSchedule).filter_by(
                    id=schedule_id,
                    guild_id=interaction.guild.id,
                    is_active=True
                ).first()
                
                if not schedule:
                    await interaction.followup.send(f"❌ Train schedule #{schedule_id} not found.", ephemeral=True)
                    return
                
                # Check if user is already signed up
                existing = session.query(TrainParticipant).filter_by(
                    schedule_id=schedule_id,
                    user_id=user.id,
                    is_active=True
                ).first()
                
                if existing:
                    await interaction.followup.send(f"⚠️ {user.display_name} is already signed up for **{schedule.name}**!", ephemeral=True)
                    return
                
                # Check participant limit
                if schedule.max_participants:
                    current_count = session.query(TrainParticipant).filter_by(
                        schedule_id=schedule_id,
                        is_active=True
                    ).count()
                    
                    if current_count >= schedule.max_participants:
                        await interaction.followup.send(f"❌ **{schedule.name}** is full! ({current_count}/{schedule.max_participants})", ephemeral=True)
                        return
                
                resolved_twitch = twitch_username
                user_record = session.query(User).filter(
                    User.id == user.id,
                    User.guild_id == interaction.guild.id
                ).first()
                
                if not resolved_twitch and user_record and user_record.twitch_login:
                    resolved_twitch = user_record.twitch_login
                
                participant = TrainParticipant(
                    schedule_id=schedule_id,
                    guild_id=interaction.guild.id,
                    user_id=user.id,
                    username=user.name,
                    display_name=user.display_name,
                    notes=notes,
                    signed_up_at=datetime.utcnow(),
                    is_active=True,
                    twitch_username=resolved_twitch
                )
                
                session.add(participant)
                session.commit()
                
                # Invalidate cache
                from utils.schedule_cache import invalidate_schedule_cache
                invalidate_schedule_cache(schedule.guild_id)
                
                embed = discord.Embed(
                    title="✅ Added to Train",
                    description=f"Added **{user.display_name}** to **{schedule.name}**",
                    color=0x00ff00,
                    timestamp=datetime.utcnow()
                )
                
                embed.add_field(name="👤 Added User", value=f"{user.mention}", inline=True)
                embed.add_field(name="🔧 Added By", value=f"{interaction.user.mention}", inline=True)
                
                if resolved_twitch:
                    twitch_source = "manually set" if twitch_username else "auto-linked"
                    embed.add_field(
                        name="🎮 Twitch Account",
                        value=f"✅ **{resolved_twitch}** ({twitch_source})\nAttendance tracking enabled!",
                        inline=True
                    )
                else:
                    embed.add_field(
                        name="🎮 Twitch Account",
                        value="❌ Not linked\nUse `/addtotrain` with `twitch_username` or user can `/linktwitch`",
                        inline=True
                    )
                
                embed.set_footer(text=f"Train ID: {schedule_id}")
                await interaction.followup.send(embed=embed)

                # DM the added user with a "Leave this train" button
                participant_cog = self.bot.cogs.get('TrainParticipantCommands')
                if participant_cog:
                    asyncio.create_task(participant_cog.send_admin_added_dm(
                        user, schedule, interaction.user, resolved_twitch
                    ))

        except Exception as e:
            self.logger.error(f"Error adding user to train: {e}")
            await interaction.followup.send(f"❌ Error: {str(e)}", ephemeral=True)
    
    @app_commands.command(name="removefromtrain", description="[Server Admin or Trusted] Remove a user from a train schedule")
    @app_commands.describe(
        schedule_id="The train schedule ID",
        user="The user to remove from the train"
    )
    @admin_or_trusted()
    async def slash_remove_from_train(self, interaction: discord.Interaction, schedule_id: int, user: discord.Member):
        """Remove someone from a train (admin command)."""
        await interaction.response.defer()
        
        try:
            with DatabaseSession() as session:
                schedule = session.query(TrainSchedule).filter_by(
                    id=schedule_id,
                    guild_id=interaction.guild.id,
                    is_active=True
                ).first()
                
                if not schedule:
                    await interaction.followup.send(f"❌ Train schedule #{schedule_id} not found.", ephemeral=True)
                    return
                
                participant = session.query(TrainParticipant).filter_by(
                    schedule_id=schedule_id,
                    user_id=user.id,
                    is_active=True
                ).first()
                
                if not participant:
                    await interaction.followup.send(f"❌ {user.display_name} is not signed up for **{schedule.name}**.", ephemeral=True)
                    return
                
                # Mark as inactive
                participant.is_active = False
                session.commit()
                
                # Invalidate cache
                from utils.schedule_cache import invalidate_schedule_cache
                invalidate_schedule_cache(schedule.guild_id)
                
                embed = discord.Embed(
                    title="✅ Removed from Train",
                    description=f"Removed **{user.display_name}** from **{schedule.name}**",
                    color=0x00ff00,
                    timestamp=datetime.utcnow()
                )
                
                embed.add_field(name="👤 Removed User", value=f"{user.mention}", inline=True)
                embed.add_field(name="🔧 Removed By", value=f"{interaction.user.mention}", inline=True)
                embed.set_footer(text=f"Train ID: {schedule_id}")
                
                await interaction.followup.send(embed=embed)
                
        except Exception as e:
            self.logger.error(f"Error removing user from train: {e}")
            await interaction.followup.send(f"❌ Error: {str(e)}", ephemeral=True)
    
    @commands.command(name='announce_signups')
    @commands.is_owner()
    async def announce_signups(self, ctx, channel: discord.TextChannel = None, *, custom_message: str = None):
        """
        Announce that train sign-ups have begun (prefix command).
        
        Usage:
            !announce_signups #channel-name [optional custom message]
            !announce_signups (uses current channel and default message)
        
        Note: Game Lounge uses a special attendance-only announcement.
        """
        # Use current channel if none specified
        target_channel = channel or ctx.channel
        
        # Check if this is Game Lounge (attendance-only, no sign-ups)
        is_game_lounge = ctx.guild and "game lounge" in ctx.guild.name.lower()
        
        # Build the announcement message
        if custom_message:
            # Use custom message
            announcement = custom_message
            title = "🎯 Train Sign-Ups Open!" if not is_game_lounge else "🎯 Raid Train Time Slots Available!"
        elif is_game_lounge:
            # Game Lounge special template (attendance-only)
            announcement = (
                "🎮 **RAID TRAIN TIME SLOTS AVAILABLE!** 🎮\n\n"
                f"📍 **Post your preferred time slot in:** {target_channel.mention}\n\n"
                "**How to join:**\n"
                "1️⃣ Share your available time slot in the channel\n"
                "2️⃣ A staff member will get you set up on the schedule\n"
                "3️⃣ Link your Twitch account with `/linktwitch` (if not already linked)\n"
                "4️⃣ **Adding `rstone203` as a mod on your Twitch channel is recommended** (for announcements & auto-shoutouts, not required for attendance)\n\n"
                "**📊 How attendance tracking works:**\n"
                "✅ **Automatic tracking** - We monitor your Twitch chat during your time slot\n"
                "✅ **No sign-ups needed** - Just stream at your scheduled time\n"
                "✅ **Live participation counted** - Active chatters are tracked automatically\n"
                "✅ **Reports sent daily** - Full attendance reports posted after each train\n\n"
                "🚂 Let's ride the train together! All aboard!"
            )
            title = "🎯 Raid Train Time Slots Available!"
        else:
            # Default sign-up template for other servers
            announcement = (
                "🎮 **RAID TRAIN SIGN-UPS ARE NOW OPEN!** 🎮\n\n"
                f"📍 **Claim your spot:** {target_channel.mention}\n\n"
                "**To sign up, you'll need:**\n"
                "✅ Your Twitch account linked (`/linktwitch`)\n"
                "✅ **Adding `rstone203` as a mod on your Twitch channel is recommended** (for announcements & auto-shoutouts, not required for attendance)\n"
                "✅ To be available during your scheduled time slot\n"
                "✅ To react to the sign-up message when it's posted\n\n"
                "🚂 Let's ride the train together! All aboard!"
            )
            title = "🎯 Train Sign-Ups Open!"
        
        # Create embed for the announcement
        embed = discord.Embed(
            title=title,
            description=announcement,
            color=0x9146FF,  # Twitch purple
            timestamp=datetime.utcnow()
        )
        
        embed.set_footer(text=f"Announced by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
        
        # Send to the target channel with @everyone ping
        try:
            await target_channel.send(content="@everyone", embed=embed)
            
            # Confirmation to admin
            if target_channel != ctx.channel:
                await ctx.send(f"✅ Sign-up announcement posted in {target_channel.mention} with @everyone ping")
            else:
                # Add reaction to confirm if sent in same channel
                await ctx.message.add_reaction("✅")
                
        except discord.Forbidden:
            await ctx.send(f"❌ I don't have permission to send messages in {target_channel.mention}")
        except Exception as e:
            self.logger.error(f"Error announcing sign-ups: {e}")
            await ctx.send(f"❌ Error sending announcement: {str(e)}")

    @commands.command(name='setannounce')
    @commands.is_owner()
    async def set_announce(self, ctx, toggle: str = None):
        """
        Enable or disable the automatic Monday noon signup announcement for Game Lounge.
        Usage: !setannounce on   — enable weekly auto-announcement
               !setannounce off  — disable weekly auto-announcement
               !setannounce      — show current status
        """
        from database import DatabaseSession
        from models import SystemSettings

        with DatabaseSession() as session:
            setting = session.query(SystemSettings).filter_by(
                setting_key='signup_announce_enabled'
            ).first()

            if toggle is None:
                status = "✅ ON" if (setting and setting.is_enabled) else "❌ OFF"
                await ctx.send(f"📢 Weekly signup announcement is currently **{status}**.\nUse `!setannounce on` or `!setannounce off` to change.")
                return

            if toggle.lower() in ('on', 'true', '1', 'yes'):
                enabled = True
            elif toggle.lower() in ('off', 'false', '0', 'no'):
                enabled = False
            else:
                await ctx.send("❌ Use `!setannounce on` or `!setannounce off`.")
                return

            if setting:
                setting.is_enabled = enabled
                setting.setting_value = 'true' if enabled else 'false'
            else:
                session.add(SystemSettings(
                    setting_key='signup_announce_enabled',
                    setting_value='true' if enabled else 'false',
                    is_enabled=enabled,
                ))
            session.commit()

        status = "✅ ON" if enabled else "❌ OFF"
        await ctx.send(f"📢 Weekly signup announcement set to **{status}**. Fires every Monday at noon EST.")

    @commands.command(name='testannounce')
    @commands.is_owner()
    async def test_announce(self, ctx):
        """
        Immediately post the signup announcement to Game Lounge #general (for testing).
        Does NOT affect the last-sent date, so the real Monday post still fires.
        Usage: !testannounce
        """
        GL_GUILD_ID = 1183084958110191616
        GL_GENERAL_CHANNEL_ID = 1183143967622168668
        GL_SIGNUPS_CHANNEL_ID = 1183135069896966154

        guild = self.bot.get_guild(GL_GUILD_ID)
        if not guild:
            await ctx.send("❌ Could not find the Game Lounge server.")
            return

        general_channel = guild.get_channel(GL_GENERAL_CHANNEL_ID)
        signups_channel = guild.get_channel(GL_SIGNUPS_CHANNEL_ID)
        if not general_channel:
            await ctx.send("❌ Could not find #general in Game Lounge.")
            return

        signups_mention = signups_channel.mention if signups_channel else "#raid-train-sign-ups"

        announcement = (
            "🎮 **RAID TRAIN TIME SLOTS AVAILABLE!** 🎮\n\n"
            f"📍 **Post your preferred time slot in:** {signups_mention}\n\n"
            "**How to join:**\n"
            "1️⃣ Share your available time slot in the channel\n"
            "2️⃣ A staff member will get you set up on the schedule\n"
            "3️⃣ Link your Twitch account with `/linktwitch` (if not already linked)\n"
            "4️⃣ **Adding `rstone203` as a mod on your Twitch channel is recommended** "
            "(for announcements & auto-shoutouts, not required for attendance)\n\n"
            "**📊 How attendance tracking works:**\n"
            "✅ **Automatic tracking** - We monitor your Twitch chat during your time slot\n"
            "✅ **No sign-ups needed** - Just stream at your scheduled time\n"
            "✅ **Live participation counted** - Active chatters are tracked automatically\n"
            "✅ **Reports sent daily** - Full attendance reports posted after each train\n\n"
            "🚂 Let's ride the train together! All aboard!"
        )

        embed = discord.Embed(
            title="🎯 Raid Train Time Slots Available!",
            description=announcement,
            color=0x9146FF,
            timestamp=datetime.utcnow()
        )
        embed.set_footer(text="Every Monday at noon • use !setannounce off to disable")

        try:
            await general_channel.send(content="@everyone", embed=embed)
            if general_channel != ctx.channel:
                await ctx.send(f"✅ Test announcement posted to {general_channel.mention}.")
            else:
                await ctx.message.add_reaction("✅")
        except discord.Forbidden:
            await ctx.send(f"❌ Missing permission to post in {general_channel.mention}.")
        except Exception as e:
            self.logger.error(f"testannounce error: {e}")
            await ctx.send(f"❌ Error: {e}")

    @commands.command(name="sendoauth")
    @commands.is_owner()
    async def send_oauth_dm(self, ctx, user_id: str = None):
        """Send OAuth authorization DM to a user. Usage: !sendoauth <user_id or @mention>"""
        if not user_id:
            await ctx.send("❌ Usage: `!sendoauth <user_id or @mention>`")
            return

        target_id = user_id.strip("<@!>")
        try:
            target_id = int(target_id)
        except ValueError:
            await ctx.send("❌ Invalid user ID")
            return

        with DatabaseSession() as session:
            db_user = session.query(User).filter(User.id == target_id).first()
            if not db_user or not db_user.twitch_login:
                await ctx.send("❌ That user doesn't have a Twitch account linked. Link their Twitch first with `/linktwitch` or manually.")
                return

            twitch_login = db_user.twitch_login
            user_display_name = db_user.display_name

            has_token = session.query(TwitchOAuthToken).filter(
                TwitchOAuthToken.user_id == target_id,
                TwitchOAuthToken.is_active == True
            ).first()

            if has_token:
                await ctx.send(f"✅ {user_display_name} already has OAuth authorization for auto-raids!")
                return

        try:
            target_user = await self.bot.fetch_user(target_id)
            dm_channel = await target_user.create_dm()

            embed = discord.Embed(
                title="🎮 Authorize Auto-Raids for Train Rides",
                description=f"Hi {target_user.display_name}! Your Twitch account **{twitch_login}** is linked, but you haven't authorized auto-raids yet.\n\nThis lets the bot automatically start raids from your channel during train rides — no manual work needed!",
                color=0x9146ff
            )
            embed.add_field(
                name="How to authorize",
                value="1. Go to the Discord server\n2. Run **/twitchoauth**\n3. Enter the code shown at **twitch.tv/activate**\n4. That's it — you're all set!",
                inline=False
            )
            embed.set_footer(text="This is a one-time setup. Your authorization stays active until you revoke it.")

            await dm_channel.send(embed=embed)
            await ctx.send(f"✅ OAuth authorization DM sent to **{target_user.display_name}**!")

        except discord.Forbidden:
            await ctx.send(f"❌ Couldn't send DM to that user — they may have DMs disabled.")
        except Exception as e:
            self.logger.error(f"Error sending OAuth DM: {e}")
            await ctx.send(f"❌ Error: {str(e)}")

    @commands.command(name='deleteschedule', aliases=['deleteschedules', 'clearschedule'])
    @is_owner_or_trusted()
    async def delete_schedule(self, ctx):
        """Delete all train schedules and participants for this server."""
        try:
            guild_id = ctx.guild.id
            guild_name = ctx.guild.name

            with DatabaseSession() as session:
                schedules = session.query(TrainSchedule).filter_by(guild_id=guild_id).all()

                if not schedules:
                    await ctx.send("❌ No train schedules found for this server.")
                    return

                schedule_ids = [s.id for s in schedules]
                schedule_count = len(schedule_ids)

                from models import TrainNotification, TwitchChatAttendance, TrainSlotCompletion, TrainParticipantReady, TrainAttendance, TwitchLinkOutreach
                from sqlalchemy import text

                participant_count = session.query(TrainParticipant).filter(
                    TrainParticipant.schedule_id.in_(schedule_ids)
                ).count()

                schedule_names = [f"• {s.name}" for s in schedules]
                preview = '\n'.join(schedule_names[:10])
                if len(schedule_names) > 10:
                    preview += f"\n... and {len(schedule_names) - 10} more"

                confirm_embed = discord.Embed(
                    title="⚠️ Delete All Schedules?",
                    description=(
                        f"This will permanently delete **all train schedules** for **{guild_name}**:\n\n"
                        f"{preview}\n\n"
                        f"**{schedule_count}** schedules and **{participant_count}** participants will be removed.\n\n"
                        "React with ✅ to confirm or ❌ to cancel."
                    ),
                    color=0xff0000
                )
                confirm_msg = await ctx.send(embed=confirm_embed)
                await confirm_msg.add_reaction('✅')
                await confirm_msg.add_reaction('❌')

                def check(reaction, user):
                    return (
                        user == ctx.author
                        and str(reaction.emoji) in ['✅', '❌']
                        and reaction.message.id == confirm_msg.id
                    )

                try:
                    reaction, user = await self.bot.wait_for('reaction_add', timeout=30.0, check=check)
                except asyncio.TimeoutError:
                    await ctx.send("⏰ Timed out. No schedules were deleted.")
                    return

                if str(reaction.emoji) == '❌':
                    await ctx.send("❌ Cancelled. No schedules were deleted.")
                    return

            with DatabaseSession() as session:
                schedules = session.query(TrainSchedule).filter_by(guild_id=guild_id).all()
                schedule_ids = [s.id for s in schedules]

                session.query(TrainParticipantReady).filter(
                    TrainParticipantReady.schedule_id.in_(schedule_ids)
                ).delete(synchronize_session='fetch')

                notification_ids = [n.id for n in session.query(TrainNotification.id).filter(
                    TrainNotification.schedule_id.in_(schedule_ids)
                ).all()]
                if notification_ids:
                    session.query(TrainAttendance).filter(
                        TrainAttendance.notification_id.in_(notification_ids)
                    ).delete(synchronize_session='fetch')

                deleted_notifications = session.query(TrainNotification).filter(
                    TrainNotification.schedule_id.in_(schedule_ids)
                ).delete(synchronize_session='fetch')

                deleted_attendance = session.query(TwitchChatAttendance).filter(
                    TwitchChatAttendance.schedule_id.in_(schedule_ids)
                ).delete(synchronize_session='fetch')

                deleted_completions = session.query(TrainSlotCompletion).filter(
                    TrainSlotCompletion.schedule_id.in_(schedule_ids)
                ).delete(synchronize_session='fetch')

                session.query(TwitchLinkOutreach).filter(
                    TwitchLinkOutreach.schedule_id.in_(schedule_ids)
                ).delete(synchronize_session='fetch')

                session.execute(
                    text("DELETE FROM google_sheet_syncs WHERE schedule_id = ANY(:ids)"),
                    {"ids": schedule_ids}
                )

                deleted_participants = session.query(TrainParticipant).filter(
                    TrainParticipant.schedule_id.in_(schedule_ids)
                ).delete(synchronize_session='fetch')

                deleted_schedules = session.query(TrainSchedule).filter_by(
                    guild_id=guild_id
                ).delete(synchronize_session='fetch')

                session.commit()

                result_embed = discord.Embed(
                    title="🗑️ Schedules Deleted",
                    description=f"All train data for **{guild_name}** has been cleared.",
                    color=0x00ff00
                )
                result_embed.add_field(
                    name="Removed",
                    value=(
                        f"📅 **{deleted_schedules}** schedules\n"
                        f"👥 **{deleted_participants}** participants\n"
                        f"🔔 **{deleted_notifications}** notifications\n"
                        f"📊 **{deleted_attendance}** attendance records\n"
                        f"✅ **{deleted_completions}** completion records"
                    ),
                    inline=False
                )
                result_embed.set_footer(text=f"Server is now ready for a fresh schedule")
                await ctx.send(embed=result_embed)

                self.logger.info(f"🗑️ {ctx.author} deleted all schedules for {guild_name} ({guild_id}): "
                               f"{deleted_schedules} schedules, {deleted_participants} participants")

        except Exception as e:
            self.logger.error(f"Error in deleteschedule command: {e}", exc_info=True)
            await ctx.send(f"❌ Error deleting schedules: {str(e)}")

    @commands.command(name='sendlinknotify', help='Resend Twitch link notification for a user to owner + Sippy Cup')
    @commands.check(lambda ctx: ctx.author.id == ctx.bot.owner_id)
    async def send_link_notify(self, ctx, user: discord.Member = None):
        """Manually resend the Twitch link notification for a user."""
        target = user or ctx.author
        SIPPY_CUP_ID = 340907358077059073
        owner_id = int(os.getenv('OWNER_ID_DISCORD', '887354716751810560'))

        try:
            with DatabaseSession() as session:
                from models import User as DbUser
                db_user = session.query(DbUser).filter_by(id=target.id).first()
                if not db_user or not db_user.twitch_login:
                    await ctx.send(f"❌ {target.display_name} has no Twitch account linked.")
                    return
                twitch_username = db_user.twitch_login

            notify_embed = discord.Embed(
                title="✅ User Linked Their Twitch Account",
                description=f"**{target.display_name}** (`{target.name}`) has successfully linked their Twitch account!",
                color=0x00ff00,
                timestamp=datetime.utcnow()
            )
            notify_embed.add_field(
                name="🔗 Link Details",
                value=f"**Discord User:** {target.mention}\n**Twitch Username:** `{twitch_username}`\n**Link Type:** Self-Service (Instant)",
                inline=False
            )
            notify_embed.set_thumbnail(url=target.display_avatar.url)
            notify_embed.set_footer(text=f"Linked via /linktwitch command • resent by {ctx.author.name}")

            sent_to = []
            for notify_id in set([owner_id, SIPPY_CUP_ID]):
                try:
                    notify_user = self.bot.get_user(notify_id) or await self.bot.fetch_user(notify_id)
                    if notify_user:
                        await notify_user.send(embed=notify_embed)
                        sent_to.append(notify_user.name)
                except discord.Forbidden:
                    self.logger.warning(f"Could not DM {notify_id} — DMs closed")
                except Exception as e:
                    self.logger.warning(f"Could not notify {notify_id}: {e}")

            await ctx.send(f"✅ Notification sent to: {', '.join(sent_to) if sent_to else 'nobody (DMs closed?)'}", delete_after=10)

        except Exception as e:
            self.logger.error(f"sendlinknotify error: {e}", exc_info=True)
            await ctx.send(f"❌ Error: {e}")


    @commands.command(name='sendtraindms', help='Send Sunday train info DMs to all participants')
    @commands.is_owner()
    async def send_train_dms(self, ctx):
        """Send attendance info + opt-in questions to all Sunday train participants."""
        try:
            from models import TrainSchedule, TrainParticipant
            import pytz
            uk_tz = pytz.timezone('Europe/London')

            with DatabaseSession() as session:
                from datetime import date
                sunday = date(2026, 4, 12)
                schedules = session.query(TrainSchedule).filter_by(specific_date=sunday, is_active=True).order_by(TrainSchedule.start_time).all()

                if not schedules:
                    await ctx.send("❌ No schedules found for Sunday April 12.")
                    return

                # Build slot lookup
                slot_map = {}
                for i, s in enumerate(schedules, 1):
                    participants = session.query(TrainParticipant).filter_by(schedule_id=s.id, is_active=True).all()
                    for p in participants:
                        from datetime import datetime
                        naive_dt = datetime.combine(date.today(), s.start_time)
                        uk_dt = uk_tz.localize(naive_dt)
                        slot_map[p.id] = {
                            'slot_num': i,
                            'slot_name': s.name,
                            'start_str': uk_dt.strftime('%I:%M %p BST'),
                            'user_id': p.user_id,
                            'display_name': p.display_name,
                            'twitch': p.twitch_username,
                            'username': p.username,
                        }

            sent, failed, skipped = [], [], []
            status_msg = await ctx.send(f"📨 Sending DMs to {len(slot_map)} participants...")

            for pid, info in slot_map.items():
                member = None

                # Try by user_id first
                if info['user_id']:
                    try:
                        member = await self.bot.fetch_user(int(info['user_id']))
                    except Exception:
                        pass

                # Fall back to searching the guild by username
                if not member and info['username']:
                    guild = ctx.guild
                    member = discord.utils.find(
                        lambda m: m.name.lower() == info['username'].lower() or
                                  (m.display_name and m.display_name.lower() == info['display_name'].lower()),
                        guild.members
                    )

                if not member:
                    skipped.append(info['display_name'])
                    continue

                twitch_status = f"✅ Linked as `{info['twitch']}`" if info['twitch'] else "❌ Not linked yet — use `/linktwitch` in the server"

                dm_embed = discord.Embed(
                    title="🚂 Sunday Raid Train — Your Info",
                    description=(
                        f"Hey **{info['display_name']}**! You're on **Slot {info['slot_num']}** of tomorrow's raid train.\n\n"
                        f"⏰ **Your slot:** {info['start_str']}\n"
                        f"🎮 **Twitch account:** {twitch_status}"
                    ),
                    color=discord.Color.purple()
                )

                dm_embed.add_field(
                    name="📊 How Attendance Tracking Works",
                    value=(
                        "The bot monitors your **Twitch chat** during your slot and takes a snapshot every **25 minutes**.\n\n"
                        "• It records everyone who chats during your stream\n"
                        "• A per-slot report is posted to **#bot-attendance** after your slot\n"
                        "• A full weekly summary goes to **#attendance-total** on Monday\n"
                        "• You must be **live on Twitch** during your slot for tracking to work"
                    ),
                    inline=False
                )

                dm_embed.add_field(
                    name="✅ What You Need",
                    value=(
                        "**1. Twitch account linked** — if not done, use `/linktwitch` in the Game Lounge server\n"
                        "**2. Be live on Twitch** during your slot time\n"
                        "**3. No mods required** — the bot can join your chat automatically"
                    ),
                    inline=False
                )

                dm_embed.add_field(
                    name="🔧 Optional Features — Reply to Enable",
                    value=(
                        "**🎉 Auto-Shoutouts** — Bot automatically welcomes first-time chatters in your stream with a custom message\n"
                        "**⚔️ `!raidnext` command** — Lets mods in your Twitch chat trigger the raid to the next person in the train\n\n"
                        "Just reply to this DM with:\n"
                        "`yes shoutouts` — to enable auto-shoutouts\n"
                        "`yes raidnext` — to enable !raidnext in your chat\n"
                        "`yes both` — to enable both\n"
                        "`no thanks` — if you don't want either"
                    ),
                    inline=False
                )

                dm_embed.set_footer(text="Game Lounge Raid Train • Sunday April 12 • Any questions, message rstone203")

                try:
                    await member.send(embed=dm_embed)
                    sent.append(info['display_name'])
                    self.logger.info(f"✅ Train DM sent to {info['display_name']}")
                    import asyncio
                    await asyncio.sleep(1)
                except discord.Forbidden:
                    failed.append(f"{info['display_name']} (DMs closed)")
                except Exception as e:
                    failed.append(f"{info['display_name']} (error: {e})")

            result_lines = [f"**📨 Train DMs — Results**"]
            if sent:
                result_lines.append(f"✅ Sent ({len(sent)}): {', '.join(sent)}")
            if failed:
                result_lines.append(f"❌ Failed ({len(failed)}): {', '.join(failed)}")
            if skipped:
                result_lines.append(f"⚠️ Couldn't find ({len(skipped)}): {', '.join(skipped)} — no Discord ID in database")

            await status_msg.edit(content="\n".join(result_lines))

        except Exception as e:
            self.logger.error(f"sendtraindms error: {e}", exc_info=True)
            await ctx.send(f"❌ Error: {e}")

    @commands.command(name='listtrains', aliases=['trainlist', 'showtrains'])
    @commands.guild_only()
    @is_owner_or_trusted()
    async def list_trains(self, ctx):
        """List all active train schedules with their IDs and times."""
        try:
            days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
            with DatabaseSession() as session:
                schedules = session.query(TrainSchedule).filter_by(
                    is_active=True,
                    guild_id=ctx.guild.id
                ).order_by(TrainSchedule.day_of_week, TrainSchedule.start_time).all()

                if not schedules:
                    await ctx.send("No active train schedules found.")
                    return

                lines = []
                for s in schedules:
                    day_name = days[s.day_of_week] if 0 <= s.day_of_week <= 6 else f"day{s.day_of_week}"
                    t = s.start_time.strftime('%H:%M') if s.start_time else '??:??'
                    lines.append(f"`ID {s.id}` | {day_name} {t} | {s.name}")

                embed = discord.Embed(
                    title="🚂 Active Train Schedules",
                    description="\n".join(lines),
                    color=discord.Color.purple()
                )
                embed.set_footer(text="Use !rescheduletrain <id> <day> <HH:MM> to change a schedule's time")
                await ctx.send(embed=embed)

        except Exception as e:
            self.logger.error(f"listtrains error: {e}", exc_info=True)
            await ctx.send(f"❌ Error: {e}")

    @commands.command(name='rescheduletrain', aliases=['edittraintime', 'settraintime'])
    @commands.guild_only()
    @is_owner_or_trusted()
    async def reschedule_train(self, ctx, schedule_id: int = None, day: str = None, new_time: str = None):
        """Change a train schedule's day and start time.

        Usage: !rescheduletrain <id> <day> <HH:MM>
        Example: !rescheduletrain 103 monday 00:00
        Days: monday tuesday wednesday thursday friday saturday sunday
        Note: For trains after midnight use the ACTUAL day (e.g. 1 AM Monday = monday 01:00)
        """
        try:
            if not schedule_id or not day or not new_time:
                embed = discord.Embed(
                    title="⚙️ Reschedule Train",
                    description="Change a train schedule's day and start time.",
                    color=discord.Color.blue()
                )
                embed.add_field(
                    name="Usage",
                    value="`!rescheduletrain <id> <day> <HH:MM>`",
                    inline=False
                )
                embed.add_field(
                    name="Example",
                    value="`!rescheduletrain 103 monday 00:00` — Sunday midnight train\n"
                          "`!rescheduletrain 103 sunday 23:00` — Sunday 11 PM train",
                    inline=False
                )
                embed.add_field(
                    name="Note",
                    value="For trains that start after midnight, use the real calendar day.\n"
                          "e.g. 1 AM Monday morning = `monday 01:00`\n"
                          "Run `!listtrains` to see all schedule IDs.",
                    inline=False
                )
                await ctx.send(embed=embed)
                return

            day_map = {
                'monday': 0, 'mon': 0,
                'tuesday': 1, 'tue': 1,
                'wednesday': 2, 'wed': 2,
                'thursday': 3, 'thu': 3,
                'friday': 4, 'fri': 4,
                'saturday': 5, 'sat': 5,
                'sunday': 6, 'sun': 6,
            }
            day_lower = day.lower()
            if day_lower not in day_map:
                await ctx.send(f"❌ Unknown day `{day}`. Use: monday tuesday wednesday thursday friday saturday sunday")
                return

            day_of_week = day_map[day_lower]

            # Parse time HH:MM
            from datetime import time as time_type
            try:
                parts = new_time.split(':')
                h, m = int(parts[0]), int(parts[1])
                if not (0 <= h <= 23 and 0 <= m <= 59):
                    raise ValueError
                parsed_time = time_type(h, m)
            except Exception:
                await ctx.send(f"❌ Invalid time `{new_time}`. Use 24-hour format: HH:MM (e.g. 00:00, 23:30)")
                return

            days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']

            with DatabaseSession() as session:
                schedule = session.query(TrainSchedule).filter_by(id=schedule_id, guild_id=ctx.guild.id).first()
                if not schedule:
                    await ctx.send(f"❌ No schedule found with ID {schedule_id} in this server. Run `!listtrains` to see all IDs.")
                    return

                old_day = days[schedule.day_of_week]
                old_time = schedule.start_time.strftime('%H:%M')

                schedule.day_of_week = day_of_week
                schedule.start_time = parsed_time
                session.commit()

                new_day_name = days[day_of_week]
                embed = discord.Embed(
                    title="✅ Train Schedule Updated",
                    color=discord.Color.green()
                )
                embed.add_field(name="Schedule", value=f"`{schedule.name}` (ID {schedule_id})", inline=False)
                embed.add_field(name="Was",  value=f"{old_day} at {old_time}", inline=True)
                embed.add_field(name="Now",  value=f"{new_day_name} at {new_time}", inline=True)
                embed.set_footer(text=f"Stage 1 notification will now fire at {new_time.split(':')[0].zfill(2)}:{new_time.split(':')[1]} minus 1 hour")
                await ctx.send(embed=embed)

                self.logger.info(f"{ctx.author} rescheduled train {schedule_id} '{schedule.name}' from {old_day} {old_time} → {new_day_name} {new_time}")

        except Exception as e:
            self.logger.error(f"rescheduletrain error: {e}", exc_info=True)
            await ctx.send(f"❌ Error: {e}")

    @commands.command(name='deletealltrains')
    @is_owner_or_trusted()
    async def delete_all_trains(self, ctx):
        """Delete all train schedules, participants, and notifications for this guild."""
        try:
            from database import DatabaseSession
            from models import TrainSchedule, TrainParticipant, TrainNotification, TrainParticipantReady

            with DatabaseSession() as session:
                schedules = session.query(TrainSchedule).filter_by(guild_id=ctx.guild.id, is_active=True).all()
                ids = [s.id for s in schedules]

                if not ids:
                    await ctx.send("ℹ️ No active train schedules found for this guild.")
                    return

                ready   = session.query(TrainParticipantReady).filter(TrainParticipantReady.schedule_id.in_(ids)).delete(synchronize_session=False)
                notifs  = session.query(TrainNotification).filter(TrainNotification.schedule_id.in_(ids)).delete(synchronize_session=False)
                parts   = session.query(TrainParticipant).filter(TrainParticipant.schedule_id.in_(ids)).delete(synchronize_session=False)
                scheds  = session.query(TrainSchedule).filter(TrainSchedule.id.in_(ids)).delete(synchronize_session=False)
                session.commit()

            await ctx.send(
                f"✅ Deleted **{scheds}** schedules, **{parts}** participants, "
                f"**{notifs}** notifications, **{ready}** ready records."
            )
            self.logger.info(f"{ctx.author} deleted all {scheds} train schedules in {ctx.guild.name}")

        except Exception as e:
            self.logger.error(f"deletealltrains error: {e}", exc_info=True)
            await ctx.send(f"❌ Error: {e}")

    @commands.command(name='dmlinkreminder')
    @is_owner_or_trusted()
    async def dm_link_reminder(self, ctx):
        """DM all schedule participants who haven't linked their Twitch account yet."""
        from database import DatabaseSession
        from models import TrainSchedule, TrainParticipant, User, TwitchOAuthToken

        with DatabaseSession() as session:
            # Get all active schedule participants in this guild
            participants = session.query(TrainParticipant).join(
                TrainSchedule, TrainParticipant.schedule_id == TrainSchedule.id
            ).filter(
                TrainSchedule.guild_id == ctx.guild.id,
                TrainSchedule.is_active == True,
                TrainParticipant.is_active == True,
                TrainParticipant.user_id != None
            ).all()

            # Find unique Discord IDs with no twitch link and no active OAuth token
            seen = set()
            unlinked = []
            for p in participants:
                if p.user_id in seen:
                    continue
                seen.add(p.user_id)
                user = session.query(User).filter_by(id=p.user_id).first()
                token = session.query(TwitchOAuthToken).filter_by(user_id=p.user_id, is_active=True).first()
                if not (user and user.twitch_login) and not token:
                    unlinked.append((p.user_id, p.twitch_username or p.username or str(p.user_id)))

        if not unlinked:
            await ctx.send("✅ Everyone on the schedule has already linked their Twitch account!")
            return

        await ctx.send(f"📨 Sending link reminder DMs to **{len(unlinked)}** unlinked participants...")

        sent, failed = 0, []
        for discord_id, name in unlinked:
            try:
                user = await self.bot.fetch_user(discord_id)
                await user.send(
                    f"👋 Hey **{user.display_name}**!\n\n"
                    f"You're scheduled on the **Sunday Raid Train** but haven't linked your Twitch account to the bot yet.\n\n"
                    f"Linking takes 30 seconds and lets the bot:\n"
                    f"• Ping you before your slot so you don't miss it\n"
                    f"• Track your attendance automatically\n"
                    f"• Send you auto-shoutouts in Twitch chat\n\n"
                    f"**To link:** Go to the Game Lounge Discord and run `/twitchoauth`\n\n"
                    f"See you Sunday! 🚂"
                )
                sent += 1
                self.logger.info(f"Sent link reminder DM to {user} ({discord_id})")
            except discord.Forbidden:
                failed.append(f"<@{discord_id}> ({name}) — DMs closed")
            except discord.NotFound:
                failed.append(f"`{discord_id}` ({name}) — user not found")
            except Exception as e:
                failed.append(f"<@{discord_id}> ({name}) — {e}")

        result = f"✅ Sent DMs to **{sent}/{len(unlinked)}** unlinked participants."
        if failed:
            result += "\n\n⚠️ **Couldn't reach:**\n" + "\n".join(f"• {f}" for f in failed)
        await ctx.send(result)

    @commands.command(name='fixscheduletimes')
    @is_owner_or_trusted()
    async def fix_schedule_times(self, ctx):
        """
        Reset the Sunday raid train schedule to absolute correct BST times.
        Sets the 9 weekly slots to fixed values starting 5 PM BST Sunday (12pm EST).
        Idempotent — safe to run multiple times, result is always the same.
        """
        try:
            from database import DatabaseSession
            from models import TrainSchedule
            from datetime import time as dtime

            # Absolute correct BST times: 10am EST start, 90-min slots
            # day 6 = Sunday, day 0 = Monday (after midnight BST)
            CORRECT_SLOTS = [
                (6, dtime(15, 0),  'Sunday 03:00 PM Train'),
                (6, dtime(16, 30), 'Sunday 04:30 PM Train'),
                (6, dtime(18, 0),  'Sunday 06:00 PM Train'),
                (6, dtime(19, 30), 'Sunday 07:30 PM Train'),
                (6, dtime(21, 0),  'Sunday 09:00 PM Train'),
                (6, dtime(22, 30), 'Sunday 10:30 PM Train'),
                (0, dtime(0, 0),   'Sunday 12:00 AM Train'),
                (0, dtime(1, 30),  'Sunday 01:30 AM Train'),
                (0, dtime(3, 0),   'Sunday 03:00 AM Train'),
            ]

            days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
            fixed = []

            with DatabaseSession() as session:
                schedules = (
                    session.query(TrainSchedule)
                    .filter_by(guild_id=ctx.guild.id, is_active=True)
                    .order_by(TrainSchedule.id)
                    .all()
                )

                if not schedules:
                    await ctx.send("ℹ️ No active schedules found for this guild.")
                    return

                for s, (new_day, new_time, new_name) in zip(schedules, CORRECT_SLOTS):
                    old_info = f"{days[s.day_of_week]} {s.start_time.strftime('%H:%M')}"
                    s.day_of_week = new_day
                    s.start_time = new_time
                    s.name = new_name
                    fixed.append(f"ID {s.id}: {old_info} → {days[new_day]} {new_time.strftime('%H:%M')} | {new_name}")

                session.commit()

            report = "\n".join(fixed)
            await ctx.send(f"✅ Reset {len(fixed)} schedules to correct BST times:\n```\n{report}\n```")

        except Exception as e:
            self.logger.error(f"fixscheduletimes error: {e}", exc_info=True)
            await ctx.send(f"❌ Error: {e}")


    @commands.command(name='loadsundayschedule')
    @is_owner_or_trusted()
    async def load_sunday_schedule(self, ctx):
        """
        One-shot: create the June 1 2026 Sunday raid train schedule with participants.
        Safe to run multiple times — deletes any existing June 1 schedules first.
        """
        from database import DatabaseSession
        from models import TrainSchedule, TrainParticipant
        from datetime import date, time as dtime, datetime

        GUILD_ID = 1183084958110191616
        SCHEDULE_DATE = date(2026, 6, 1)

        # (day_of_week, UK start time, slot name, twitch_login, discord_id or 0)
        SLOTS = [
            (6, dtime(15,  0), 'Sunday 03:00 PM Train', 'stripyfuel7',          988045736140746852),
            (6, dtime(16, 30), 'Sunday 04:30 PM Train', 'chilledwolf40',        1176533358826750047),
            (6, dtime(18,  0), 'Sunday 06:00 PM Train', 'fiestymushy',          761438997502492693),
            (6, dtime(19, 30), 'Sunday 07:30 PM Train', 'spydermayhem17_tlc',   0),
            (6, dtime(21,  0), 'Sunday 09:00 PM Train', 'callofdutymw147gamin', 0),
            (6, dtime(22, 30), 'Sunday 10:30 PM Train', 'furry_gaymer96',       0),
            (0, dtime( 0,  0), 'Sunday 12:00 AM Train', 'mustang8105',          1032724675022835743),
            (0, dtime( 1, 30), 'Sunday 01:30 AM Train', 'mphgamingtv',          0),
            (0, dtime( 3,  0), 'Sunday 03:00 AM Train', 'fotusdragon420',       1296160935785795722),
        ]

        try:
            with DatabaseSession() as session:
                # Remove any existing June 1 schedules for this guild
                existing = session.query(TrainSchedule).filter_by(
                    guild_id=GUILD_ID,
                    specific_date=SCHEDULE_DATE
                ).all()
                for s in existing:
                    # Cascade-delete participants
                    session.query(TrainParticipant).filter_by(schedule_id=s.id).delete()
                    session.delete(s)
                session.flush()

                created = []
                for day_of_week, start_time, name, twitch_login, discord_id in SLOTS:
                    schedule = TrainSchedule(
                        guild_id=GUILD_ID,
                        name=name,
                        day_of_week=day_of_week,
                        start_time=start_time,
                        duration_minutes=90,
                        notify_before_minutes=60,
                        is_active=True,
                        description='90-minute raid train slot',
                        max_participants=1,
                        specific_date=SCHEDULE_DATE,
                        schedule_type='one-time',
                    )
                    session.add(schedule)
                    session.flush()  # get schedule.id

                    participant = TrainParticipant(
                        schedule_id=schedule.id,
                        guild_id=GUILD_ID,
                        user_id=discord_id,
                        username=twitch_login,
                        display_name=twitch_login,
                        twitch_username=twitch_login,
                        is_host=True,
                        is_active=True,
                        signed_up_at=datetime.utcnow(),
                        notes='Pre-loaded from schedule image',
                    )
                    session.add(participant)
                    created.append(f"ID {schedule.id} | {name} → {twitch_login}")

                session.commit()

            from utils.schedule_cache import invalidate_schedule_cache
            invalidate_schedule_cache(GUILD_ID)

            lines = "\n".join(created)
            await ctx.send(f"✅ Created {len(created)} slots for June 1 2026:\n```\n{lines}\n```")

        except Exception as e:
            self.logger.error(f"loadsundayschedule error: {e}", exc_info=True)
            await ctx.send(f"❌ Error: {e}")


    @commands.command(name='listparticipants', aliases=['lsparts', 'showparts'])
    @owner_only()
    async def list_participants(self, ctx):
        """List all active train participants in schedule order with their DB IDs."""
        try:
            with DatabaseSession() as session:
                schedules = session.query(TrainSchedule).filter_by(is_active=True).order_by(
                    TrainSchedule.specific_date.asc().nullslast(),
                    TrainSchedule.day_of_week.asc(),
                    TrainSchedule.start_time.asc()
                ).all()

                if not schedules:
                    await ctx.send("No active schedules found.")
                    return

                lines = []
                for sched in schedules:
                    date_str = sched.specific_date.strftime('%Y-%m-%d') if sched.specific_date else f"day {sched.day_of_week}"
                    time_str = sched.start_time.strftime('%H:%M') if sched.start_time else '??:??'
                    lines.append(f"\n**[Sched {sched.id}] {sched.name}** — {date_str} {time_str}")
                    parts = session.query(TrainParticipant).filter_by(
                        schedule_id=sched.id, is_active=True
                    ).order_by(TrainParticipant.signed_up_at.asc()).all()
                    if parts:
                        for p in parts:
                            uid_display = str(p.user_id) if p.user_id else "0 (unset)"
                            lines.append(f"  • Part#{p.id} `{p.username}` / `{p.display_name}` → discord_id: **{uid_display}**")
                    else:
                        lines.append("  *(no participants)*")

                # Send in chunks to avoid Discord 2000 char limit
                chunk = ""
                for line in lines:
                    if len(chunk) + len(line) + 1 > 1900:
                        await ctx.send(chunk)
                        chunk = line
                    else:
                        chunk += "\n" + line if chunk else line
                if chunk:
                    await ctx.send(chunk)

        except Exception as e:
            self.logger.error(f"listparticipants error: {e}", exc_info=True)
            await ctx.send(f"❌ Error: {e}")

    @commands.command(name='setparticipantid', aliases=['setpartid', 'fixpart'])
    @owner_only()
    async def set_participant_id(self, ctx, participant_id: int = None, discord_id: int = None):
        """Set the Discord user_id for a train participant by their DB row ID.
        Usage: !setparticipantid <participant_db_id> <discord_user_id>
        Run !listparticipants first to see participant DB IDs."""
        if participant_id is None or discord_id is None:
            await ctx.send(
                "Usage: `!setparticipantid <participant_db_id> <discord_user_id>`\n"
                "Run `!listparticipants` first to see DB row IDs."
            )
            return
        try:
            with DatabaseSession() as session:
                participant = session.query(TrainParticipant).filter_by(id=participant_id).first()
                if not participant:
                    await ctx.send(f"❌ No participant found with DB id **{participant_id}**.")
                    return

                old_id = participant.user_id
                participant.user_id = discord_id

                # Also try to update display_name from the guild member
                member = ctx.guild.get_member(discord_id) if ctx.guild else None
                if member:
                    participant.display_name = member.display_name
                    participant.username = member.name

                session.commit()

                sched = session.query(TrainSchedule).filter_by(id=participant.schedule_id).first()
                sched_name = sched.name if sched else f"schedule {participant.schedule_id}"

                mention = f"<@{discord_id}>"
                await ctx.send(
                    f"✅ Updated **Part#{participant_id}** (`{participant.username}`) in **{sched_name}**\n"
                    f"Discord ID: `{old_id}` → `{discord_id}` ({mention})"
                )
                self.logger.info(f"setparticipantid: Part#{participant_id} updated {old_id} → {discord_id} by {ctx.author}")

        except Exception as e:
            self.logger.error(f"setparticipantid error: {e}", exc_info=True)
            await ctx.send(f"❌ Error: {e}")


    @commands.command(name='settiktokfeed', aliases=['tiktokfeed'])
    async def set_tiktok_feed(self, ctx, channel: discord.TextChannel = None):
        """
        Owner/trusted only: set a channel to auto-repost TikTok links posted anywhere in this server.
        Run without a channel to disable.
        """
        if not await self.bot.is_owner_or_trusted(ctx.author):
            await ctx.send("❌ This command requires owner or trusted access.")
            return
        if not ctx.guild:
            await ctx.send("❌ Server only.")
            return
        try:
            with DatabaseSession() as session:
                guild_data = session.query(Guild).filter_by(id=ctx.guild.id).first()
                if not guild_data:
                    guild_data = Guild(id=ctx.guild.id, name=ctx.guild.name,
                                       owner_id=ctx.guild.owner_id, member_count=ctx.guild.member_count)
                    session.add(guild_data)
                guild_data.tiktok_feed_channel_id = channel.id if channel else None
                session.commit()
            if channel:
                embed = discord.Embed(
                    title="✅ TikTok Feed Set",
                    description=f"TikTok videos posted anywhere in this server will be reposted to {channel.mention}.",
                    color=0x010101
                )
                embed.add_field(
                    name="ℹ️ How it works",
                    value=(
                        "• Any message containing a TikTok link triggers a repost\n"
                        "• The embed shows the video title, creator, and thumbnail\n"
                        "• Links posted directly in the feed channel are not re-echoed\n"
                        "• Up to 3 TikTok links per message are reposted"
                    ),
                    inline=False
                )
                await ctx.send(embed=embed)
            else:
                await ctx.send("✅ TikTok feed disabled.")
        except Exception as e:
            await ctx.send(f"❌ Error: {e}")
            self.logger.error(f"settiktokfeed error: {e}")

    @commands.command(name='tiktokwebhookurl', aliases=['tiktokwebhook', 'tiktokhook'])
    async def tiktok_webhook_url(self, ctx):
        """
        Owner/trusted only: generate (or retrieve) this server's unique TikTok
        inbound webhook URL for use with Make, Zapier, or IFTTT.
        """
        if not await self.bot.is_owner_or_trusted(ctx.author):
            await ctx.send("❌ This command requires owner or trusted access.")
            return
        if not ctx.guild:
            await ctx.send("❌ Server only.")
            return
        try:
            import secrets as _secrets
            from utils.bot_urls import get_oauth_base_url
            with DatabaseSession() as session:
                guild_data = session.query(Guild).filter_by(id=ctx.guild.id).first()
                if not guild_data:
                    guild_data = Guild(id=ctx.guild.id, name=ctx.guild.name,
                                       owner_id=ctx.guild.owner_id, member_count=ctx.guild.member_count)
                    session.add(guild_data)
                if not guild_data.tiktok_webhook_token:
                    guild_data.tiktok_webhook_token = _secrets.token_urlsafe(32)
                token = guild_data.tiktok_webhook_token
                feed_ch_id = guild_data.tiktok_feed_channel_id
                session.commit()

            base = get_oauth_base_url()
            webhook_url = f"{base}/webhooks/tiktok/{ctx.guild.id}?token={token}"

            feed_note = (
                f"✅ Feed channel is set."
                if feed_ch_id else
                "⚠️ No feed channel set yet — run `!settiktokfeed #channel` first."
            )

            embed = discord.Embed(
                title="📲 TikTok Webhook URL",
                description=(
                    "Use this URL in **Make**, **Zapier**, or **IFTTT** to auto-post "
                    "your new TikTok videos to Discord.\n\n"
                    f"**Status:** {feed_note}"
                ),
                color=0x010101
            )
            embed.add_field(
                name="🔗 Your Webhook URL",
                value=f"```\n{webhook_url}\n```",
                inline=False
            )
            embed.add_field(
                name="📱 IFTTT Setup (Easiest — free, has mobile app)",
                value=(
                    "1. Go to **ifttt.com** → Create\n"
                    "2. **If This:** TikTok → New video by you\n"
                    "3. **Then That:** Webhooks → Make a web request\n"
                    "4. URL: paste your webhook URL above\n"
                    "5. Method: **POST** | Content Type: **application/json**\n"
                    "6. Body: `{\"url\": \"{{VideoUrl}}\"}`"
                ),
                inline=False
            )
            embed.add_field(
                name="⚙️ Make / Zapier",
                value=(
                    "Use a **TikTok → New Video** trigger, then an **HTTP POST** action "
                    "to the webhook URL with body `{\"url\": \"<video url field>\"}`."
                ),
                inline=False
            )
            embed.set_footer(text="Keep this URL private — anyone with it can post to your feed channel.")
            await ctx.send(embed=embed)
        except Exception as e:
            await ctx.send(f"❌ Error: {e}")
            self.logger.error(f"tiktokwebhookurl error: {e}")

    @commands.command(name='serverroles', aliases=['rolepermissions', 'roleaccess'])
    async def server_roles(self, ctx):
        """
        Owner-only: show which roles in this server can use the bot without
        being explicitly added to the trusted roles list.

        Roles with Administrator or Manage Server automatically pass the
        admin_or_trusted permission check.
        """
        if not await self.bot.is_owner(ctx.author):
            await ctx.send("❌ This command is restricted to the bot owner.")
            return

        if not ctx.guild:
            await ctx.send("❌ Run this inside a server.")
            return

        admin_roles = []
        manage_roles = []

        for role in ctx.guild.roles:
            if role.is_default():
                continue
            perms = role.permissions
            if perms.administrator:
                admin_roles.append(role)
            elif perms.manage_guild:
                manage_roles.append(role)

        # Fetch explicitly trusted roles from DB
        with DatabaseSession() as session:
            trusted_rows = session.query(TrustedRole).filter_by(guild_id=ctx.guild.id).all()
            trusted_role_ids = {tr.role_id for tr in trusted_rows}

        explicit_roles = [ctx.guild.get_role(rid) for rid in trusted_role_ids]
        explicit_roles = [r for r in explicit_roles if r]

        embed = discord.Embed(
            title=f"🔐 Bot Access — {ctx.guild.name}",
            description=(
                "Roles listed here can use bot admin/trusted commands **without** being "
                "added to the trusted roles list.\n\n"
                "**How implicit access works:**\n"
                "• **Administrator** → full bot access automatically\n"
                "• **Manage Server** → admin-level bot access automatically"
            ),
            color=0x9146FF
        )

        if admin_roles:
            embed.add_field(
                name=f"🛡️ Administrator Roles ({len(admin_roles)})",
                value="\n".join(r.mention for r in admin_roles) or "None",
                inline=False
            )
        else:
            embed.add_field(name="🛡️ Administrator Roles", value="None", inline=False)

        if manage_roles:
            embed.add_field(
                name=f"⚙️ Manage Server Roles ({len(manage_roles)})",
                value="\n".join(r.mention for r in manage_roles) or "None",
                inline=False
            )
        else:
            embed.add_field(name="⚙️ Manage Server Roles", value="None", inline=False)

        if explicit_roles:
            embed.add_field(
                name=f"✅ Explicitly Trusted Roles ({len(explicit_roles)})",
                value="\n".join(r.mention for r in explicit_roles),
                inline=False
            )
        else:
            embed.add_field(
                name="✅ Explicitly Trusted Roles",
                value="None — use `/addtrustedrole` to add some",
                inline=False
            )

        total = len(admin_roles) + len(manage_roles) + len(explicit_roles)
        embed.set_footer(text=f"{total} role(s) total have bot access in this server")
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(AdminCommands(bot))