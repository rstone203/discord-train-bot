"""
Comprehensive update management system for the Discord bot.
Handles bot updates, weekly summaries, and formatting for Discord embeds.
"""

import logging
import discord
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any, Union
from sqlalchemy import and_, or_, desc
import pytz

from database import DatabaseSession
from models import BotUpdate, Guild, get_est_time

logger = logging.getLogger(__name__)

# Valid update categories
VALID_CATEGORIES = [
    'feature',      # New features or functionality
    'bugfix',       # Bug fixes and corrections
    'enhancement',  # Improvements to existing features
    'performance',  # Performance optimizations
    'security',     # Security improvements
    'ui'           # User interface improvements
]

# Category emojis for Discord formatting
CATEGORY_EMOJIS = {
    'feature': '✨',
    'bugfix': '🐛',
    'enhancement': '⚡',
    'performance': '🚀',
    'security': '🔒',
    'ui': '🎨'
}

class UpdateManager:
    """Manages bot updates and weekly summaries."""
    
    def __init__(self):
        """Initialize the UpdateManager."""
        self.logger = logging.getLogger('discord_bot.update_manager')

    # Core Update Functions
    
    async def add_update(
        self,
        title: str,
        description: str,
        category: str,
        guild_id: Optional[int] = None,
        author_id: Optional[int] = None,
        version: Optional[str] = None,
        is_published: bool = True
    ) -> Optional[BotUpdate]:
        """
        Add a new update to the database.
        
        Args:
            title (str): Brief title of the update
            description (str): Detailed description of the change
            category (str): Category of update (feature, bugfix, etc.)
            guild_id (int, optional): Guild this update affects (None for global)
            author_id (int, optional): Discord ID of who made the change
            version (str, optional): Version number if applicable
            is_published (bool): Whether this update should appear publicly
            
        Returns:
            BotUpdate: The created update object, None if failed
        """
        try:
            # Validate inputs
            if not self.validate_category(category):
                self.logger.error(f"Invalid category: {category}")
                return None
            
            title = self.sanitize_update_content(title)
            description = self.sanitize_update_content(description)
            
            if not title or not description:
                self.logger.error("Title and description are required")
                return None
            
            # Create the update
            with DatabaseSession() as session:
                update = BotUpdate(
                    title=title[:100],  # Ensure title length limit
                    description=description,
                    category=category.lower(),
                    guild_id=guild_id,
                    author_id=author_id,
                    version=version,
                    is_published=is_published,
                    is_weekly_posted=False
                )
                
                session.add(update)
                session.commit()  # Commit to get the ID and timestamps
                
                # Get the update ID for logging
                update_id = update.id
                update_title = update.title
                
                self.logger.info(f"Added update: {update_title} (ID: {update_id})")
            
            # Return a fresh copy from database to avoid session issues
            with DatabaseSession() as session:
                fresh_update = session.query(BotUpdate).filter(BotUpdate.id == update_id).first()
                return fresh_update
                
        except Exception as e:
            self.logger.error(f"Failed to add update: {e}")
            return None

    async def get_recent_updates(
        self,
        days: int = 7,
        guild_id: Optional[int] = None,
        published_only: bool = True
    ) -> List[BotUpdate]:
        """
        Get updates from the last X days.
        
        Args:
            days (int): Number of days to look back
            guild_id (int, optional): Filter by guild ID (None for global updates)
            published_only (bool): Only return published updates
            
        Returns:
            List[BotUpdate]: List of recent updates
        """
        try:
            cutoff_date = get_est_time() - timedelta(days=days)
            
            with DatabaseSession() as session:
                query = session.query(BotUpdate).filter(
                    BotUpdate.created_at >= cutoff_date
                )
                
                if guild_id is not None:
                    query = query.filter(
                        or_(BotUpdate.guild_id == guild_id, BotUpdate.guild_id.is_(None))
                    )
                
                if published_only:
                    query = query.filter(BotUpdate.is_published == True)
                
                updates = query.order_by(desc(BotUpdate.created_at)).all()
                
                # Force-load all attributes while session is active to prevent detached object errors
                for update in updates:
                    # Access all attributes to ensure they're loaded
                    _ = update.id
                    _ = update.title
                    _ = update.description
                    _ = update.category
                    _ = update.version
                    _ = update.author_id
                    _ = update.guild_id
                    _ = update.is_published
                    _ = update.is_weekly_posted
                    _ = update.created_at
                    _ = update.updated_at
                
                self.logger.info(f"Retrieved {len(updates)} recent updates for last {days} days")
                return updates
                
        except Exception as e:
            self.logger.error(f"Failed to get recent updates: {e}")
            return []

    async def get_updates_by_category(
        self,
        category: str,
        limit: int = 10,
        published_only: bool = True
    ) -> List[BotUpdate]:
        """
        Get updates filtered by category.
        
        Args:
            category (str): Category to filter by
            limit (int): Maximum number of updates to return
            published_only (bool): Only return published updates
            
        Returns:
            List[BotUpdate]: List of updates in the category
        """
        try:
            if not self.validate_category(category):
                self.logger.error(f"Invalid category: {category}")
                return []
            
            with DatabaseSession() as session:
                query = session.query(BotUpdate).filter(
                    BotUpdate.category == category.lower()
                )
                
                if published_only:
                    query = query.filter(BotUpdate.is_published == True)
                
                updates = query.order_by(desc(BotUpdate.created_at)).limit(limit).all()
                
                self.logger.info(f"Retrieved {len(updates)} updates for category: {category}")
                return updates
                
        except Exception as e:
            self.logger.error(f"Failed to get updates by category: {e}")
            return []

    async def mark_updates_as_weekly_posted(self, update_ids: List[int]) -> bool:
        """
        Mark updates as included in weekly summary.
        
        Args:
            update_ids (List[int]): List of update IDs to mark
            
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            with DatabaseSession() as session:
                session.query(BotUpdate).filter(
                    BotUpdate.id.in_(update_ids)
                ).update(
                    {BotUpdate.is_weekly_posted: True, BotUpdate.updated_at: get_est_time()},
                    synchronize_session=False
                )
                session.commit()  # CRITICAL: Commit changes to database
                
                self.logger.info(f"Marked {len(update_ids)} updates as weekly posted")
                return True
                
        except Exception as e:
            self.logger.error(f"Failed to mark updates as weekly posted: {e}")
            return False

    async def get_unpublished_updates(self) -> List[BotUpdate]:
        """
        Get all unpublished updates for admin review.
        
        Returns:
            List[BotUpdate]: List of unpublished updates
        """
        try:
            with DatabaseSession() as session:
                updates = session.query(BotUpdate).filter(
                    BotUpdate.is_published == False
                ).order_by(desc(BotUpdate.created_at)).all()
                
                self.logger.info(f"Retrieved {len(updates)} unpublished updates")
                return updates
                
        except Exception as e:
            self.logger.error(f"Failed to get unpublished updates: {e}")
            return []

    # Weekly Summary Functions
    
    async def get_weekly_summary(self, guild_id: Optional[int] = None) -> Dict[str, Any]:
        """
        Generate formatted weekly summary of new updates.
        
        Args:
            guild_id (int, optional): Filter by guild ID (None for global)
            
        Returns:
            Dict[str, Any]: Weekly summary data including updates and statistics
        """
        try:
            # Get updates from last 7 days
            updates = await self.get_recent_updates(days=7, guild_id=guild_id)
            
            if not updates:
                return {
                    'updates': [],
                    'total_count': 0,
                    'by_category': {},
                    'has_new_updates': False
                }
            
            # Categorize updates
            categorized = await self.categorize_updates(updates)
            
            # Calculate statistics
            stats = {
                'total_count': len(updates),
                'by_category': {cat: len(upds) for cat, upds in categorized.items()},
                'has_new_updates': len(updates) > 0
            }
            
            summary = {
                'updates': updates,
                'categorized': categorized,
                'statistics': stats,
                'has_new_updates': stats['has_new_updates']
            }
            
            self.logger.info(f"Generated weekly summary with {len(updates)} updates")
            return summary
            
        except Exception as e:
            self.logger.error(f"Failed to generate weekly summary: {e}")
            return {'updates': [], 'total_count': 0, 'by_category': {}, 'has_new_updates': False}

    async def get_updates_for_weekly_post(self, guild_id: Optional[int] = None) -> List[BotUpdate]:
        """
        Get all updates that need to be included in next weekly post.
        
        Args:
            guild_id (int, optional): Filter by guild ID (None for global updates)
            
        Returns:
            List[BotUpdate]: List of updates not yet included in weekly posts
        """
        try:
            cutoff_date = get_est_time() - timedelta(days=7)
            
            with DatabaseSession() as session:
                query = session.query(BotUpdate).filter(
                    and_(
                        BotUpdate.is_published == True,
                        BotUpdate.is_weekly_posted == False,
                        BotUpdate.created_at >= cutoff_date
                    )
                )
                
                # Add guild filtering to prevent cross-guild leakage
                if guild_id is not None:
                    query = query.filter(
                        or_(BotUpdate.guild_id == guild_id, BotUpdate.guild_id.is_(None))
                    )
                
                updates = query.order_by(desc(BotUpdate.created_at)).all()
                
                self.logger.info(f"Retrieved {len(updates)} updates for weekly post (guild_id: {guild_id})")
                return updates
                
        except Exception as e:
            self.logger.error(f"Failed to get updates for weekly post: {e}")
            return []

    async def has_new_updates_this_week(self, guild_id: Optional[int] = None) -> bool:
        """
        Check if there are new updates to post this week.
        
        Args:
            guild_id (int, optional): Filter by guild ID (None for global updates)
        
        Returns:
            bool: True if there are new updates, False otherwise
        """
        try:
            updates = await self.get_updates_for_weekly_post(guild_id=guild_id)
            return len(updates) > 0
            
        except Exception as e:
            self.logger.error(f"Failed to check for new updates: {e}")
            return False

    # Formatting Functions
    
    async def format_update_for_discord(self, update: BotUpdate) -> discord.Embed:
        """
        Format single update for Discord embed.
        
        Args:
            update (BotUpdate): The update to format
            
        Returns:
            discord.Embed: Formatted embed for Discord
        """
        try:
            # Safely extract values from BotUpdate object
            category_str = getattr(update, 'category', 'unknown')
            title_str = getattr(update, 'title', 'Untitled Update')
            description_str = getattr(update, 'description', 'No description available')
            created_at_dt = getattr(update, 'created_at', None)
            
            emoji = CATEGORY_EMOJIS.get(category_str, '📝')
            color = self._get_category_color(category_str)
            
            embed = discord.Embed(
                title=f"{emoji} {title_str}",
                description=description_str,
                color=color,
                timestamp=self._convert_est_to_utc_aware(created_at_dt)
            )
            
            # Add fields
            embed.add_field(
                name="Category",
                value=str(category_str).title(),
                inline=True
            )
            
            version_str = getattr(update, 'version', None)
            if version_str:
                embed.add_field(
                    name="Version",
                    value=str(version_str),
                    inline=True
                )
            
            guild_id_val = getattr(update, 'guild_id', None)
            if guild_id_val:
                embed.add_field(
                    name="Scope",
                    value="Server-specific",
                    inline=True
                )
            else:
                embed.add_field(
                    name="Scope",
                    value="Global",
                    inline=True
                )
            
            embed.set_footer(text=f"Update ID: {update.id}")
            
            return embed
            
        except Exception as e:
            self.logger.error(f"Failed to format update for Discord: {e}")
            # Return a basic embed on error
            return discord.Embed(
                title="Update Formatting Error",
                description="Failed to format update",
                color=0xff0000
            )

    async def format_weekly_summary_embed(self, updates: List[BotUpdate]) -> discord.Embed:
        """
        Create Discord embed for weekly summary.
        
        Args:
            updates (List[BotUpdate]): List of updates to include in summary
            
        Returns:
            discord.Embed: Formatted weekly summary embed
        """
        try:
            if not updates:
                return discord.Embed(
                    title="📊 Weekly Update Summary",
                    description="No new updates this week.",
                    color=0x9146ff,
                    timestamp=self._convert_est_to_utc_aware(get_est_time())
                )
            
            # Categorize updates
            categorized = await self.categorize_updates(updates)
            
            embed = discord.Embed(
                title="📊 Weekly Update Summary",
                description=f"Here are the {len(updates)} updates from this week:",
                color=0x9146ff,
                timestamp=self._convert_est_to_utc_aware(get_est_time())
            )
            
            # Add field for each category with updates
            for category, cat_updates in categorized.items():
                if not cat_updates:
                    continue
                    
                emoji = CATEGORY_EMOJIS.get(category, '📝')
                update_list = []
                
                for update in cat_updates[:5]:  # Limit to 5 per category
                    title_str = getattr(update, 'title', 'Untitled')
                    version_str = getattr(update, 'version', None)
                    version_info = f" (v{version_str})" if version_str else ""
                    update_list.append(f"• {title_str}{version_info}")
                
                if len(cat_updates) > 5:
                    update_list.append(f"• ... and {len(cat_updates) - 5} more")
                
                embed.add_field(
                    name=f"{emoji} {category.title()} ({len(cat_updates)})",
                    value="\n".join(update_list),
                    inline=False
                )
            
            # Add statistics
            stats_text = f"Total Updates: {len(updates)}"
            embed.add_field(
                name="📈 Statistics",
                value=stats_text,
                inline=False
            )
            
            embed.set_footer(text="Game Lounge Train Bot • Weekly Summary")
            
            return embed
            
        except Exception as e:
            self.logger.error(f"Failed to format weekly summary embed: {e}")
            return discord.Embed(
                title="Weekly Summary Error",
                description="Failed to generate weekly summary",
                color=0xff0000
            )

    async def categorize_updates(self, updates: List[BotUpdate]) -> Dict[str, List[BotUpdate]]:
        """
        Group updates by category for organized display.
        
        Args:
            updates (List[BotUpdate]): List of updates to categorize
            
        Returns:
            Dict[str, List[BotUpdate]]: Updates grouped by category
        """
        try:
            categorized = {category: [] for category in VALID_CATEGORIES}
            
            # Use a fresh session to avoid detached object issues
            with DatabaseSession() as session:
                update_ids = [update.id for update in updates]
                fresh_updates = session.query(BotUpdate).filter(BotUpdate.id.in_(update_ids)).all()
                
                for update in fresh_updates:
                    category = update.category
                    if category in categorized:
                        categorized[category].append(update)
                    else:
                        # Handle unknown categories
                        if 'other' not in categorized:
                            categorized['other'] = []
                        categorized['other'].append(update)
            
            # Remove empty categories
            categorized = {k: v for k, v in categorized.items() if v}
            
            self.logger.info(f"Categorized {len(updates)} updates into {len(categorized)} categories")
            return categorized
            
        except Exception as e:
            self.logger.error(f"Failed to categorize updates: {e}")
            return {}

    # Helper Functions
    
    def validate_category(self, category: str) -> bool:
        """
        Ensure category is valid.
        
        Args:
            category (str): Category to validate
            
        Returns:
            bool: True if valid, False otherwise
        """
        return category.lower() in VALID_CATEGORIES

    def _convert_est_to_utc_aware(self, est_naive_dt: Optional[datetime]) -> Optional[datetime]:
        """
        Convert EST naive datetime to UTC-aware datetime for Discord embeds.
        
        Args:
            est_naive_dt (datetime): EST naive datetime from database
            
        Returns:
            datetime: UTC-aware datetime for Discord
        """
        try:
            if est_naive_dt is None:
                return datetime.now(pytz.UTC)
            
            # Create EST timezone
            est_tz = pytz.timezone('Europe/London')
            
            # Localize the naive datetime to EST
            est_aware = est_tz.localize(est_naive_dt)
            
            # Convert to UTC for Discord
            utc_aware = est_aware.astimezone(pytz.UTC)
            
            return utc_aware
            
        except Exception as e:
            self.logger.error(f"Failed to convert timezone: {e}")
            # Return current UTC time as fallback
            return datetime.now(pytz.UTC)

    def sanitize_update_content(self, text: str) -> str:
        """
        Clean and validate update text.
        
        Args:
            text (str): Text to sanitize
            
        Returns:
            str: Sanitized text
        """
        if not text:
            return ""
        
        # Strip whitespace and limit length
        text = text.strip()
        
        # Remove any potentially harmful characters (basic sanitization)
        # For Discord, we mainly need to escape certain markdown characters
        text = text.replace('@everyone', '@\u200beveryone')
        text = text.replace('@here', '@\u200bhere')
        
        return text

    async def get_update_statistics(self) -> Dict[str, Any]:
        """
        Get count of updates by category and timeframe.
        
        Returns:
            Dict[str, Any]: Statistics about updates
        """
        try:
            with DatabaseSession() as session:
                # Total updates
                total_updates = session.query(BotUpdate).count()
                
                # Updates by category
                by_category = {}
                for category in VALID_CATEGORIES:
                    count = session.query(BotUpdate).filter(
                        BotUpdate.category == category
                    ).count()
                    by_category[category] = count
                
                # Recent updates (last 30 days)
                thirty_days_ago = get_est_time() - timedelta(days=30)
                recent_updates = session.query(BotUpdate).filter(
                    BotUpdate.created_at >= thirty_days_ago
                ).count()
                
                # Published vs unpublished
                published = session.query(BotUpdate).filter(
                    BotUpdate.is_published == True
                ).count()
                unpublished = total_updates - published
                
                stats = {
                    'total_updates': total_updates,
                    'by_category': by_category,
                    'recent_updates_30_days': recent_updates,
                    'published': published,
                    'unpublished': unpublished,
                    'generated_at': get_est_time()
                }
                
                self.logger.info("Generated update statistics")
                return stats
                
        except Exception as e:
            self.logger.error(f"Failed to get update statistics: {e}")
            return {}

    def _get_category_color(self, category: str) -> int:
        """
        Get color for a category embed.
        
        Args:
            category (str): Category name
            
        Returns:
            int: Color hex value
        """
        colors = {
            'feature': 0x00ff00,     # Green
            'bugfix': 0xff0000,      # Red
            'enhancement': 0x0099ff, # Blue
            'performance': 0xff9900, # Orange
            'security': 0xff0099,    # Pink
            'ui': 0x9900ff          # Purple
        }
        return colors.get(category, 0x9146ff)  # Default purple


# Create global instance
update_manager = UpdateManager()

# Convenience functions for easy importing
async def add_update(title: str, description: str, category: str, **kwargs) -> Optional[BotUpdate]:
    """Convenience function for adding updates."""
    return await update_manager.add_update(title, description, category, **kwargs)

async def get_recent_updates(days: int = 7, **kwargs) -> List[BotUpdate]:
    """Convenience function for getting recent updates."""
    return await update_manager.get_recent_updates(days, **kwargs)

async def get_weekly_summary(**kwargs) -> Dict[str, Any]:
    """Convenience function for getting weekly summary."""
    return await update_manager.get_weekly_summary(**kwargs)

async def format_weekly_summary_embed(updates: List[BotUpdate]) -> discord.Embed:
    """Convenience function for formatting weekly summary."""
    return await update_manager.format_weekly_summary_embed(updates)