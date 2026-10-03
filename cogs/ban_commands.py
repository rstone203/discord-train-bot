"""
Commands for managing banned users from train schedules.
"""

import discord
from discord.ext import commands
from discord import app_commands
from sqlalchemy.orm import Session
from models import BannedUser, get_est_time
from database import get_db_session
import logging
from typing import Optional


class UserOrID(commands.Converter):
    """Accepts a user mention, username, or raw user ID."""
    async def convert(self, ctx, argument):
        # Try the built-in converter first (handles mentions + cached users)
        try:
            return await commands.UserConverter().convert(ctx, argument)
        except commands.BadArgument:
            pass
        # Fall back to a direct API fetch by ID
        try:
            user_id = int(argument.strip())
            return await ctx.bot.fetch_user(user_id)
        except (ValueError, discord.NotFound):
            raise commands.BadArgument(
                f"Could not find a user with ID or mention `{argument}`. "
                "Make sure the ID is correct."
            )


class BanCommands(commands.Cog):
    """Ban management commands for train schedules."""
    
    def __init__(self, bot):
        self.bot = bot
        self.logger = logging.getLogger('discord_bot.ban_commands')
    
    @commands.command(name='banuser')
    @commands.has_permissions(administrator=True)
    async def ban_user(self, ctx, user: UserOrID, *, reason: Optional[str] = None):
        """Ban a user from joining train schedules across all servers.
        
        Usage: !banuser @user [reason]  OR  !banuser <user_id> [reason]
        Example: !banuser @JohnDoe Not following train rules
        Example: !banuser 123456789012345678 Not following train rules
        """
        session: Session = get_db_session()
        try:
            # Check if user has any existing ban record (active or inactive)
            existing_ban = session.query(BannedUser).filter(
                BannedUser.user_id == user.id
            ).first()
            
            if existing_ban:
                # If already actively banned, notify
                if existing_ban.is_active:
                    await ctx.send(f"❌ {user.mention} is already banned from trains.")
                    return
                
                # Reactivate existing ban record
                existing_ban.is_active = True
                existing_ban.reason = reason
                existing_ban.banned_by = ctx.author.id
                existing_ban.banned_at = get_est_time()  # Reset timestamp to now
                existing_ban.username = f"{user.name}#{user.discriminator}" if user.discriminator != "0" else user.name
            else:
                # Create new ban record
                new_ban = BannedUser(
                    user_id=user.id,
                    username=f"{user.name}#{user.discriminator}" if user.discriminator != "0" else user.name,
                    reason=reason,
                    banned_by=ctx.author.id,
                    is_active=True
                )
                session.add(new_ban)
            
            session.commit()
            
            # Try to send DM to the banned user
            dm_sent = False
            try:
                reason_dm = f"\n**Reason:** {reason}" if reason else "\n**Reason:** No reason provided"
                await user.send(
                    f"🚫 **You have been banned from joining train schedules**\n\n"
                    f"Banned by: {ctx.author.name}{reason_dm}\n\n"
                    f"You will not be able to join any train schedules. "
                    f"If you believe this is a mistake, please contact a server administrator."
                )
                dm_sent = True
            except discord.Forbidden:
                self.logger.warning(f"Could not DM user {user.id} about ban - DMs are disabled")
            except Exception as dm_error:
                self.logger.error(f"Error sending DM to banned user {user.id}: {dm_error}")
            
            reason_text = f"\n**Reason:** {reason}" if reason else ""
            dm_status = "✅ User has been notified via DM" if dm_sent else "⚠️ Could not DM user (DMs disabled)"
            await ctx.send(
                f"🚫 **User Banned from Trains**\n"
                f"User: {user.mention} (`{user.id}`)\n"
                f"Banned by: {ctx.author.mention}{reason_text}\n\n"
                f"This user can no longer join train schedules on any server.\n"
                f"{dm_status}"
            )
            
            self.logger.info(f"User {user.id} banned from trains by {ctx.author.id}. Reason: {reason}. DM sent: {dm_sent}")
            
        except Exception as e:
            session.rollback()
            self.logger.error(f"Error banning user: {e}")
            await ctx.send(f"❌ Error banning user: {str(e)}")
        finally:
            session.close()
    
    @app_commands.command(name="banuser", description="Ban a user from joining train schedules")
    @app_commands.describe(
        user="Pick the user from the list (optional if using user_id)",
        user_id="Paste the user's Discord ID instead of picking (optional if using user)",
        reason="Reason for the ban"
    )
    @app_commands.default_permissions(administrator=True)
    async def ban_user_slash(self, interaction: discord.Interaction, user: Optional[discord.User] = None, user_id: Optional[str] = None, reason: Optional[str] = None):
        """Ban a user from joining train schedules (slash command)."""
        # Resolve the target user from either the picker or a raw ID
        if user is None and user_id is None:
            await interaction.response.send_message("❌ Please provide either a user (picker) or a user_id.", ephemeral=True)
            return
        if user is None:
            try:
                user = await interaction.client.fetch_user(int(user_id.strip()))
            except (ValueError, discord.NotFound):
                await interaction.response.send_message(f"❌ Could not find a user with ID `{user_id}`. Double-check the ID.", ephemeral=True)
                return

        session: Session = get_db_session()
        try:
            # Check if user has any existing ban record (active or inactive)
            existing_ban = session.query(BannedUser).filter(
                BannedUser.user_id == user.id
            ).first()
            
            if existing_ban:
                # If already actively banned, notify
                if existing_ban.is_active:
                    await interaction.response.send_message(
                        f"❌ {user.mention} is already banned from trains.",
                        ephemeral=True
                    )
                    return
                
                # Reactivate existing ban record
                existing_ban.is_active = True
                existing_ban.reason = reason
                existing_ban.banned_by = interaction.user.id
                existing_ban.banned_at = get_est_time()  # Reset timestamp to now
                existing_ban.username = f"{user.name}#{user.discriminator}" if user.discriminator != "0" else user.name
            else:
                # Create new ban record
                new_ban = BannedUser(
                    user_id=user.id,
                    username=f"{user.name}#{user.discriminator}" if user.discriminator != "0" else user.name,
                    reason=reason,
                    banned_by=interaction.user.id,
                    is_active=True
                )
                session.add(new_ban)
            
            session.commit()
            
            # Try to send DM to the banned user
            dm_sent = False
            try:
                reason_dm = f"\n**Reason:** {reason}" if reason else "\n**Reason:** No reason provided"
                await user.send(
                    f"🚫 **You have been banned from joining train schedules**\n\n"
                    f"Banned by: {interaction.user.name}{reason_dm}\n\n"
                    f"You will not be able to join any train schedules. "
                    f"If you believe this is a mistake, please contact a server administrator."
                )
                dm_sent = True
            except discord.Forbidden:
                self.logger.warning(f"Could not DM user {user.id} about ban - DMs are disabled")
            except Exception as dm_error:
                self.logger.error(f"Error sending DM to banned user {user.id}: {dm_error}")
            
            reason_text = f"\n**Reason:** {reason}" if reason else ""
            dm_status = "✅ User has been notified via DM" if dm_sent else "⚠️ Could not DM user (DMs disabled)"
            await interaction.response.send_message(
                f"🚫 **User Banned from Trains**\n"
                f"User: {user.mention} (`{user.id}`)\n"
                f"Banned by: {interaction.user.mention}{reason_text}\n\n"
                f"This user can no longer join train schedules on any server.\n"
                f"{dm_status}",
                ephemeral=False
            )
            
            self.logger.info(f"User {user.id} banned from trains by {interaction.user.id}. Reason: {reason}. DM sent: {dm_sent}")
            
        except Exception as e:
            session.rollback()
            self.logger.error(f"Error banning user: {e}")
            await interaction.response.send_message(f"❌ Error banning user: {str(e)}", ephemeral=True)
        finally:
            session.close()
    
    @commands.command(name='unbanuser')
    @commands.has_permissions(administrator=True)
    async def unban_user(self, ctx, user: UserOrID):
        """Unban a user from train schedules.
        
        Usage: !unbanuser @user  OR  !unbanuser <user_id>
        Example: !unbanuser @JohnDoe
        Example: !unbanuser 123456789012345678
        """
        session: Session = get_db_session()
        try:
            # Find active ban
            ban = session.query(BannedUser).filter(
                BannedUser.user_id == user.id,
                BannedUser.is_active == True
            ).first()
            
            if not ban:
                await ctx.send(f"❌ {user.mention} is not currently banned from trains.")
                return
            
            # Deactivate ban
            ban.is_active = False
            session.commit()
            
            await ctx.send(
                f"✅ **User Unbanned from Trains**\n"
                f"User: {user.mention} (`{user.id}`)\n"
                f"Unbanned by: {ctx.author.mention}\n\n"
                f"This user can now join train schedules again."
            )
            
            self.logger.info(f"User {user.id} unbanned from trains by {ctx.author.id}")
            
        except Exception as e:
            session.rollback()
            self.logger.error(f"Error unbanning user: {e}")
            await ctx.send(f"❌ Error unbanning user: {str(e)}")
        finally:
            session.close()
    
    @app_commands.command(name="unbanuser", description="Unban a user from train schedules")
    @app_commands.describe(
        user="Pick the user from the list (optional if using user_id)",
        user_id="Paste the user's Discord ID instead of picking (optional if using user)"
    )
    @app_commands.default_permissions(administrator=True)
    async def unban_user_slash(self, interaction: discord.Interaction, user: Optional[discord.User] = None, user_id: Optional[str] = None):
        """Unban a user from train schedules (slash command)."""
        # Resolve the target user from either the picker or a raw ID
        if user is None and user_id is None:
            await interaction.response.send_message("❌ Please provide either a user (picker) or a user_id.", ephemeral=True)
            return
        if user is None:
            try:
                user = await interaction.client.fetch_user(int(user_id.strip()))
            except (ValueError, discord.NotFound):
                await interaction.response.send_message(f"❌ Could not find a user with ID `{user_id}`. Double-check the ID.", ephemeral=True)
                return

        session: Session = get_db_session()
        try:
            # Find active ban
            ban = session.query(BannedUser).filter(
                BannedUser.user_id == user.id,
                BannedUser.is_active == True
            ).first()
            
            if not ban:
                await interaction.response.send_message(
                    f"❌ {user.mention} is not currently banned from trains.",
                    ephemeral=True
                )
                return
            
            # Deactivate ban
            ban.is_active = False
            session.commit()
            
            await interaction.response.send_message(
                f"✅ **User Unbanned from Trains**\n"
                f"User: {user.mention} (`{user.id}`)\n"
                f"Unbanned by: {interaction.user.mention}\n\n"
                f"This user can now join train schedules again.",
                ephemeral=False
            )
            
            self.logger.info(f"User {user.id} unbanned from trains by {interaction.user.id}")
            
        except Exception as e:
            session.rollback()
            self.logger.error(f"Error unbanning user: {e}")
            await interaction.response.send_message(f"❌ Error unbanning user: {str(e)}", ephemeral=True)
        finally:
            session.close()
    
    @commands.command(name='listbans')
    @commands.has_permissions(administrator=True)
    async def list_bans(self, ctx):
        """List all users currently banned from trains.
        
        Usage: !listbans
        """
        session: Session = get_db_session()
        try:
            # Get all active bans
            bans = session.query(BannedUser).filter(
                BannedUser.is_active == True
            ).order_by(BannedUser.banned_at.desc()).all()
            
            if not bans:
                await ctx.send("✅ No users are currently banned from trains.")
                return
            
            # Create embed with ban list
            embed = discord.Embed(
                title="🚫 Banned Users from Train Schedules",
                description=f"Total banned users: {len(bans)}",
                color=discord.Color.red()
            )
            
            for ban in bans[:25]:  # Limit to 25 to avoid embed limits
                user_info = f"<@{ban.user_id}> (`{ban.user_id}`)"
                banned_by_info = f"<@{ban.banned_by}>"
                reason_text = ban.reason if ban.reason else "No reason provided"
                
                embed.add_field(
                    name=f"{ban.username}",
                    value=f"**User:** {user_info}\n**Banned by:** {banned_by_info}\n**Reason:** {reason_text}\n**Date:** {ban.banned_at.strftime('%Y-%m-%d %H:%M')}",
                    inline=False
                )
            
            if len(bans) > 25:
                embed.set_footer(text=f"Showing 25 of {len(bans)} banned users")
            
            await ctx.send(embed=embed)
            
        except Exception as e:
            self.logger.error(f"Error listing bans: {e}")
            await ctx.send(f"❌ Error listing bans: {str(e)}")
        finally:
            session.close()
    
    @app_commands.command(name="listbans", description="List all users banned from train schedules")
    @app_commands.default_permissions(administrator=True)
    async def list_bans_slash(self, interaction: discord.Interaction):
        """List all users currently banned from trains (slash command)."""
        session: Session = get_db_session()
        try:
            # Get all active bans
            bans = session.query(BannedUser).filter(
                BannedUser.is_active == True
            ).order_by(BannedUser.banned_at.desc()).all()
            
            if not bans:
                await interaction.response.send_message(
                    "✅ No users are currently banned from trains.",
                    ephemeral=True
                )
                return
            
            # Create embed with ban list
            embed = discord.Embed(
                title="🚫 Banned Users from Train Schedules",
                description=f"Total banned users: {len(bans)}",
                color=discord.Color.red()
            )
            
            for ban in bans[:25]:  # Limit to 25 to avoid embed limits
                user_info = f"<@{ban.user_id}> (`{ban.user_id}`)"
                banned_by_info = f"<@{ban.banned_by}>"
                reason_text = ban.reason if ban.reason else "No reason provided"
                
                embed.add_field(
                    name=f"{ban.username}",
                    value=f"**User:** {user_info}\n**Banned by:** {banned_by_info}\n**Reason:** {reason_text}\n**Date:** {ban.banned_at.strftime('%Y-%m-%d %H:%M')}",
                    inline=False
                )
            
            if len(bans) > 25:
                embed.set_footer(text=f"Showing 25 of {len(bans)} banned users")
            
            await interaction.response.send_message(embed=embed, ephemeral=True)
            
        except Exception as e:
            self.logger.error(f"Error listing bans: {e}")
            await interaction.response.send_message(f"❌ Error listing bans: {str(e)}", ephemeral=True)
        finally:
            session.close()

async def setup(bot):
    """Add the cog to the bot."""
    await bot.add_cog(BanCommands(bot))
