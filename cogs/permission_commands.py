"""
Permission-based commands for the Discord bot.
"""

import discord
from discord.ext import commands
# App commands removed to fix double messaging
from datetime import datetime
import logging

class PermissionCommands(commands.Cog):
    """Commands that require specific Discord permissions."""
    
    def __init__(self, bot):
        self.bot = bot
        self.logger = logging.getLogger('discord_bot.permissions')
    
    def has_manage_messages_permission():
        """Check if user has manage messages permission."""
        async def predicate(ctx):
            if not ctx.guild:
                return False
            return ctx.author.guild_permissions.manage_messages
        return commands.check(predicate)
    
    @commands.command(name='startforward', help='Start message forwarding (requires Manage messages permission)')
    @has_manage_messages_permission()
    async def start_forwarding(self, ctx):
        """Start message forwarding (requires Manage messages permission)."""
        self.bot.bot_active = True
        
        embed = discord.Embed(
            title="✅ Message Forwarding Started",
            description="Message forwarding has been activated. The bot will now forward messages from configured channels.",
            color=0x00ff00,
            timestamp=datetime.utcnow()
        )
        
        embed.add_field(
            name="🔧 Status",
            value="Active",
            inline=True
        )
        
        embed.add_field(
            name="👤 Started by",
            value=ctx.author.mention,
            inline=True
        )
        
        embed.set_footer(text="Use !stopforward to deactivate forwarding")
        
        await ctx.send(embed=embed)
        self.logger.info(f"Message forwarding activated by {ctx.author} (manage messages permission)")
    
    @commands.command(name='stopforward', help='Stop message forwarding (requires Manage messages permission)')
    @has_manage_messages_permission()
    async def stop_forwarding(self, ctx):
        """Stop message forwarding (requires Manage messages permission)."""
        self.bot.bot_active = False
        
        embed = discord.Embed(
            title="⛔ Message Forwarding Stopped",
            description="Message forwarding has been deactivated. The bot will no longer forward messages.",
            color=0xff0000,
            timestamp=datetime.utcnow()
        )
        
        embed.add_field(
            name="🔧 Status",
            value="Inactive",
            inline=True
        )
        
        embed.add_field(
            name="👤 Stopped by",
            value=ctx.author.mention,
            inline=True
        )
        
        embed.set_footer(text="Use !startforward to reactivate forwarding")
        
        await ctx.send(embed=embed)
        self.logger.info(f"Message forwarding deactivated by {ctx.author} (manage messages permission)")
    
    @commands.command(name='forwardstatus', aliases=['fstatus'], help='Check forwarding status')
    async def forwarding_status(self, ctx):
        """Check current forwarding status."""
        status = "Active" if getattr(self.bot, 'bot_active', False) else "Inactive"
        color = 0x00ff00 if getattr(self.bot, 'bot_active', False) else 0xff0000
        
        embed = discord.Embed(
            title="📊 Forwarding Status",
            color=color,
            timestamp=datetime.utcnow()
        )
        
        embed.add_field(
            name="🔧 Legacy Forwarding",
            value=status,
            inline=True
        )
        
        # Check cross-server forwarding configurations
        active_configs = 0
        try:
            from database import DatabaseSession
            from models import ForwardingConfig
            
            with DatabaseSession() as session:
                active_configs = session.query(ForwardingConfig).filter(
                    ForwardingConfig.is_active == True
                ).count()
        except Exception as e:
            self.logger.error(f"Error checking forwarding configs: {e}")
        
        embed.add_field(
            name="🌐 Cross-Server Configs",
            value=f"{active_configs} active",
            inline=True
        )
        
        embed.add_field(
            name="📈 Statistics",
            value=f"Messages seen: {getattr(self.bot, 'messages_seen', 0):,}\nCommands executed: {getattr(self.bot, 'commands_executed', 0):,}",
            inline=False
        )
        
        # Show permission info
        has_manage_messages = ctx.author.guild_permissions.manage_messages if ctx.guild else False
        perm_status = "✅ Can control forwarding" if has_manage_messages else "❌ Cannot control forwarding"
        
        embed.add_field(
            name="🛡️ Your Permissions",
            value=perm_status,
            inline=False
        )
        
        await ctx.send(embed=embed)
    
    @start_forwarding.error
    @stop_forwarding.error
    async def permission_error(self, ctx, error):
        """Handle permission errors for forwarding commands."""
        if isinstance(error, commands.CheckFailure):
            await ctx.send("You do not have access to this command, only staff members are able to access this. If you believe this is a mistake, please contact higher authority or rstone203 (if he's in your server or a server you are in)")
            return

    # All slash command functions removed

    # Remaining slash command functions removed


async def setup(bot):
    await bot.add_cog(PermissionCommands(bot))