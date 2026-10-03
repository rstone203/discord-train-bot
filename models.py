"""
Database models for the Discord bot.
"""

from datetime import datetime
import pytz

def get_uk_time():
    """Get current time in UK timezone (GMT/BST) as naive datetime for database storage."""
    uk_tz = pytz.timezone('Europe/London')
    return datetime.now(uk_tz).replace(tzinfo=None)

def get_est_time():
    """Alias for get_uk_time for backward compatibility."""
    return get_uk_time()
from sqlalchemy import Column, Integer, String, DateTime, Boolean, BigInteger, Text, ForeignKey, Time, Date, ARRAY, Index, Float, UniqueConstraint
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship

Base = declarative_base()

class Guild(Base):
    """Represents a Discord guild/server."""
    __tablename__ = 'guilds'
    
    id = Column(BigInteger, primary_key=True)  # Discord guild ID
    name = Column(String(100), nullable=False)
    owner_id = Column(BigInteger, nullable=False)
    member_count = Column(Integer, default=0)
    joined_at = Column(DateTime, default=get_est_time)
    is_active = Column(Boolean, default=True)
    server_description = Column(Text, nullable=True)  # Custom server description for notifications
    
    # Live role tracking
    live_role_id = Column(BigInteger, nullable=True)  # Discord role ID for live streamers
    live_tracking_enabled = Column(Boolean, default=False)  # Whether live role tracking is enabled
    live_role_trains_only = Column(Boolean, default=False)  # Only assign live role during active trains

    # Go-live alerts
    stream_alert_channel_id = Column(BigInteger, nullable=True)  # Channel to post go-live alerts
    stream_alert_mention_role_id = Column(BigInteger, nullable=True)  # Optional role to ping with go-live alerts

    # TikTok feed
    tiktok_feed_channel_id = Column(BigInteger, nullable=True)  # Channel to repost TikTok videos
    tiktok_webhook_token = Column(String(64), nullable=True)    # Secret token for inbound TikTok webhooks
    
    # Spam protection settings
    spam_alert_channel_id = Column(String(50), nullable=True)  # Channel for spam/suspicious member alerts
    auto_kick_high_risk = Column(Boolean, default=False)  # Auto-kick members with high spam risk score
    log_all_joins = Column(Boolean, default=False)  # Log all joins, not just suspicious ones
    spam_alert_dm_owner = Column(Boolean, default=False)  # Send spam alerts via DM to server owner instead of channel
    spam_alert_user_ids = Column(ARRAY(BigInteger), nullable=True)  # Custom user IDs to receive spam alerts (overrides owner)
    
    # Relationships
    users = relationship("User", back_populates="guild")
    messages = relationship("Message", back_populates="guild")
    channels = relationship("Channel", back_populates="guild")
    train_schedules = relationship("TrainSchedule", back_populates="guild")

class User(Base):
    """Represents a Discord user."""
    __tablename__ = 'users'
    
    id = Column(BigInteger, primary_key=True)  # Discord user ID
    username = Column(String(32), nullable=False)
    display_name = Column(String(32))
    guild_id = Column(BigInteger, ForeignKey('guilds.id'))
    first_seen = Column(DateTime, default=get_est_time)
    last_seen = Column(DateTime, default=get_est_time)
    message_count = Column(Integer, default=0)
    is_bot = Column(Boolean, default=False)
    
    # Twitch integration fields
    twitch_id = Column(String(50))  # Twitch user ID
    twitch_login = Column(String(32))  # Twitch username
    twitch_display_name = Column(String(50))  # Twitch display name
    twitch_linked_at = Column(DateTime)  # When Twitch account was linked
    twitch_source = Column(String(20), default='discord_oauth')  # How account was linked
    twitch_consent = Column(Boolean, default=True)  # User consent for data storage
    exclude_from_attendance = Column(Boolean, default=False)  # Exclude from attendance tracking
    
    # Relationships
    guild = relationship("Guild", back_populates="users")
    messages = relationship("Message", back_populates="user")

class Message(Base):
    """Represents a forwarded message."""
    __tablename__ = 'messages'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    discord_message_id = Column(BigInteger, unique=True, nullable=False)
    user_id = Column(BigInteger, ForeignKey('users.id'))
    guild_id = Column(BigInteger, ForeignKey('guilds.id'))
    channel_id = Column(BigInteger, nullable=False)
    content = Column(Text)
    timestamp = Column(DateTime, default=get_est_time)
    was_forwarded = Column(Boolean, default=False)
    forwarded_to_channel = Column(BigInteger)
    forwarded_at = Column(DateTime)
    
    # Relationships
    user = relationship("User", back_populates="messages")
    guild = relationship("Guild", back_populates="messages")

class ForwardingConfig(Base):
    """Configuration for message forwarding between channels."""
    __tablename__ = 'forwarding_configs'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    source_guild_id = Column(String(50), nullable=False)  # Source guild ID as string
    source_channel_id = Column(String(50), nullable=False)  # Source channel ID as string
    target_guild_id = Column(String(50), nullable=False)  # Target guild ID as string
    target_channel_id = Column(String(50), nullable=False)  # Target channel ID as string
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=get_est_time)
    deleted_at = Column(DateTime, nullable=True)  # When forwarding was disabled
    created_by = Column(String(50), nullable=False)  # User ID who created the config

