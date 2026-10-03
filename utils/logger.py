"""
Logging configuration for the Discord bot.
"""

import logging
import sys
from datetime import datetime
import os

class ColoredFormatter(logging.Formatter):
    """Custom formatter with colors for different log levels."""
    
    # ANSI color codes
    COLORS = {
        'DEBUG': '\033[36m',      # Cyan
        'INFO': '\033[32m',       # Green
        'WARNING': '\033[33m',    # Yellow
        'ERROR': '\033[31m',      # Red
        'CRITICAL': '\033[35m',   # Magenta
    }
    RESET = '\033[0m'
    
    def format(self, record):
        """Format the log record with colors."""
        # Add color if terminal supports it
        if hasattr(sys.stderr, 'isatty') and sys.stderr.isatty():
            log_color = self.COLORS.get(record.levelname, '')
            record.levelname = f"{log_color}{record.levelname}{self.RESET}"
        
        return super().format(record)

def setup_logger(name='discord_bot', level=None):
    """
    Setup and configure the logger for the Discord bot.
    
    Args:
        name (str): Logger name
        level (str): Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    
    Returns:
        logging.Logger: Configured logger instance
    """
    if level is None:
        level = os.getenv('LOG_LEVEL', 'INFO').upper()
    
    # Create logger
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level, logging.INFO))
    
    # Avoid duplicate handlers
    if logger.handlers:
        return logger
    
    # Create console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(getattr(logging, level, logging.INFO))
    
    # Create formatter
    formatter = ColoredFormatter(
        fmt='%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    console_handler.setFormatter(formatter)
    
    # Add handler to logger
    logger.addHandler(console_handler)
    
    # Setup Discord.py logging
    discord_logger = logging.getLogger('discord')
    discord_logger.setLevel(logging.WARNING)  # Reduce Discord.py verbosity
    
    # Setup other library loggers
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('requests').setLevel(logging.WARNING)
    
    return logger

def log_system_info():
    """Log system information for debugging."""
    logger = logging.getLogger('discord_bot')
    
    try:
        logger.info("=" * 50)
        logger.info("Discord Bot Starting Up")
        logger.info("=" * 50)
        logger.info(f"Python Version: {sys.version}")
        logger.info(f"Platform: {sys.platform}")
        logger.info(f"Start Time: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}")
        
        # Log environment variables (safely)
        env_vars = [
            'COMMAND_PREFIX',
            'BOT_NAME',
            'LOG_LEVEL',
            'ENABLE_MUSIC',
            'ENABLE_MODERATION',
            'ENABLE_ECONOMY'
        ]
        
        logger.info("Environment Variables:")
        for var in env_vars:
            value = os.getenv(var, 'Not Set')
            logger.info(f"  {var}: {value}")
        
        logger.info("=" * 50)
    except Exception as e:
        logger.error(f"Error logging system info: {e}")
