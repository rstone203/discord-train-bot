"""
AFK Command - Auto-reply when mentioned while AFK
"""

import discord
from discord import app_commands
from discord.ext import commands
from database import get_db_session
from models import AFKStatus
from datetime import datetime
from models import get_est_time
import logging

logger = logging.getLogger('discord_bot.afk_command')

class AFKCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.afk_cooldown = {}

    @app_commands.command(name="afk", description="Set your AFK status with optional message")
    @app_commands.describe(message="Optional AFK message (e.g., 'At lunch, back in 30 mins')")
    async def afk(self, interaction: discord.Interaction, message: str = None):
        await interaction.response.defer(ephemeral=True)
        
        session = get_db_session()
        try:
            existing = session.query(AFKStatus).filter_by(
                user_id=interaction.user.id,
                guild_id=interaction.guild_id
            ).first()
            
            if existing:
                session.delete(existing)
                session.commit()
            
            afk_status = AFKStatus(
                user_id=interaction.user.id,
                guild_id=interaction.guild_id,
                afk_message=message
            )
            
            session.add(afk_status)
            session.commit()
            
            if message:
                await interaction.followup.send(
                    f"💤 **AFK Status Set**\n"
                    f"Message: {message}\n"
                    f"When someone mentions you, they'll see this message!",
                    ephemeral=True
                )
            else:
                await interaction.followup.send(
                    f"💤 **AFK Status Set**\n"
                    f"When someone mentions you, they'll know you're away!",
                    ephemeral=True
                )
            
            logger.info(f"User {interaction.user.name} set AFK status in {interaction.guild.name}")
            
        except Exception as e:
            session.rollback()
            logger.error(f"Error setting AFK status: {e}")
            await interaction.followup.send(f"❌ Error setting AFK status: {e}", ephemeral=True)
        finally:
            session.close()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        
        session = get_db_session()
        try:
            author_afk = session.query(AFKStatus).filter_by(
                user_id=message.author.id,
                guild_id=message.guild.id
            ).first()
            
            if author_afk:
                session.delete(author_afk)
                session.commit()
                
                try:
                    await message.channel.send(
                        f"👋 Welcome back, {message.author.mention}! Your AFK status has been removed.",
                        delete_after=10
                    )
                except:
                    pass
                
                logger.info(f"Removed AFK status for {message.author.name} in {message.guild.name}")
            
            if message.mentions:
                for mentioned_user in message.mentions:
                    if mentioned_user.bot:
                        continue
                    
                    cooldown_key = (message.guild.id, message.author.id, mentioned_user.id)
                    now = get_est_time()
                    
                    if cooldown_key in self.afk_cooldown:
                        last_notif = self.afk_cooldown[cooldown_key]
                        if (now - last_notif).total_seconds() < 300:
                            continue
                    
                    afk = session.query(AFKStatus).filter_by(
                        user_id=mentioned_user.id,
                        guild_id=message.guild.id
                    ).first()
                    
                    if afk:
                        time_elapsed = get_est_time() - afk.set_at
                        hours = int(time_elapsed.total_seconds() // 3600)
                        minutes = int((time_elapsed.total_seconds() % 3600) // 60)
                        
                        time_str = ""
                        if hours > 0:
                            time_str = f"{hours}h {minutes}m ago"
                        else:
                            time_str = f"{minutes}m ago"
                        
                        afk_msg = f"💤 **{mentioned_user.display_name}** is AFK (set {time_str})"
                        if afk.afk_message:
                            afk_msg += f"\n📝 Message: {afk.afk_message}"
                        
                        try:
                            await message.reply(afk_msg, mention_author=False, delete_after=15)
                            self.afk_cooldown[cooldown_key] = now
                        except:
                            pass
            
        except Exception as e:
            logger.error(f"Error in AFK message handler: {e}")
        finally:
            session.close()

async def setup(bot):
    await bot.add_cog(AFKCog(bot))
    logger.info("AFK command cog loaded")