class BotStats(Base):
    """Bot statistics and metrics."""
    __tablename__ = 'bot_stats'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(DateTime, default=get_est_time)
    guilds_count = Column(Integer, default=0)
    users_count = Column(Integer, default=0)
    messages_processed = Column(Integer, default=0)
    messages_forwarded = Column(Integer, default=0)
    commands_executed = Column(Integer, default=0)
    uptime_seconds = Column(Integer, default=0)

class CommandLog(Base):
    """Log of executed commands."""
    __tablename__ = 'command_logs'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False)
    guild_id = Column(BigInteger)
    channel_id = Column(BigInteger, nullable=False)
    command_name = Column(String(50), nullable=False)
    arguments = Column(Text)
    timestamp = Column(DateTime, default=get_est_time)
    success = Column(Boolean, default=True)
    error_message = Column(Text)

class BackupApplication(Base):
    """Users who applied as backup streamers via reaction post."""
    __tablename__ = 'backup_applications'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False)  # Discord user ID
    username = Column(String(32), nullable=False)
    display_name = Column(String(32))
    guild_id = Column(BigInteger, ForeignKey('guilds.id'))
    applied_at = Column(DateTime, default=get_est_time)
    is_active = Column(Boolean, default=True)
    twitch_username = Column(String(32))  # Optional Twitch username
    message_id = Column(BigInteger, nullable=False)  # ID of the reaction post they applied to
    notes = Column(Text)  # Optional notes

class TrustedUser(Base):
    """Users trusted with owner-level permissions."""
    __tablename__ = 'trusted_users'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, unique=True, nullable=False)  # Discord user ID
    username = Column(String(32), nullable=False)
    display_name = Column(String(32))
    granted_by = Column(BigInteger, nullable=False)  # Who granted the trust
    granted_at = Column(DateTime, default=get_est_time)
    is_active = Column(Boolean, default=True)
    notes = Column(Text)  # Optional notes about why they were trusted

class Channel(Base):
    """Represents a Discord channel."""
    __tablename__ = 'channels'
    
    id = Column(BigInteger, primary_key=True)  # Discord channel ID
    guild_id = Column(BigInteger, ForeignKey('guilds.id'), nullable=False)
    name = Column(String(100), nullable=False)
    type = Column(String(20), default='text')
    position = Column(Integer, default=0)
    last_updated = Column(DateTime, default=get_est_time)
    
    # Relationship to guild
    guild = relationship("Guild", back_populates="channels")

class NotificationSettings(Base):
    """Notification settings for each guild."""
    __tablename__ = 'notification_settings'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    guild_id = Column(BigInteger, ForeignKey('guilds.id'), unique=True, nullable=False)
    
    # Raid train notification settings
    raid_notification_channel_id = Column(BigInteger)  # Channel for raid notifications
    raid_ping_role_id = Column(BigInteger)  # Role to ping for raids
    backup_notification_channel_id = Column(BigInteger)  # Channel for backup notifications
    backup_ping_role_id = Column(BigInteger)  # Role to ping for backup requests
    
    # Stream notification settings
    stream_notification_channel_id = Column(BigInteger)  # Channel for stream notifications
    stream_ping_role_id = Column(BigInteger)  # Role to ping for stream updates
    
    # Updates notification settings
    updates_notification_channel_id = Column(BigInteger)  # Channel for bot update notifications
    updates_ping_role_id = Column(BigInteger)  # Role to ping for bot updates
    
    # Attendance notification settings
    attendance_channel_id = Column(BigInteger)  # Channel for train attendance notifications
    attendance_interval_minutes = Column(Integer, default=25)  # Interval for attendance reports (default: 25 minutes)
    
    # Twitch chat bot settings
    twitch_chat_bot_enabled = Column(Boolean, default=True)  # Enable/disable Twitch chat bot features
    auto_announcements_enabled = Column(Boolean, default=True)  # Auto-announce when next rider goes live
    ten_minute_warnings_enabled = Column(Boolean, default=True)  # Send 10-minute warnings before next rider
    one_hour_warnings_enabled = Column(Boolean, default=True)  # Send 1-hour warnings with live status check
    
    # Comprehensive attendance report settings
    comprehensive_reports_enabled = Column(Boolean, default=True)  # Enable comprehensive daily/weekend reports
    comprehensive_report_channel_id = Column(BigInteger, nullable=True)  # Channel for comprehensive reports

    # Community engagement settings
    shoutout_discord_channel_id = Column(BigInteger, nullable=True)  # Channel for Discord shoutout + raid alert embeds

    # General settings
    auto_ping_enabled = Column(Boolean, default=True)
    backup_system_enabled = Column(Boolean, default=True)
    ping_timeout_minutes = Column(Integer, default=15)  # Timeout before pinging backup
    
    # Backup signup reaction settings
    backup_signup_message_id = Column(BigInteger, nullable=True)  # Message users react to for backup role
    backup_signup_emoji = Column(String(10), default="✅")  # Emoji to react with
    backup_remove_on_unreact = Column(Boolean, default=True)  # Remove role when unreacting
    
    created_at = Column(DateTime, default=get_est_time)
    updated_at = Column(DateTime, default=get_est_time)
    
    # Relationship to guild
    guild = relationship("Guild")

