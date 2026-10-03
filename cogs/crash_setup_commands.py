"""
Crash notification setup commands for managing webhook URLs.
"""

import discord
from discord.ext import commands
import os
import requests
import json
import logging
from typing import Optional
logger = logging.getLogger(__name__)

def is_owner_or_trusted():
    """Check if user is the bot owner or a trusted user."""
    async def predicate(ctx):
        return await ctx.bot.is_owner_or_trusted(ctx.author)
    return commands.check(predicate)

class CrashSetupCommands(commands.Cog):
    """Commands for setting up crash notifications."""
    
    def __init__(self, bot):
        self.bot = bot
        
    @commands.command(name='setup_crash_alerts', help='Set up crash notification webhook (requires webhook URL)')
    @is_owner_or_trusted()
    async def setup_crash_alerts(self, ctx, *, webhook_url: str = None):
        """Set up crash notification webhook URL."""
        if not webhook_url:
            await ctx.send("❌ Please provide a webhook URL: `!setup_crash_alerts <webhook_url>`")
            return
            
        try:
            # Validate webhook URL format
            if not webhook_url.startswith("https://discord.com/api/webhooks/"):
                await ctx.send("❌ Invalid webhook URL format. Must be a Discord webhook URL.")
                return
            
            # Test the webhook
            test_payload = {
                "embeds": [{
                    "title": "🔧 Crash Alert Setup",
                    "description": "Crash notifications have been successfully configured!",
                    "color": 0x00ff00,
                    "footer": {"text": "Test message from Jordies Train Bot"}
                }],
                "username": "Bot Monitor"
            }
            
            response = requests.post(webhook_url, json=test_payload, timeout=10)
            
            if response.status_code != 204:
                await ctx.send(f"❌ Failed to send test message to webhook: {response.status_code}")
                return
            
            # Store webhook URL in environment (for this session)
            os.environ['CRASH_WEBHOOK_URL'] = webhook_url
            
            # Initialize/reinitialize the crash notifier
            from utils.crash_notifications import initialize_notifier
            initialize_notifier(webhook_url)
            
            logger.info(f"Crash webhook configured by {ctx.author}")
            
            embed = discord.Embed(
                title="✅ Crash Notifications Configured!",
                description="You will now receive Discord alerts when:",
                color=0x00ff00
            )
            embed.add_field(name="🚨 Bot Crashes", value="Immediate notification when the bot goes down unexpectedly", inline=False)
            embed.add_field(name="⚠️ Health Warnings", value="Advance warnings before critical failures", inline=False)
            embed.add_field(name="🔄 Restart Success", value="Confirmation when the bot comes back online", inline=False)
            embed.set_footer(text="This configuration is temporary and will reset on bot restart. For permanent setup, add CRASH_WEBHOOK_URL to your environment secrets.")
            
            await ctx.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Error setting up crash alerts: {e}")
            await ctx.send(f"❌ Error setting up crash alerts: {str(e)}")
    
    @commands.command(name='test_crash_alert', help='Send a test crash notification')
    @is_owner_or_trusted()
    async def test_crash_alert(self, ctx):
        """Send a test crash notification."""
        try:
            from utils.crash_notifications import send_crash_alert, get_notifier
            
            notifier = get_notifier()
            if not notifier.enabled:
                await ctx.send("❌ Crash notifications not configured. Use `!setup_crash_alerts` first.")
                return
            
            # Send test crash alert
            success = send_crash_alert("Test crash alert triggered by user command", {
                'consecutive_failures': 1,
                'uptime_seconds': 3600,
                'restarts_triggered': 1
            })
            
            if success:
                await ctx.send("✅ Test crash alert sent successfully! Check your configured webhook channel.")
            else:
                await ctx.send("❌ Failed to send test crash alert.")
                
        except Exception as e:
            logger.error(f"Error sending test crash alert: {e}")
            await ctx.send(f"❌ Error: {str(e)}")
    
    @commands.command(name='crash_status', help='Check crash notification status')
    @is_owner_or_trusted()
    async def crash_status(self, ctx):
        """Check the status of crash notifications."""
        try:
            from utils.crash_notifications import get_notifier
            from utils.bot_monitor import get_monitor
            
            notifier = get_notifier()
            monitor = get_monitor()
            health = monitor.check_health()
            
            embed = discord.Embed(
                title="🚨 Crash Notification Status",
                color=0x00ff00 if notifier.enabled else 0xff0000
            )
            
            embed.add_field(
                name="📡 Webhook Status",
                value="✅ Configured" if notifier.enabled else "❌ Not configured",
                inline=True
            )
            
            embed.add_field(
                name="💚 Bot Health",
                value="✅ Healthy" if health["healthy"] else "⚠️ Issues detected",
                inline=True
            )
            
            embed.add_field(
                name="🔄 Restart Count",
                value=str(monitor.stats.get("restarts_triggered", 0)),
                inline=True
            )
            
            if not health["healthy"]:
                embed.add_field(
                    name="⚠️ Current Issues",
                    value="\n".join(health["issues"]) or "None",
                    inline=False
                )
            
            embed.add_field(
                name="⏱️ Uptime",
                value=f"{int(health['uptime_seconds'] / 60)} minutes",
                inline=True
            )
            
            if not notifier.enabled:
                embed.add_field(
                    name="🔧 Setup Commands",
                    value="`!setup_crash_alerts <webhook_url>` - Configure notifications\n`!test_crash_alert` - Test the system",
                    inline=False
                )
            
            await ctx.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Error checking crash status: {e}")
            await ctx.send(f"❌ Error: {str(e)}")

async def setup(bot):
    await bot.add_cog(CrashSetupCommands(bot))