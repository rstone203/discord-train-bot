#!/usr/bin/env python3
"""Temporary cog for cleaning up redundant trusted users."""

import discord
from discord import app_commands
from discord.ext import commands
import logging
from database import DatabaseSession
from models import TrustedUser
from utils.slash_permissions import owner_only
from datetime import datetime

GAME_LOUNGE_ID = 1183084958110191616
NOTIFICATION_CHANNEL_ID = 1200514710777368586

class CleanupTrustedCommand(commands.Cog):
    """Cleanup redundant trusted users."""
    
    def __init__(self, bot):
        self.bot = bot
        self.logger = logging.getLogger('discord_bot.cleanup_trusted')
    
    @app_commands.command(name='cleanuptrusted', description='[Owner] Remove redundant trusted users with Administrator permission')
    @owner_only()
    async def cleanup_trusted(self, interaction: discord.Interaction):
        """Remove trusted users who have Administrator permission in Game Lounge."""
        
        # Send immediate response to avoid timeout
        await interaction.response.send_message(
            "🔄 Starting cleanup... This may take a moment.",
            ephemeral=True
        )
        
        try:
            # Get Game Lounge server
            guild = self.bot.get_guild(GAME_LOUNGE_ID)
            if not guild:
                await interaction.edit_original_response(
                    content=f"❌ Could not find Game Lounge server (ID: {GAME_LOUNGE_ID})"
                )
                return
            
            self.logger.info(f"✅ Found server: {guild.name}")
            
            # Get notification channel
            notification_channel = guild.get_channel(NOTIFICATION_CHANNEL_ID)
            if not notification_channel:
                await interaction.edit_original_response(
                    content=f"❌ Could not find notification channel (ID: {NOTIFICATION_CHANNEL_ID})"
                )
                return
            
            self.logger.info(f"✅ Found notification channel: {notification_channel.name}")
            
            # Get all active trusted users from database
            with DatabaseSession() as session:
                trusted_users = session.query(TrustedUser).filter_by(is_active=True).all()
                
                self.logger.info(f"📋 Found {len(trusted_users)} active trusted users")
                
                users_to_remove = []
                status_lines = []
                
                for trusted_user in trusted_users:
                    user_id = trusted_user.user_id
                    
                    # Check if user is in Game Lounge
                    member = guild.get_member(user_id)
                    
                    if not member:
                        status_lines.append(f"⚪ {trusted_user.display_name or trusted_user.username} - Not in Game Lounge")
                        continue
                    
                    # Check if member has Administrator permission
                    if member.guild_permissions.administrator:
                        users_to_remove.append({
                            'id': trusted_user.id,
                            'user_id': user_id,
                            'username': trusted_user.username,
                            'display_name': trusted_user.display_name or trusted_user.username,
                            'member': member
                        })
                        status_lines.append(f"🔴 {trusted_user.display_name or trusted_user.username} - Has Administrator (removing)")
                    else:
                        status_lines.append(f"🟢 {trusted_user.display_name or trusted_user.username} - No Administrator (keeping)")
                
                if not users_to_remove:
                    msg = "✅ No redundant trusted users found!\n\nAll trusted users either don't have Administrator permission or aren't in this server."
                    await interaction.edit_original_response(content=msg)
                    await notification_channel.send(
                        "✅ **Trusted User Cleanup Complete**\n\n"
                        "No redundant trusted users were found. All trusted users either don't have Administrator permission or aren't in this server."
                    )
                    return
                
                self.logger.info(f"🧹 Removing {len(users_to_remove)} redundant trusted users")
                
                # Build notification message
                removed_users_list = []
                for user_data in users_to_remove:
                    # Remove from database
                    db_user = session.query(TrustedUser).filter_by(id=user_data['id']).first()
                    db_user.is_active = False
                    old_notes = db_user.notes or ''
                    db_user.notes = f"Removed - has Administrator permission (redundant). {old_notes}".strip()
                    
                    removed_users_list.append(f"• {user_data['member'].mention} ({user_data['display_name']})")
                    self.logger.info(f"  ✅ Removed: {user_data['display_name']}")
                
                session.commit()
                
                # Send notification to channel AFTER cleanup completes
                embed = discord.Embed(
                    title="🧹 Trusted User Cleanup Complete",
                    description=(
                        f"**{len(users_to_remove)} user(s)** were removed from the Trusted Users list.\n\n"
                        "**📋 Who was removed:**\n"
                        + "\n".join(removed_users_list) + "\n\n"
                        "**❓ Why were they removed?**\n"
                        "These users have Discord's **Administrator permission**, which already gives them full access to all bot commands. "
                        "The Trusted User system is meant for people who need bot access *without* having full server admin rights.\n\n"
                        "**✅ What does this mean?**\n"
                        "Nothing changes! If you were removed, you still have full access to all bot commands through your Administrator permission. "
                        "This cleanup just removes redundant permission entries."
                    ),
                    color=0x00ff00,
                    timestamp=datetime.utcnow()
                )
                
                embed.set_footer(text="Cleanup helps keep permissions organized and prevents duplication")
                
                await notification_channel.send(embed=embed)
                self.logger.info(f"✅ Notification sent to #{notification_channel.name}")
                
                # Send summary to command user
                summary = f"✅ **Cleanup Complete!**\n\nRemoved {len(users_to_remove)} redundant trusted users:\n" + "\n".join(removed_users_list)
                summary += f"\n\n📢 Notification sent to <#{NOTIFICATION_CHANNEL_ID}>"
                await interaction.edit_original_response(content=summary)
                
        except Exception as e:
            self.logger.error(f"Error cleaning up trusted users: {e}", exc_info=True)
            try:
                await interaction.edit_original_response(content=f"❌ Error: {str(e)}")
            except:
                # If we can't edit, try followup
                await interaction.followup.send(f"❌ Error: {str(e)}", ephemeral=True)

async def setup(bot):
    await bot.add_cog(CleanupTrustedCommand(bot))