class TrainSchedule(Base):
    """Train schedule and time slots for raid trains."""
    __tablename__ = 'train_schedules'

    __table_args__ = (
        # open-seat detector scans all recurring active slots every tick
        Index('idx_train_schedules_active_recurring', 'is_active', 'schedule_type'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    guild_id = Column(BigInteger, ForeignKey('guilds.id'), nullable=False)
    
    # Schedule information
    name = Column(String(255), nullable=False)  # Name of the train slot
    day_of_week = Column(Integer, nullable=False)  # 0=Monday, 6=Sunday
    start_time = Column(Time, nullable=False)  # Start time (UK wall clock, Europe/London)
    duration_minutes = Column(Integer, nullable=False)  # Duration in minutes
    
    # Date-specific scheduling
    specific_date = Column(Date, nullable=True)  # For one-time events or planning ahead
    schedule_type = Column(String(20), default='recurring')  # 'recurring' or 'one-time'
    
    # Notification settings
    notify_before_minutes = Column(Integer, default=60)  # Notify X minutes before start
    is_active = Column(Boolean, default=True)
    
    # Additional metadata
    description = Column(Text)
    host_user_id = Column(BigInteger)  # Optional host user
    max_participants = Column(Integer)  # Optional participant limit
    participant_ids = Column(ARRAY(String), nullable=True)  # Discord IDs to ping
    
    created_at = Column(DateTime, default=get_est_time)
    updated_at = Column(DateTime, default=get_est_time, onupdate=get_est_time)
    
    # Relationship to guild
    guild = relationship("Guild", back_populates="train_schedules")
    
    # Relationship to notifications and participants
    notifications = relationship("TrainNotification", back_populates="schedule")
    participants = relationship("TrainParticipant", back_populates="schedule")

class TrainNotification(Base):
    """Track train notifications and responses."""
    __tablename__ = 'train_notifications'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    schedule_id = Column(Integer, ForeignKey('train_schedules.id'), nullable=True)  # Nullable for test notifications
    guild_id = Column(BigInteger, nullable=False)
    
    # Notification details
    notification_date = Column(DateTime, nullable=False)  # The date this notification is for
    stage = Column(Integer, nullable=False, default=1)  # 1=1hr, 2=30min, 3=20min
    message_id = Column(BigInteger)  # Discord message ID for tracking reactions
    channel_id = Column(BigInteger)  # Channel where notification was sent
    
    # Response tracking
    primary_confirmed = Column(Boolean, default=False)  # Primary streamer confirmed ready
    primary_user_id = Column(BigInteger)  # User ID who confirmed
    backup_pinged = Column(Boolean, default=False)  # Backup streamers have been pinged
    backup_confirmed = Column(Boolean, default=False)  # Backup streamer confirmed
    backup_user_id = Column(BigInteger)  # Backup user ID who confirmed
    
    # Status tracking
    is_sent = Column(Boolean, default=False)
    sent_at = Column(DateTime)
    is_completed = Column(Boolean, default=False)  # All notifications for this schedule sent
    
    created_at = Column(DateTime, default=get_est_time)
    updated_at = Column(DateTime, default=get_est_time, onupdate=get_est_time)
    
    # Relationships
    schedule = relationship("TrainSchedule", back_populates="notifications")

class TrainParticipant(Base):
    """Track users who sign up for specific train sessions."""
    __tablename__ = 'train_participants'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    schedule_id = Column(Integer, ForeignKey('train_schedules.id'), nullable=False)
    guild_id = Column(BigInteger, nullable=False)
    user_id = Column(BigInteger, nullable=False)  # Discord user ID
    username = Column(String(32), nullable=False)
    display_name = Column(String(32))
    
    # Participation details
    signed_up_at = Column(DateTime, default=get_est_time)
    twitch_username = Column(String(32))  # Optional Twitch username
    is_host = Column(Boolean, default=False)  # If they're hosting this session
    is_active = Column(Boolean, default=True)
    notes = Column(Text)  # Optional notes
    
    # Beta: signup waitlist (additive). Waitlisted rows are stored with is_active=False
    # so existing roster/count queries (filter is_active=True) naturally exclude them.
    is_waitlisted = Column(Boolean, default=False)  # True while on the waitlist
    waitlist_position = Column(Integer, nullable=True)  # 1-based queue position when waitlisted
    # Beta: slot waitlist panel — when an open-seat offer DM is sent to a waitlisted
    # user, this records when, so the detector throttles re-offers and can expire them.
    offer_sent_at = Column(DateTime, nullable=True)

    __table_args__ = (
        # active_rider_count: counts active riders per slot
        Index('idx_train_participants_slot_active', 'schedule_id', 'is_active'),
        # first_waitlister / waitlisted_rows: ordered waitlist lookup per slot
        Index('idx_train_participants_slot_waitlist', 'schedule_id', 'is_waitlisted', 'waitlist_position'),
    )

    # Relationship to schedule
    schedule = relationship("TrainSchedule")

class UserTrainReminderPreference(Base):
    """Beta: per-user opt-in preference for train reminder DMs before their own slot."""
    __tablename__ = 'user_train_reminder_preferences'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    guild_id = Column(BigInteger, nullable=False)
    user_id = Column(BigInteger, nullable=False)  # Discord user ID
    enabled = Column(Boolean, default=True)  # Opt-in state
    lead_minutes = Column(Integer, default=30)  # Minutes before slot to DM
    
    created_at = Column(DateTime, default=get_est_time)
    updated_at = Column(DateTime, default=get_est_time, onupdate=get_est_time)
    
    __table_args__ = (
        Index('idx_reminder_pref_guild_user', 'guild_id', 'user_id'),
    )

class TrainReminderSent(Base):
    """Beta: dedup tracking so a reminder DM is sent at most once per occurrence."""
    __tablename__ = 'train_reminder_sent'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    schedule_id = Column(Integer, nullable=False)
    user_id = Column(BigInteger, nullable=False)
    guild_id = Column(BigInteger, nullable=False)
    occurrence_date = Column(Date, nullable=False)  # The date of the slot occurrence
    sent_at = Column(DateTime, default=get_est_time)
    
    __table_args__ = (
        Index('idx_reminder_sent_lookup', 'schedule_id', 'user_id', 'occurrence_date'),
        UniqueConstraint('schedule_id', 'user_id', 'occurrence_date', name='uq_reminder_sent_occurrence'),
    )

class TrainAttendance(Base):
    """Track attendance responses to train notifications via emoji reactions."""
    __tablename__ = 'train_attendance'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    notification_id = Column(Integer, ForeignKey('train_notifications.id'), nullable=False)
    user_id = Column(BigInteger, nullable=False)  # Discord user ID
    status = Column(String(20), nullable=False)  # 'ready', 'not_ready', 'backup_needed'
    reaction_time = Column(DateTime, default=get_est_time)
    
    created_at = Column(DateTime, default=get_est_time)
    
    # Relationship to notification
    notification = relationship("TrainNotification")

class TrainParticipantReady(Base):
    """Track participant readiness reactions for train notifications."""
    __tablename__ = 'train_participant_ready'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    notification_id = Column(Integer, ForeignKey('train_notifications.id'), nullable=False)
    schedule_id = Column(Integer, ForeignKey('train_schedules.id'), nullable=False)
    user_id = Column(BigInteger, nullable=False)  # Discord user ID who marked ready
    username = Column(String(32), nullable=False)
    guild_id = Column(BigInteger, nullable=False)
    
    # Readiness tracking
    marked_ready_at = Column(DateTime, default=get_est_time)
    host_notified = Column(Boolean, default=False)  # Whether host was notified
    host_notified_at = Column(DateTime, nullable=True)
    notification_stage = Column(Integer, nullable=False)  # Which stage (60min, 30min, 15min)
    
    # Relationships
    notification = relationship("TrainNotification")
    schedule = relationship("TrainSchedule")

class TwitchChatAttendance(Base):
    """Track real-time Twitch chat presence during train sessions."""
    __tablename__ = 'twitch_chat_attendance'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    schedule_id = Column(Integer, ForeignKey('train_schedules.id'), nullable=False)
    user_id = Column(BigInteger, nullable=False)  # Discord user ID
    twitch_username = Column(String(25), nullable=False)  # Twitch username
    guild_id = Column(BigInteger, nullable=False)
    
    # Attendance tracking
    was_present_in_chat = Column(Boolean, default=False)
    first_chat_message_at = Column(DateTime, nullable=True)
    last_chat_message_at = Column(DateTime, nullable=True)
    total_messages = Column(Integer, default=0)
    
    # Session info
    train_date = Column(Date, nullable=False)
    monitoring_started_at = Column(DateTime, default=get_est_time)
    monitoring_ended_at = Column(DateTime, nullable=True)
    
    # Relationships
    schedule = relationship("TrainSchedule")

class ComprehensiveTrainReport(Base):
    """Aggregated daily/weekend train attendance report."""
    __tablename__ = 'comprehensive_train_reports'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    guild_id = Column(BigInteger, ForeignKey('guilds.id'), nullable=False)
    
    # Report metadata
    report_date = Column(Date, nullable=False)  # The date this report covers (last train day)
    train_days = Column(ARRAY(Integer), nullable=False)  # List of day numbers covered (e.g., [5, 6] for Sat+Sun)
    report_generated_at = Column(DateTime, nullable=True)  # When report was generated
    report_sent_at = Column(DateTime, nullable=True)  # When report was sent to Discord
    
    # Aggregated statistics
    total_trains = Column(Integer, default=0)  # Number of trains that day/weekend
    total_unique_chatters = Column(Integer, default=0)  # Unique chatters across all trains
    total_messages = Column(Integer, default=0)  # Total messages across all trains
    chatter_names = Column(ARRAY(String), nullable=True)  # List of all unique chatter usernames
    
    # Per-train breakdown (JSON stored as Text)
    train_breakdown = Column(Text, nullable=True)  # JSON with per-train stats
    
    # Report status
    is_sent = Column(Boolean, default=False)
    scheduled_send_time = Column(DateTime, nullable=True)  # When to send (next day 12pm UTC)
    
    # Relationships
    guild = relationship("Guild")

class BotUpdate(Base):
    """Track bot updates and changes with timestamps, descriptions, and categories."""
    __tablename__ = 'bot_updates'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(100), nullable=False)  # Brief update title
    description = Column(Text)  # Detailed description of the change
    category = Column(String(50), nullable=False)  # Type of update (feature, bugfix, enhancement, etc.)
    version = Column(String(20), nullable=True)  # Version number if applicable
    author_id = Column(BigInteger, nullable=True)  # Discord ID of who made the change
    guild_id = Column(BigInteger, ForeignKey('guilds.id'), nullable=True)  # Guild this update affects (null for global updates)
    is_published = Column(Boolean, default=False)  # Whether this update should appear publicly
    is_weekly_posted = Column(Boolean, default=False)  # Whether this was included in a weekly summary
    
    created_at = Column(DateTime, default=get_est_time)
    updated_at = Column(DateTime, default=get_est_time, onupdate=get_est_time)
    
    # Relationship to guild (optional)
    guild = relationship("Guild")

class UpdateChannelConfig(Base):
    """Configuration for weekly update posting channels per guild."""
    __tablename__ = 'update_channel_configs'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    guild_id = Column(BigInteger, ForeignKey('guilds.id'), unique=True, nullable=False)
    update_channel_id = Column(BigInteger, nullable=False)  # Discord channel ID for weekly updates
    is_enabled = Column(Boolean, default=True)  # Whether weekly posting is enabled for this guild
    notify_role_id = Column(BigInteger, nullable=True)  # Optional role to ping when posting updates
    posting_day = Column(Integer, default=6)  # Day of week (0=Monday, 6=Sunday)
    posting_hour = Column(Integer, default=18)  # Hour to post (24-hour format, EST)
    include_global_updates = Column(Boolean, default=True)  # Whether to include global bot updates
    
    # Duplicate-post prevention fields
    last_posted_at = Column(DateTime, nullable=True)  # Last time guild-specific updates were posted
    last_global_posted_at = Column(DateTime, nullable=True)  # Last time global updates were posted
    
    created_at = Column(DateTime, default=get_est_time)
    updated_at = Column(DateTime, default=get_est_time, onupdate=get_est_time)
    configured_by = Column(BigInteger, nullable=False)  # User ID who configured this
    
    # Relationship to guild
    guild = relationship("Guild")

class SystemSettings(Base):
    """System-wide bot settings and flags."""
    __tablename__ = 'system_settings'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    setting_key = Column(String(50), unique=True, nullable=False)  # Setting name
    setting_value = Column(String(255), nullable=True)  # Setting value
    is_enabled = Column(Boolean, default=False)  # Boolean flag for on/off settings
    
    created_at = Column(DateTime, default=get_est_time)
    updated_at = Column(DateTime, default=get_est_time, onupdate=get_est_time)

class TwitchLinkRequest(Base):
    """Track pending Twitch account link requests requiring user consent."""
    __tablename__ = 'twitch_link_requests'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    target_user_id = Column(BigInteger, nullable=False)  # Discord user ID being linked
    target_username = Column(String(32), nullable=False)  # Discord username
    target_display_name = Column(String(32))  # Discord display name
    requested_twitch_username = Column(String(32), nullable=False)  # Twitch username to link
    guild_id = Column(BigInteger, ForeignKey('guilds.id'), nullable=False)
    
    # Request metadata
    admin_user_id = Column(BigInteger, nullable=False)  # Admin who made the request
    admin_username = Column(String(32), nullable=False)  # Admin username for context
    request_message_id = Column(BigInteger, nullable=True)  # DM message ID with buttons
    request_channel_id = Column(BigInteger, nullable=True)  # DM channel ID
    
    # Request status and timing
    status = Column(String(20), default='pending')  # 'pending', 'approved', 'denied', 'expired', 'failed'
    request_sent_at = Column(DateTime, default=get_est_time)
    expires_at = Column(DateTime, nullable=False)  # When this request expires
    responded_at = Column(DateTime, nullable=True)  # When user responded
    response_message = Column(Text, nullable=True)  # Optional user response message
    
    # Error handling
    dm_failed = Column(Boolean, default=False)  # If DM couldn't be sent
    error_message = Column(Text, nullable=True)  # Error details if any
    
    created_at = Column(DateTime, default=get_est_time)
    updated_at = Column(DateTime, default=get_est_time, onupdate=get_est_time)
    
    # Relationship to guild
    guild = relationship("Guild")

class TrainSlotCompletion(Base):
    """Tracks completed train slots to prevent duplicate end-of-slot notifications."""
    __tablename__ = 'train_slot_completions'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    schedule_id = Column(Integer, ForeignKey('train_schedules.id'), nullable=False)
    guild_id = Column(BigInteger, ForeignKey('guilds.id'), nullable=False)
    completion_date = Column(Date, nullable=False)  # Date when slot occurred (EST)
    slot_start = Column(DateTime, nullable=False)  # Actual start time in EST
    slot_end = Column(DateTime, nullable=False)  # Actual end time in EST
    notified_at = Column(DateTime, default=get_est_time)
    attendance_channel_id = Column(BigInteger)  # Channel where notification was sent
    participant_count = Column(Integer, default=0)  # Number of participants in slot
    warning_sent = Column(Boolean, default=False)  # Whether 5-min warning was sent
    attendance_posted = Column(Boolean, default=False)  # Whether attendance summary was posted
    
    created_at = Column(DateTime, default=get_est_time)
    
    # Relationships
    schedule = relationship("TrainSchedule")
    guild = relationship("Guild")
    
    # Ensure one completion record per schedule per date
    __table_args__ = (
        Index('idx_schedule_date_unique', 'schedule_id', 'completion_date', unique=True),
    )

class TrustedRole(Base):
    """Stores Discord roles that have trusted user permissions."""
    __tablename__ = 'trusted_roles'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    guild_id = Column(BigInteger, ForeignKey('guilds.id'), nullable=False)
    role_id = Column(BigInteger, nullable=False)  # Discord role ID
    role_name = Column(String(100), nullable=False)  # Role name for reference
    
    # Management info
    granted_by = Column(BigInteger, nullable=False)  # User ID who added this role
    granted_at = Column(DateTime, default=get_est_time)
    is_active = Column(Boolean, default=True)
    notes = Column(Text, nullable=True)  # Optional notes about this role
    
    created_at = Column(DateTime, default=get_est_time)
    updated_at = Column(DateTime, default=get_est_time, onupdate=get_est_time)
    
    # Relationship to guild
    guild = relationship("Guild")
    
    # Ensure unique role per guild
    __table_args__ = (
        Index('idx_guild_role_unique', 'guild_id', 'role_id', unique=True),
    )

class PersistentMessageDisplay(Base):
    """Stores settings for persistent auto-updating messages like timeslots displays."""
    __tablename__ = 'persistent_message_displays'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    guild_id = Column(BigInteger, ForeignKey('guilds.id'), nullable=False)
    
    # Message location
    channel_id = Column(BigInteger, nullable=False)  # Where the persistent message is posted
    message_id = Column(BigInteger, nullable=True)  # Discord message ID of the persistent message
    
    # Display settings
    display_type = Column(String(50), nullable=False)  # 'timeslots', 'schedule', etc
    title = Column(String(255), nullable=True)  # Optional custom title
    auto_update_enabled = Column(Boolean, default=True)  # Whether to auto-update
    update_interval_minutes = Column(Integer, default=10)  # How often to update in minutes
    
    # Display options
    show_twitch_usernames = Column(Boolean, default=True)  # Show Twitch info in display
    show_participant_counts = Column(Boolean, default=True)  # Show signup counts
    max_schedules_displayed = Column(Integer, default=24)  # Max schedules to show
    
    # Status tracking
    is_active = Column(Boolean, default=True)
    last_updated_at = Column(DateTime, nullable=True)  # When it was last updated
    update_error_count = Column(Integer, default=0)  # Count of consecutive update errors
    last_error_message = Column(Text, nullable=True)  # Last error that occurred
    
    created_at = Column(DateTime, default=get_est_time)
    updated_at = Column(DateTime, default=get_est_time, onupdate=get_est_time)
    
    # Relationship to guild
    guild = relationship("Guild")
    
    # Ensure one persistent display per type per guild
    __table_args__ = (
        Index('idx_guild_display_type_unique', 'guild_id', 'display_type', unique=True),
    )

class TwitchOAuthToken(Base):
    """Stores Twitch OAuth tokens for chat monitoring."""
    __tablename__ = 'twitch_oauth_tokens'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False)  # Discord user ID of the bot admin
    guild_id = Column(BigInteger, ForeignKey('guilds.id'), nullable=True)  # Optional guild association
    
    # OAuth tokens
    access_token = Column(String(255), nullable=False)
    refresh_token = Column(String(255), nullable=True)
    token_type = Column(String(50), default='bearer')
    
    # Token metadata
    twitch_user_id = Column(String(50), nullable=True)  # Twitch user ID for the authorized account
    twitch_username = Column(String(50), nullable=True)  # Twitch username
    scopes = Column(ARRAY(String), nullable=True)  # OAuth scopes granted
    
    # Expiration tracking
    expires_at = Column(DateTime, nullable=True)  # When the access token expires
    
    # Status
    is_active = Column(Boolean, default=True)  # Whether this token is currently being used
    last_used_at = Column(DateTime, nullable=True)  # Last time this token was used
    
    created_at = Column(DateTime, default=get_est_time)
    updated_at = Column(DateTime, default=get_est_time, onupdate=get_est_time)

