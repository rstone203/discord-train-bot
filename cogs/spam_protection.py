import discord
from discord.ext import commands
from datetime import datetime, timedelta, timezone
import logging
from typing import Optional
from database import DatabaseSession
from models import Guild, SpamReport

class SpamProtection(commands.Cog):
    """Monitor new member joins and detect suspicious accounts that may spam DMs."""
    
    def __init__(self, bot):
        self.bot = bot
        self.logger = logging.getLogger('discord_bot.spam_protection')
    
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        """Monitor new member joins and flag suspicious accounts."""
        try:
            self.logger.info(f"🚪 MEMBER JOIN EVENT: {member.name}#{member.discriminator} joined {member.guild.name} (ID: {member.id})")
            
            # Get guild settings from database
            with DatabaseSession() as session:
                guild = session.query(Guild).filter_by(
                    id=member.guild.id
                ).first()
                
                # Skip if spam protection not configured
                if not guild or (not guild.spam_alert_channel_id and not guild.spam_alert_dm_owner):
                    self.logger.info(f"⏭️ Skipping spam check for {member.guild.name} - not configured (guild exists: {guild is not None})")
                    return
                
                # Extract settings we need
                alert_channel_id = guild.spam_alert_channel_id
                auto_kick_enabled = guild.auto_kick_high_risk
                log_all = guild.log_all_joins
                dm_owner = guild.spam_alert_dm_owner
                custom_user_ids = guild.spam_alert_user_ids or []
            
            # Determine where to send alerts
            alert_destinations = []
            if dm_owner:
                # Send DM to custom users if set, otherwise server owner
                if custom_user_ids:
                    for user_id in custom_user_ids:
                        try:
                            user = await self.bot.fetch_user(int(user_id))
                            alert_destinations.append(user)
                        except Exception as e:
                            self.logger.warning(f"Failed to fetch custom alert user {user_id}: {e}")
                    
                    # Fall back to owner if all custom users failed
                    if not alert_destinations:
                        alert_destinations.append(member.guild.owner)
                else:
                    # Send to server owner
                    alert_destinations.append(member.guild.owner)
            elif alert_channel_id:
                # Send to channel
                channel = member.guild.get_channel(int(alert_channel_id))
                if channel:
                    alert_destinations.append(channel)
            
            if not alert_destinations:
                self.logger.warning(f"⚠️ No alert destinations configured for {member.guild.name}")
                return
            
            self.logger.info(f"📊 Analyzing {member.name} - Will send to {len(alert_destinations)} destination(s)")
            
            account_age = datetime.now(timezone.utc) - member.created_at
            account_age_days = account_age.days
            
            risk_level = "🟢 Low"
            risk_score = 0
            flags = []
            
            if account_age_days < 1:
                flags.append("⚠️ Account created today")
                risk_score += 3
            elif account_age_days < 7:
                flags.append("⚠️ Account less than 7 days old")
                risk_score += 2
            elif account_age_days < 30:
                flags.append("⚠️ Account less than 30 days old")
                risk_score += 1
            
            if member.avatar is None:
                flags.append("⚠️ No profile picture")
                risk_score += 1
            
            suspicious_patterns = [
                'discord', 'nitro', 'steam', 'free', 'gift', 'giveaway',
                'promo', 'win', 'prize', 'boost', 'airdrop', 'crypto',
                'nft', 'eth', 'bitcoin', 'trade', 'invest', 'profit',
                'money', 'earn', 'click', 'link', 'dm me', 'check', 'bio'
            ]
            username_lower = member.name.lower()
            if any(pattern in username_lower for pattern in suspicious_patterns):
                flags.append(f"⚠️ Suspicious username pattern detected")
                risk_score += 2
            
            if len(member.name) < 3 or member.name.isdigit():
                flags.append("⚠️ Unusual username format")
                risk_score += 1
            
            if len(member.name) > 25:
                flags.append("⚠️ Unusually long username")
                risk_score += 1
            
            import re
            if re.search(r'\d{3,}', member.name):
                flags.append("⚠️ Username contains many numbers")
                risk_score += 1
            
            special_char_count = sum(1 for c in member.name if not c.isalnum() and c != '_')
            if special_char_count > 3:
                flags.append("⚠️ Excessive special characters")
                risk_score += 1
            
            if member.name.isupper() and len(member.name) > 4:
                flags.append("⚠️ All caps username")
                risk_score += 1
            
            sequential_chars = any(
                member.name[i:i+3].lower() in 'abcdefghijklmnopqrstuvwxyz0123456789'
                for i in range(len(member.name) - 2)
            )
            if sequential_chars:
                flags.append("⚠️ Sequential character pattern")
                risk_score += 1
            
            if member.discriminator == "0000" or (member.discriminator and member.discriminator.startswith("000")):
                flags.append("⚠️ Suspicious discriminator")
                risk_score += 1
            
            if risk_score >= 5:
                risk_level = "🔴 High"
            elif risk_score >= 3:
                risk_level = "🟡 Medium"
            
            if risk_score >= 2 or log_all:
                embed = discord.Embed(
                    title="👤 New Member Joined",
                    description=f"{member.mention} joined the server",
                    color=0xff0000 if risk_score >= 5 else (0xffaa00 if risk_score >= 3 else 0x00ff00),
                    timestamp=datetime.utcnow()
                )
                
                embed.add_field(
                    name="📊 Member Info",
                    value=f"**Username:** {member.name}#{member.discriminator}\n**ID:** {member.id}",
                    inline=False
                )
                
                embed.add_field(
                    name="📅 Account Age",
                    value=f"{account_age_days} days old\n**Created:** {member.created_at.strftime('%Y-%m-%d %H:%M UTC')}",
                    inline=True
                )
                
                embed.add_field(
                    name="⚠️ Risk Level",
                    value=f"{risk_level} (Score: {risk_score})",
                    inline=True
                )
                
                if flags:
                    embed.add_field(
                        name="🚩 Flags Detected",
                        value="\n".join(flags),
                        inline=False
                    )
                
                if auto_kick_enabled and risk_score >= 5:
                    embed.add_field(
                        name="⚡ Auto-Action",
                        value="❌ **Auto-kicked** due to high risk score",
                        inline=False
                    )
                    
                    try:
                        await member.kick(reason=f"Auto-kick: High spam risk (score: {risk_score})")
                        embed.set_footer(text="✅ Member was automatically kicked")
                    except discord.Forbidden:
                        embed.set_footer(text="❌ Failed to kick - missing permissions")
                    except Exception as e:
                        self.logger.error(f"Error auto-kicking member: {e}")
                        embed.set_footer(text=f"❌ Kick failed: {str(e)}")
                else:
                    action_text = "💡 Suggested Actions:\n"
                    if risk_score >= 5:
                        action_text += "• Consider kicking/banning this user\n"
                        action_text += "• Watch for DM spam reports from members\n"
                    elif risk_score >= 3:
                        action_text += "• Monitor this user's activity\n"
                    
                    if risk_score >= 3:
                        embed.add_field(
                            name="🔧 Recommended Action",
                            value=action_text,
                            inline=False
                        )
                
                embed.set_thumbnail(url=member.display_avatar.url if member.avatar else member.default_avatar.url)
                embed.add_field(
                    name="🏰 Server",
                    value=f"{member.guild.name}",
                    inline=False
                )
                
                # Send alerts to all destinations
                for alert_destination in alert_destinations:
                    dest_type = "DM" if isinstance(alert_destination, discord.User) else "channel"
                    try:
                        await alert_destination.send(embed=embed)
                        self.logger.info(f"Spam alert sent via {dest_type} for {member.name}#{member.discriminator} (ID: {member.id}) - Risk: {risk_level} ({risk_score})")
                    except (discord.Forbidden, discord.HTTPException) as e:
                        self.logger.warning(f"Failed to send spam alert via {dest_type}: {e}")
                        # Fallback to channel if DM failed
                        if dm_owner and alert_channel_id:
                            try:
                                fallback_channel = member.guild.get_channel(int(alert_channel_id))
                                if fallback_channel:
                                    await fallback_channel.send(embed=embed)
                                    self.logger.info(f"Spam alert sent via fallback channel for {member.name} - DM failed")
                            except Exception as fallback_error:
                                self.logger.error(f"Fallback channel send also failed: {fallback_error}")
        
        except Exception as e:
            self.logger.error(f"Error in spam protection join monitor: {e}")
    
    @commands.command(name='setspamchannel')
    @commands.is_owner()
    async def set_spam_channel(self, ctx, channel: discord.TextChannel):
        """
        Set the channel where spam alerts will be sent (owner-only prefix command).
        
        Usage: !setspamchannel #channel-name
        """
        try:
            with DatabaseSession() as session:
                guild = session.query(Guild).filter_by(
                    id=ctx.guild.id
                ).first()
                
                if not guild:
                    guild = Guild(
                        id=ctx.guild.id,
                        name=ctx.guild.name,
                        owner_id=ctx.guild.owner_id
                    )
                    session.add(guild)
                
                guild.spam_alert_channel_id = str(channel.id)
                session.commit()
            
            embed = discord.Embed(
                title="✅ Spam Alert Channel Set",
                description=f"Suspicious member alerts will now be sent to {channel.mention}",
                color=0x00ff00,
                timestamp=datetime.utcnow()
            )
            
            embed.add_field(
                name="📊 What gets monitored",
                value="• New account joins (< 7 days old)\n• No profile picture\n• Suspicious username patterns\n• Unusual account characteristics",
                inline=False
            )
            
            embed.add_field(
                name="🛡️ Risk Levels",
                value="🟢 **Low** - Normal account\n🟡 **Medium** - Some suspicious flags\n🔴 **High** - Multiple red flags, likely spam bot",
                inline=False
            )
            
            await ctx.send(embed=embed)
            self.logger.info(f"Spam alert channel set to {channel.name} (ID: {channel.id}) by {ctx.author} in {ctx.guild.name}")
            
        except Exception as e:
            self.logger.error(f"Error setting spam alert channel: {e}")
            await ctx.send(f"❌ Error: {str(e)}")
    
    @commands.command(name='toggleautokick')
    @commands.is_owner()
    async def toggle_auto_kick(self, ctx, enabled: str):
        """
        Toggle auto-kick for high-risk suspicious accounts (owner-only prefix command).
        
        Usage: !toggleautokick on|off
        """
        # Convert string to boolean
        enabled = enabled.lower() in ['on', 'true', 'yes', '1', 'enable', 'enabled']
        
        try:
            with DatabaseSession() as session:
                guild = session.query(Guild).filter_by(
                    id=ctx.guild.id
                ).first()
                
                if not guild:
                    guild = Guild(
                        id=ctx.guild.id,
                        name=ctx.guild.name,
                        owner_id=ctx.guild.owner_id
                    )
                    session.add(guild)
                
                guild.auto_kick_high_risk = enabled
                session.commit()
            
            embed = discord.Embed(
                title="✅ Auto-Kick Setting Updated",
                description=f"Auto-kick for high-risk accounts: **{'Enabled' if enabled else 'Disabled'}**",
                color=0x00ff00 if enabled else 0xff0000,
                timestamp=datetime.utcnow()
            )
            
            if enabled:
                embed.add_field(
                    name="⚡ What happens",
                    value="Members with **5+ risk score** will be automatically kicked upon joining.\n\nHigh-risk indicators include:\n• Brand new account (< 1 day)\n• No avatar\n• Suspicious username patterns\n• Multiple red flags",
                    inline=False
                )
            else:
                embed.add_field(
                    name="📋 Manual Mode",
                    value="You will receive alerts for suspicious members, but they will NOT be automatically kicked. You can review and take action manually.",
                    inline=False
                )
            
            await ctx.send(embed=embed)
            self.logger.info(f"Auto-kick {'enabled' if enabled else 'disabled'} by {ctx.author} in {ctx.guild.name}")
            
        except Exception as e:
            self.logger.error(f"Error toggling auto-kick: {e}")
            await ctx.send(f"❌ Error: {str(e)}")
    
    @commands.command(name='togglespamdm')
    @commands.is_owner()
    async def toggle_spam_dm(self, ctx, enabled: str):
        """
        Toggle DM alerts to server owner instead of channel posting (owner-only prefix command).
        
        Usage: !togglespamdm on|off
        """
        # Convert string to boolean
        enabled = enabled.lower() in ['on', 'true', 'yes', '1', 'enable', 'enabled']
        
        try:
            with DatabaseSession() as session:
                guild = session.query(Guild).filter_by(
                    id=ctx.guild.id
                ).first()
                
                if not guild:
                    guild = Guild(
                        id=ctx.guild.id,
                        name=ctx.guild.name,
                        owner_id=ctx.guild.owner_id
                    )
                    session.add(guild)
                
                guild.spam_alert_dm_owner = enabled
                session.commit()
            
            embed = discord.Embed(
                title="✅ DM Alert Mode Updated",
                description=f"DM alerts to server owner: **{'Enabled' if enabled else 'Disabled'}**",
                color=0x00ff00 if enabled else 0xff0000,
                timestamp=datetime.utcnow()
            )
            
            if enabled:
                embed.add_field(
                    name="📬 DM Mode Active",
                    value=f"Spam alerts will be sent directly to {ctx.guild.owner.mention} via DM.\n\n💡 If DMs fail, alerts will fall back to the configured channel (if set).",
                    inline=False
                )
            else:
                embed.add_field(
                    name="📢 Channel Mode",
                    value="Alerts will be sent to the configured spam alert channel.\n\nUse `!setspamchannel #channel` to configure.",
                    inline=False
                )
            
            await ctx.send(embed=embed)
            self.logger.info(f"Spam DM alerts {'enabled' if enabled else 'disabled'} by {ctx.author} in {ctx.guild.name}")
            
        except Exception as e:
            self.logger.error(f"Error toggling spam DM alerts: {e}")
            await ctx.send(f"❌ Error: {str(e)}")
    
    @commands.command(name='setspamuser')
    @commands.is_owner()
    async def set_spam_user(self, ctx, *users: discord.User):
        """
        Set custom users to receive spam alerts via DM (owner-only prefix command).
        
        Usage: !setspamuser @user1 @user2 @user3
        """
        if not users:
            await ctx.send("❌ Please mention at least one user to receive spam alerts.")
            return
        
        try:
            user_ids = [user.id for user in users]
            
            with DatabaseSession() as session:
                guild = session.query(Guild).filter_by(
                    id=ctx.guild.id
                ).first()
                
                if not guild:
                    guild = Guild(
                        id=ctx.guild.id,
                        name=ctx.guild.name,
                        owner_id=ctx.guild.owner_id
                    )
                    session.add(guild)
                
                guild.spam_alert_user_ids = user_ids
                guild.spam_alert_dm_owner = True  # Auto-enable DM mode
                session.commit()
            
            embed = discord.Embed(
                title="✅ Spam Alert Recipients Set",
                description=f"Spam alerts will now be sent to **{len(users)} user(s)** via DM",
                color=0x00ff00,
                timestamp=datetime.utcnow()
            )
            
            user_list = "\n".join([f"• {user.mention} ({user.name}#{user.discriminator})" for user in users])
            embed.add_field(
                name="📬 Recipients",
                value=user_list,
                inline=False
            )
            
            embed.add_field(
                name="💡 Note",
                value="If DMs fail, alerts will fall back to the configured channel (if set).\n\nUse `!addspamuser @user` to add more recipients or `!removespamuser @user` to remove.",
                inline=False
            )
            
            await ctx.send(embed=embed)
            self.logger.info(f"Spam alert recipients set to {len(users)} users by {ctx.author} in {ctx.guild.name}")
            
        except Exception as e:
            self.logger.error(f"Error setting spam alert recipients: {e}")
            await ctx.send(f"❌ Error: {str(e)}")
    
    @commands.command(name='togglelogalljoins')
    @commands.is_owner()
    async def toggle_log_all_joins(self, ctx, enabled: str):
        """
        Toggle logging ALL member joins (owner-only prefix command).
        
        Usage: !togglelogalljoins on|off
        """
        # Convert string to boolean
        enabled = enabled.lower() in ['on', 'true', 'yes', '1', 'enable', 'enabled']
        
        try:
            with DatabaseSession() as session:
                guild = session.query(Guild).filter_by(
                    id=ctx.guild.id
                ).first()
                
                if not guild:
                    guild = Guild(
                        id=ctx.guild.id,
                        name=ctx.guild.name,
                        owner_id=ctx.guild.owner_id
                    )
                    session.add(guild)
                
                guild.log_all_joins = enabled
                session.commit()
            
            embed = discord.Embed(
                title="✅ Join Logging Updated",
                description=f"Log all member joins: **{'Enabled' if enabled else 'Disabled'}**",
                color=0x00ff00,
                timestamp=datetime.utcnow()
            )
            
            if enabled:
                embed.add_field(
                    name="📋 Full Logging",
                    value="ALL members will be logged when they join, even if they have no suspicious flags.\n\nThis is useful for smaller servers or when you want complete join history.",
                    inline=False
                )
            else:
                embed.add_field(
                    name="🎯 Filtered Logging",
                    value="Only members with **2+ risk score** will be logged.\n\nThis reduces spam in the alert channel while still catching suspicious accounts.",
                    inline=False
                )
            
            await ctx.send(embed=embed)
            self.logger.info(f"Log all joins {'enabled' if enabled else 'disabled'} by {ctx.author} in {ctx.guild.name}")
            
        except Exception as e:
            self.logger.error(f"Error toggling log all joins: {e}")
            await ctx.send(f"❌ Error: {str(e)}")
    
    @commands.command(name='testintent')
    @commands.is_owner()
    async def test_intent(self, ctx):
        """
        Test if the bot can receive member join events (owner-only prefix command).
        
        Usage: !testintent
        """
        try:
            embed = discord.Embed(
                title="🔍 Member Intent Diagnostic",
                description="Checking if the bot can receive member join events...",
                color=0x9146FF,
                timestamp=datetime.utcnow()
            )
            
            # Check if members intent is enabled
            members_intent_enabled = self.bot.intents.members
            
            embed.add_field(
                name="1️⃣ Code Configuration",
                value=f"**Members Intent in Code:** {'✅ Enabled' if members_intent_enabled else '❌ Disabled'}",
                inline=False
            )
            
            # Check if we can see guild members
            member_count = ctx.guild.member_count if ctx.guild.member_count else "Unknown"
            cached_members = len(ctx.guild.members) if ctx.guild.members else 0
            
            embed.add_field(
                name="2️⃣ Member Access Test",
                value=f"**Total Members:** {member_count}\n**Cached Members:** {cached_members}",
                inline=False
            )
            
            # Check guild settings
            with DatabaseSession() as session:
                guild = session.query(Guild).filter_by(id=ctx.guild.id).first()
                configured = guild and (guild.spam_alert_channel_id or guild.spam_alert_dm_owner)
            
            embed.add_field(
                name="3️⃣ Spam Protection Config",
                value=f"**Database Setup:** {'✅ Configured' if configured else '❌ Not configured'}",
                inline=False
            )
            
            # Final verdict
            if members_intent_enabled and cached_members > 0:
                verdict = "✅ **WORKING** - Bot should receive member join events!"
                verdict_color = 0x00ff00
            elif members_intent_enabled and cached_members == 0:
                verdict = "⚠️ **PARTIAL** - Intent enabled but no cached members.\n\n**Action Required:**\n1. Enable 'Server Members Intent' in Discord Developer Portal\n2. Re-invite bot using `!botinvite`\n3. Wait a few minutes for cache to populate"
                verdict_color = 0xffaa00
            else:
                verdict = "❌ **NOT WORKING** - Members intent is disabled!"
                verdict_color = 0xff0000
            
            embed.add_field(
                name="🎯 Verdict",
                value=verdict,
                inline=False
            )
            
            embed.color = verdict_color
            embed.set_footer(text="Use !spamstatus to check spam protection settings")
            
            await ctx.send(embed=embed)
            self.logger.info(f"Intent test run by {ctx.author} in {ctx.guild.name} - Members intent: {members_intent_enabled}, Cached: {cached_members}")
            
        except Exception as e:
            self.logger.error(f"Error testing intent: {e}")
            await ctx.send(f"❌ Error: {str(e)}")
    
    @commands.command(name='spamstatus')
    @commands.is_owner()
    async def spam_status(self, ctx):
        """
        Check current spam protection settings (owner-only prefix command).
        
        Usage: !spamstatus
        """
        try:
            # Get guild settings from database
            with DatabaseSession() as session:
                guild = session.query(Guild).filter_by(
                    id=ctx.guild.id
                ).first()
                
                # Extract settings we need
                if guild:
                    alert_channel_id = guild.spam_alert_channel_id
                    auto_kick = guild.auto_kick_high_risk
                    log_all = guild.log_all_joins
                    dm_owner = guild.spam_alert_dm_owner
                    custom_user_ids = guild.spam_alert_user_ids or []
                else:
                    alert_channel_id = None
                    auto_kick = False
                    log_all = False
                    dm_owner = False
                    custom_user_ids = []
            
            embed = discord.Embed(
                title="🛡️ Spam Protection Status",
                color=0x9146FF,
                timestamp=datetime.utcnow()
            )
            
            # Show DM mode or channel mode
            if dm_owner:
                # Determine who receives the alerts
                if custom_user_ids:
                    recipient_lines = []
                    for user_id in custom_user_ids:
                        try:
                            alert_user = await self.bot.fetch_user(int(user_id))
                            recipient_lines.append(f"• {alert_user.mention} ({alert_user.name}#{alert_user.discriminator})")
                        except:
                            recipient_lines.append(f"• User ID: {user_id}")
                    recipient_text = f"✅ **DM Mode** - Alerts sent to {len(custom_user_ids)} user(s):\n" + "\n".join(recipient_lines)
                else:
                    recipient_text = f"✅ **DM Mode** - Alerts sent to {ctx.guild.owner.mention}"
                
                embed.add_field(
                    name="📬 Alert Destination",
                    value=recipient_text,
                    inline=False
                )
                if alert_channel_id:
                    channel = ctx.guild.get_channel(int(alert_channel_id))
                    embed.add_field(
                        name="🔄 Fallback Channel",
                        value=channel.mention if channel else "❌ Not found",
                        inline=False
                    )
            elif alert_channel_id:
                channel = ctx.guild.get_channel(int(alert_channel_id))
                embed.add_field(
                    name="📢 Alert Channel",
                    value=channel.mention if channel else "❌ Channel not found",
                    inline=False
                )
            else:
                embed.add_field(
                    name="📢 Alert Destination",
                    value="❌ Not configured - use `!setspamchannel` or `!togglespamdm on`",
                    inline=False
                )
            
            # Check if bot has kick permission
            bot_member = ctx.guild.get_member(self.bot.user.id)
            can_kick = bot_member.guild_permissions.kick_members if bot_member else False
            
            embed.add_field(
                name="⚡ Auto-Kick High Risk",
                value=f"{'✅ Enabled' if auto_kick else '❌ Disabled'}",
                inline=True
            )
            
            embed.add_field(
                name="📋 Log All Joins",
                value=f"{'✅ Enabled' if log_all else '❌ Disabled (filtered)'}",
                inline=True
            )
            
            # Add permission check field
            permission_status = "✅ Yes" if can_kick else "❌ No (needs Kick Members permission)"
            permission_warning = ""
            
            if auto_kick and not can_kick:
                permission_warning = "\n⚠️ **Warning:** Auto-kick is enabled but bot lacks Kick Members permission!"
            
            embed.add_field(
                name="🔐 Can Kick Members",
                value=f"{permission_status}{permission_warning}",
                inline=True
            )
            
            embed.add_field(
                name="🎯 Detection Rules",
                value="• New accounts (< 7 days)\n• No profile picture\n• Suspicious usernames\n• Unusual patterns",
                inline=False
            )
            
            embed.set_footer(text=f"Server: {ctx.guild.name}")
            
            await ctx.send(embed=embed)
            
        except Exception as e:
            self.logger.error(f"Error checking spam status: {e}")
            await ctx.send(f"❌ Error: {str(e)}")
    
    @discord.app_commands.command(name='reportspam', description='Report a spam DM or suspicious user')
    @discord.app_commands.describe(
        user='The user who sent spam (optional - provide username if not in server)',
        username='Username of spammer if they are not in the server',
        details='Details about the spam (what they said, when, etc.)'
    )
    async def report_spam(
        self, 
        interaction: discord.Interaction,
        user: Optional[discord.User] = None,
        username: Optional[str] = None,
        details: Optional[str] = None
    ):
        """Allow members to report spam DMs."""
        try:
            await interaction.response.defer(ephemeral=True)
            
            if not user and not username:
                await interaction.followup.send(
                    "❌ Please provide either a user mention or username of the spammer.",
                    ephemeral=True
                )
                return
            
            reported_user_id = user.id if user else None
            reported_username = f"{user.name}#{user.discriminator}" if user else username
            
            with DatabaseSession() as session:
                guild = session.query(Guild).filter_by(
                    id=interaction.guild.id
                ).first()
                
                if not guild:
                    guild = Guild(
                        id=interaction.guild.id,
                        name=interaction.guild.name,
                        owner_id=interaction.guild.owner_id
                    )
                    session.add(guild)
                    session.flush()
                
                spam_report = SpamReport(
                    guild_id=interaction.guild.id,
                    reporter_id=interaction.user.id,
                    reporter_username=f"{interaction.user.name}#{interaction.user.discriminator}",
                    reported_user_id=reported_user_id,
                    reported_username=reported_username,
                    report_type='dm_spam',
                    report_details=details or "No details provided",
                    severity='medium',
                    is_resolved=False
                )
                session.add(spam_report)
                session.commit()
                
                report_id = spam_report.id
            
            embed = discord.Embed(
                title="✅ Spam Report Submitted",
                description="Thank you for helping keep our community safe!",
                color=0x00ff00,
                timestamp=datetime.utcnow()
            )
            
            embed.add_field(
                name="📋 Report Details",
                value=f"**Report ID:** #{report_id}\n**Reported User:** {reported_username}\n**Details:** {details or 'None provided'}",
                inline=False
            )
            
            embed.add_field(
                name="✅ Next Steps",
                value="Server moderators have been notified and will review this report. If the user is still in the server, they may be removed.",
                inline=False
            )
            
            await interaction.followup.send(embed=embed, ephemeral=True)
            
            self.logger.info(
                f"Spam report #{report_id} submitted by {interaction.user.name} "
                f"in {interaction.guild.name} - Reported: {reported_username}"
            )
            
            try:
                alert_embed = discord.Embed(
                    title="🚨 New Spam Report",
                    description=f"A member has reported spam in **{interaction.guild.name}**",
                    color=0xff0000,
                    timestamp=datetime.utcnow()
                )
                
                alert_embed.add_field(
                    name="👤 Reporter",
                    value=f"{interaction.user.mention} ({interaction.user.name}#{interaction.user.discriminator})",
                    inline=False
                )
                
                alert_embed.add_field(
                    name="🎯 Reported User",
                    value=f"{user.mention if user else reported_username}",
                    inline=True
                )
                
                alert_embed.add_field(
                    name="📋 Report ID",
                    value=f"#{report_id}",
                    inline=True
                )
                
                alert_embed.add_field(
                    name="📝 Details",
                    value=details or "No details provided",
                    inline=False
                )
                
                alert_embed.add_field(
                    name="⚠️ Suggested Actions",
                    value="• Review the user's message history\n• Check if they're still in the server\n• Consider kicking/banning if confirmed spam\n• Use `/spamreports` to view all reports",
                    inline=False
                )
                
                alert_embed.set_footer(text=f"Server: {interaction.guild.name}")
                
                with DatabaseSession() as session:
                    guild_settings = session.query(Guild).filter_by(
                        id=interaction.guild.id
                    ).first()
                    
                    if guild_settings:
                        custom_user_ids = guild_settings.spam_alert_user_ids or []
                        dm_owner = guild_settings.spam_alert_dm_owner
                        alert_channel_id = guild_settings.spam_alert_channel_id
                        
                        if dm_owner and custom_user_ids:
                            # Send to all custom recipients
                            for user_id in custom_user_ids:
                                try:
                                    alert_user = await self.bot.fetch_user(int(user_id))
                                    await alert_user.send(embed=alert_embed)
                                    self.logger.info(f"Spam report alert sent to user {user_id}")
                                except Exception as e:
                                    self.logger.warning(f"Failed to DM user {user_id}: {e}")
                            
                            # Fallback to channel if all DMs failed
                            # Note: Could be improved to only fallback if ALL failed
                        elif dm_owner:
                            try:
                                await interaction.guild.owner.send(embed=alert_embed)
                                self.logger.info(f"Spam report alert sent to server owner")
                            except Exception as e:
                                self.logger.warning(f"Failed to DM server owner: {e}")
                                if alert_channel_id:
                                    channel = interaction.guild.get_channel(int(alert_channel_id))
                                    if channel:
                                        await channel.send(embed=alert_embed)
                        elif alert_channel_id:
                            channel = interaction.guild.get_channel(int(alert_channel_id))
                            if channel:
                                await channel.send(embed=alert_embed)
                                self.logger.info(f"Spam report alert sent to channel {alert_channel_id}")
            
            except Exception as e:
                self.logger.error(f"Error sending spam report alert: {e}")
        
        except Exception as e:
            self.logger.error(f"Error creating spam report: {e}")
            await interaction.followup.send(
                f"❌ Error submitting report: {str(e)}",
                ephemeral=True
            )
    
    @commands.command(name='spamreports')
    @commands.is_owner()
    async def spam_reports(self, ctx, limit: int = 10):
        """
        View recent spam reports (owner-only prefix command).
        
        Usage: !spamreports [limit]
        """
        try:
            with DatabaseSession() as session:
                reports = session.query(SpamReport).filter_by(
                    guild_id=ctx.guild.id
                ).order_by(SpamReport.created_at.desc()).limit(limit).all()
                
                if not reports:
                    await ctx.send("📭 No spam reports found for this server.")
                    return
                
                embed = discord.Embed(
                    title=f"🚨 Recent Spam Reports ({len(reports)})",
                    color=0x9146FF,
                    timestamp=datetime.utcnow()
                )
                
                for report in reports:
                    status_emoji = "✅" if report.is_resolved else "⏳"
                    severity_emoji = {
                        'low': '🟢',
                        'medium': '🟡',
                        'high': '🔴'
                    }.get(report.severity, '⚪')
                    
                    report_time = report.created_at.strftime('%Y-%m-%d %H:%M') + ' UK'
                    
                    embed.add_field(
                        name=f"{status_emoji} {severity_emoji} Report #{report.id}",
                        value=f"**Reporter:** {report.reporter_username}\n"
                              f"**Reported:** {report.reported_username or 'Unknown'}\n"
                              f"**Details:** {report.report_details[:100]}...\n"
                              f"**Date:** {report_time}\n"
                              f"**Status:** {'Resolved' if report.is_resolved else 'Pending'}",
                        inline=False
                    )
                
                embed.set_footer(text=f"Showing {len(reports)} most recent reports • Server: {ctx.guild.name}")
                
                await ctx.send(embed=embed)
            
        except Exception as e:
            self.logger.error(f"Error fetching spam reports: {e}")
            await ctx.send(f"❌ Error: {str(e)}")

async def setup(bot):
    await bot.add_cog(SpamProtection(bot))
