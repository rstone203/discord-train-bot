#!/usr/bin/env python3
"""
One-time script to configure Game Lounge server with restricted features.
Only trains/attendance features will be enabled.
"""

import os
import sys
import logging
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models import Base, ServerFeaturePermissions, get_est_time
from config import BotConfig

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('configure_game_lounge')

def configure_game_lounge():
    """Configure Game Lounge to only allow train/attendance features."""
    
    # Get database URL
    database_url = os.getenv('DATABASE_URL')
    if not database_url:
        logger.error("DATABASE_URL not found in environment variables")
        return False
    
    try:
        # Create engine and session
        engine = create_engine(database_url)
        Session = sessionmaker(bind=engine)
        session = Session()
        
        # Create all tables (if they don't exist)
        Base.metadata.create_all(engine)
        logger.info("✅ Database tables created/verified")
        
        # Game Lounge server ID
        game_lounge_id = 1183084958110191616
        owner_id = BotConfig.OWNER_ID_DISCORD
        
        # Check if permissions already exist
        existing = session.query(ServerFeaturePermissions).filter_by(
            guild_id=game_lounge_id
        ).first()
        
        if existing:
            logger.info(f"Updating existing permissions for Game Lounge ({game_lounge_id})")
            existing.trains_enabled = True
            existing.twitch_enabled = False
            existing.forwarding_enabled = False
            existing.admin_enabled = True  # Keep admin for bot owner
            existing.analytics_enabled = False
            existing.general_enabled = True  # Keep general (ping, help, dashboard)
            existing.configured_by = owner_id
            existing.updated_at = get_est_time()
            existing.notes = "Game Lounge restriction: Only trains/attendance features enabled per admin request"
        else:
            logger.info(f"Creating new permissions for Game Lounge ({game_lounge_id})")
            permissions = ServerFeaturePermissions(
                guild_id=game_lounge_id,
                trains_enabled=True,  # ✅ ENABLED
                twitch_enabled=False,  # ❌ DISABLED
                forwarding_enabled=False,  # ❌ DISABLED
                admin_enabled=True,  # ✅ ENABLED (for owner)
                analytics_enabled=False,  # ❌ DISABLED
                general_enabled=True,  # ✅ ENABLED (ping, help, dashboard)
                configured_by=owner_id,
                notes="Game Lounge restriction: Only trains/attendance features enabled per admin request"
            )
            session.add(permissions)
        
        session.commit()
        
        logger.info("✅ Game Lounge successfully configured!")
        logger.info("   Enabled features:")
        logger.info("     - 🚂 Trains & Attendance")
        logger.info("     - ⚙️  Admin Commands (owner only)")
        logger.info("     - 🌐 General Commands (ping, help, dashboard)")
        logger.info("   Disabled features:")
        logger.info("     - 📺 Twitch Integration")
        logger.info("     - ↪️  Message Forwarding")
        logger.info("     - 📊 Analytics & Google Sheets")
        
        session.close()
        return True
        
    except Exception as e:
        logger.error(f"❌ Error configuring Game Lounge: {e}", exc_info=True)
        return False

if __name__ == '__main__':
    success = configure_game_lounge()
    sys.exit(0 if success else 1)
