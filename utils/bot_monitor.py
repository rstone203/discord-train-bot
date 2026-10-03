#!/usr/bin/env python3
"""
Bot monitoring utilities for auto-restart and health checking.
Designed for Reserved VM Deployment environments.
"""

import os
import sys
import time
import logging
import asyncio
import threading
from datetime import datetime, timedelta
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

class BotHealthMonitor:
    """
    Monitors Discord bot health and triggers restarts when needed.
    Works with Reserved VM Deployment auto-restart capabilities.
    """
    
    def __init__(self, bot_instance=None):
        self.bot_instance = bot_instance
        self.last_heartbeat = datetime.now()
        self.last_notification_check = datetime.now()
        self.consecutive_failures = 0
        self.max_failures = 5
        self.monitoring_enabled = True
        self.last_warning_sent = None  # Track when last warning was sent
        self.warning_cooldown_seconds = 3600  # 1 hour cooldown between warnings
        self.stats = {
            "restarts_triggered": 0,
            "rate_limits_detected": 0,
            "connection_failures": 0,
            "last_restart": None
        }
        
    def update_heartbeat(self):
        """Update the last heartbeat timestamp."""
        self.last_heartbeat = datetime.now()
        self.consecutive_failures = 0
        
    def update_notification_check(self):
        """Update the last notification check timestamp."""
        self.last_notification_check = datetime.now()
        
    def check_health(self) -> Dict[str, Any]:
        """
        Check bot health status.
        Returns dict with health information.
        """
        now = datetime.now()
        health_status = {
            "healthy": True,
            "issues": [],
            "uptime_seconds": (now - self.last_heartbeat).total_seconds(),
            "last_heartbeat": self.last_heartbeat.isoformat(),
            "consecutive_failures": self.consecutive_failures,
            "stats": self.stats.copy()
        }
        
        # Check if heartbeat is stale (no activity in 10 minutes) - increased tolerance
        if (now - self.last_heartbeat).total_seconds() > 600:
            health_status["healthy"] = False
            health_status["issues"].append("Stale heartbeat - no activity in 10+ minutes")
            
        # Check if notification system is stale (no checks in 5 minutes)
        # Threshold is 300s to allow for slow scheduler ticks (DB + API calls can push
        # the gap between ticks beyond the old 120s limit, causing false-positive kills)
        if (now - self.last_notification_check).total_seconds() > 300:
            health_status["healthy"] = False
            health_status["issues"].append("Notification system stale - no checks in 5+ minutes")
            
        # Check consecutive failures
        if self.consecutive_failures >= self.max_failures:
            health_status["healthy"] = False
            health_status["issues"].append(f"Too many consecutive failures: {self.consecutive_failures}")
            
        return health_status
        
    def record_failure(self, failure_type: str, details: str = ""):
        """Record a failure event."""
        self.consecutive_failures += 1
        logger.warning(f"Bot failure recorded: {failure_type} - {details}")
        
        if failure_type == "rate_limit":
            self.stats["rate_limits_detected"] += 1
        elif failure_type == "connection":
            self.stats["connection_failures"] += 1
            
        # Trigger restart if too many failures
        if self.consecutive_failures >= self.max_failures:
            self.trigger_restart(f"Max failures reached: {failure_type}")
            
    def trigger_restart(self, reason: str):
        """
        Trigger a bot restart.
        For Reserved VM, this will exit the process and let VM restart it.
        """
        logger.error(f"TRIGGERING BOT RESTART: {reason}")
        self.stats["restarts_triggered"] += 1
        self.stats["last_restart"] = datetime.now().isoformat()
        
        # Send crash notification before restarting
        try:
            from utils.crash_notifications import send_crash_alert
            health_status = self.check_health()
            send_crash_alert(reason, {
                'consecutive_failures': self.consecutive_failures,
                'uptime_seconds': health_status.get('uptime_seconds', 0),
                'restarts_triggered': self.stats["restarts_triggered"]
            })
            logger.info("✅ Crash notification sent")
        except Exception as e:
            logger.error(f"❌ Failed to send crash notification: {e}")
        
        # In Reserved VM, exiting the process triggers VM-level restart
        logger.info("Exiting process - Reserved VM will restart automatically")
        os._exit(1)  # Force exit to trigger VM restart
        
    def start_monitoring(self):
        """Start background monitoring thread."""
        if not self.monitoring_enabled:
            return
            
        def monitor_loop():
            while self.monitoring_enabled:
                try:
                    health = self.check_health()
                    if not health["healthy"]:
                        logger.warning(f"Bot health issues detected: {', '.join(health['issues'])}")
                        
                        # Only send health warning if cooldown has passed
                        now = datetime.now()
                        should_send_warning = (
                            self.last_warning_sent is None or 
                            (now - self.last_warning_sent).total_seconds() >= self.warning_cooldown_seconds
                        )
                        
                        if should_send_warning:
                            try:
                                from utils.crash_notifications import send_health_warning
                                warning_details = '\n'.join(health["issues"])
                                send_health_warning("Health Issues Detected", warning_details)
                                self.last_warning_sent = now
                                logger.info(f"✅ Health warning sent (cooldown: {self.warning_cooldown_seconds}s)")
                            except Exception as e:
                                logger.error(f"❌ Failed to send health warning: {e}")
                        else:
                            time_since_last = (now - self.last_warning_sent).total_seconds()
                            logger.debug(f"⏱️ Health warning skipped (cooldown active, {int(time_since_last)}s since last warning)")
                        
                        # Trigger restart for critical issues
                        critical_issues = [
                            "Stale heartbeat - no activity in 10+ minutes",
                            "Notification system stale - no checks in 5+ minutes"
                        ]
                        
                        for issue in health["issues"]:
                            if issue in critical_issues:
                                self.trigger_restart(f"Critical health issue: {issue}")
                                return
                                
                    time.sleep(30)  # Check every 30 seconds
                    
                except Exception as e:
                    logger.error(f"Health monitor error: {e}")
                    time.sleep(60)  # Wait longer if monitor itself fails
                    
        monitor_thread = threading.Thread(target=monitor_loop, daemon=True)
        monitor_thread.start()
        logger.info("Bot health monitoring started")
        
    def stop_monitoring(self):
        """Stop health monitoring."""
        self.monitoring_enabled = False
        logger.info("Bot health monitoring stopped")

# Global monitor instance
_monitor_instance: Optional[BotHealthMonitor] = None

def get_monitor() -> BotHealthMonitor:
    """Get the global monitor instance."""
    global _monitor_instance
    if _monitor_instance is None:
        _monitor_instance = BotHealthMonitor()
    return _monitor_instance

def initialize_monitor(bot_instance=None):
    """Initialize the global monitor with bot instance."""
    global _monitor_instance
    _monitor_instance = BotHealthMonitor(bot_instance)
    _monitor_instance.start_monitoring()
    return _monitor_instance

def update_heartbeat():
    """Update the global monitor heartbeat."""
    get_monitor().update_heartbeat()

def update_notification_check():
    """Update the global monitor notification check."""
    get_monitor().update_notification_check()

def record_failure(failure_type: str, details: str = ""):
    """Record a failure in the global monitor."""
    get_monitor().record_failure(failure_type, details)

def get_health_status() -> Dict[str, Any]:
    """Get current health status from global monitor."""
    return get_monitor().check_health()