"""
Database-related commands for the Discord bot.
"""

import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timedelta
from database import DatabaseSession
from models import Guild, User, Message, CommandLog, BotStats

class DatabaseCommands(commands.Cog):
    """Database management and statistics commands."""
    
    def __init__(self, bot):
        self.bot = bot
    
    async def respond(self, interaction, *args, **kwargs):
        """Helper method to safely respond to an interaction."""
        if interaction.response.is_done():
            if 'view' in kwargs and kwargs['view'] is None:
                del kwargs['view']
            return await interaction.followup.send(*args, **kwargs)
        return await interaction.response.send_message(*args, **kwargs)
        
    @app_commands.command(name='dbstats', description='Show database statistics')
    @app_commands.checks.cooldown(1, 60.0, key=lambda i: (i.guild_id, i.user.id))
    async def database_stats(self, interaction: discord.Interaction):
        """Show database statistics."""
        if not self.bot.db_manager:
            await self.respond(interaction, "❌ Database is not connected")
            return
            
        try:
            with DatabaseSession() as session:
                # Get counts
                guild_count = session.query(Guild).filter(Guild.is_active == True).count()
                user_count = session.query(User).count()
                message_count = session.query(Message).count()
                forwarded_count = session.query(Message).filter(Message.was_forwarded == True).count()
                command_count = session.query(CommandLog).count()
                
                # Get recent activity (last 24 hours)
                yesterday = datetime.utcnow() - timedelta(days=1)
                recent_messages = session.query(Message).filter(Message.timestamp >= yesterday).count()
                recent_commands = session.query(CommandLog).filter(CommandLog.timestamp >= yesterday).count()
                recent_forwards = session.query(Message).filter(
                    Message.timestamp >= yesterday,
                    Message.was_forwarded == True
                ).count()
                
                embed = discord.Embed(
                    title="📊 Database Statistics",
                    color=0x00ff00,
                    timestamp=datetime.utcnow()
                )
                
                embed.add_field(
                    name="📈 Total Counts",
                    value=f"**Guilds:** {guild_count:,}\n"
                          f"**Users:** {user_count:,}\n"
                          f"**Messages:** {message_count:,}\n"
                          f"**Forwarded:** {forwarded_count:,}\n"
                          f"**Commands:** {command_count:,}",
                    inline=True
                )
                
                embed.add_field(
                    name="⚡ Last 24 Hours",
                    value=f"**Messages:** {recent_messages:,}\n"
                          f"**Commands:** {recent_commands:,}\n"
                          f"**Forwarded:** {recent_forwards:,}",
                    inline=True
                )
                
                # Database status
                embed.add_field(
                    name="💾 Database Status",
                    value="✅ Connected\n🟢 Operational",
                    inline=True
                )
                
                embed.set_footer(text="Database statistics updated in real-time")
                
                await self.respond(interaction, embed=embed)
                
        except Exception as e:
            self.bot.logger.error(f"Error getting database stats: {e}")
            await interaction.followup.send("❌ Failed to retrieve database statistics")
    
    @app_commands.command(name='topusers', description='Show top users by message count')
    async def top_users(self, interaction: discord.Interaction, limit: int = 10):
        """Show top users by message count."""
        if not self.bot.db_manager:
            await self.respond(interaction, "❌ Database is not connected")
            return
            
        if limit > 20:
            limit = 20
        elif limit < 1:
            limit = 10
            
        try:
            with DatabaseSession() as session:
                top_users = session.query(User).filter(
                    User.is_bot == False
                ).order_by(User.message_count.desc()).limit(limit).all()
                
                if not top_users:
                    await self.respond(interaction, "No user data found in database")
                    return
                
                embed = discord.Embed(
                    title=f"🏆 Top {limit} Users by Message Count",
                    color=0xffd700,
                    timestamp=datetime.utcnow()
                )
                
                description = ""
                for i, user in enumerate(top_users, 1):
                    medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
                    description += f"{medal} **{user.display_name or user.username}** - {user.message_count:,} messages\n"
                
                embed.description = description
                embed.set_footer(text="Based on messages tracked since database setup")
                
                await self.respond(interaction, embed=embed)
                
        except Exception as e:
            self.bot.logger.error(f"Error getting top users: {e}")
            await interaction.followup.send("❌ Failed to retrieve top users")
    
    @app_commands.command(name='guildinfo', description='Show information about the current server from database')
    async def guild_info(self, interaction: discord.Interaction):
        """Show information about the current guild from database."""
        if not self.bot.db_manager:
            await self.respond(interaction, "❌ Database is not connected")
            return
            
        if not interaction.guild:
            await self.respond(interaction, "This command can only be used in a server")
            return
            
        try:
            with DatabaseSession() as session:
                guild = session.query(Guild).filter(Guild.id == interaction.guild.id).first()
                
                if not guild:
                    await self.respond(interaction, "Server not found in database")
                    return
                
                # Get guild statistics
                user_count = session.query(User).filter(User.guild_id == guild.id).count()
                message_count = session.query(Message).filter(Message.guild_id == guild.id).count()
                forwarded_count = session.query(Message).filter(
                    Message.guild_id == guild.id,
                    Message.was_forwarded == True
                ).count()
                
                embed = discord.Embed(
                    title=f"🏛️ {guild.name}",
                    color=0x7289da,
                    timestamp=datetime.utcnow()
                )
                
                embed.add_field(
                    name="📊 Statistics",
                    value=f"**Tracked Users:** {user_count:,}\n"
                          f"**Messages Logged:** {message_count:,}\n"
                          f"**Messages Forwarded:** {forwarded_count:,}",
                    inline=True
                )
                
                embed.add_field(
                    name="ℹ️ Info",
                    value=f"**Guild ID:** {guild.id}\n"
                          f"**Joined At:** {guild.joined_at.strftime('%Y-%m-%d %H:%M')}\n"
                          f"**Status:** {'✅ Active' if guild.is_active else '❌ Inactive'}",
                    inline=True
                )
                
                if guild.member_count:
                    embed.add_field(
                        name="👥 Members",
                        value=f"**Total:** {guild.member_count:,}",
                        inline=True
                    )
                
                await self.respond(interaction, embed=embed)
                
        except Exception as e:
            self.bot.logger.error(f"Error getting guild info: {e}")
            await interaction.followup.send("❌ Failed to retrieve server information")

    # All orphaned slash command code cleaned up

    # All orphaned slash command code cleaned up

    # All orphaned slash command code removed completely

async def setup(bot):
    await bot.add_cog(DatabaseCommands(bot))