class BannedUser(Base):
    """Users banned from joining train schedules across all servers."""
    __tablename__ = 'banned_users'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False, unique=True)  # Discord user ID
    username = Column(String(100))  # Discord username for reference
    reason = Column(Text, nullable=True)  # Reason for ban
    banned_by = Column(BigInteger, nullable=False)  # User ID who issued the ban
    banned_at = Column(DateTime, default=get_est_time)
    is_active = Column(Boolean, default=True)  # Can be used to temporarily lift bans
    
    # Index for fast lookups
    __table_args__ = (
        Index('idx_user_id_active', 'user_id', 'is_active'),
    )

class ServerFeaturePermissions(Base):
    """Controls which feature categories are enabled per server."""
    __tablename__ = 'server_feature_permissions'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    guild_id = Column(BigInteger, nullable=False, unique=True)  # Discord guild ID
    
    # Feature categories - True means enabled, False means disabled
    # By default, all features are enabled (True)
    trains_enabled = Column(Boolean, default=True)  # Train scheduling and attendance
    twitch_enabled = Column(Boolean, default=True)  # Twitch integration, live roles, analytics
    forwarding_enabled = Column(Boolean, default=True)  # Message forwarding
    admin_enabled = Column(Boolean, default=True)  # Admin commands (setup, config)
    analytics_enabled = Column(Boolean, default=True)  # Analytics and stats
    general_enabled = Column(Boolean, default=True)  # General commands (ping, info, help)
    
    # Beta feature flags - default OFF for all servers until explicitly enabled per-server
    reminders_enabled = Column(Boolean, default=False)  # Per-user opt-in train reminder DMs
    waitlist_enabled = Column(Boolean, default=False)  # Signup waitlist with auto-promote
    stats_enabled = Column(Boolean, default=False)  # Participant stats / leaderboard
    self_signup_enabled = Column(Boolean, default=False)  # Public /jointrain self-service signup (beta)
    
    # Subscription/Trusted Access Control (Bot owner only)
    trusted_access_enabled = Column(Boolean, default=False)  # Bot owner enables trusted access per server
    
    # Metadata
    configured_by = Column(BigInteger, nullable=False)  # User ID who set restrictions
    configured_at = Column(DateTime, default=get_est_time)
    updated_at = Column(DateTime, default=get_est_time, onupdate=get_est_time)
    notes = Column(Text, nullable=True)  # Optional notes about why restrictions were set
    
    # Index for fast lookups
    __table_args__ = (
        Index('idx_guild_id_permissions', 'guild_id'),
    )

