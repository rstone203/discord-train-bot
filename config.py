"""
Configuration settings for the Discord bot.
"""

import os
from typing import List

class BotConfig:
    """Bot configuration class."""
    
    # Bot settings
    COMMAND_PREFIX: str = os.getenv('COMMAND_PREFIX', '!')
    BOT_NAME: str = os.getenv('BOT_NAME', 'Game Lounge Train')
    BOT_DESCRIPTION: str = os.getenv('BOT_DESCRIPTION', 'Game Lounge Train - Discord automation for gaming communities')
    
    # Logging settings
    LOG_LEVEL: str = os.getenv('LOG_LEVEL', 'INFO')
    
    # Bot permissions
    ADMIN_ROLES: List[str] = os.getenv('ADMIN_ROLES', '').split(',') if os.getenv('ADMIN_ROLES') else []
    MODERATOR_ROLES: List[str] = os.getenv('MODERATOR_ROLES', '').split(',') if os.getenv('MODERATOR_ROLES') else []
    
    # Feature flags
    ENABLE_MUSIC: bool = os.getenv('ENABLE_MUSIC', 'False').lower() == 'true'
    ENABLE_MODERATION: bool = os.getenv('ENABLE_MODERATION', 'True').lower() == 'true'
    ENABLE_ECONOMY: bool = os.getenv('ENABLE_ECONOMY', 'False').lower() == 'true'
    
    # API Keys (if needed for additional features)
    WEATHER_API_KEY: str = os.getenv('WEATHER_API_KEY', '')
    YOUTUBE_API_KEY: str = os.getenv('YOUTUBE_API_KEY', '')
    
    # Database (if needed)
    DATABASE_URL: str = os.getenv('DATABASE_URL', '')
    
    # Bot owner
    OWNER_ID_DISCORD: int = int(os.getenv('OWNER_ID_DISCORD', '887354716751810560'))
    
    # Message forwarding configuration
    SOURCE_CHANNEL_ID: str = os.getenv('SOURCE_CHANNEL_ID', '1398094211320119358')
    TARGET_CHANNEL_ID: str = os.getenv('TARGET_CHANNEL_ID', '1183143967622168668')
    AUTHORIZED_ROLE_NAME: str = os.getenv('AUTHORIZED_ROLE_NAME', 'supervisor')
    AUTHORIZED_USER_IDS: str = os.getenv('AUTHORIZED_USER_IDS', '887354716751810560')
    
    @classmethod
    def get_required_env_vars(cls) -> List[str]:
        """Get list of required environment variables."""
        return ['DISCORD_BOT_TOKEN']
    
    @classmethod
    def validate_config(cls) -> bool:
        """Validate that all required configuration is present."""
        missing_vars = []
        
        for var in cls.get_required_env_vars():
            if not os.getenv(var):
                missing_vars.append(var)
        
        if missing_vars:
            print(f"Missing required environment variables: {', '.join(missing_vars)}")
            return False
        
        return True
