"""
Trusted user management commands for the Discord bot.
Allows the bot owner to grant and revoke trusted status for specific users.
"""

import discord
from discord.ext import commands
# App commands removed to fix double messaging
from datetime import datetime
from database import DatabaseSession
from models import TrustedUser
import logging

def is_owner():
    """Check if user is the bot owner."""
    async def predicate(ctx):
        return await ctx.bot.is_owner(ctx.author)
    return commands.check(predicate)

class TrustedUserCommands(commands.Cog):
    """Commands for managing trusted users with owner-level permissions."""
    
    def __init__(self, bot):
        self.bot = bot
        self.logger = logging.getLogger('discord_bot.trusted_users')
    
    @commands.command(name='trustuser', aliases=['addtrust'])
    @is_owner()
    async def trust_user(self, ctx, user: discord.User, *, notes: str = ""):
        """Add a user to the trusted users list (Owner only)."""
        if not self.bot.db_manager:
            await ctx.send("❌ Database is not connected")
            return
            
        try:
            with DatabaseSession() as session:
                # Check if user is already trusted
                existing = session.query(TrustedUser).filter(
                    TrustedUser.user_id == user.id,
                    TrustedUser.is_active == True
                ).first()
                
                if existing:
                    await ctx.send(f"❌ {user.display_name} is already a trusted user.")
                    return
                
                # Add to trusted users
                trusted_user = TrustedUser(
                    user_id=user.id,
                    username=user.name,
                    display_name=user.display_name or user.name,
                    granted_by=ctx.author.id,
                    granted_at=datetime.utcnow(),
                    is_active=True,
                    notes=notes if notes is not None else f"Trusted by {ctx.author} via command"
                )
                
                session.add(trusted_user)
                session.commit()
                session.refresh(trusted_user)  # Refresh to get ID after commit
            
            embed = discord.Embed(
                title="✅ User Trusted",
                description=f"{user.mention} has been granted trusted status with owner-level permissions.",
                color=0x00ff00,
                timestamp=datetime.utcnow()
            )
            
            embed.add_field(
                name="👤 User",
                value=f"**Name:** {user.display_name}\n**ID:** {user.id}",
                inline=True
            )
            
            embed.add_field(
                name="🛡️ Permissions",
                value="• All owner commands\n• Full bot administration\n• Database management\n• User management",
                inline=True
            )
            
            if notes:
                embed.add_field(
                    name="📝 Notes",
                    value=notes,
                    inline=False
                )
            
            embed.set_footer(text="Use !untrustuser to revoke access")
            
            await ctx.send(embed=embed)
            self.logger.info(f"User {user} trusted by {ctx.author}")
            
        except Exception as e:
            await ctx.send(f"❌ Database error: {str(e)}")
            self.logger.error(f"Error trusting user: {e}")
    
    @commands.command(name='untrustuser', aliases=['removetrust'])
    @is_owner()
    async def untrust_user(self, ctx, user: discord.User):
        """Remove a user from the trusted users list (Owner only)."""
        if not self.bot.db_manager:
            await ctx.send("❌ Database is not connected")
            return
            
        try:
            with DatabaseSession() as session:
                trusted_user = session.query(TrustedUser).filter(
                    TrustedUser.user_id == user.id,
                    TrustedUser.is_active == True
                ).first()
                
                if not trusted_user:
                    await ctx.send(f"❌ {user.display_name} is not a trusted user.")
                    return
                
                # Store user info before modifying
                user_name = trusted_user.display_name
                
                # Deactivate instead of deleting for audit trail
                trusted_user.is_active = False
                session.commit()
            
            embed = discord.Embed(
                title="🚫 Trust Revoked",
                description=f"Trusted status has been revoked for {user.mention}.",
                color=0xff0000,
                timestamp=datetime.utcnow()
            )
            
            embed.add_field(
                name="👤 User",
                value=f"**Name:** {user.display_name}\n**ID:** {user.id}",
                inline=True
            )
            
            embed.add_field(
                name="⚠️ Access Removed",
                value="All owner-level permissions have been revoked.",
                inline=True
            )
            
            await ctx.send(embed=embed)
            self.logger.info(f"User {user} untrusted by {ctx.author}")
            
        except Exception as e:
            await ctx.send(f"❌ Database error: {str(e)}")
            self.logger.error(f"Error untrusting user: {e}")
    
    @commands.command(name='trustedusers', aliases=['listtrusted'])
    @is_owner()
    async def list_trusted_users(self, ctx):
        """List all trusted users (Owner only)."""
        if not self.bot.db_manager:
            await ctx.send("❌ Database is not connected")
            return
            
        try:
            with DatabaseSession() as session:
                trusted_users = session.query(TrustedUser).filter(
                    TrustedUser.is_active == True
                ).order_by(TrustedUser.granted_at.desc()).all()
                
                if not trusted_users:
                    embed = discord.Embed(
                        title="👥 Trusted Users",
                        description="No trusted users found.",
                        color=0xffa500
                    )
                    await ctx.send(embed=embed)
                    return
                
                # Process data while in session
                user_data = []
                for trusted in trusted_users:
                    user_obj = self.bot.get_user(trusted.user_id)
                    user_name = user_obj.display_name if user_obj else trusted.display_name
                    granted_date = trusted.granted_at.strftime('%Y-%m-%d')
                    user_data.append({
                        'name': user_name,
                        'id': trusted.user_id,
                        'date': granted_date
                    })
                
            # Create embed outside session
            embed = discord.Embed(
                title="👥 Trusted Users",
                description=f"Found {len(user_data)} trusted users with owner-level access:",
                color=0x00ff00,
                timestamp=datetime.utcnow()
            )
            
            user_list = []
            for i, user in enumerate(user_data, 1):
                user_list.append(f"{i}. **{user['name']}** (ID: {user['id']})\n   Trusted: {user['date']}")
            
            embed.add_field(
                name="🛡️ Trusted Users",
                value="\n\n".join(user_list),
                inline=False
            )
            
            embed.set_footer(text="These users have full owner-level access")
            
            await ctx.send(embed=embed)
            
        except Exception as e:
            await ctx.send(f"❌ Database error: {str(e)}")
            self.logger.error(f"Error listing trusted users: {e}")


async def setup(bot):
    await bot.add_cog(TrustedUserCommands(bot))