class SpamReport(Base):
    """Track spam DM reports from server members."""
    __tablename__ = 'spam_reports'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    guild_id = Column(BigInteger, nullable=False)
    reporter_id = Column(BigInteger, nullable=False)
    reporter_username = Column(String(100))
    
    reported_user_id = Column(BigInteger, nullable=True)
    reported_username = Column(String(100), nullable=True)
    
    report_type = Column(String(50), default='dm_spam')
    report_details = Column(Text, nullable=True)
    severity = Column(String(20), default='medium')
    
    action_taken = Column(String(100), nullable=True)
    reviewed_by = Column(BigInteger, nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    is_resolved = Column(Boolean, default=False)
    
    created_at = Column(DateTime, default=get_est_time)
    
    __table_args__ = (
        Index('idx_spam_reports_guild', 'guild_id'),
        Index('idx_spam_reports_reporter', 'reporter_id'),
        Index('idx_spam_reports_reported', 'reported_user_id'),
        Index('idx_spam_reports_resolved', 'is_resolved'),
    )

class AFKStatus(Base):
    """Track AFK status for users."""
    __tablename__ = 'afk_status'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False)
    guild_id = Column(BigInteger, nullable=False)
    afk_message = Column(String(500), nullable=True)
    set_at = Column(DateTime, default=get_est_time)
    
    __table_args__ = (
        Index('idx_afk_user_guild', 'user_id', 'guild_id', unique=True),
    )

