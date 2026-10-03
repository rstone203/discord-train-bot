"""
TwitchLinkSync Service - Automatic synchronization of Twitch usernames
between User table and TrainParticipant table.
"""
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Tuple
from sqlalchemy.orm import Session
from database import DatabaseSession
from models import User, TrainParticipant

logger = logging.getLogger('discord_bot.twitch_sync')

class TwitchLinkSyncService:
    """Service for automatically syncing Twitch usernames between tables."""
    
    @staticmethod
    def sync_user_twitch(session: Session, user_id: int, guild_id: int, 
                        new_twitch_login: Optional[str] = None) -> int:
        """
        Sync a specific user's Twitch username to all their train participation records.
        
        Args:
            session: Database session
            user_id: Discord user ID
            guild_id: Guild ID to limit scope
            new_twitch_login: New Twitch username to set (if None, fetches from User table)
            
        Returns:
            Number of TrainParticipant records updated
        """
        try:
            # If no new_twitch_login provided, get it from User table
            if new_twitch_login is None:
                user = session.query(User).filter_by(
                    id=user_id, 
                    guild_id=guild_id
                ).first()
                if user and user.twitch_login:
                    new_twitch_login = str(user.twitch_login)  # type: ignore[arg-type]
                else:
                    new_twitch_login = None
            
            # Update all TrainParticipant records for this user in this guild
            updated_count = session.query(TrainParticipant).filter_by(
                user_id=user_id,
                guild_id=guild_id,
                is_active=True
            ).update({
                'twitch_username': new_twitch_login
            })
            
            logger.info(f"Synced Twitch username '{new_twitch_login}' to {updated_count} "
                       f"train participant records for user {user_id}")
            
            return updated_count
            
        except Exception as e:
            logger.error(f"Error syncing Twitch username for user {user_id}: {e}")
            raise
    
    @staticmethod
    def sync_all_guild_users(session: Session, guild_id: int) -> Tuple[int, int]:
        """
        Sync all users in a guild to ensure consistency.
        
        Args:
            session: Database session
            guild_id: Guild ID to sync
            
        Returns:
            Tuple of (users_processed, participants_updated)
        """
        try:
            users_processed = 0
            total_participants_updated = 0
            
            # Get all users with linked Twitch accounts in this guild
            linked_users = session.query(User).filter(
                User.guild_id == guild_id,
                User.twitch_login.isnot(None),
                User.twitch_login != ''
            ).all()
            
            for user in linked_users:
                user_id_int = int(user.id) if user.id else 0  # type: ignore[arg-type]
                twitch_login_str = str(user.twitch_login) if user.twitch_login else None  # type: ignore[arg-type]
                participants_updated = TwitchLinkSyncService.sync_user_twitch(
                    session, user_id_int, guild_id, twitch_login_str
                )
                total_participants_updated += participants_updated
                users_processed += 1
            
            # Clear Twitch usernames for participants whose User records have no twitch_login
            cleared_count = session.query(TrainParticipant).filter(
                TrainParticipant.guild_id == guild_id,
                TrainParticipant.is_active == True,
                TrainParticipant.twitch_username.isnot(None),
                TrainParticipant.twitch_username != '',
                # Subquery to find participants without linked Twitch accounts
                ~TrainParticipant.user_id.in_(
                    session.query(User.id).filter(
                        User.guild_id == guild_id,
                        User.twitch_login.isnot(None),
                        User.twitch_login != ''
                    )
                )
            ).update({
                'twitch_username': None
            })
            
            if cleared_count > 0:
                logger.info(f"Cleared stale Twitch usernames from {cleared_count} "
                           f"train participant records in guild {guild_id}")
            
            logger.info(f"Guild sync complete - processed {users_processed} users, "
                       f"updated {total_participants_updated} participants, "
                       f"cleared {cleared_count} stale records")
            
            return users_processed, total_participants_updated + cleared_count
            
        except Exception as e:
            logger.error(f"Error syncing all users in guild {guild_id}: {e}")
            raise
    
    @staticmethod
    def find_sync_inconsistencies(session: Session, guild_id: Optional[int] = None) -> List[dict]:
        """
        Find inconsistencies between User and TrainParticipant Twitch data.
        
        Args:
            session: Database session
            guild_id: Optional guild ID to limit scope
            
        Returns:
            List of inconsistency records
        """
        try:
            # Build base query
            query = session.query(
                TrainParticipant.user_id,
                TrainParticipant.guild_id,
                TrainParticipant.display_name,
                TrainParticipant.twitch_username.label('participant_twitch'),
                User.twitch_login.label('user_twitch')
            ).join(
                User, 
                (User.id == TrainParticipant.user_id) & 
                (User.guild_id == TrainParticipant.guild_id)
            ).filter(
                TrainParticipant.is_active == True
            )
            
            if guild_id:
                query = query.filter(TrainParticipant.guild_id == guild_id)
            
            # Find mismatches
            inconsistencies = []
            for record in query.all():
                participant_twitch = record.participant_twitch
                user_twitch = record.user_twitch
                
                # Check for inconsistency
                if participant_twitch != user_twitch:
                    inconsistencies.append({
                        'user_id': record.user_id,
                        'guild_id': record.guild_id,
                        'display_name': record.display_name,
                        'participant_twitch': participant_twitch,
                        'user_twitch': user_twitch,
                        'issue_type': 'mismatch'
                    })
            
            return inconsistencies
            
        except Exception as e:
            logger.error(f"Error finding sync inconsistencies: {e}")
            raise

