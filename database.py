"""
Database connection and session management.
"""

import os
import time
import asyncio
from contextlib import contextmanager
from typing import Generator
from sqlalchemy import create_engine, exc
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool
from models import Base
import logging

logger = logging.getLogger(__name__)

NEON_SLEEP_ERRORS = ["endpoint has been disabled", "endpoint is disabled", "the database system is starting up"]

def _is_neon_sleep_error(error):
    error_str = str(error).lower()
    return any(msg in error_str for msg in NEON_SLEEP_ERRORS)

class DatabaseManager:
    """Manages database connections and sessions."""
    
    def __init__(self):
        self.database_url = os.getenv('DATABASE_URL')
        if not self.database_url:
            raise ValueError("DATABASE_URL environment variable is required")
        
        self._create_engine()
        self._create_tables_with_retry()
    
    def _create_engine(self):
        self.engine = create_engine(
            self.database_url,
            pool_size=10,
            max_overflow=20,
            pool_pre_ping=True,
            pool_recycle=300,
            pool_reset_on_return='commit',
            echo=False,
            connect_args={
                "application_name": "discord_bot",
                "options": "-c statement_timeout=10000",
                "connect_timeout": 30,
            }
        )
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
    
    # Additive columns that create_all() cannot add to already-existing tables.
    # PostgreSQL ADD COLUMN IF NOT EXISTS makes this safe and idempotent.
    _ADDITIVE_COLUMNS = [
        ("server_feature_permissions", "reminders_enabled", "BOOLEAN DEFAULT FALSE"),
        ("server_feature_permissions", "waitlist_enabled", "BOOLEAN DEFAULT FALSE"),
        ("server_feature_permissions", "stats_enabled", "BOOLEAN DEFAULT FALSE"),
        ("server_feature_permissions", "self_signup_enabled", "BOOLEAN DEFAULT FALSE"),
        ("train_participants", "is_waitlisted", "BOOLEAN DEFAULT FALSE"),
        ("train_participants", "waitlist_position", "INTEGER"),
        ("train_participants", "offer_sent_at", "TIMESTAMP"),
    ]

    def _ensure_additive_columns(self):
        """Add new nullable/defaulted columns to existing tables (idempotent)."""
        from sqlalchemy import text, inspect as sa_inspect
        try:
            with self.engine.begin() as conn:
                for table, column, coldef in self._ADDITIVE_COLUMNS:
                    conn.execute(text(
                        f'ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {coldef}'
                    ))
            logger.info("Additive column check complete")
        except Exception as e:
            # Non-fatal: code reads beta columns with safe getattr defaults
            logger.warning(f"Could not ensure additive columns (continuing): {e}")

        # Verify the columns actually exist now. The beta features that own these
        # columns are OFF by default, but the ORM still references them in queries,
        # so a missing column would be a real (not silent) problem. Log loudly.
        try:
            inspector = sa_inspect(self.engine)
            missing = []
            for table, column, _ in self._ADDITIVE_COLUMNS:
                try:
                    existing = {c["name"] for c in inspector.get_columns(table)}
                except Exception:
                    existing = set()
                if column not in existing:
                    missing.append(f"{table}.{column}")
            if missing:
                logger.error(
                    "Additive columns still MISSING after migration attempt: "
                    f"{', '.join(missing)}. Beta features depending on these may fail "
                    "until the schema is fixed (republish required for prod)."
                )
        except Exception as e:
            logger.warning(f"Could not verify additive columns: {e}")

    def check_additive_columns(self):
        """Return the list of beta-schema columns missing from the live DB.

        Reuses _ADDITIVE_COLUMNS (the same list the migration applies) so the
        health report can never drift from the migration. Returns an empty list
        when the schema is healthy. Reports column *presence*, not feature
        enablement, so it never false-alarms on features that are simply off.
        """
        from sqlalchemy import inspect as sa_inspect
        inspector = sa_inspect(self.engine)
        missing = []
        for table, column, _ in self._ADDITIVE_COLUMNS:
            try:
                existing = {c["name"] for c in inspector.get_columns(table)}
            except Exception:
                existing = set()
            if column not in existing:
                missing.append(f"{table}.{column}")
        return missing

    # Unique constraints that create_all() will NOT add to a table that already
    # exists without them. PostgreSQL has no "ADD CONSTRAINT IF NOT EXISTS", so
    # each entry is applied via a guarded DO block (idempotent).
    # (table, constraint_name, "(col, col, ...)")
    _ENSURE_UNIQUE_CONSTRAINTS = [
        (
            "train_reminder_sent",
            "uq_reminder_sent_occurrence",
            "(schedule_id, user_id, occurrence_date)",
        ),
    ]

    def _ensure_unique_constraints(self):
        """Ensure unique constraints exist on already-existing tables (idempotent).

        create_all() only adds a constraint when it creates the table. A table
        created before the constraint was defined keeps no DB-level guarantee, so
        we add it here. Any pre-existing duplicate rows are removed first (keeping
        the lowest id per group) so the ADD CONSTRAINT cannot fail.
        """
        from sqlalchemy import text, inspect as sa_inspect
        for table, name, cols in self._ENSURE_UNIQUE_CONSTRAINTS:
            try:
                col_list = cols.strip("() ")
                with self.engine.begin() as conn:
                    # Remove duplicates that would block the unique constraint.
                    conn.execute(text(
                        f"DELETE FROM {table} a USING {table} b "
                        f"WHERE a.id > b.id "
                        f"AND ({', '.join('a.' + c.strip() for c in col_list.split(','))}) "
                        f"= ({', '.join('b.' + c.strip() for c in col_list.split(','))})"
                    ))
                    # Add the constraint only if it is not already present.
                    conn.execute(text(
                        "DO $$\nBEGIN\n"
                        f"  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = '{name}') THEN\n"
                        f"    ALTER TABLE {table} ADD CONSTRAINT {name} UNIQUE {cols};\n"
                        "  END IF;\nEND $$;"
                    ))
                logger.info(f"Unique constraint check complete: {name} on {table}")
            except Exception as e:
                # Non-fatal: the app-level dedup check still prevents duplicate sends.
                logger.warning(f"Could not ensure unique constraint {name} on {table} (continuing): {e}")

        # Verify the constraints actually exist now. Log loudly if not, since the
        # DB-level dedup guarantee is the point of this migration.
        try:
            inspector = sa_inspect(self.engine)
            missing = []
            for table, name, _ in self._ENSURE_UNIQUE_CONSTRAINTS:
                try:
                    existing = {uc.get("name") for uc in inspector.get_unique_constraints(table)}
                except Exception:
                    existing = set()
                if name not in existing:
                    missing.append(f"{table}.{name}")
            if missing:
                logger.error(
                    "Unique constraints still MISSING after migration attempt: "
                    f"{', '.join(missing)}. Duplicate-send protection is relying on the "
                    "app-level check only (republish required for prod)."
                )
        except Exception as e:
            logger.warning(f"Could not verify unique constraints: {e}")

    # Indexes that CREATE TABLE won't add to already-existing tables.
    # Each entry: (index_name, table_name, "col1, col2, ...")
    _ENSURE_INDEXES = [
        ("idx_train_schedules_active_recurring", "train_schedules",    "is_active, schedule_type"),
        ("idx_train_participants_slot_active",   "train_participants",  "schedule_id, is_active"),
        ("idx_train_participants_slot_waitlist", "train_participants",  "schedule_id, is_waitlisted, waitlist_position"),
    ]

    def _ensure_indexes(self):
        """Create performance indexes on already-existing tables (idempotent).

        create_all() only adds an index when it creates the table; a table that
        already existed keeps no index. CREATE INDEX IF NOT EXISTS is safe to
        run on every startup — Postgres skips it silently when it already exists.
        """
        from sqlalchemy import text
        for idx_name, table, cols in self._ENSURE_INDEXES:
            try:
                with self.engine.begin() as conn:
                    conn.execute(text(
                        f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table} ({cols})"
                    ))
                logger.info(f"Index check complete: {idx_name} on {table}")
            except Exception as e:
                logger.warning(f"Could not ensure index {idx_name} on {table} (continuing): {e}")

    def _create_tables_with_retry(self):
        for attempt in range(6):
            try:
                Base.metadata.create_all(bind=self.engine)
                logger.info("Database tables created successfully")
                self._ensure_additive_columns()
                self._ensure_unique_constraints()
                self._ensure_indexes()
                return
            except Exception as e:
                if _is_neon_sleep_error(e) and attempt < 5:
                    wait = min(5 * (attempt + 1), 30)
                    logger.warning(f"Database endpoint sleeping, retrying in {wait}s (attempt {attempt+1}/6)...")
                    time.sleep(wait)
                    self.engine.dispose()
                    self._create_engine()
                else:
                    logger.error(f"Failed to create database tables: {e}")
                    raise
    
    def _reconnect_engine(self):
        logger.info("Disposing stale connections and reconnecting to database...")
        try:
            self.engine.dispose()
        except Exception:
            pass
        self._create_engine()
    
    def get_session(self) -> Session:
        """Get a new database session."""
        return self.SessionLocal()
    
    def close_session(self, session: Session):
        """Close a database session."""
        try:
            session.close()
        except Exception as e:
            logger.error(f"Error closing database session: {e}")
    
    @contextmanager
    def get_robust_session(self, max_retries: int = 5) -> Generator[Session, None, None]:
        """Get a database session with automatic retry logic including Neon wake-up."""
        session = None
        retry_count = 0
        
        while retry_count <= max_retries:
            try:
                session = self.get_session()
                yield session
                session.commit()
                break
            except (exc.DisconnectionError, exc.TimeoutError, exc.InterfaceError, exc.OperationalError) as e:
                retry_count += 1
                
                if session:
                    try:
                        session.rollback()
                        session.close()
                    except:
                        pass
                    session = None
                
                is_neon_sleep = _is_neon_sleep_error(e)
                if is_neon_sleep:
                    wait = min(10 * retry_count, 30)
                    logger.warning(f"Database endpoint sleeping, waiting {wait}s for wake-up (attempt {retry_count}/{max_retries + 1})...")
                    time.sleep(wait)
                    self._reconnect_engine()
                elif retry_count <= max_retries:
                    wait = min(2 ** retry_count, 10)
                    logger.warning(f"Database connection error (attempt {retry_count}/{max_retries + 1}): {e}")
                    time.sleep(wait)
                
                if retry_count > max_retries:
                    logger.error(f"Failed to establish database connection after {max_retries + 1} attempts")
                    raise
            except Exception as e:
                if session:
                    try:
                        session.rollback()
                    except:
                        pass
                if _is_neon_sleep_error(e):
                    retry_count += 1
                    if session:
                        try:
                            session.close()
                        except:
                            pass
                        session = None
                    if retry_count <= max_retries:
                        wait = min(10 * retry_count, 30)
                        logger.warning(f"Database endpoint sleeping (generic), waiting {wait}s (attempt {retry_count}/{max_retries + 1})...")
                        time.sleep(wait)
                        self._reconnect_engine()
                        continue
                logger.error(f"Database error: {e}")
                raise
            finally:
                if session:
                    try:
                        session.close()
                    except:
                        pass

