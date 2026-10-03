"""
Commands for managing automatic stream chat announcements in friends' channels.
"""

import discord
from discord.ext import commands
import logging
from database import DatabaseSession
from models import StreamChatAnnouncement, User

logger = logging.getLogger('discord_bot.live_chat_announcements')

class LiveChatAnnouncementCommands(commands.Cog):
    """Manage automatic announcements in friends' stream chats when going live."""
    
    def __init__(self, bot):
        self.bot = bot
        self.logger = logging.getLogger('discord_bot.live_chat_announcements')
    
    @commands.command(name='setliveannouncement')
    async def set_live_announcement(self, ctx, *, args: str = None):
        """
        Set a custom message to auto-post in your friends' Twitch chats when you go live.
        
        Usage:
            !setliveannouncement friend1,friend2,friend3 | Hey, I'm live! Come watch!
            !setliveannouncement channel1,channel2 | Just went live!
        
        The message will be posted in your friends' Twitch channels when you start streaming.
        """
        if not args:
            await ctx.send("❌ Please provide channels and a message!\n\n"
                          "**Format:** `!setliveannouncement <channels> | <message>`\n\n"
                          "**Example:** `!setliveannouncement friend1,friend2,friend3 | Hey everyone, I'm live!`\n\n"
                          "• Separate channel names with commas\n"
                          "• Use `|` to separate channels from message\n"
                          "• Message will be posted in those channels when you go live")
            return
        
        # Parse channels and message
        if '|' not in args:
            await ctx.send("❌ Missing `|` separator!\n\n"
                          "**Format:** `!setliveannouncement <channels> | <message>`\n"
                          "**Example:** `!setliveannouncement friend1,friend2 | I'm live now!`")
            return
        
        parts = args.split('|', 1)
        channels_str = parts[0].strip()
        message = parts[1].strip()
        
        if not channels_str or not message:
            await ctx.send("❌ Both channels and message are required!\n"
                          "**Example:** `!setliveannouncement friend1,friend2 | Hey, I'm live!`")
            return
        
        if len(message) > 500:
            await ctx.send("❌ Message is too long! Maximum 500 characters.")
            return
        
        # Parse channel list
        channels = [ch.strip().lower() for ch in channels_str.split(',') if ch.strip()]
        
        if not channels:
            await ctx.send("❌ No valid channels provided!")
            return
        
        if len(channels) > 10:
            await ctx.send("❌ Maximum 10 channels allowed!")
            return
        
        try:
            with DatabaseSession() as session:
                user = session.query(User).filter_by(
                    id=ctx.author.id,
                    guild_id=ctx.guild.id
                ).first()
                
                if not user or not user.twitch_login:
                    await ctx.send("❌ You need to link your Twitch account first!\n"
                                  "Use `/linktwitch` to connect your account.")
                    return
                
                existing = session.query(StreamChatAnnouncement).filter_by(
                    user_id=ctx.author.id,
                    guild_id=ctx.guild.id
                ).first()
                
                if existing:
                    existing.target_twitch_channels = channels
                    existing.announcement_message = message
                    existing.enabled = True
                    session.commit()
                    
                    embed = discord.Embed(
                        title="✅ Live Announcement Updated!",
                        description=f"Your announcement will be posted in **{len(channels)} friend channel(s)** when you go live on Twitch!",
                        color=0x9146ff
                    )
                    embed.add_field(
                        name="📢 Your Message",
                        value=f"```{message}```",
                        inline=False
                    )
                    embed.add_field(
                        name="🎯 Target Channels",
                        value=", ".join([f"`{ch}`" for ch in channels]),
                        inline=False
                    )
                    embed.add_field(
                        name="💡 How It Works",
                        value="• You start streaming on Twitch\n"
                              "• Bot detects you're live (checks every 2 min)\n"
                              "• Bot posts your message in all friend chats\n"
                              "• Your friends see the notification instantly!",
                        inline=False
                    )
                    
                else:
                    announcement = StreamChatAnnouncement(
                        user_id=ctx.author.id,
                        guild_id=ctx.guild.id,
                        target_twitch_channels=channels,
                        announcement_message=message,
                        enabled=True
                    )
                    session.add(announcement)
                    session.commit()
                    
                    embed = discord.Embed(
                        title="🎉 Live Announcement Created!",
                        description=f"Your announcement will be posted in **{len(channels)} friend channel(s)** when you go live!",
                        color=0x00ff00
                    )
                    embed.add_field(
                        name="📢 Your Message",
                        value=f"```{message}```",
                        inline=False
                    )
                    embed.add_field(
                        name="🎯 Friend Channels",
                        value=", ".join([f"`{ch}`" for ch in channels]),
                        inline=False
                    )
                    embed.add_field(
                        name="💡 Perfect for DND Friends",
                        value="Your friends have DND enabled on Discord? No problem!\n"
                              "They'll see your message directly in their Twitch chat when they're streaming.",
                        inline=False
                    )
                    embed.set_footer(text=f"Streaming as {user.twitch_login}")
                
                await ctx.send(embed=embed)
                self.logger.info(f"✅ Live announcement set for {ctx.author.name} → {len(channels)} channels")
        
        except Exception as e:
            self.logger.error(f"Error setting live announcement: {e}")
            await ctx.send(f"❌ An error occurred: {str(e)}")
    
    @commands.command(name='removeliveannouncement', aliases=['deleteliveannouncement'])
    async def remove_live_announcement(self, ctx):
        """
        Remove your automatic stream chat announcement.
        
        Usage: !removeliveannouncement
        """
        try:
            with DatabaseSession() as session:
                announcement = session.query(StreamChatAnnouncement).filter_by(
                    user_id=ctx.author.id,
                    guild_id=ctx.guild.id
                ).first()
                
                if not announcement:
                    await ctx.send("❌ You don't have an active live announcement set.")
                    return
                
                session.delete(announcement)
                session.commit()
                
                embed = discord.Embed(
                    title="🗑️ Live Announcement Removed",
                    description="Your automatic stream chat announcement has been deleted.",
                    color=0xff0000
                )
                embed.add_field(
                    name="What Changed",
                    value="• No messages will be posted in friend chats when you go live\n"
                          "• You can create a new one anytime with `!setliveannouncement`",
                    inline=False
                )
                
                await ctx.send(embed=embed)
                self.logger.info(f"🗑️ Live announcement removed for {ctx.author.name}")
        
        except Exception as e:
            self.logger.error(f"Error removing live announcement: {e}")
            await ctx.send(f"❌ An error occurred: {str(e)}")
    
    @commands.command(name='viewliveannouncement', aliases=['showliveannouncement'])
    async def view_live_announcement(self, ctx):
        """
        View your current automatic stream chat announcement.
        
        Usage: !viewliveannouncement
        """
        try:
            with DatabaseSession() as session:
                announcement = session.query(StreamChatAnnouncement).filter_by(
                    user_id=ctx.author.id,
                    guild_id=ctx.guild.id
                ).first()
                
                if not announcement:
                    await ctx.send("❌ You don't have a live announcement set.\n\n"
                                  "**Create one with:**\n"
                                  "`!setliveannouncement friend1,friend2 | Hey, I'm live!`")
                    return
                
                status_emoji = "✅" if announcement.enabled else "❌"
                status_text = "Active" if announcement.enabled else "Disabled"
                
                embed = discord.Embed(
                    title="📋 Your Live Announcement",
                    description=f"Status: {status_emoji} **{status_text}**",
                    color=0x667eea if announcement.enabled else 0x808080
                )
                embed.add_field(
                    name="📢 Message",
                    value=f"```{announcement.announcement_message}```",
                    inline=False
                )
                embed.add_field(
                    name="🎯 Target Channels",
                    value=", ".join([f"`{ch}`" for ch in announcement.target_twitch_channels]),
                    inline=False
                )
                
                if announcement.last_sent_at:
                    last_sent = announcement.last_sent_at.strftime('%b %d, %Y at %I:%M %p') + ' UK'
                    embed.add_field(
                        name="🕐 Last Sent",
                        value=last_sent,
                        inline=True
                    )
                
                embed.add_field(
                    name="📝 Commands",
                    value="`!setliveannouncement channels | message` - Update\n"
                          "`!removeliveannouncement` - Delete",
                    inline=False
                )
                
                await ctx.send(embed=embed)
        
        except Exception as e:
            self.logger.error(f"Error viewing live announcement: {e}")
            await ctx.send(f"❌ An error occurred: {str(e)}")

async def setup(bot):
    await bot.add_cog(LiveChatAnnouncementCommands(bot))
