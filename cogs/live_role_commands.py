import discord
from discord.ext import commands
import logging
from database import DatabaseSession
from models import Guild, User

logger = logging.getLogger('discord_bot.live_role_commands')

class LiveRoleCommands(commands.Cog):
    """Commands for managing the live streamer role feature."""
    
    def __init__(self, bot):
        self.bot = bot
        self.logger = logging.getLogger('discord_bot.live_role_commands')
    
    def is_owner_or_trusted():
        """Check if user is the bot owner or a trusted user."""
        async def predicate(ctx):
            return await ctx.bot.is_owner_or_trusted(ctx.author)
        return commands.check(predicate)
    
    @commands.command(name='setliverole', help='Set the role to assign to live streamers (Admin only)')
    @is_owner_or_trusted()
    async def set_live_role(self, ctx, role: discord.Role):
        """Set which Discord role to assign to users when they go live on Twitch."""
        if not ctx.guild:
            await ctx.send("❌ This command can only be used in a server.")
            return
        
        if not ctx.guild.me.top_role > role:
            await ctx.send(f"❌ I don't have permission to manage the {role.mention} role. Please move my role above it in Server Settings > Roles.")
            return
        
        try:
            with DatabaseSession() as session:
                guild_data = session.query(Guild).filter(Guild.id == ctx.guild.id).first()
                
                if not guild_data:
                    guild_data = Guild(
                        id=ctx.guild.id,
                        name=ctx.guild.name,
                        owner_id=ctx.guild.owner_id,
                        member_count=ctx.guild.member_count
                    )
                    session.add(guild_data)
                
                guild_data.live_role_id = role.id
                session.commit()
                
                # Read the tracking status before the session closes
                tracking_enabled = guild_data.live_tracking_enabled
            
            embed = discord.Embed(
                title="✅ Live Role Configured",
                description=f"Users will now receive {role.mention} when they go live on Twitch!",
                color=0x9146FF
            )
            
            embed.add_field(
                name="🎮 Role",
                value=role.mention,
                inline=True
            )
            
            embed.add_field(
                name="📊 Status",
                value="Use `!togglelivetracking` to enable" if not tracking_enabled else "✅ Enabled",
                inline=True
            )
            
            embed.set_footer(text="Users must link their Twitch accounts via /twitchoauth first")
            
            await ctx.send(embed=embed)
            self.logger.info(f"Live role set to {role.name} in {ctx.guild.name} by {ctx.author}")
        
        except Exception as e:
            await ctx.send(f"❌ Error setting live role: {str(e)}")
            self.logger.error(f"Error setting live role: {e}")
    
    @commands.command(name='togglelivetracking', aliases=['togglelive'], help='Enable or disable live role tracking (Admin only)')
    @is_owner_or_trusted()
    async def toggle_live_tracking(self, ctx):
        """Enable or disable the live role tracking feature."""
        if not ctx.guild:
            await ctx.send("❌ This command can only be used in a server.")
            return
        
        try:
            with DatabaseSession() as session:
                guild_data = session.query(Guild).filter(Guild.id == ctx.guild.id).first()
                
                if not guild_data:
                    await ctx.send("❌ Please set a live role first using `!setliverole @role`")
                    return
                
                if not guild_data.live_role_id:
                    await ctx.send("❌ Please set a live role first using `!setliverole @role`")
                    return
                
                guild_data.live_tracking_enabled = not guild_data.live_tracking_enabled
                new_status = guild_data.live_tracking_enabled
                session.commit()
            
            status_emoji = "✅" if new_status else "❌"
            status_text = "enabled" if new_status else "disabled"
            
            embed = discord.Embed(
                title=f"{status_emoji} Live Role Tracking {status_text.title()}",
                description=f"Live role tracking has been **{status_text}**.",
                color=0x00ff00 if new_status else 0xff0000
            )
            
            if new_status:
                embed.add_field(
                    name="🔄 What happens now?",
                    value="• Bot checks Twitch every 60 seconds\n"
                          "• Users streaming on Twitch get the live role\n"
                          "• Role is removed when they go offline",
                    inline=False
                )
            else:
                embed.add_field(
                    name="⏸️ What happens now?",
                    value="• Live role updates are paused\n"
                          "• Existing live roles won't be removed\n"
                          "• Use `!togglelivetracking` again to resume",
                    inline=False
                )
            
            await ctx.send(embed=embed)
            self.logger.info(f"Live tracking {status_text} in {ctx.guild.name} by {ctx.author}")
        
        except Exception as e:
            await ctx.send(f"❌ Error toggling live tracking: {str(e)}")
            self.logger.error(f"Error toggling live tracking: {e}")
    
    @commands.command(name='toggletrainsonlylive', aliases=['toggletrainslive'], help='Toggle trains-only mode for live roles (Admin only)')
    @is_owner_or_trusted()
    async def toggle_trains_only_live(self, ctx):
        """Enable or disable trains-only mode - only assign live role during active trains."""
        if not ctx.guild:
            await ctx.send("❌ This command can only be used in a server.")
            return
        
        try:
            with DatabaseSession() as session:
                guild_data = session.query(Guild).filter(Guild.id == ctx.guild.id).first()
                
                if not guild_data:
                    await ctx.send("❌ Please set a live role first using `!setliverole @role`")
                    return
                
                if not guild_data.live_role_id:
                    await ctx.send("❌ Please set a live role first using `!setliverole @role`")
                    return
                
                if not guild_data.live_tracking_enabled:
                    await ctx.send("❌ Please enable live tracking first using `!togglelivetracking`")
                    return
                
                guild_data.live_role_trains_only = not guild_data.live_role_trains_only
                new_status = guild_data.live_role_trains_only
                session.commit()
            
            status_emoji = "🚂" if new_status else "🎮"
            status_text = "enabled" if new_status else "disabled"
            
            embed = discord.Embed(
                title=f"{status_emoji} Trains-Only Mode {status_text.title()}",
                description=f"Live role trains-only mode has been **{status_text}**.",
                color=0x9146FF
            )
            
            if new_status:
                embed.add_field(
                    name="🚂 What happens now?",
                    value="• Live role is only assigned during active trains\n"
                          "• Streamers get the role when their train is scheduled\n"
                          "• Perfect for train-focused communities!",
                    inline=False
                )
            else:
                embed.add_field(
                    name="🎮 What happens now?",
                    value="• Live role is assigned whenever streaming\n"
                          "• No train schedule required\n"
                          "• Use `!toggletrainsonlylive` to enable trains-only mode",
                    inline=False
                )
            
            await ctx.send(embed=embed)
            self.logger.info(f"Trains-only live mode {status_text} in {ctx.guild.name} by {ctx.author}")
        
        except Exception as e:
            await ctx.send(f"❌ Error toggling trains-only mode: {str(e)}")
            self.logger.error(f"Error toggling trains-only mode: {e}")
    
    @commands.command(name='livestatus', help='View live role configuration and current live streamers')
    async def live_status(self, ctx):
        """View the current live role configuration and who's currently live."""
        if not ctx.guild:
            await ctx.send("❌ This command can only be used in a server.")
            return
        
        try:
            with DatabaseSession() as session:
                guild_data = session.query(Guild).filter(Guild.id == ctx.guild.id).first()
                
                embed = discord.Embed(
                    title="🔴 Live Role Status",
                    description=f"Configuration for **{ctx.guild.name}**",
                    color=0x9146FF
                )
                
                if not guild_data or not guild_data.live_role_id:
                    embed.add_field(
                        name="⚙️ Configuration",
                        value="❌ Not configured\nUse `!setliverole @role` to set up",
                        inline=False
                    )
                    await ctx.send(embed=embed)
                    return
                
                live_role = ctx.guild.get_role(guild_data.live_role_id)
                if not live_role:
                    embed.add_field(
                        name="⚙️ Configuration",
                        value="⚠️ Configured role was deleted\nUse `!setliverole @role` to configure a new role",
                        inline=False
                    )
                    await ctx.send(embed=embed)
                    return
                
                status_emoji = "✅" if guild_data.live_tracking_enabled else "❌"
                status_text = "Enabled" if guild_data.live_tracking_enabled else "Disabled"
                
                trains_only_emoji = "🚂" if guild_data.live_role_trains_only else "🎮"
                trains_only_text = "Trains Only" if guild_data.live_role_trains_only else "Always Active"
                
                embed.add_field(
                    name="⚙️ Configuration",
                    value=f"**Role:** {live_role.mention}\n"
                          f"**Status:** {status_emoji} {status_text}\n"
                          f"**Mode:** {trains_only_emoji} {trains_only_text}\n"
                          f"**Update Interval:** Every 60 seconds",
                    inline=False
                )
                
                users_with_twitch = session.query(User).filter(
                    User.guild_id == ctx.guild.id,
                    User.twitch_id.isnot(None)
                ).count()
                
                embed.add_field(
                    name="📊 Statistics",
                    value=f"**Linked Accounts:** {users_with_twitch} users\n"
                          f"**Users can link:** Use `/twitchoauth`",
                    inline=False
                )
                
                live_members = [member for member in ctx.guild.members if live_role in member.roles]
                if live_members:
                    live_list = "\n".join([f"• {member.mention}" for member in live_members[:10]])
                    if len(live_members) > 10:
                        live_list += f"\n*... and {len(live_members) - 10} more*"
                    
                    embed.add_field(
                        name=f"🔴 Currently Live ({len(live_members)})",
                        value=live_list,
                        inline=False
                    )
                else:
                    embed.add_field(
                        name="🔴 Currently Live",
                        value="No one is streaming right now",
                        inline=False
                    )
                
                await ctx.send(embed=embed)
        
        except Exception as e:
            await ctx.send(f"❌ Error getting live status: {str(e)}")
            self.logger.error(f"Error getting live status: {e}")

    @commands.command(name='setlivenotifchannel', aliases=['setlivealerts'], help='Set the channel for go-live alerts (Admin only)')
    @is_owner_or_trusted()
    async def set_live_notif_channel(self, ctx, channel: discord.TextChannel = None):
        """Set which channel receives a go-live embed when a linked member starts streaming on Twitch."""
        if not ctx.guild:
            await ctx.send("❌ This command can only be used in a server.")
            return

        try:
            with DatabaseSession() as session:
                guild_data = session.query(Guild).filter(Guild.id == ctx.guild.id).first()
                if not guild_data:
                    guild_data = Guild(
                        id=ctx.guild.id,
                        name=ctx.guild.name,
                        owner_id=ctx.guild.owner_id,
                        member_count=ctx.guild.member_count
                    )
                    session.add(guild_data)

                if channel is None:
                    # Clear the setting
                    guild_data.stream_alert_channel_id = None
                    session.commit()
                    await ctx.send("✅ Go-live alerts disabled — no channel configured.")
                    return

                guild_data.stream_alert_channel_id = channel.id
                session.commit()

            embed = discord.Embed(
                title="✅ Go-Live Alerts Configured",
                description=f"Go-live notifications will now be posted to {channel.mention}.",
                color=0x9146FF
            )
            embed.add_field(
                name="🔔 How it works",
                value=(
                    "• Members must link their Twitch via `/linktwitch`\n"
                    "• When they go live, an embed posts here automatically\n"
                    "• Optionally ping a role with `!setlivepingrole @role`\n"
                    "• Run `!togglelivetracking` to also enable the live Discord role"
                ),
                inline=False
            )
            embed.set_footer(text="Live checks run every 60 seconds")
            await ctx.send(embed=embed)
            self.logger.info(f"Go-live alert channel set to #{channel.name} in {ctx.guild.name}")

        except Exception as e:
            await ctx.send(f"❌ Error: {str(e)}")
            self.logger.error(f"Error setting live notif channel: {e}")

    @commands.command(name='setlivepingrole', help='Set a role to ping with go-live alerts (Admin only)')
    @is_owner_or_trusted()
    async def set_live_ping_role(self, ctx, role: discord.Role = None):
        """Set an optional role to mention when someone goes live. Run without a role to clear it."""
        if not ctx.guild:
            await ctx.send("❌ This command can only be used in a server.")
            return

        try:
            with DatabaseSession() as session:
                guild_data = session.query(Guild).filter(Guild.id == ctx.guild.id).first()
                if not guild_data:
                    await ctx.send("❌ Set a live alert channel first with `!setlivenotifchannel #channel`.")
                    return

                guild_data.stream_alert_mention_role_id = role.id if role else None
                session.commit()

            if role:
                await ctx.send(f"✅ Go-live alerts will now ping {role.mention}.")
            else:
                await ctx.send("✅ Role ping cleared — alerts will post without a mention.")

        except Exception as e:
            await ctx.send(f"❌ Error: {str(e)}")
            self.logger.error(f"Error setting live ping role: {e}")

    @commands.command(name='livenotifsettings', aliases=['livealertstatus'], help='Show current go-live alert settings')
    async def live_notif_settings(self, ctx):
        """Show the current go-live alert configuration for this server."""
        if not ctx.guild:
            await ctx.send("❌ Server only.")
            return

        try:
            with DatabaseSession() as session:
                guild_data = session.query(Guild).filter(Guild.id == ctx.guild.id).first()

            embed = discord.Embed(title="📺 Go-Live Alert Settings", color=0x9146FF)

            if guild_data and guild_data.stream_alert_channel_id:
                ch = ctx.guild.get_channel(guild_data.stream_alert_channel_id)
                embed.add_field(name="📣 Alert Channel", value=ch.mention if ch else f"<#{guild_data.stream_alert_channel_id}> *(not found)*", inline=True)
            else:
                embed.add_field(name="📣 Alert Channel", value="Not set", inline=True)

            if guild_data and guild_data.stream_alert_mention_role_id:
                role = ctx.guild.get_role(guild_data.stream_alert_mention_role_id)
                embed.add_field(name="🔔 Ping Role", value=role.mention if role else "*(role not found)*", inline=True)
            else:
                embed.add_field(name="🔔 Ping Role", value="None", inline=True)

            tracking = guild_data.live_tracking_enabled if guild_data else False
            embed.add_field(name="🎮 Live Role Tracking", value="✅ Enabled" if tracking else "❌ Disabled", inline=True)

            embed.add_field(
                name="⚙️ Setup commands",
                value=(
                    "`!setlivenotifchannel #channel` — set alert channel\n"
                    "`!setlivepingrole @role` — set ping role (optional)\n"
                    "`!setliverole @role` — set live Discord role (optional)\n"
                    "`!togglelivetracking` — enable/disable live Discord role"
                ),
                inline=False
            )
            await ctx.send(embed=embed)

        except Exception as e:
            await ctx.send(f"❌ Error: {str(e)}")


async def setup(bot):
    await bot.add_cog(LiveRoleCommands(bot))