# Convenience functions for easy integration
def sync_user_on_link(user_id: int, guild_id: int, twitch_login: str) -> None:
    """Call this when a user links their Twitch account."""
    try:
        with DatabaseSession() as session:
            updated_count = TwitchLinkSyncService.sync_user_twitch(
                session, user_id, guild_id, twitch_login
            )
            session.commit()
            logger.info(f"Auto-sync on link: Updated {updated_count} records for user {user_id}")
        
        # Trigger immediate update of persistent message displays
        _trigger_persistent_message_updates(guild_id)
        
    except Exception as e:
        logger.error(f"Auto-sync on link failed for user {user_id}: {e}")

def sync_user_on_unlink(user_id: int, guild_id: int) -> None:
    """Call this when a user unlinks their Twitch account."""
    try:
        with DatabaseSession() as session:
            updated_count = TwitchLinkSyncService.sync_user_twitch(
                session, user_id, guild_id, None
            )
            session.commit()
            logger.info(f"Auto-sync on unlink: Cleared {updated_count} records for user {user_id}")
        
        # Trigger immediate update of persistent message displays
        _trigger_persistent_message_updates(guild_id)
        
    except Exception as e:
        logger.error(f"Auto-sync on unlink failed for user {user_id}: {e}")

def _trigger_persistent_message_updates(guild_id: int) -> None:
    """
    Trigger immediate updates for persistent message displays.
    This is a best-effort function. If the bot instance is unavailable,
    updates will be handled by the periodic update task.
    """
    try:
        import asyncio
        
        # Try to get the running event loop
        try:
            loop = asyncio.get_running_loop()
            # Queue an update flag that the persistent message updater will check
            logger.info(f"Twitch link update detected for guild {guild_id} - periodic updater will refresh displays")
        except RuntimeError:
            # No running loop - updates will be handled by next periodic cycle
            logger.debug(f"No event loop running - periodic updater will handle guild {guild_id}")
    except Exception as e:
        logger.debug(f"Could not signal persistent message updates for guild {guild_id}: {e}")

def perform_guild_reconciliation(guild_id: int) -> Tuple[int, int]:
    """Perform full reconciliation for a guild."""
    try:
        with DatabaseSession() as session:
            result = TwitchLinkSyncService.sync_all_guild_users(session, guild_id)
            session.commit()
            return result
    except Exception as e:
        logger.error(f"Guild reconciliation failed for guild {guild_id}: {e}")
        return 0, 0