class StreamPlatform(Base):
    """Multi-platform streaming support (YouTube, Kick, etc.)."""
    __tablename__ = 'stream_platforms'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey('users.id'), nullable=False)
    guild_id = Column(BigInteger, nullable=False)
    
    # Platform info
    platform = Column(String(20), nullable=False)  # 'youtube', 'kick', 'twitch'
    platform_user_id = Column(String(100), nullable=False)
    platform_username = Column(String(100), nullable=False)
    
    # Notification settings
    notify_live = Column(Boolean, default=True)
    notify_clips = Column(Boolean, default=False)
    notify_vods = Column(Boolean, default=False)
    notification_channel_id = Column(BigInteger, nullable=True)
    
    # Status tracking
    is_live = Column(Boolean, default=False)
    last_checked = Column(DateTime, nullable=True)
    last_stream_id = Column(String(100), nullable=True)
    
    # Metadata
    linked_at = Column(DateTime, default=get_est_time)
    updated_at = Column(DateTime, default=get_est_time, onupdate=get_est_time)
    
    __table_args__ = (
        Index('idx_stream_platform_user', 'user_id', 'platform'),
        Index('idx_stream_platform_guild', 'guild_id'),
    )

class ClipNotification(Base):
    """Track Twitch/YouTube clip notifications to avoid duplicates."""
    __tablename__ = 'clip_notifications'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False)
    guild_id = Column(BigInteger, nullable=False)
    platform = Column(String(20), nullable=False)  # 'twitch', 'youtube'
    
    clip_id = Column(String(100), nullable=False, unique=True)
    clip_url = Column(String(500), nullable=False)
    clip_title = Column(String(256), nullable=True)
    clip_creator = Column(String(100), nullable=True)
    
    posted_at = Column(DateTime, default=get_est_time)
    posted_to_channel_id = Column(BigInteger, nullable=False)
    
    __table_args__ = (
        Index('idx_clip_notifications_user', 'user_id'),
        Index('idx_clip_notifications_guild', 'guild_id'),
        Index('idx_clip_notifications_clip_id', 'clip_id', unique=True),
    )