# Global database manager instance
db_manager = None

def get_db_manager() -> DatabaseManager:
    """Get the global database manager instance."""
    global db_manager
    if db_manager is None:
        db_manager = DatabaseManager()
    return db_manager

def get_db_session() -> Session:
    """Get a database session."""
    return get_db_manager().get_session()

@contextmanager
def get_robust_db_session(max_retries: int = 3) -> Generator[Session, None, None]:
    """Get a robust database session with automatic retry logic."""
    with get_db_manager().get_robust_session(max_retries=max_retries) as session:
        yield session

# Context manager for database sessions (Legacy - use get_robust_db_session instead)
class DatabaseSession:
    """Context manager for database sessions with enhanced error handling."""
    
    def __init__(self, max_retries: int = 3):
        self.session = None
        self.max_retries = max_retries
        self.retry_count = 0
    
    def __enter__(self) -> Session:
        while self.retry_count <= self.max_retries:
            try:
                self.session = get_db_session()
                return self.session
            except (exc.DisconnectionError, exc.TimeoutError, exc.InterfaceError, exc.OperationalError) as e:
                self.retry_count += 1
                is_neon_sleep = _is_neon_sleep_error(e)
                
                if is_neon_sleep:
                    wait = min(10 * self.retry_count, 30)
                    logger.warning(f"Database endpoint sleeping, waiting {wait}s (attempt {self.retry_count}/{self.max_retries + 1})...")
                    time.sleep(wait)
                    mgr = get_db_manager()
                    mgr._reconnect_engine()
                elif self.retry_count <= self.max_retries:
                    logger.warning(f"Database connection error (attempt {self.retry_count}/{self.max_retries + 1}): {e}")
                    time.sleep(min(2 ** self.retry_count, 5))
                
                if self.retry_count > self.max_retries:
                    logger.error(f"Failed to create database session after {self.max_retries + 1} attempts")
                    raise
            except Exception as e:
                if _is_neon_sleep_error(e):
                    self.retry_count += 1
                    if self.retry_count <= self.max_retries:
                        wait = min(10 * self.retry_count, 30)
                        logger.warning(f"Database endpoint sleeping (generic), waiting {wait}s (attempt {self.retry_count}/{self.max_retries + 1})...")
                        time.sleep(wait)
                        mgr = get_db_manager()
                        mgr._reconnect_engine()
                        continue
                raise
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            try:
                if exc_type:
                    self.session.rollback()
                else:
                    self.session.commit()
            except Exception as e:
                logger.error(f"Error during session cleanup: {e}")
                try:
                    self.session.rollback()
                except:
                    pass
            finally:
                try:
                    self.session.close()
                except:
                    pass
                self.session = None


