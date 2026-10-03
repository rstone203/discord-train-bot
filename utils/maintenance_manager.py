"""
Maintenance Mode Manager
Handles bot maintenance state with database persistence and environment variable fallback.
"""

import os
import logging
from typing import Optional
from database import get_db_session
from models import SystemSettings

logger = logging.getLogger('discord_bot.maintenance_manager')

class MaintenanceManager:
    """Manages maintenance mode state for the Discord bot."""
    
    def __init__(self):
        self._cache: Optional[bool] = None
        self.setting_key = 'maintenance_mode'
    
    async def load(self) -> bool:
        """
        Load maintenance mode state from database or environment variable.
        Priority: Database > Environment Variable > Default (False)
        """
        try:
            session = get_db_session()
            try:
                setting = session.query(SystemSettings).filter_by(
                    setting_key=self.setting_key
                ).first()
                
                if setting:
                    self._cache = bool(setting.is_enabled)
                    logger.info(f"Loaded maintenance mode from database: {self._cache}")
                    return self._cache
            finally:
                session.close()
        except Exception as db_error:
            logger.warning(f"Could not load from database: {db_error}")
        
        # Fallback to environment variable
        env_value = os.getenv('MAINTENANCE_MODE', 'false').lower()
        self._cache = env_value in ('true', '1', 'yes', 'on')
        logger.info(f"Loaded maintenance mode from environment: {self._cache}")
        
        # Try to persist to database for future use
        try:
            await self._persist_to_db(self._cache)
        except Exception as persist_error:
            logger.warning(f"Could not persist to database: {persist_error}")
        
        return self._cache
    
    async def get(self) -> bool:
        """Get current maintenance mode state."""
        if self._cache is None:
            return await self.load()
        return self._cache
    
    async def set(self, enabled: bool) -> bool:
        """
        Set maintenance mode state and persist to database.
        Returns True if successful.
        """
        try:
            self._cache = enabled
            
            # Persist to database
            await self._persist_to_db(enabled)
            
            logger.info(f"Maintenance mode {'enabled' if enabled else 'disabled'}")
            return True
        except Exception as e:
            logger.error(f"Failed to set maintenance mode: {e}")
            return False
    
    async def _persist_to_db(self, enabled: bool):
        """Persist maintenance mode state to database."""
        session = get_db_session()
        try:
            setting = session.query(SystemSettings).filter_by(
                setting_key=self.setting_key
            ).first()
            
            value_str = 'true' if enabled else 'false'
            
            if setting:
                setting.setting_value = value_str
                setting.is_enabled = enabled
            else:
                setting = SystemSettings(
                    setting_key=self.setting_key,
                    setting_value=value_str,
                    is_enabled=enabled,
                    description='Bot maintenance mode flag'
                )
                session.add(setting)
            
            session.commit()
            logger.debug(f"Persisted maintenance mode to database: {value_str}")
        except Exception as e:
            session.rollback()
            logger.error(f"Database persistence error: {e}")
            raise
        finally:
            session.close()

# Global instance
maintenance_manager = MaintenanceManager()
