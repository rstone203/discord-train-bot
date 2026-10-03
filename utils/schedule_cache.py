"""
Schedule caching system for improved performance.
Reduces database queries for frequently accessed schedule data.
"""

import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
import logging

logger = logging.getLogger('schedule_cache')


@dataclass
class CacheEntry:
    """Represents a cached item with expiration."""
    data: Any
    expires_at: datetime
    
    def is_expired(self) -> bool:
        """Check if this cache entry has expired."""
        return datetime.utcnow() >= self.expires_at


class ScheduleCache:
    """
    In-memory cache for schedule data with TTL expiration.
    Reduces database load for frequently accessed schedule information.
    """
    
    def __init__(self, default_ttl_seconds: int = 300):
        """
        Initialize the schedule cache.
        
        Args:
            default_ttl_seconds: Default time-to-live for cache entries (default: 5 minutes)
        """
        self.cache: Dict[str, CacheEntry] = {}
        self.default_ttl = default_ttl_seconds
        self.hits = 0
        self.misses = 0
        self._cleanup_task: Optional[asyncio.Task] = None
        logger.info(f"Schedule cache initialized with {default_ttl_seconds}s TTL")
    
    def _make_key(self, guild_id: int, key_type: str, *args) -> str:
        """Generate a cache key from guild_id and parameters."""
        return f"{guild_id}:{key_type}:{':'.join(map(str, args))}"
    
    def get(self, guild_id: int, key_type: str, *args) -> Optional[Any]:
        """
        Get a value from the cache.
        
        Args:
            guild_id: Discord guild ID
            key_type: Type of cached data (e.g., 'schedules', 'roster', 'timeslots')
            *args: Additional key components
            
        Returns:
            Cached data if found and not expired, None otherwise
        """
        key = self._make_key(guild_id, key_type, *args)
        
        if key not in self.cache:
            self.misses += 1
            return None
        
        entry = self.cache[key]
        
        if entry.is_expired():
            del self.cache[key]
            self.misses += 1
            logger.debug(f"Cache expired: {key}")
            return None
        
        self.hits += 1
        logger.debug(f"Cache hit: {key}")
        return entry.data
    
    def set(self, guild_id: int, key_type: str, *args, data: Any, ttl: Optional[int] = None):
        """
        Store a value in the cache.
        
        Args:
            guild_id: Discord guild ID
            key_type: Type of cached data
            *args: Additional key components
            data: Data to cache
            ttl: Time-to-live in seconds (uses default if not specified)
        """
        key = self._make_key(guild_id, key_type, *args)
        ttl_seconds = ttl if ttl is not None else self.default_ttl
        expires_at = datetime.utcnow() + timedelta(seconds=ttl_seconds)
        
        self.cache[key] = CacheEntry(data=data, expires_at=expires_at)
        logger.debug(f"Cache set: {key} (expires in {ttl_seconds}s)")
    
    def invalidate(self, guild_id: int, key_type: str, *args):
        """
        Invalidate a specific cache entry.
        
        Args:
            guild_id: Discord guild ID
            key_type: Type of cached data
            *args: Additional key components
        """
        key = self._make_key(guild_id, key_type, *args)
        if key in self.cache:
            del self.cache[key]
            logger.debug(f"Cache invalidated: {key}")
    
    def invalidate_guild(self, guild_id: int):
        """
        Invalidate all cache entries for a specific guild.
        
        Args:
            guild_id: Discord guild ID
        """
        prefix = f"{guild_id}:"
        keys_to_delete = [k for k in self.cache.keys() if k.startswith(prefix)]
        
        for key in keys_to_delete:
            del self.cache[key]
        
        logger.info(f"Invalidated {len(keys_to_delete)} cache entries for guild {guild_id}")
    
    def invalidate_all(self):
        """Clear the entire cache."""
        count = len(self.cache)
        self.cache.clear()
        logger.info(f"Cleared entire cache ({count} entries)")
    
    def cleanup_expired(self):
        """Remove all expired entries from the cache."""
        now = datetime.utcnow()
        expired_keys = [
            key for key, entry in self.cache.items()
            if entry.expires_at <= now
        ]
        
        for key in expired_keys:
            del self.cache[key]
        
        if expired_keys:
            logger.debug(f"Cleaned up {len(expired_keys)} expired cache entries")
    
    async def start_cleanup_task(self, interval_seconds: int = 300):
        """
        Start a background task to periodically clean up expired entries.
        
        Args:
            interval_seconds: How often to run cleanup (default: 5 minutes)
        """
        if self._cleanup_task and not self._cleanup_task.done():
            logger.warning("Cleanup task already running")
            return
        
        async def cleanup_loop():
            logger.info(f"Cache cleanup task started (interval: {interval_seconds}s)")
            while True:
                try:
                    await asyncio.sleep(interval_seconds)
                    self.cleanup_expired()
                except asyncio.CancelledError:
                    logger.info("Cache cleanup task cancelled")
                    break
                except Exception as e:
                    logger.error(f"Error in cache cleanup task: {e}")
        
        self._cleanup_task = asyncio.create_task(cleanup_loop())
    
    def stop_cleanup_task(self):
        """Stop the background cleanup task."""
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            logger.info("Cache cleanup task stopped")
    
    def get_stats(self) -> Dict[str, Any]:
        """
        Get cache statistics.
        
        Returns:
            Dictionary with cache stats (entries, hits, misses, hit rate)
        """
        total_requests = self.hits + self.misses
        hit_rate = (self.hits / total_requests * 100) if total_requests > 0 else 0
        
        return {
            'entries': len(self.cache),
            'hits': self.hits,
            'misses': self.misses,
            'hit_rate': f"{hit_rate:.1f}%",
            'total_requests': total_requests
        }


# Global cache instance
_schedule_cache: Optional[ScheduleCache] = None


def get_cache() -> ScheduleCache:
    """Get the global schedule cache instance."""
    global _schedule_cache
    if _schedule_cache is None:
        _schedule_cache = ScheduleCache()
    return _schedule_cache


def invalidate_schedule_cache(guild_id: int):
    """
    Invalidate all schedule-related cache for a guild.
    Call this when schedules are created, modified, or deleted.
    """
    get_cache().invalidate_guild(guild_id)
