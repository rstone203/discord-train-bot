#!/usr/bin/env python3
"""
Crash notification system for Discord bot monitoring.
Sends alerts via Discord webhook when the bot crashes or restarts.
"""

import os
import requests
import json
import logging
from datetime import datetime
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

class CrashNotifier:
    """
    Handles crash notifications via Discord webhook.
    """
    
    def __init__(self, webhook_url: Optional[str] = None):
        """Initialize crash notifier with webhook URL."""
        self.webhook_url = webhook_url or os.getenv('CRASH_WEBHOOK_URL')
        self.enabled = bool(self.webhook_url)
        
        if not self.enabled:
            logger.warning("Crash notifications disabled - no webhook URL provided")
        else:
            logger.info("Crash notifications enabled")
    
    def send_crash_alert(self, reason: str, details: Optional[Dict[str, Any]] = None):
        """Send crash alert to Discord webhook."""
        if not self.enabled:
            return False
            
        try:
            embed = {
                "title": "🚨 **BOT CRASH DETECTED**",
                "description": f"**Jordies Train Bot** has unexpectedly crashed and is restarting.",
                "color": 0xff0000,  # Red color
                "fields": [
                    {
                        "name": "🔴 Crash Reason",
                        "value": f"```{reason}```",
                        "inline": False
                    },
                    {
                        "name": "⏰ Time",
                        "value": f"<t:{int(datetime.now().timestamp())}:F>",
                        "inline": True
                    },
                    {
                        "name": "🔄 Status",
                        "value": "Auto-restarting...",
                        "inline": True
                    }
                ],
                "footer": {
                    "text": "Replit Reserved VM - Auto-restart enabled"
                },
                "timestamp": datetime.now().isoformat()
            }
            
            # Add additional details if provided
            if details:
                if details.get('consecutive_failures'):
                    embed["fields"].append({
                        "name": "⚠️ Consecutive Failures", 
                        "value": str(details['consecutive_failures']),
                        "inline": True
                    })
                
                if details.get('uptime_seconds'):
                    uptime_minutes = int(details['uptime_seconds'] / 60)
                    embed["fields"].append({
                        "name": "⏱️ Uptime Before Crash",
                        "value": f"{uptime_minutes} minutes",
                        "inline": True
                    })
            
            payload = {
                "embeds": [embed],
                "username": "Train Bot Monitor"
            }
            
            response = requests.post(
                self.webhook_url,  # type: ignore
                json=payload,
                timeout=10
            )
            
            if response.status_code == 204:
                logger.info("✅ Crash alert sent successfully")
                return True
            else:
                logger.error(f"❌ Failed to send crash alert: {response.status_code}")
                return False
                
        except Exception as e:
            logger.error(f"❌ Error sending crash notification: {e}")
            return False
    
    def send_restart_success(self, restart_count: int = 0):
        """Send notification when bot successfully restarts after crash."""
        if not self.enabled:
            return False
            
        try:
            embed = {
                "title": "✅ **BOT RESTART SUCCESSFUL**",
                "description": f"**Jordies Train Bot** has successfully restarted and is back online!",
                "color": 0x00ff00,  # Green color
                "fields": [
                    {
                        "name": "🟢 Status",
                        "value": "Online and operational",
                        "inline": True
                    },
                    {
                        "name": "⏰ Restart Time",
                        "value": f"<t:{int(datetime.now().timestamp())}:F>",
                        "inline": True
                    }
                ],
                "footer": {
                    "text": "Train notifications resuming normal operation"
                },
                "timestamp": datetime.now().isoformat()
            }
            
            if restart_count > 0:
                embed["fields"].append({
                    "name": "🔄 Total Restarts Today",
                    "value": str(restart_count),
                    "inline": True
                })
            
            payload = {
                "embeds": [embed],
                "username": "Train Bot Monitor"
            }
            
            response = requests.post(
                self.webhook_url,  # type: ignore
                json=payload,
                timeout=10
            )
            
            if response.status_code == 204:
                logger.info("✅ Restart success notification sent")
                return True
            else:
                logger.error(f"❌ Failed to send restart notification: {response.status_code}")
                return False
                
        except Exception as e:
            logger.error(f"❌ Error sending restart notification: {e}")
            return False
    
    def send_health_warning(self, warning_type: str, details: str):
        """Send health warning before critical failure."""
        if not self.enabled:
            return False
            
        try:
            embed = {
                "title": "⚠️ **BOT HEALTH WARNING**",
                "description": f"**Jordies Train Bot** is experiencing health issues",
                "color": 0xffa500,  # Orange color
                "fields": [
                    {
                        "name": "⚠️ Warning Type",
                        "value": warning_type,
                        "inline": False
                    },
                    {
                        "name": "📝 Details",
                        "value": details,
                        "inline": False
                    },
                    {
                        "name": "⏰ Time",
                        "value": f"<t:{int(datetime.now().timestamp())}:F>",
                        "inline": True
                    }
                ],
                "footer": {
                    "text": "Monitoring for potential restart"
                },
                "timestamp": datetime.now().isoformat()
            }
            
            payload = {
                "embeds": [embed],
                "username": "Train Bot Monitor"
            }
            
            response = requests.post(
                self.webhook_url,  # type: ignore
                json=payload,
                timeout=10
            )
            
            if response.status_code == 204:
                logger.info("✅ Health warning sent")
                return True
            else:
                logger.error(f"❌ Failed to send health warning: {response.status_code}")
                return False
                
        except Exception as e:
            logger.error(f"❌ Error sending health warning: {e}")
            return False

# Global notifier instance
_notifier_instance: Optional[CrashNotifier] = None

def get_notifier() -> CrashNotifier:
    """Get the global crash notifier instance."""
    global _notifier_instance
    if _notifier_instance is None:
        _notifier_instance = CrashNotifier()
    return _notifier_instance

def initialize_notifier(webhook_url: Optional[str] = None):
    """Initialize the global crash notifier."""
    global _notifier_instance
    _notifier_instance = CrashNotifier(webhook_url)
    return _notifier_instance

def send_crash_alert(reason: str, details: Optional[Dict[str, Any]] = None):
    """Send crash alert using global notifier."""
    return get_notifier().send_crash_alert(reason, details)

def send_restart_success(restart_count: int = 0):
    """Send restart success notification using global notifier."""
    return get_notifier().send_restart_success(restart_count)

def send_health_warning(warning_type: str, details: str):
    """Send health warning using global notifier."""
    return get_notifier().send_health_warning(warning_type, details)

async def send_error_notification(error_type: str, error_details: str):
    """Send error notification (async wrapper for send_health_warning)."""
    return send_health_warning(error_type, error_details)