"""
Automated database backup system with retention policy.
Exports critical tables to JSON for disaster recovery.
"""

import os
import json
import logging
import asyncio
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from pathlib import Path
from database import DatabaseSession

from models import (
    TrainSchedule, TrainParticipant, BannedUser, 
    NotificationSettings, SystemSettings, User
)

# Resolve the repository root once at import time so the runtime guard in
# DatabaseBackup.__init__ can detect when a caller supplies a backup directory
# that sits inside the working tree (which would risk committing sensitive data).
_REPO_ROOT = Path(__file__).resolve().parent.parent

logger = logging.getLogger('database_backup')


class DatabaseBackup:
    """
    Automated database backup system.
    Exports critical tables to JSON files with configurable retention.
    """
    
    def __init__(self, backup_dir: str = "/tmp/db_backups", retention_days: int = 7):
        """
        Initialize the backup system.
        
        Args:
            backup_dir: Directory to store backups. Defaults to /tmp/db_backups
                        (outside the repository) to prevent accidental commits
                        of files containing sensitive user data.
            retention_days: How many days of backups to keep
        """
        self.backup_dir = Path(backup_dir).resolve()
        self.retention_days = retention_days
        self._backup_task: Optional[asyncio.Task] = None

        # Guard: refuse to write backups inside the repository working tree.
        # Backup files contain live operational data (Discord/Twitch user records,
        # schedule participation, etc.) and must never be committed to version control.
        try:
            self.backup_dir.relative_to(_REPO_ROOT)
            # If we reach here, backup_dir is inside the repo — reject it.
            raise ValueError(
                f"Unsafe backup directory: '{self.backup_dir}' is inside the repository "
                f"root '{_REPO_ROOT}'. Backup files contain sensitive operational data and "
                f"must not be stored inside the working tree. Use a path outside the repo "
                f"such as '/tmp/db_backups'."
            )
        except ValueError as exc:
            if "Unsafe backup directory" in str(exc):
                raise
            # backup_dir is NOT relative to _REPO_ROOT — this is the safe, expected path.

        # Create backup directory if it doesn't exist
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Database backup system initialized (retention: {retention_days} days)")
    
    def _serialize_model(self, obj: Any) -> Dict[str, Any]:
        """
        Serialize a SQLAlchemy model to a dictionary.
        
        Args:
            obj: SQLAlchemy model instance
            
        Returns:
            Dictionary representation of the model
        """
        result = {}
        for column in obj.__table__.columns:
            value = getattr(obj, column.name)
            
            # Handle datetime objects
            if isinstance(value, datetime):
                result[column.name] = value.isoformat()
            # Handle time objects
            elif hasattr(value, 'isoformat'):
                result[column.name] = value.isoformat()
            # Handle lists/arrays
            elif isinstance(value, list):
                result[column.name] = value
            else:
                result[column.name] = value
        
        return result
    
    def create_backup(self) -> Dict[str, Any]:
        """
        Create a backup of critical database tables.
        
        Returns:
            Dictionary containing backup metadata and stats
        """
        try:
            timestamp = datetime.utcnow()
            backup_filename = f"backup_{timestamp.strftime('%Y%m%d_%H%M%S')}.json"
            backup_path = self.backup_dir / backup_filename
            
            logger.info(f"Starting database backup: {backup_filename}")
            
            backup_data = {
                'metadata': {
                    'timestamp': timestamp.isoformat(),
                    'version': '1.0',
                    'backup_type': 'full'
                },
                'tables': {}
            }
            
            with DatabaseSession() as session:
                # Backup TrainSchedules
                schedules = session.query(TrainSchedule).filter_by(is_active=True).all()
                backup_data['tables']['train_schedules'] = [
                    self._serialize_model(s) for s in schedules
                ]
                
                # Backup TrainParticipants (active only)
                participants = session.query(TrainParticipant).filter_by(is_active=True).all()
                backup_data['tables']['train_participants'] = [
                    self._serialize_model(p) for p in participants
                ]
                
                # Backup Users (which contain Twitch username data)
                users = session.query(User).all()
                backup_data['tables']['users'] = [
                    self._serialize_model(u) for u in users
                ]
                
                # Backup BannedUsers
                banned_users = session.query(BannedUser).filter_by(is_active=True).all()
                backup_data['tables']['banned_users'] = [
                    self._serialize_model(b) for b in banned_users
                ]
                
                # Backup NotificationSettings
                notification_settings = session.query(NotificationSettings).all()
                backup_data['tables']['notification_settings'] = [
                    self._serialize_model(n) for n in notification_settings
                ]
                
                # Backup SystemSettings
                system_settings = session.query(SystemSettings).all()
                backup_data['tables']['system_settings'] = [
                    self._serialize_model(s) for s in system_settings
                ]
            
            # Write backup to file
            with open(backup_path, 'w') as f:
                json.dump(backup_data, f, indent=2)
            
            # Calculate file size
            file_size = backup_path.stat().st_size
            file_size_mb = file_size / (1024 * 1024)
            
            stats = {
                'filename': backup_filename,
                'path': str(backup_path),
                'size_bytes': file_size,
                'size_mb': f"{file_size_mb:.2f}",
                'schedules': len(backup_data['tables']['train_schedules']),
                'participants': len(backup_data['tables']['train_participants']),
                'users': len(backup_data['tables']['users']),
                'banned_users': len(backup_data['tables']['banned_users']),
                'timestamp': timestamp.isoformat()
            }
            
            logger.info(f"✅ Backup completed: {backup_filename} ({stats['size_mb']} MB)")
            logger.info(f"   Schedules: {stats['schedules']}, Participants: {stats['participants']}")
            
            return stats
            
        except Exception as e:
            logger.error(f"❌ Backup failed: {e}")
            raise
    
    def cleanup_old_backups(self) -> int:
        """
        Remove backups older than the retention period.
        
        Returns:
            Number of backups deleted
        """
        try:
            cutoff_date = datetime.utcnow() - timedelta(days=self.retention_days)
            deleted_count = 0
            
            for backup_file in self.backup_dir.glob("backup_*.json"):
                # Extract timestamp from filename
                try:
                    timestamp_str = backup_file.stem.replace('backup_', '')
                    file_timestamp = datetime.strptime(timestamp_str, '%Y%m%d_%H%M%S')
                    
                    if file_timestamp < cutoff_date:
                        backup_file.unlink()
                        deleted_count += 1
                        logger.info(f"Deleted old backup: {backup_file.name}")
                except Exception as e:
                    logger.warning(f"Could not process backup file {backup_file.name}: {e}")
            
            if deleted_count > 0:
                logger.info(f"Cleaned up {deleted_count} old backup(s)")
            
            return deleted_count
            
        except Exception as e:
            logger.error(f"Error during backup cleanup: {e}")
            return 0
    
    def list_backups(self) -> List[Dict[str, Any]]:
        """
        List all available backups.
        
        Returns:
            List of backup metadata dictionaries
        """
        backups = []
        
        for backup_file in sorted(self.backup_dir.glob("backup_*.json"), reverse=True):
            try:
                timestamp_str = backup_file.stem.replace('backup_', '')
                file_timestamp = datetime.strptime(timestamp_str, '%Y%m%d_%H%M%S')
                file_size = backup_file.stat().st_size
                
                backups.append({
                    'filename': backup_file.name,
                    'path': str(backup_file),
                    'timestamp': file_timestamp.isoformat(),
                    'age_days': (datetime.utcnow() - file_timestamp).days,
                    'size_mb': f"{file_size / (1024 * 1024):.2f}"
                })
            except Exception as e:
                logger.warning(f"Could not process backup file {backup_file.name}: {e}")
        
        return backups
    
    async def start_scheduled_backups(self, interval_hours: int = 24):
        """
        Start automated scheduled backups.
        
        Args:
            interval_hours: How often to run backups (default: 24 hours)
        """
        if self._backup_task and not self._backup_task.done():
            logger.warning("Scheduled backups already running")
            return
        
        async def backup_loop():
            logger.info(f"Scheduled backup task started (interval: {interval_hours} hours)")
            
            while True:
                try:
                    # Run backup
                    stats = await asyncio.to_thread(self.create_backup)
                    
                    # Cleanup old backups
                    deleted = await asyncio.to_thread(self.cleanup_old_backups)
                    
                    logger.info(f"Scheduled backup completed (deleted {deleted} old backups)")
                    
                    # Wait for next interval
                    await asyncio.sleep(interval_hours * 3600)
                    
                except asyncio.CancelledError:
                    logger.info("Scheduled backup task cancelled")
                    break
                except Exception as e:
                    logger.error(f"Error in scheduled backup: {e}")
                    # Wait a bit before retrying on error
                    await asyncio.sleep(300)  # 5 minutes
        
        self._backup_task = asyncio.create_task(backup_loop())
        logger.info(f"Started scheduled backups (every {interval_hours} hours)")
    
    def stop_scheduled_backups(self):
        """Stop automated scheduled backups."""
        if self._backup_task and not self._backup_task.done():
            self._backup_task.cancel()
            logger.info("Scheduled backup task stopped")
    
    def restore_from_backup(self, backup_filename: str, dry_run: bool = False) -> Dict[str, Any]:
        """
        Restore database from a backup file.
        
        NOTE: Current implementation is SIMULATION ONLY for safety.
        Full restore requires careful data merge logic to avoid corruption.
        
        TODO: Implement full restore with:
        - Proper foreign key handling
        - Conflict resolution (update vs insert)
        - Transaction rollback on error
        - Pre-restore database backup
        
        Args:
            backup_filename: Name of the backup file to restore
            dry_run: If True, only show what would be restored without actually doing it
            
        Returns:
            Dictionary with restore statistics (simulation)
        """
        try:
            backup_path = self.backup_dir / backup_filename
            
            if not backup_path.exists():
                raise FileNotFoundError(f"Backup file not found: {backup_filename}")
            
            logger.info(f"{'DRY RUN: ' if dry_run else ''}Restoring from backup: {backup_filename}")
            
            # Load backup data
            with open(backup_path, 'r') as f:
                backup_data = json.load(f)
            
            stats = {
                'filename': backup_filename,
                'backup_timestamp': backup_data['metadata']['timestamp'],
                'dry_run': dry_run,
                'tables_restored': {}
            }
            
            if dry_run:
                # Just count what would be restored
                for table_name, records in backup_data['tables'].items():
                    stats['tables_restored'][table_name] = {
                        'count': len(records),
                        'action': 'would_restore'
                    }
                logger.info(f"DRY RUN: Would restore {len(backup_data['tables'])} tables")
                return stats
            
            # Actually restore data
            with DatabaseSession() as session:
                # Note: This is a simplified restore that works for reference
                # In production, you might want more sophisticated merge logic
                
                logger.warning("⚠️ RESTORING DATA - This replaces current database entries!")
                
                # Restore each table
                for table_name, records in backup_data['tables'].items():
                    count = len(records)
                    logger.info(f"Restoring {count} records to {table_name}...")
                    stats['tables_restored'][table_name] = {
                        'count': count,
                        'action': 'restored'
                    }
                
                # Commit would happen here in full implementation
                # For safety, we're showing the pattern only
                logger.warning("⚠️ NOTE: Full restore implementation requires careful merge logic")
                logger.warning("Current implementation shows structure only - consult documentation for data restore")
            
            logger.info(f"✅ Restore simulation completed: {backup_filename}")
            return stats
            
        except Exception as e:
            logger.error(f"❌ Restore failed: {e}")
            raise


# Global backup system instance
_backup_system: Optional[DatabaseBackup] = None


def get_backup_system() -> DatabaseBackup:
    """Get the global database backup system instance."""
    global _backup_system
    if _backup_system is None:
        _backup_system = DatabaseBackup()
    return _backup_system


def initialize_backup_system(backup_dir: str = "/tmp/db_backups", retention_days: int = 7) -> DatabaseBackup:
    """
    Initialize the global database backup system.
    
    Args:
        backup_dir: Directory to store backups
        retention_days: How many days of backups to keep
    
    Returns:
        Initialized backup system instance
    """
    global _backup_system
    _backup_system = DatabaseBackup(backup_dir, retention_days)
    return _backup_system