async def run_db(func, *args, max_retries: int = 3, **kwargs):
    """Run a fully synchronous database operation off the event loop.

    Unlike AsyncDatabaseSession (which only makes *retry waits* non-blocking
    while the actual session creation/query/commit/close still run inline on
    the event loop thread), this executes the ENTIRE operation - including
    connecting, querying, committing, closing, and any retry sleeps - inside
    a background thread via loop.run_in_executor(). That is the only way to
    guarantee the Discord gateway heartbeat is never blocked by a slow or
    sleeping Postgres/Neon endpoint.

    `func(session, *args, **kwargs)` must be a plain synchronous function.
    It should not call session.commit()/rollback()/close() itself - this
    wrapper manages the session lifecycle. Return whatever `func` returns;
    if you need ORM objects to survive after the session closes, load their
    attributes and call session.expunge(obj) inside `func`.
    """
    loop = asyncio.get_event_loop()

    def _worker():
        retry_count = 0
        while True:
            session = None
            try:
                session = get_db_session()
                result = func(session, *args, **kwargs)
                session.commit()
                return result
            except (exc.DisconnectionError, exc.TimeoutError, exc.InterfaceError, exc.OperationalError) as e:
                if session:
                    try:
                        session.rollback()
                    except Exception:
                        pass
                retry_count += 1
                is_neon_sleep = _is_neon_sleep_error(e)
                if retry_count > max_retries:
                    logger.error(f"Failed sync DB operation after {max_retries + 1} attempts")
                    raise
                if is_neon_sleep:
                    wait = min(10 * retry_count, 30)
                    logger.warning(f"Database endpoint sleeping (bg thread), waiting {wait}s (attempt {retry_count}/{max_retries + 1})...")
                    time.sleep(wait)
                    get_db_manager()._reconnect_engine()
                else:
                    wait = min(2 ** retry_count, 5)
                    logger.warning(f"Database connection error (bg thread) (attempt {retry_count}/{max_retries + 1}): {e}")
                    time.sleep(wait)
            except Exception as e:
                if session:
                    try:
                        session.rollback()
                    except Exception:
                        pass
                if _is_neon_sleep_error(e):
                    retry_count += 1
                    if retry_count <= max_retries:
                        wait = min(10 * retry_count, 30)
                        logger.warning(f"Database endpoint sleeping (bg thread, generic), waiting {wait}s (attempt {retry_count}/{max_retries + 1})...")
                        time.sleep(wait)
                        get_db_manager()._reconnect_engine()
                        continue
                raise
            finally:
                if session:
                    try:
                        session.close()
                    except Exception:
                        pass

    return await loop.run_in_executor(None, _worker)


