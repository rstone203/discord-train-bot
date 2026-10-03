#!/usr/bin/env python3
"""
Comprehensive Stability Manager for Discord Bot
Provides bulletproof error handling, memory management, and crash prevention.
"""

import asyncio
import gc
import logging
import psutil
import time
import traceback
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Callable, Any
from functools import wraps
import discord
from discord.ext import tasks

class StabilityManager:
    """Comprehensive stability management for Discord bot operations."""
    
    def __init__(self, bot: discord.Client):
        self.bot = bot
        self.logger = logging.getLogger('stability_manager')
        self.error_counts: Dict[str, int] = {}
        self.last_errors: Dict[str, datetime] = {}
        self.memory_threshold = 95  # Percentage - raised for shared Replit machine environment
        self.error_threshold = 5  # Max errors per function in 5 minutes
        self.circuit_breakers: Dict[str, bool] = {}
        self.startup_time = datetime.now()
        self.was_connected = False
        self.health_metrics = {
            'last_heartbeat': datetime.now(),
            'memory_usage': 0,
            'error_count': 0,
            'connection_status': 'unknown'
        }
        
        # Health monitoring disabled - user preference
        # self.health_monitor.start()
        self.memory_cleanup.start()
        
    def robust_error_handler(self, operation_name: str, max_retries: int = 3):
        """Decorator for robust error handling with automatic retries."""
        def decorator(func):
            @wraps(func)
            async def wrapper(*args, **kwargs):
                last_exception = None
                
                for attempt in range(max_retries):
                    try:
                        # Check circuit breaker
                        if self.circuit_breakers.get(operation_name, False):
                            self.logger.warning(f"Circuit breaker open for {operation_name} - skipping")
                            return None
                            
                        # Execute function
                        result = await func(*args, **kwargs) if asyncio.iscoroutinefunction(func) else func(*args, **kwargs)
                        
                        # Reset error count on success
                        if operation_name in self.error_counts:
                            self.error_counts[operation_name] = 0
                            
                        return result
                        
                    except discord.errors.RateLimited as e:
                        wait_time = e.retry_after
                        self.logger.warning(f"Rate limited in {operation_name}, waiting {wait_time}s")
                        await asyncio.sleep(wait_time)
                        last_exception = e
                        
                    except discord.errors.ConnectionClosed as e:
                        self.logger.warning(f"Connection lost in {operation_name}, attempt {attempt + 1}")
                        await asyncio.sleep(2 ** attempt)  # Exponential backoff
                        last_exception = e
                        
                    except discord.errors.HTTPException as e:
                        if e.status == 429:  # Rate limit
                            await asyncio.sleep(1)
                        else:
                            self.logger.error(f"HTTP error in {operation_name}: {e}")
                        last_exception = e
                        
                    except Exception as e:
                        self.logger.error(f"Error in {operation_name} (attempt {attempt + 1}): {e}")
                        self.logger.debug(f"Traceback: {traceback.format_exc()}")
                        last_exception = e
                        
                        # Increment error count
                        current_time = datetime.now()
                        self.error_counts[operation_name] = self.error_counts.get(operation_name, 0) + 1
                        self.last_errors[operation_name] = current_time
                        
                        # Check if we should open circuit breaker
                        if self.error_counts[operation_name] >= self.error_threshold:
                            self.circuit_breakers[operation_name] = True
                            self.logger.error(f"Circuit breaker opened for {operation_name} - too many errors")
                            
                        if attempt < max_retries - 1:
                            await asyncio.sleep(1 * (attempt + 1))  # Progressive delay
                            
                # All retries failed
                self.health_metrics['error_count'] += 1
                self.logger.error(f"Operation {operation_name} failed after {max_retries} attempts")
                
                # Send crash notification if available
                try:
                    from utils.crash_notifications import send_error_notification
                    await send_error_notification(f"Operation {operation_name} failed", str(last_exception))
                except:
                    pass  # Don't crash on notification failure
                    
                return None
                
            return wrapper
        return decorator
    
    # Health monitoring disabled - user preference
    # @tasks.loop(minutes=1)
    # async def health_monitor(self):
    #     """Monitor bot health and system resources."""
    #     pass
    
    @tasks.loop(minutes=15)
    async def memory_cleanup(self):
        """Periodic memory cleanup to prevent memory leaks."""
        try:
            # Force garbage collection
            collected = gc.collect()
            
            if collected > 0:
                self.logger.debug(f"Garbage collected {collected} objects")
                
            # Clear old error tracking
            current_time = datetime.now()
            old_errors = [
                op for op, last_time in self.last_errors.items()
                if current_time - last_time > timedelta(hours=1)
            ]
            
            for operation in old_errors:
                del self.last_errors[operation]
                if operation in self.error_counts:
                    del self.error_counts[operation]
                    
        except Exception as e:
            self.logger.error(f"Memory cleanup error: {e}")
    
    async def cleanup_memory(self):
        """Emergency memory cleanup procedure."""
        try:
            self.logger.info("Performing emergency memory cleanup...")
            
            # Note: Guild cache clearing removed - it causes instability by losing server data
                    
            # Force aggressive garbage collection
            for _ in range(3):
                collected = gc.collect()
                if collected == 0:
                    break
                    
            self.logger.info("Emergency memory cleanup completed")
            
        except Exception as e:
            self.logger.error(f"Emergency cleanup failed: {e}")
    
    def get_health_status(self) -> Dict[str, Any]:
        """Get current health status."""
        return {
            **self.health_metrics,
            'active_circuit_breakers': list(self.circuit_breakers.keys()),
            'error_counts': dict(self.error_counts),
            'memory_threshold': self.memory_threshold
        }
    
    async def graceful_shutdown(self):
        """Gracefully shutdown stability manager."""
        try:
            # self.health_monitor.stop()  # Disabled
            self.memory_cleanup.stop()
            self.logger.info("Stability manager shutdown complete")
        except Exception as e:
            self.logger.error(f"Shutdown error: {e}")

# Global stability manager instance
stability_manager: Optional[StabilityManager] = None

def get_stability_manager(bot: Optional[discord.Client] = None) -> Optional[StabilityManager]:
    """Get or create stability manager instance."""
    global stability_manager
    if stability_manager is None and bot is not None:
        stability_manager = StabilityManager(bot)
    return stability_manager

def stable_operation(operation_name: str, max_retries: int = 3):
    """Decorator for making operations stable."""
    def decorator(func):
        if stability_manager:
            return stability_manager.robust_error_handler(operation_name, max_retries)(func)
        return func
    return decorator

# Enhanced database operation wrapper
def stable_database_operation(func):
    """Wrapper for database operations with automatic retry and error handling."""
    @wraps(func)
    def wrapper(*args, **kwargs):
        max_retries = 3
        last_exception = None
        
        for attempt in range(max_retries):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                last_exception = e
                if "connection" in str(e).lower() or "timeout" in str(e).lower():
                    time.sleep(1 * (attempt + 1))  # Progressive delay for connection issues
                    continue
                elif attempt == max_retries - 1:
                    # Last attempt failed
                    logging.getLogger('stability_manager').error(f"Database operation failed: {e}")
                    raise
                else:
                    time.sleep(0.5)  # Short delay for other errors
        
        # If we get here, all retries failed
        if last_exception:
            raise last_exception
        else:
            raise RuntimeError("Database operation failed with no exception recorded")
    return wrapper