class VODNotification(Base):
    """Track VOD notifications to avoid duplicates."""
    __tablename__ = 'vod_notifications'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False)
    guild_id = Column(BigInteger, nullable=False)
    platform = Column(String(20), nullable=False)  # 'twitch', 'youtube'
    
    vod_id = Column(String(100), nullable=False, unique=True)
    vod_url = Column(String(500), nullable=False)
    vod_title = Column(String(256), nullable=True)
    vod_duration = Column(Integer, nullable=True)  # Duration in seconds
    
    posted_at = Column(DateTime, default=get_est_time)
    posted_to_channel_id = Column(BigInteger, nullable=False)
    
    __table_args__ = (
        Index('idx_vod_notifications_user', 'user_id'),
        Index('idx_vod_notifications_guild', 'guild_id'),
        Index('idx_vod_notifications_vod_id', 'vod_id', unique=True),
    )

class StreamChatAnnouncement(Base):
    """Custom announcements to post in friends' stream chats when going live."""
    __tablename__ = 'stream_chat_announcements'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False)  # Discord user who goes live
    guild_id = Column(BigInteger, nullable=False)
    target_twitch_channels = Column(ARRAY(String), nullable=False)  # List of friend channels to post in
    
    announcement_message = Column(String(500), nullable=False)  # Message to send in friend chats
    enabled = Column(Boolean, default=True)  # Whether this announcement is active
    
    created_at = Column(DateTime, default=get_est_time)
    last_sent_at = Column(DateTime, nullable=True)  # Last time this announcement was sent
    
    __table_args__ = (
        Index('idx_stream_announcement_user', 'user_id'),
        Index('idx_stream_announcement_guild', 'guild_id'),
    )

