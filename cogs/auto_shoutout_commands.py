"""Auto-shoutout commands - automatically shoutout new chatters in Twitch streams."""

import discord
from discord.ext import commands
from discord import app_commands
import logging
from database import DatabaseSession
from models import User, AutoShoutoutSettings
from typing import Optional
from sqlalchemy import func

logger = logging.getLogger('discord_bot.auto_shoutout_commands')

class AutoShoutoutCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.logger = logger
    
    @commands.command(name='autoso', help='Toggle auto-shoutout for your Twitch stream')
    @commands.guild_only()
    async def toggle_auto_shoutout(self, ctx):
        """Toggle auto-shoutout on/off for your Twitch channel."""
        try:
            with DatabaseSession() as session:
                user = session.query(User).filter_by(
                    id=ctx.author.id,
                    guild_id=ctx.guild.id
                ).first()
                
                twitch_login = None
                if user:
                    twitch_login = user.twitch_login
                if not twitch_login:
                    linked_user = session.query(User).filter(
                        User.id == ctx.author.id,
                        User.twitch_login.isnot(None)
                    ).first()
                    if linked_user:
                        twitch_login = linked_user.twitch_login
                        if not user:
                            user = User(id=ctx.author.id, guild_id=ctx.guild.id, username=ctx.author.name)
                            session.add(user)
                            session.flush()
                        user.twitch_login = twitch_login
                        user.twitch_display_name = linked_user.twitch_display_name
                        user.twitch_id = linked_user.twitch_id
                        user.twitch_consent = linked_user.twitch_consent
                        user.twitch_linked_at = linked_user.twitch_linked_at
                        user.twitch_source = linked_user.twitch_source
                        session.commit()
                
                if not twitch_login:
                    await ctx.send("❌ You need to link your Twitch account first! Use `/linktwitch` to get started.")
                    return
                
                settings = session.query(AutoShoutoutSettings).filter_by(
                    user_id=user.id,
                    guild_id=ctx.guild.id
                ).first()
                
                if not settings:
                    settings = AutoShoutoutSettings(
                        user_id=user.id,
                        guild_id=ctx.guild.id,
                        enabled=True,
                        shoutout_message="Thanks for stopping by @{username}! Check them out at twitch.tv/{username}"
                    )
                    session.add(settings)
                    session.commit()
                    
                    embed = discord.Embed(
                        title="✅ Auto-Shoutout Enabled!",
                        description=f"Auto-shoutout is now **enabled** for your Twitch channel!",
                        color=discord.Color.green()
                    )
                    embed.add_field(
                        name="How it works",
                        value="• When someone chats for the **first time** in your stream, they get an automatic shoutout\n"
                              "• Default message: `Thanks for stopping by @{username}! Check them out at twitch.tv/{username}`\n"
                              "• Cooldown: Each user gets shouted out once per hour\n"
                              "• 3-second delay to prevent spam",
                        inline=False
                    )
                    embed.add_field(
                        name="Profile Placeholders",
                        value="`{username}` — display name\n"
                              "`{game}` — what they stream\n"
                              "`{title}` — their stream title\n"
                              "`{live_tag}` — `🔴 LIVE ` if currently streaming\n"
                              "`{viewers}` — viewer count if live\n"
                              "`{bio}` — their Twitch bio",
                        inline=False
                    )
                    embed.add_field(
                        name="Commands",
                        value="• `!autoso` - Toggle on/off\n"
                              "• `!sosetmessage <message>` - Customize message\n"
                              "• `!sostatus` - View your settings",
                        inline=False
                    )
                    await ctx.send(embed=embed)
                else:
                    settings.enabled = not settings.enabled
                    session.commit()
                    
                    status = "**enabled**" if settings.enabled else "**disabled**"
                    emoji = "✅" if settings.enabled else "❌"
                    
                    embed = discord.Embed(
                        title=f"{emoji} Auto-Shoutout {status.replace('**', '').title()}",
                        description=f"Auto-shoutout is now {status} for your Twitch channel.",
                        color=discord.Color.green() if settings.enabled else discord.Color.dark_grey()
                    )
                    await ctx.send(embed=embed)
                
                self.logger.info(f"User {ctx.author} toggled auto-shoutout: {settings.enabled}")
                
        except Exception as e:
            await ctx.send(f"❌ Error toggling auto-shoutout: {str(e)}")
            self.logger.error(f"Error toggling auto-shoutout: {e}", exc_info=True)
    
    @commands.command(name='sosetmessage', help='Set your custom auto-shoutout message')
    @commands.guild_only()
    async def set_shoutout_message(self, ctx, *, message: str):
        """Set a custom shoutout message. Use {username} as a placeholder for the chatter's name."""
        try:
            if len(message) > 500:
                await ctx.send("❌ Message is too long! Maximum 500 characters.")
                return

            if '{username}' not in message:
                await ctx.send(
                    "❌ Your message must include `{username}` as a placeholder for the chatter's name!\n\n"
                    "**Available placeholders:**\n"
                    "`{username}` — their Twitch display name\n"
                    "`{game}` — what they stream (e.g. Fortnite)\n"
                    "`{title}` — their last/current stream title\n"
                    "`{live_tag}` — shows `🔴 LIVE ` if they're streaming right now\n"
                    "`{viewers}` — their current viewer count (if live)\n"
                    "`{bio}` — their Twitch bio"
                )
                return
            
            with DatabaseSession() as session:
                user = session.query(User).filter_by(
                    id=ctx.author.id,
                    guild_id=ctx.guild.id
                ).first()
                
                twitch_login = None
                if user:
                    twitch_login = user.twitch_login
                if not twitch_login:
                    linked_user = session.query(User).filter(
                        User.id == ctx.author.id,
                        User.twitch_login.isnot(None)
                    ).first()
                    if linked_user:
                        twitch_login = linked_user.twitch_login
                        if not user:
                            user = User(id=ctx.author.id, guild_id=ctx.guild.id, username=ctx.author.name)
                            session.add(user)
                            session.flush()
                        user.twitch_login = twitch_login
                        user.twitch_display_name = linked_user.twitch_display_name
                        user.twitch_id = linked_user.twitch_id
                        user.twitch_consent = linked_user.twitch_consent
                        user.twitch_linked_at = linked_user.twitch_linked_at
                        user.twitch_source = linked_user.twitch_source
                        session.commit()
                
                if not twitch_login:
                    await ctx.send("❌ You need to link your Twitch account first! Use `/linktwitch`.")
                    return
                
                settings = session.query(AutoShoutoutSettings).filter_by(
                    user_id=user.id,
                    guild_id=ctx.guild.id
                ).first()
                
                if not settings:
                    settings = AutoShoutoutSettings(
                        user_id=user.id,
                        guild_id=ctx.guild.id,
                        enabled=True,
                        shoutout_message=message
                    )
                    session.add(settings)
                else:
                    settings.shoutout_message = message
                
                session.commit()
                
                example_message = message.replace('{username}', 'ExampleUser')
                
                embed = discord.Embed(
                    title="✅ Shoutout Message Updated!",
                    description=f"Your auto-shoutout message has been set!",
                    color=discord.Color.green()
                )
                embed.add_field(
                    name="Your Message",
                    value=f"`{message}`",
                    inline=False
                )
                embed.add_field(
                    name="Example",
                    value=f"`{example_message}`",
                    inline=False
                )
                await ctx.send(embed=embed)
                
                self.logger.info(f"User {ctx.author} set shoutout message: {message}")
                
        except Exception as e:
            await ctx.send(f"❌ Error setting shoutout message: {str(e)}")
            self.logger.error(f"Error setting shoutout message: {e}", exc_info=True)
    
    @commands.command(name='sostatus', help='View your auto-shoutout settings')
    @commands.guild_only()
    async def shoutout_status(self, ctx):
        """View your current auto-shoutout settings."""
        try:
            with DatabaseSession() as session:
                user = session.query(User).filter_by(
                    id=ctx.author.id,
                    guild_id=ctx.guild.id
                ).first()
                
                twitch_login = None
                if user:
                    twitch_login = user.twitch_login
                if not twitch_login:
                    linked_user = session.query(User).filter(
                        User.id == ctx.author.id,
                        User.twitch_login.isnot(None)
                    ).first()
                    if linked_user:
                        twitch_login = linked_user.twitch_login
                        if not user:
                            user = User(id=ctx.author.id, guild_id=ctx.guild.id, username=ctx.author.name)
                            session.add(user)
                            session.flush()
                        user.twitch_login = twitch_login
                        user.twitch_display_name = linked_user.twitch_display_name
                        user.twitch_id = linked_user.twitch_id
                        user.twitch_consent = linked_user.twitch_consent
                        user.twitch_linked_at = linked_user.twitch_linked_at
                        user.twitch_source = linked_user.twitch_source
                        session.commit()
                
                if not twitch_login:
                    await ctx.send("❌ You need to link your Twitch account first! Use `/linktwitch`.")
                    return
                
                settings = session.query(AutoShoutoutSettings).filter_by(
                    user_id=user.id,
                    guild_id=ctx.guild.id
                ).first()
                
                if not settings:
                    embed = discord.Embed(
                        title="❌ Auto-Shoutout Not Set Up",
                        description=f"You haven't enabled auto-shoutout yet!\n\nUse `!autoso` to enable it.",
                        color=discord.Color.red()
                    )
                else:
                    status_emoji = "✅" if settings.enabled else "❌"
                    status_text = "Enabled" if settings.enabled else "Disabled"

                    # Build a realistic example substituting all known placeholders
                    example_message = (settings.shoutout_message
                        .replace('{username}', 'ExampleUser')
                        .replace('{game}',     'Just Chatting')
                        .replace('{title}',    'Chill stream vibes')
                        .replace('{live_tag}', '🔴 LIVE ')
                        .replace('{viewers}',  '42')
                        .replace('{bio}',      'Full-time vibes merchant'))

                    embed = discord.Embed(
                        title=f"{status_emoji} Auto-Shoutout Settings",
                        description=f"Settings for **{user.twitch_login}**",
                        color=discord.Color.green() if settings.enabled else discord.Color.dark_grey()
                    )
                    embed.add_field(name="Status",   value=f"{status_emoji} {status_text}", inline=True)
                    embed.add_field(name="Delay",    value=f"{settings.delay_seconds} seconds",            inline=True)
                    embed.add_field(name="Cooldown", value=f"{settings.cooldown_per_user_minutes} minutes", inline=True)
                    embed.add_field(
                        name="Shoutout Template",
                        value=f"`{settings.shoutout_message}`",
                        inline=False
                    )
                    embed.add_field(
                        name="Example Output",
                        value=f"`{example_message}`",
                        inline=False
                    )
                    embed.add_field(
                        name="Available Placeholders",
                        value="`{username}` `{game}` `{title}` `{live_tag}` `{viewers}` `{bio}`",
                        inline=False
                    )
                    embed.set_footer(text="!autoso to toggle • !sosetmessage <message> to customize")
                
                await ctx.send(embed=embed)
                
        except Exception as e:
            await ctx.send(f"❌ Error checking shoutout status: {str(e)}")
            self.logger.error(f"Error checking shoutout status: {e}", exc_info=True)
    
    @app_commands.command(name='autoshoutoutinfo', description='Learn how the auto-shoutout feature works')
    @app_commands.guild_only()
    async def autoshoutout_info(self, interaction: discord.Interaction):
        """Explain how the auto-shoutout feature works."""
        try:
            embed = discord.Embed(
                title="🎉 Auto-Shoutout Feature - How It Works",
                description="Automatically welcome new chatters to your Twitch stream!",
                color=discord.Color.purple()
            )
            
            embed.add_field(
                name="📺 What It Does",
                value="When someone chats in your Twitch stream for the **first time**, the bot automatically sends a welcome message to make them feel at home!",
                inline=False
            )
            
            embed.add_field(
                name="✅ Requirements",
                value="1️⃣ Link your Twitch account using `/linktwitch`\n"
                      "2️⃣ Enable auto-shoutout with `!autoso` in Discord\n"
                      "3️⃣ Go live on Twitch - that's it!",
                inline=False
            )
            
            embed.add_field(
                name="🔄 How It Works (Step-by-Step)",
                value="1. You go live on Twitch → Bot joins your channel\n"
                      "2. Someone chats for the first time → Bot detects them\n"
                      "3. Bot waits 3 seconds (to prevent spam)\n"
                      "4. Bot sends your custom shoutout in chat!\n"
                      "5. Cooldown activates (60 min per user)",
                inline=False
            )
            
            embed.add_field(
                name="💬 Commands (Use in Discord)",
                value="`!autoso` - Turn auto-shoutout on/off\n"
                      "`!sosetmessage <message>` - Customize your message\n"
                      "`!sostatus` - View your current settings\n"
                      "`/autoshoutoutinfo` - Show this help",
                inline=False
            )
            
            embed.add_field(
                name="🎨 Profile-Aware Placeholders",
                value="The bot looks up each chatter's Twitch profile in real time so you can use:\n"
                      "`{username}` — their display name\n"
                      "`{game}` — what they stream (e.g. Fortnite)\n"
                      "`{title}` — their last/current stream title\n"
                      "`{live_tag}` — shows `🔴 LIVE ` if they're streaming right now\n"
                      "`{viewers}` — current viewer count if live\n"
                      "`{bio}` — their Twitch bio",
                inline=False
            )
            embed.add_field(
                name="🎨 Example Templates",
                value="**Simple:**\n"
                      "```!sosetmessage Welcome @{username}! Check them out at twitch.tv/{username} 💜```\n"
                      "**Profile-aware:**\n"
                      "```!sosetmessage Hey chat! {live_tag}@{username} is here — they stream {game}! Go check them out at twitch.tv/{username} 🎮```\n"
                      "**Must always include `{username}`!**",
                inline=False
            )

            embed.add_field(
                name="⚙️ Default Settings",
                value="• **Delay:** 3 seconds before sending\n"
                      "• **Cooldown:** 60 minutes per user\n"
                      "• **Default message:** `Thanks for stopping by @{username}! Check them out at twitch.tv/{username}`",
                inline=False
            )
            
            embed.add_field(
                name="❓ Important Notes",
                value="✅ Works for ALL streams (not just trains!)\n"
                      "✅ Bot sends messages as YOU (no mod needed)\n"
                      "✅ Only works when you're live on Twitch\n"
                      "❌ Won't shoutout yourself or repeat users",
                inline=False
            )
            
            embed.set_footer(text="Questions? Ask in the server! • Ready to start? Use !autoso to enable")
            
            await interaction.response.send_message(embed=embed)
            self.logger.info(f"User {interaction.user} viewed auto-shoutout info")
            
        except Exception as e:
            await interaction.response.send_message(f"❌ Error showing info: {str(e)}", ephemeral=True)
            self.logger.error(f"Error showing auto-shoutout info: {e}", exc_info=True)

async def setup(bot):
    await bot.add_cog(AutoShoutoutCommands(bot))
