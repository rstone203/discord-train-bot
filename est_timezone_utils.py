"""
UK Timezone utilities - Helper functions for consistent UK (GMT/BST) timezone handling
"""

from datetime import datetime
import pytz

UK_TZ = pytz.timezone('Europe/London')

def uk_now():
    """Get current time in UK timezone (GMT/BST)."""
    return datetime.now(UK_TZ)

def uk_naive_now():
    """Get current time in UK timezone as naive datetime (for database storage)."""
    return datetime.now(UK_TZ).replace(tzinfo=None)

def est_now():
    """Alias for uk_now for backward compatibility."""
    return uk_now()

def est_naive_now():
    """Alias for uk_naive_now for backward compatibility."""
    return uk_naive_now()