class AsyncDatabaseSession:
    """Async-safe database session context manager.

    Identical to DatabaseSession but uses asyncio.sleep() instead of
    time.sleep() during Neon wake-up retries, so it never blocks the
    asyncio event loop.  Use with `async with AsyncDatabaseSession()`.
    """

    def __init__(self, max_retries: int = 3):
        self.session = None
        self.max_retries = max_retries

    async def __aenter__(self) -> Session:
        retry_count = 0
        while retry_count <= self.max_retries:
            try:
                self.session = get_db_session()
                return self.session
            except (exc.DisconnectionError, exc.TimeoutError, exc.InterfaceError, exc.OperationalError) as e:
                retry_count += 1
                is_neon_sleep = _is_neon_sleep_error(e)
                if is_neon_sleep:
                    wait = min(10 * retry_count, 30)
                    logger.warning(f"Database endpoint sleeping, waiting {wait}s async (attempt {retry_count}/{self.max_retries + 1})...")
                    await asyncio.sleep(wait)
                    get_db_manager()._reconnect_engine()
                elif retry_count <= self.max_retries:
                    wait = min(2 ** retry_count, 5)
                    logger.warning(f"Database connection error async (attempt {retry_count}/{self.max_retries + 1}): {e}")
                    await asyncio.sleep(wait)
                if retry_count > self.max_retries:
                    logger.error(f"Failed to create async database session after {self.max_retries + 1} attempts")
                    raise
            except Exception as e:
                if _is_neon_sleep_error(e):
                    retry_count += 1
                    if retry_count <= self.max_retries:
                        wait = min(10 * retry_count, 30)
                        logger.warning(f"Database endpoint sleeping (generic) async, waiting {wait}s (attempt {retry_count}/{self.max_retries + 1})...")
                        await asyncio.sleep(wait)
                        get_db_manager()._reconnect_engine()
                        continue
                raise

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            try:
                if exc_type:
                    self.session.rollback()
                else:
                    self.session.commit()
            except Exception as e:
                logger.error(f"Error during async session cleanup: {e}")
                try:
                    self.session.rollback()
                except:
                    pass
            finally:
                try:
                    self.session.close()
                except:
                    pass
                self.session = None
        return False