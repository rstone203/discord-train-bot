"""
Background task for periodic Twitch username synchronization.
"""
import asyncio
import logging
from datetime import datetime, timedelta
from utils.twitch_sync import TwitchLinkSyncService
from database import DatabaseSession

logger = logging.getLogger('discord_bot.twitch_sync_task')

class TwitchSyncTask:
    """Background task for periodic Twitch data reconciliation."""
    
    def __init__(self, bot):
        self.bot = bot
        self.task = None
        self.interval_hours = 6  # Run every 6 hours
        self.running = False
    
    def start(self):
        """Start the background sync task."""
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self._sync_loop())
            self.running = True
            logger.info(f"Twitch sync task started (interval: {self.interval_hours} hours)")
    
    def stop(self):
        """Stop the background sync task."""
        self.running = False
        if self.task and not self.task.done():
            self.task.cancel()
            logger.info("Twitch sync task stopped")
    
    async def _sync_loop(self):
        """Main sync loop that runs periodically."""
        while self.running:
            try:
                await self._perform_reconciliation()
                # Wait for the specified interval
                await asyncio.sleep(self.interval_hours * 3600)  # Convert hours to seconds
                
            except asyncio.CancelledError:
                logger.info("Twitch sync task cancelled")
                break
            except Exception as e:
                logger.error(f"Error in Twitch sync task: {e}")
                # Wait 30 minutes before retrying on error
                await asyncio.sleep(1800)
    
    async def _perform_reconciliation(self):
        """Perform reconciliation for all guilds."""
        try:
            total_users = 0
            total_participants = 0
            guilds_processed = 0
            
            # Get all guild IDs the bot is connected to
            guild_ids = [guild.id for guild in self.bot.guilds]
            
            with DatabaseSession() as session:
                for guild_id in guild_ids:
                    try:
                        users_processed, participants_updated = TwitchLinkSyncService.sync_all_guild_users(
                            session, guild_id
                        )
                        total_users += users_processed
                        total_participants += participants_updated
                        guilds_processed += 1
                        
                        # Also check for inconsistencies
                        inconsistencies = TwitchLinkSyncService.find_sync_inconsistencies(
                            session, guild_id
                        )
                        if inconsistencies:
                            logger.warning(f"Found {len(inconsistencies)} sync inconsistencies "
                                         f"in guild {guild_id} - they should be resolved now")
                        
                    except Exception as e:
                        logger.error(f"Error syncing guild {guild_id}: {e}")
                        continue
                
                session.commit()
            
            if total_users > 0 or total_participants > 0:
                logger.info(f"Twitch sync reconciliation complete - processed {guilds_processed} guilds, "
                           f"{total_users} users, updated {total_participants} participant records")
            else:
                logger.debug("Twitch sync reconciliation complete - no updates needed")
                
        except Exception as e:
            logger.error(f"Error in Twitch sync reconciliation: {e}")
    
    async def force_sync_guild(self, guild_id: int) -> tuple:
        """Manually trigger sync for a specific guild."""
        try:
            with DatabaseSession() as session:
                result = TwitchLinkSyncService.sync_all_guild_users(session, guild_id)
                session.commit()
                logger.info(f"Manual guild sync for {guild_id} - processed {result[0]} users, "
                           f"updated {result[1]} participants")
                return result
        except Exception as e:
            logger.error(f"Error in manual guild sync for {guild_id}: {e}")
            return 0, 0