class AutoShoutoutSettings(Base):
    """Auto-shoutout settings for streamers - automatically shoutout new chatters."""
    __tablename__ = 'auto_shoutout_settings'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False)  # Discord user who owns the stream
    guild_id = Column(BigInteger, nullable=False)
    
    enabled = Column(Boolean, default=True)  # Whether auto-shoutout is active
    shoutout_message = Column(String(500), nullable=False, default="Thanks for stopping by @{username}! Check them out at twitch.tv/{username}")
    delay_seconds = Column(Integer, default=3)  # Delay before sending shoutout (to prevent spam)
    
    # Cooldown settings
    cooldown_per_user_minutes = Column(Integer, default=60)  # Only shoutout each user once per X minutes
    
    created_at = Column(DateTime, default=get_est_time)
    updated_at = Column(DateTime, default=get_est_time, onupdate=get_est_time)
    
    __table_args__ = (
        Index('idx_auto_shoutout_user', 'user_id'),
        Index('idx_auto_shoutout_guild', 'guild_id'),
    )


class TwitchLinkOutreach(Base):
    """Track DM outreach to users who haven't linked their Twitch accounts."""
    __tablename__ = 'twitch_link_outreach'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False)
    guild_id = Column(BigInteger, nullable=False)
    schedule_id = Column(Integer, ForeignKey('train_schedules.id'), nullable=True)
    
    dm_sent_at = Column(DateTime, default=get_est_time)
    responded = Column(Boolean, default=False)
    responded_at = Column(DateTime, nullable=True)
    twitch_username_provided = Column(String(32), nullable=True)
    oauth_authorized = Column(Boolean, default=False)
    
    awaiting_reply = Column(Boolean, default=True)
    
    __table_args__ = (
        Index('idx_outreach_user_guild', 'user_id', 'guild_id'),
        Index('idx_outreach_awaiting', 'awaiting_reply'),
    )


class SubscriptionSettings(Base):
    """Global subscription system configuration (single row, bot-owner controlled).

    The system defaults to disabled (subscription_required=False) so enabling
    the models/table does not change access for any existing server. Only
    /togglesubscriptions enable flips it on.
    """
    __tablename__ = 'subscription_settings'

    id = Column(Integer, primary_key=True, autoincrement=True)
    subscription_required = Column(Boolean, default=False)  # Master on/off switch
    subscription_price_monthly = Column(Integer, default=1200)  # Cents ($12.00)
    free_trial_days = Column(Integer, default=14)
    stripe_price_id = Column(String(64), nullable=True)  # Stripe recurring Price ID
    grace_period_days = Column(Integer, default=30)
    grace_period_end_date = Column(DateTime, nullable=True)

    updated_by = Column(BigInteger, nullable=True)  # Discord user ID who last changed this
    enabled_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=get_est_time)
    updated_at = Column(DateTime, default=get_est_time, onupdate=get_est_time)


class UserSubscription(Base):
    """Tracks a user's trial/subscription status. Global (works across all servers)."""
    __tablename__ = 'user_subscriptions'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False, unique=True)
    username = Column(String(32), nullable=True)

    stripe_customer_id = Column(String(64), nullable=True)
    stripe_subscription_id = Column(String(64), nullable=True)
    stripe_status = Column(String(32), nullable=True)  # active, past_due, canceled, etc.

    is_active = Column(Boolean, default=False)  # Active paid subscriber
    subscription_start = Column(DateTime, nullable=True)
    subscription_end = Column(DateTime, nullable=True)
    current_period_start = Column(DateTime, nullable=True)
    current_period_end = Column(DateTime, nullable=True)

    is_trial = Column(Boolean, default=False)
    trial_start = Column(DateTime, nullable=True)
    trial_end = Column(DateTime, nullable=True)
    trial_expiry_notification_sent = Column(Boolean, default=False)
    trial_first_used_guild_id = Column(BigInteger, nullable=True)

    created_at = Column(DateTime, default=get_est_time)
    updated_at = Column(DateTime, default=get_est_time, onupdate=get_est_time)

    __table_args__ = (
        Index('idx_user_subscriptions_user', 'user_id'),
        Index('idx_user_subscriptions_active', 'is_active'),
        Index('idx_user_subscriptions_trial_end', 'trial_end'),
    )


class TrustedServerAccess(Base):
    """Per-guild free organizer access, manually granted by the bot owner.

    Distinct from TrustedRole (which grants trusted permissions to a role
    within a server that already has trusted access enabled). This table
    controls whether a server participates in trusted-access at all.
    """
    __tablename__ = 'trusted_server_access'

    id = Column(Integer, primary_key=True, autoincrement=True)
    guild_id = Column(BigInteger, nullable=False, unique=True)
    enabled = Column(Boolean, default=True)
    enabled_by = Column(BigInteger, nullable=False)  # Discord user ID (bot owner)
    enabled_at = Column(DateTime, default=get_est_time)

    __table_args__ = (
        Index('idx_trusted_server_access_guild', 'guild_id'),
    )


