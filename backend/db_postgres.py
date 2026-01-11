# PostgreSQL Database Module for GEX Dashboard
import os
import asyncpg
import aiosqlite
import uuid
from typing import Optional, List, Any
from datetime import datetime, timezone, timedelta, date
from contextlib import asynccontextmanager

# Database URL from environment (Render provides this)
DATABASE_URL = os.environ.get("DATABASE_URL", "")

# Connection pool (PostgreSQL)
_pool: Optional[asyncpg.Pool] = None


def to_uuid(user_id: str):
    """Convert string user_id to UUID for PostgreSQL queries"""
    if isinstance(user_id, str):
        return uuid.UUID(user_id)
    return user_id

# SQLite fallback for AI tracking when PostgreSQL not available
SQLITE_AI_DB = os.path.join(os.path.dirname(__file__), "ai_tracking.db")
_sqlite_initialized = False


async def init_sqlite_ai_tables():
    """Initialize SQLite tables for AI tracking (fallback when no PostgreSQL)"""
    global _sqlite_initialized
    if _sqlite_initialized:
        return True

    try:
        async with aiosqlite.connect(SQLITE_AI_DB) as db:
            # Users table (complete for SQLite auth)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    email TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    email_verified INTEGER DEFAULT 0,
                    email_verification_token TEXT,
                    email_verification_expires TEXT,
                    is_approved INTEGER DEFAULT 0,
                    is_admin INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    last_login TEXT,
                    failed_login_attempts INTEGER DEFAULT 0,
                    locked_until TEXT,
                    password_reset_token TEXT,
                    password_reset_expires TEXT
                )
            """)

            # Refresh tokens table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS refresh_tokens (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    token_hash TEXT UNIQUE NOT NULL,
                    expires_at TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    revoked INTEGER DEFAULT 0
                )
            """)

            # AI Chat History
            await db.execute("""
                CREATE TABLE IF NOT EXISTS ai_chat_history (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    tokens_used INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # User Token Limits
            await db.execute("""
                CREATE TABLE IF NOT EXISTS user_token_limits (
                    user_id TEXT PRIMARY KEY,
                    monthly_token_limit INTEGER DEFAULT 500000,
                    tokens_used_this_month INTEGER DEFAULT 0,
                    month_start TEXT DEFAULT CURRENT_DATE,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # AI Usage Log
            await db.execute("""
                CREATE TABLE IF NOT EXISTS ai_usage_log (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    endpoint TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    input_tokens INTEGER NOT NULL,
                    output_tokens INTEGER NOT NULL,
                    total_tokens INTEGER NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Create indexes
            await db.execute("CREATE INDEX IF NOT EXISTS idx_chat_user ON ai_chat_history(user_id)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_chat_symbol ON ai_chat_history(user_id, symbol)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_usage_user ON ai_usage_log(user_id)")

            await db.commit()
            _sqlite_initialized = True
            print("[DB] SQLite AI tables initialized")
            return True
    except Exception as e:
        print(f"[DB] SQLite init error: {e}")
        return False


async def init_db():
    """Initialize database connection pool and create tables"""
    global _pool

    if not DATABASE_URL:
        print("[DB] DATABASE_URL not set - using SQLite fallback")
        # Initialize SQLite for AI tracking
        await init_sqlite_ai_tables()
        return False

    try:
        # Create connection pool
        _pool = await asyncpg.create_pool(
            DATABASE_URL,
            min_size=2,
            max_size=10,
            command_timeout=60,
            # Handle Render's SSL requirements
            ssl='require' if 'render.com' in DATABASE_URL else None
        )

        # Create tables
        await create_tables()
        print("[DB] PostgreSQL connected and tables created")
        return True

    except Exception as e:
        print(f"[DB] PostgreSQL connection failed: {e}")
        return False


async def close_db():
    """Close database connection pool"""
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


async def create_tables():
    """Create all required tables"""
    if not _pool:
        return

    async with _pool.acquire() as conn:
        # Users table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                email VARCHAR(255) UNIQUE NOT NULL,
                password_hash VARCHAR(255) NOT NULL,
                email_verified BOOLEAN DEFAULT FALSE,
                email_verification_token VARCHAR(255),
                email_verification_expires TIMESTAMPTZ,
                is_approved BOOLEAN DEFAULT FALSE,
                is_admin BOOLEAN DEFAULT FALSE,
                ai_enabled BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                last_login TIMESTAMPTZ,
                failed_login_attempts INTEGER DEFAULT 0,
                locked_until TIMESTAMPTZ,
                password_reset_token VARCHAR(255),
                password_reset_expires TIMESTAMPTZ
            )
        """)

        # Add ai_enabled column if it doesn't exist (migration)
        try:
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS ai_enabled BOOLEAN DEFAULT FALSE")
        except Exception:
            pass  # Column already exists

        # User symbols table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS user_symbols (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                symbol VARCHAR(10) NOT NULL,
                display_order INTEGER DEFAULT 0,
                added_at TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE(user_id, symbol)
            )
        """)

        # User preferences table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS user_preferences (
                user_id UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                theme VARCHAR(10) DEFAULT 'dark',
                current_symbol VARCHAR(10) DEFAULT 'SPX',
                refresh_interval INTEGER DEFAULT 1,
                current_view VARCHAR(10) DEFAULT 'gex',
                expiration_mode VARCHAR(10) DEFAULT 'all',
                view_mode VARCHAR(10) DEFAULT 'single',
                trinity_symbols JSONB DEFAULT '["SPY", "QQQ", "IWM"]',
                trend_filter VARCHAR(20) DEFAULT 'all',
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        # Refresh tokens table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS refresh_tokens (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                token_hash VARCHAR(255) NOT NULL,
                expires_at TIMESTAMPTZ NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                revoked BOOLEAN DEFAULT FALSE
            )
        """)

        # WAVE indicator data (cumulative call/put flow over time)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS wave_data (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                symbol VARCHAR(10) NOT NULL,
                timestamp TIMESTAMPTZ NOT NULL,
                cumulative_call DECIMAL(18,2) NOT NULL DEFAULT 0,
                cumulative_put DECIMAL(18,2) NOT NULL DEFAULT 0,
                wave_value DECIMAL(18,2) NOT NULL DEFAULT 0,
                call_premium DECIMAL(18,2) DEFAULT 0,
                put_premium DECIMAL(18,2) DEFAULT 0,
                UNIQUE(symbol, timestamp)
            )
        """)

        # Large trades for trade tape
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS flow_trades (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                symbol VARCHAR(10) NOT NULL,
                strike DECIMAL(10,2) NOT NULL,
                expiration DATE NOT NULL,
                contract_type VARCHAR(4) NOT NULL,
                trade_type VARCHAR(10) NOT NULL,
                size INTEGER NOT NULL,
                premium DECIMAL(14,2) NOT NULL,
                sentiment VARCHAR(10),
                timestamp TIMESTAMPTZ NOT NULL
            )
        """)

        # Flow leaderboard cache
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS flow_leaderboard (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                symbol VARCHAR(10) NOT NULL,
                timestamp TIMESTAMPTZ NOT NULL,
                total_premium DECIMAL(18,2) NOT NULL,
                net_premium DECIMAL(18,2) NOT NULL,
                call_premium DECIMAL(18,2) DEFAULT 0,
                put_premium DECIMAL(18,2) DEFAULT 0,
                unusual_score DECIMAL(10,2),
                sentiment VARCHAR(10),
                rank INTEGER,
                UNIQUE(symbol, timestamp)
            )
        """)

        # Create indexes
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_users_approval ON users(is_approved)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_user_symbols_user ON user_symbols(user_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_refresh_tokens_user ON refresh_tokens(user_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_refresh_tokens_hash ON refresh_tokens(token_hash)")

        # Flow feature indexes
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_wave_symbol_time ON wave_data(symbol, timestamp DESC)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_symbol_time ON flow_trades(symbol, timestamp DESC)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_premium ON flow_trades(premium DESC)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_leaderboard_rank ON flow_leaderboard(timestamp DESC, rank)")

    # Create AI tables (chat history, token limits, usage log)
    await create_ai_tables()


def get_pool() -> Optional[asyncpg.Pool]:
    """Get the database connection pool"""
    return _pool


@asynccontextmanager
async def get_connection():
    """Context manager for database connections"""
    if not _pool:
        raise RuntimeError("Database not initialized")
    async with _pool.acquire() as conn:
        yield conn


# ============== User Operations ==============

async def create_user(
    email: str,
    password_hash: str,
    verification_token: str,
    is_admin: bool = False
) -> Optional[str]:
    """Create a new user, returns user ID"""
    expires = datetime.now(timezone.utc) + timedelta(hours=24)

    # PostgreSQL
    if _pool:
        try:
            async with _pool.acquire() as conn:
                result = await conn.fetchrow("""
                    INSERT INTO users (email, password_hash, email_verification_token,
                                       email_verification_expires, is_admin, is_approved)
                    VALUES ($1, $2, $3, $4, $5, $5)
                    RETURNING id
                """, email, password_hash, verification_token, expires, is_admin)
                return str(result['id'])
        except asyncpg.UniqueViolationError:
            return None
        except Exception as e:
            print(f"[DB] Error creating user: {e}")
            return None

    # SQLite fallback
    try:
        user_id = str(uuid.uuid4())
        async with aiosqlite.connect(SQLITE_AI_DB) as db:
            await db.execute("""
                INSERT INTO users (id, email, password_hash, email_verification_token,
                                   email_verification_expires, is_admin, is_approved)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (user_id, email, password_hash, verification_token, expires.isoformat(),
                  1 if is_admin else 0, 1 if is_admin else 0))
            await db.commit()
            return user_id
    except Exception as e:
        print(f"[DB SQLite] Error creating user: {e}")
        return None


async def get_user_by_email(email: str) -> Optional[dict]:
    """Get user by email"""
    # PostgreSQL
    if _pool:
        async with _pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT id, email, password_hash, email_verified, email_verification_token,
                       email_verification_expires, is_approved, is_admin, created_at,
                       last_login, failed_login_attempts, locked_until,
                       password_reset_token, password_reset_expires
                FROM users WHERE email = $1
            """, email)
            if row:
                return dict(row)
            return None

    # SQLite fallback
    try:
        async with aiosqlite.connect(SQLITE_AI_DB) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("""
                SELECT id, email, password_hash, email_verified, email_verification_token,
                       email_verification_expires, is_approved, is_admin, created_at,
                       last_login, failed_login_attempts, locked_until,
                       password_reset_token, password_reset_expires
                FROM users WHERE email = ?
            """, (email,))
            row = await cursor.fetchone()
            if row:
                return {
                    'id': row['id'],
                    'email': row['email'],
                    'password_hash': row['password_hash'],
                    'email_verified': bool(row['email_verified']),
                    'email_verification_token': row['email_verification_token'],
                    'email_verification_expires': row['email_verification_expires'],
                    'is_approved': bool(row['is_approved']),
                    'is_admin': bool(row['is_admin']),
                    'created_at': row['created_at'],
                    'last_login': row['last_login'],
                    'failed_login_attempts': row['failed_login_attempts'] or 0,
                    'locked_until': row['locked_until'],
                    'password_reset_token': row['password_reset_token'],
                    'password_reset_expires': row['password_reset_expires']
                }
    except Exception as e:
        print(f"[DB SQLite] Error getting user by email: {e}")
    return None


async def get_user_by_id(user_id: str) -> Optional[dict]:
    """Get user by ID"""
    # PostgreSQL
    if _pool:
        try:
            async with _pool.acquire() as conn:
                row = await conn.fetchrow("""
                    SELECT id, email, password_hash, email_verified, is_approved, is_admin,
                           COALESCE(ai_enabled, FALSE) as ai_enabled,
                           created_at, last_login, failed_login_attempts, locked_until
                    FROM users WHERE id = $1
                """, to_uuid(user_id))
                if row:
                    return dict(row)
                return None
        except Exception as e:
            print(f"[DB] Error in get_user_by_id: {e}")
            return None

    # SQLite fallback
    try:
        async with aiosqlite.connect(SQLITE_AI_DB) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("""
                SELECT id, email, password_hash, email_verified, is_approved, is_admin,
                       created_at, last_login, failed_login_attempts, locked_until
                FROM users WHERE id = ?
            """, (user_id,))
            row = await cursor.fetchone()
            if row:
                return {
                    'id': row['id'],
                    'email': row['email'],
                    'password_hash': row['password_hash'],
                    'email_verified': bool(row['email_verified']),
                    'is_approved': bool(row['is_approved']),
                    'is_admin': bool(row['is_admin']),
                    'created_at': row['created_at'],
                    'last_login': row['last_login'],
                    'failed_login_attempts': row['failed_login_attempts'] or 0,
                    'locked_until': row['locked_until']
                }
    except Exception as e:
        print(f"[DB SQLite] Error getting user by id: {e}")
    return None


async def get_user_by_verification_token(token: str) -> Optional[dict]:
    """Get user by email verification token"""
    if not _pool:
        return None

    async with _pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT id, email, email_verification_expires
            FROM users
            WHERE email_verification_token = $1 AND email_verified = FALSE
        """, token)

        if row:
            return dict(row)
        return None


async def verify_user_email(user_id: str) -> bool:
    """Mark user's email as verified"""
    # PostgreSQL
    if _pool:
        try:
            async with _pool.acquire() as conn:
                await conn.execute("""
                    UPDATE users
                    SET email_verified = TRUE, email_verification_token = NULL
                    WHERE id = $1
                """, to_uuid(user_id))
                return True
        except Exception as e:
            print(f"[DB] Error in verify_user_email: {e}")
            return False

    # SQLite fallback
    try:
        async with aiosqlite.connect(SQLITE_AI_DB) as db:
            await db.execute("""
                UPDATE users SET email_verified = 1, email_verification_token = NULL WHERE id = ?
            """, (user_id,))
            await db.commit()
            return True
    except Exception as e:
        print(f"[DB SQLite] Error verifying email: {e}")
    return False


async def update_login_success(user_id: str) -> bool:
    """Update user after successful login"""
    # PostgreSQL
    if _pool:
        try:
            async with _pool.acquire() as conn:
                await conn.execute("""
                    UPDATE users
                    SET last_login = NOW(), failed_login_attempts = 0, locked_until = NULL
                    WHERE id = $1
                """, to_uuid(user_id))
                return True
        except Exception as e:
            print(f"[DB] Error in update_login_success: {e}")
            return False

    # SQLite fallback
    try:
        async with aiosqlite.connect(SQLITE_AI_DB) as db:
            await db.execute("""
                UPDATE users SET last_login = ?, failed_login_attempts = 0, locked_until = NULL WHERE id = ?
            """, (datetime.now(timezone.utc).isoformat(), user_id))
            await db.commit()
            return True
    except Exception as e:
        print(f"[DB SQLite] Error updating login success: {e}")
    return False


async def update_login_failure(user_id: str, lock_until: Optional[datetime] = None) -> bool:
    """Update user after failed login"""
    # PostgreSQL
    if _pool:
        try:
            async with _pool.acquire() as conn:
                if lock_until:
                    await conn.execute("""
                        UPDATE users
                        SET failed_login_attempts = failed_login_attempts + 1, locked_until = $2
                        WHERE id = $1
                    """, to_uuid(user_id), lock_until)
                else:
                    await conn.execute("""
                        UPDATE users
                        SET failed_login_attempts = failed_login_attempts + 1
                        WHERE id = $1
                    """, to_uuid(user_id))
                return True
        except Exception as e:
            print(f"[DB] Error in update_login_failure: {e}")
            return False

    # SQLite fallback
    try:
        async with aiosqlite.connect(SQLITE_AI_DB) as db:
            if lock_until:
                await db.execute("""
                    UPDATE users SET failed_login_attempts = failed_login_attempts + 1, locked_until = ? WHERE id = ?
                """, (lock_until.isoformat(), user_id))
            else:
                await db.execute("""
                    UPDATE users SET failed_login_attempts = failed_login_attempts + 1 WHERE id = ?
                """, (user_id,))
            await db.commit()
            return True
    except Exception as e:
        print(f"[DB SQLite] Error updating login failure: {e}")
    return False


async def get_all_users(include_pending: bool = True) -> List[dict]:
    """Get all users for admin"""
    if not _pool:
        return []

    async with _pool.acquire() as conn:
        if include_pending:
            rows = await conn.fetch("""
                SELECT id, email, email_verified, is_approved, is_admin,
                       COALESCE(ai_enabled, FALSE) as ai_enabled, created_at, last_login
                FROM users ORDER BY created_at DESC
            """)
        else:
            rows = await conn.fetch("""
                SELECT id, email, email_verified, is_approved, is_admin,
                       COALESCE(ai_enabled, FALSE) as ai_enabled, created_at, last_login
                FROM users WHERE is_approved = TRUE ORDER BY created_at DESC
            """)

        return [dict(row) for row in rows]


async def set_ai_enabled(user_id: str, enabled: bool) -> bool:
    """Enable or disable AI access for a user"""
    if not _pool:
        return False

    try:
        async with _pool.acquire() as conn:
            await conn.execute("""
                UPDATE users SET ai_enabled = $2 WHERE id = $1
            """, to_uuid(user_id), enabled)
            return True
    except Exception as e:
        print(f"[DB] Error in set_ai_enabled: {e}")
        return False


async def get_user_ai_enabled(user_id: str) -> bool:
    """Check if user has AI access enabled"""
    if not _pool:
        return False

    try:
        async with _pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT COALESCE(ai_enabled, FALSE) as ai_enabled FROM users WHERE id = $1
            """, to_uuid(user_id))
            return row['ai_enabled'] if row else False
    except Exception as e:
        print(f"[DB] Error in get_user_ai_enabled: {e}")
        return False


async def get_pending_users() -> List[dict]:
    """Get users pending approval"""
    if not _pool:
        return []

    async with _pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT id, email, email_verified, created_at
            FROM users
            WHERE is_approved = FALSE AND email_verified = TRUE
            ORDER BY created_at ASC
        """)
        return [dict(row) for row in rows]


async def approve_user(user_id: str) -> bool:
    """Approve a user"""
    if not _pool:
        return False

    try:
        async with _pool.acquire() as conn:
            result = await conn.execute("""
                UPDATE users SET is_approved = TRUE WHERE id = $1
            """, to_uuid(user_id))
            return result == "UPDATE 1"
    except Exception as e:
        print(f"[DB] Error in approve_user: {e}")
        return False


async def delete_user(user_id: str) -> bool:
    """Delete a user"""
    if not _pool:
        return False

    try:
        async with _pool.acquire() as conn:
            result = await conn.execute("DELETE FROM users WHERE id = $1", to_uuid(user_id))
            return result == "DELETE 1"
    except Exception as e:
        print(f"[DB] Error in delete_user: {e}")
        return False


async def set_admin(user_id: str, is_admin: bool) -> bool:
    """Set admin status for a user"""
    if not _pool:
        return False

    try:
        async with _pool.acquire() as conn:
            await conn.execute("""
                UPDATE users SET is_admin = $2 WHERE id = $1
            """, to_uuid(user_id), is_admin)
            return True
    except Exception as e:
        print(f"[DB] Error in set_admin: {e}")
        return False


async def set_password_reset_token(email: str, token: str) -> bool:
    """Set password reset token for user"""
    if not _pool:
        return False

    expires = datetime.now(timezone.utc) + timedelta(hours=1)
    async with _pool.acquire() as conn:
        result = await conn.execute("""
            UPDATE users
            SET password_reset_token = $2, password_reset_expires = $3
            WHERE email = $1
        """, email, token, expires)
        return result == "UPDATE 1"


async def get_user_by_reset_token(token: str) -> Optional[dict]:
    """Get user by password reset token"""
    if not _pool:
        return None

    async with _pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT id, email, password_reset_expires
            FROM users
            WHERE password_reset_token = $1
        """, token)

        if row:
            return dict(row)
        return None


async def update_password(user_id: str, password_hash: str) -> bool:
    """Update user's password and clear reset token"""
    if not _pool:
        return False

    async with _pool.acquire() as conn:
        await conn.execute("""
            UPDATE users
            SET password_hash = $2, password_reset_token = NULL, password_reset_expires = NULL
            WHERE id = $1
        """, user_id, password_hash)
        return True


# ============== Refresh Token Operations ==============

async def store_refresh_token(user_id: str, token_hash: str, expires_at: datetime) -> bool:
    """Store a refresh token"""
    # PostgreSQL
    if _pool:
        try:
            async with _pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO refresh_tokens (user_id, token_hash, expires_at)
                    VALUES ($1, $2, $3)
                """, to_uuid(user_id), token_hash, expires_at)
                return True
        except Exception as e:
            print(f"[DB] Error storing refresh token: {e}")
            return False

    # SQLite fallback
    try:
        token_id = str(uuid.uuid4())
        async with aiosqlite.connect(SQLITE_AI_DB) as db:
            await db.execute("""
                INSERT INTO refresh_tokens (id, user_id, token_hash, expires_at)
                VALUES (?, ?, ?, ?)
            """, (token_id, user_id, token_hash, expires_at.isoformat()))
            await db.commit()
            return True
    except Exception as e:
        print(f"[DB SQLite] Error storing refresh token: {e}")
    return False


async def get_refresh_token(token_hash: str) -> Optional[dict]:
    """Get refresh token by hash"""
    # PostgreSQL
    if _pool:
        async with _pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT rt.id, rt.user_id, rt.expires_at, rt.revoked,
                       u.is_approved, u.is_admin, u.email
                FROM refresh_tokens rt
                JOIN users u ON rt.user_id = u.id
                WHERE rt.token_hash = $1 AND rt.revoked = FALSE
            """, token_hash)
            if row:
                return dict(row)
            return None

    # SQLite fallback
    try:
        async with aiosqlite.connect(SQLITE_AI_DB) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("""
                SELECT rt.id, rt.user_id, rt.expires_at, rt.revoked,
                       u.is_approved, u.is_admin, u.email
                FROM refresh_tokens rt
                JOIN users u ON rt.user_id = u.id
                WHERE rt.token_hash = ? AND rt.revoked = 0
            """, (token_hash,))
            row = await cursor.fetchone()
            if row:
                return {
                    'id': row['id'],
                    'user_id': row['user_id'],
                    'expires_at': row['expires_at'],
                    'revoked': bool(row['revoked']),
                    'is_approved': bool(row['is_approved']),
                    'is_admin': bool(row['is_admin']),
                    'email': row['email']
                }
    except Exception as e:
        print(f"[DB SQLite] Error getting refresh token: {e}")
    return None


async def revoke_refresh_token(token_id: str) -> bool:
    """Revoke a refresh token"""
    # PostgreSQL
    if _pool:
        async with _pool.acquire() as conn:
            await conn.execute("""
                UPDATE refresh_tokens SET revoked = TRUE WHERE id = $1
            """, token_id)
            return True

    # SQLite fallback
    try:
        async with aiosqlite.connect(SQLITE_AI_DB) as db:
            await db.execute("UPDATE refresh_tokens SET revoked = 1 WHERE id = ?", (token_id,))
            await db.commit()
            return True
    except Exception as e:
        print(f"[DB SQLite] Error revoking token: {e}")
    return False


async def revoke_all_user_tokens(user_id: str) -> bool:
    """Revoke all refresh tokens for a user"""
    # PostgreSQL
    if _pool:
        try:
            async with _pool.acquire() as conn:
                await conn.execute("""
                    UPDATE refresh_tokens SET revoked = TRUE WHERE user_id = $1
                """, to_uuid(user_id))
                return True
        except Exception as e:
            print(f"[DB] Error in revoke_all_user_tokens: {e}")
            return False

    # SQLite fallback
    try:
        async with aiosqlite.connect(SQLITE_AI_DB) as db:
            await db.execute("UPDATE refresh_tokens SET revoked = 1 WHERE user_id = ?", (user_id,))
            await db.commit()
            return True
    except Exception as e:
        print(f"[DB SQLite] Error revoking user tokens: {e}")
    return False


async def cleanup_expired_tokens() -> int:
    """Remove expired refresh tokens"""
    # PostgreSQL
    if _pool:
        async with _pool.acquire() as conn:
            result = await conn.execute("""
                DELETE FROM refresh_tokens WHERE expires_at < NOW()
            """)
            return int(result.split()[1]) if result else 0

    # SQLite fallback
    try:
        async with aiosqlite.connect(SQLITE_AI_DB) as db:
            cursor = await db.execute("""
                DELETE FROM refresh_tokens WHERE expires_at < ?
            """, (datetime.now(timezone.utc).isoformat(),))
            await db.commit()
            return cursor.rowcount
    except Exception as e:
        print(f"[DB SQLite] Error cleaning up tokens: {e}")
    return 0


# ============== User Symbols Operations ==============

async def get_user_symbols(user_id: str) -> List[str]:
    """Get user's saved symbols"""
    if not _pool:
        return []

    try:
        async with _pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT symbol FROM user_symbols
                WHERE user_id = $1 ORDER BY display_order, added_at
            """, to_uuid(user_id))
            return [row['symbol'] for row in rows]
    except Exception as e:
        print(f"[DB] Error in get_user_symbols: {e}")
        return []


async def add_user_symbol(user_id: str, symbol: str) -> bool:
    """Add a symbol to user's watchlist"""
    if not _pool:
        return False

    try:
        user_uuid = to_uuid(user_id)
        async with _pool.acquire() as conn:
            # Get max display order
            max_order = await conn.fetchval("""
                SELECT COALESCE(MAX(display_order), 0) + 1 FROM user_symbols WHERE user_id = $1
            """, user_uuid)

            await conn.execute("""
                INSERT INTO user_symbols (user_id, symbol, display_order)
                VALUES ($1, $2, $3)
                ON CONFLICT (user_id, symbol) DO NOTHING
            """, user_uuid, symbol.upper(), max_order)
            return True
    except Exception as e:
        print(f"[DB] Error adding symbol: {e}")
        return False


async def remove_user_symbol(user_id: str, symbol: str) -> bool:
    """Remove a symbol from user's watchlist"""
    if not _pool:
        return False

    try:
        async with _pool.acquire() as conn:
            result = await conn.execute("""
                DELETE FROM user_symbols WHERE user_id = $1 AND symbol = $2
            """, to_uuid(user_id), symbol.upper())
            return result == "DELETE 1"
    except Exception as e:
        print(f"[DB] Error in remove_user_symbol: {e}")
        return False


async def reorder_user_symbols(user_id: str, symbols: List[str]) -> bool:
    """Reorder user's symbols"""
    if not _pool:
        return False

    try:
        user_uuid = to_uuid(user_id)
        async with _pool.acquire() as conn:
            for i, symbol in enumerate(symbols):
                await conn.execute("""
                    UPDATE user_symbols SET display_order = $3
                    WHERE user_id = $1 AND symbol = $2
                """, user_uuid, symbol.upper(), i)
            return True
    except Exception as e:
        print(f"[DB] Error in reorder_user_symbols: {e}")
        return False


# ============== User Preferences Operations ==============

async def get_user_preferences(user_id: str) -> Optional[dict]:
    """Get user's preferences"""
    if not _pool:
        return None

    try:
        async with _pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT theme, current_symbol, refresh_interval, current_view,
                       expiration_mode, view_mode, trinity_symbols, trend_filter
                FROM user_preferences WHERE user_id = $1
            """, to_uuid(user_id))

            if row:
                result = dict(row)
                # Parse JSON fields
                if result.get('trinity_symbols'):
                    import json
                    result['trinity_symbols'] = json.loads(result['trinity_symbols'])
                return result
            return None
    except Exception as e:
        print(f"[DB] Error in get_user_preferences: {e}")
        return None


async def save_user_preferences(user_id: str, preferences: dict) -> bool:
    """Save user's preferences (upsert)"""
    if not _pool:
        return False

    import json

    try:
        async with _pool.acquire() as conn:
            trinity = json.dumps(preferences.get('trinity_symbols', ["SPY", "QQQ", "IWM"]))

            await conn.execute("""
                INSERT INTO user_preferences (
                    user_id, theme, current_symbol, refresh_interval, current_view,
                    expiration_mode, view_mode, trinity_symbols, trend_filter, updated_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, NOW())
                ON CONFLICT (user_id) DO UPDATE SET
                    theme = EXCLUDED.theme,
                    current_symbol = EXCLUDED.current_symbol,
                    refresh_interval = EXCLUDED.refresh_interval,
                    current_view = EXCLUDED.current_view,
                    expiration_mode = EXCLUDED.expiration_mode,
                    view_mode = EXCLUDED.view_mode,
                    trinity_symbols = EXCLUDED.trinity_symbols,
                    trend_filter = EXCLUDED.trend_filter,
                    updated_at = NOW()
            """,
                to_uuid(user_id),
                preferences.get('theme', 'dark'),
                preferences.get('current_symbol', 'SPX'),
                preferences.get('refresh_interval', 1),
                preferences.get('current_view', 'gex'),
                preferences.get('expiration_mode', 'all'),
                preferences.get('view_mode', 'single'),
                trinity,
                preferences.get('trend_filter', 'all')
            )
            return True
    except Exception as e:
        print(f"[DB] Error in save_user_preferences: {e}")
        return False


async def init_user_defaults(user_id: str, default_symbols: List[str] = None) -> bool:
    """Initialize default preferences and symbols for new user"""
    # PostgreSQL
    if _pool:
        if default_symbols is None:
            default_symbols = ["SPX", "SPY", "QQQ"]

        # Create default preferences
        await save_user_preferences(user_id, {})

        # Add default symbols
        for i, symbol in enumerate(default_symbols):
            await add_user_symbol(user_id, symbol)

        return True

    # SQLite fallback - just return True (preferences optional)
    return True


# ============== WAVE Data Operations ==============

async def save_wave_data(
    symbol: str,
    timestamp: datetime,
    cumulative_call: float,
    cumulative_put: float,
    call_premium: float = 0,
    put_premium: float = 0
) -> bool:
    """Save WAVE indicator data point"""
    if not _pool:
        return False

    wave_value = cumulative_call - cumulative_put

    async with _pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO wave_data (symbol, timestamp, cumulative_call, cumulative_put, wave_value, call_premium, put_premium)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (symbol, timestamp) DO UPDATE SET
                cumulative_call = EXCLUDED.cumulative_call,
                cumulative_put = EXCLUDED.cumulative_put,
                wave_value = EXCLUDED.wave_value,
                call_premium = EXCLUDED.call_premium,
                put_premium = EXCLUDED.put_premium
        """, symbol, timestamp, cumulative_call, cumulative_put, wave_value, call_premium, put_premium)
        return True


async def get_wave_history(symbol: str, minutes: int = 60) -> List[dict]:
    """Get WAVE history for a symbol"""
    if not _pool:
        return []

    async with _pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT timestamp, cumulative_call, cumulative_put, wave_value, call_premium, put_premium
            FROM wave_data
            WHERE symbol = $1 AND timestamp > NOW() - INTERVAL '%s minutes'
            ORDER BY timestamp ASC
        """ % minutes, symbol)

        return [dict(row) for row in rows]


async def get_latest_wave(symbol: str) -> Optional[dict]:
    """Get the most recent WAVE data for a symbol"""
    if not _pool:
        return None

    async with _pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT timestamp, cumulative_call, cumulative_put, wave_value, call_premium, put_premium
            FROM wave_data
            WHERE symbol = $1
            ORDER BY timestamp DESC
            LIMIT 1
        """, symbol)

        return dict(row) if row else None


async def cleanup_old_wave_data(days: int = 7) -> int:
    """Delete WAVE data older than specified days"""
    if not _pool:
        return 0

    async with _pool.acquire() as conn:
        result = await conn.execute("""
            DELETE FROM wave_data WHERE timestamp < NOW() - INTERVAL '%s days'
        """ % days)
        return int(result.split()[1]) if result else 0


# ============== Flow Trades Operations ==============

async def save_flow_trade(trade: dict) -> bool:
    """Save a large trade to the tape"""
    if not _pool:
        return False

    async with _pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO flow_trades (symbol, strike, expiration, contract_type, trade_type, size, premium, sentiment, timestamp)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        """,
            trade['symbol'],
            trade['strike'],
            trade['expiration'],
            trade['contract_type'],
            trade['trade_type'],
            trade['size'],
            trade['premium'],
            trade.get('sentiment'),
            trade['timestamp']
        )
        return True


async def get_recent_trades(symbol: str = None, min_premium: float = 10000, limit: int = 50) -> List[dict]:
    """Get recent large trades for the tape"""
    if not _pool:
        return []

    async with _pool.acquire() as conn:
        if symbol:
            rows = await conn.fetch("""
                SELECT symbol, strike, expiration, contract_type, trade_type, size, premium, sentiment, timestamp
                FROM flow_trades
                WHERE symbol = $1 AND premium >= $2
                ORDER BY timestamp DESC
                LIMIT $3
            """, symbol, min_premium, limit)
        else:
            rows = await conn.fetch("""
                SELECT symbol, strike, expiration, contract_type, trade_type, size, premium, sentiment, timestamp
                FROM flow_trades
                WHERE premium >= $1
                ORDER BY timestamp DESC
                LIMIT $2
            """, min_premium, limit)

        return [dict(row) for row in rows]


async def cleanup_old_trades(days: int = 7) -> int:
    """Delete trades older than specified days"""
    if not _pool:
        return 0

    async with _pool.acquire() as conn:
        result = await conn.execute("""
            DELETE FROM flow_trades WHERE timestamp < NOW() - INTERVAL '%s days'
        """ % days)
        return int(result.split()[1]) if result else 0


# ============== Flow Leaderboard Operations ==============

async def update_leaderboard(entries: List[dict]) -> bool:
    """Update the flow leaderboard with new rankings"""
    if not _pool or not entries:
        return False

    now = datetime.now(timezone.utc)

    async with _pool.acquire() as conn:
        for i, entry in enumerate(entries):
            await conn.execute("""
                INSERT INTO flow_leaderboard
                    (symbol, timestamp, total_premium, net_premium, call_premium, put_premium, unusual_score, sentiment, rank)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                ON CONFLICT (symbol, timestamp) DO UPDATE SET
                    total_premium = EXCLUDED.total_premium,
                    net_premium = EXCLUDED.net_premium,
                    call_premium = EXCLUDED.call_premium,
                    put_premium = EXCLUDED.put_premium,
                    unusual_score = EXCLUDED.unusual_score,
                    sentiment = EXCLUDED.sentiment,
                    rank = EXCLUDED.rank
            """,
                entry['symbol'],
                now,
                entry.get('total_premium', 0),
                entry.get('net_premium', 0),
                entry.get('call_premium', 0),
                entry.get('put_premium', 0),
                entry.get('unusual_score'),
                entry.get('sentiment'),
                i + 1
            )
        return True


async def get_leaderboard(limit: int = 20) -> List[dict]:
    """Get the current flow leaderboard"""
    if not _pool:
        return []

    async with _pool.acquire() as conn:
        # Get the most recent leaderboard entries
        rows = await conn.fetch("""
            SELECT DISTINCT ON (symbol)
                symbol, total_premium, net_premium, call_premium, put_premium,
                unusual_score, sentiment, rank, timestamp
            FROM flow_leaderboard
            WHERE timestamp > NOW() - INTERVAL '5 minutes'
            ORDER BY symbol, timestamp DESC
        """)

        # Sort by rank
        result = sorted([dict(row) for row in rows], key=lambda x: x.get('rank', 999))
        return result[:limit]


# ============== AI Chat & Usage Tables ==============

async def create_ai_tables():
    """Create AI-related tables for chat history and usage tracking"""
    if not _pool:
        return

    async with _pool.acquire() as conn:
        # AI Chat History - stores conversations per user/symbol
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS ai_chat_history (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                symbol VARCHAR(10) NOT NULL,
                role VARCHAR(10) NOT NULL,
                content TEXT NOT NULL,
                tokens_used INTEGER DEFAULT 0,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        # User Token Limits - monthly token allowance per user
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS user_token_limits (
                user_id UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                monthly_token_limit INTEGER DEFAULT 500000,
                tokens_used_this_month INTEGER DEFAULT 0,
                month_start DATE DEFAULT CURRENT_DATE,
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        # AI Usage Log - detailed log for billing
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS ai_usage_log (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                endpoint VARCHAR(20) NOT NULL,
                symbol VARCHAR(10) NOT NULL,
                input_tokens INTEGER NOT NULL,
                output_tokens INTEGER NOT NULL,
                total_tokens INTEGER NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        # Create indexes for AI tables
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_history_user ON ai_chat_history(user_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_history_user_symbol ON ai_chat_history(user_id, symbol)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_history_created ON ai_chat_history(created_at)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_usage_log_user ON ai_usage_log(user_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_usage_log_created ON ai_usage_log(created_at)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_usage_log_month ON ai_usage_log(user_id, created_at)")

        print("[DB] AI tables created")


# ============== AI Chat History Operations ==============

async def save_chat_message(
    user_id: str,
    symbol: str,
    role: str,
    content: str,
    tokens_used: int = 0
) -> bool:
    """Save a chat message to history"""
    if _pool:
        try:
            async with _pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO ai_chat_history (user_id, symbol, role, content, tokens_used)
                    VALUES ($1, $2, $3, $4, $5)
                """, to_uuid(user_id), symbol.upper(), role, content, tokens_used)
                return True
        except Exception as e:
            print(f"[DB] Error in save_chat_message: {e}")
            return False
    else:
        # SQLite fallback
        try:
            async with aiosqlite.connect(SQLITE_AI_DB) as db:
                await db.execute("""
                    INSERT INTO ai_chat_history (id, user_id, symbol, role, content, tokens_used)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (str(uuid.uuid4()), user_id, symbol.upper(), role, content, tokens_used))
                await db.commit()
                return True
        except Exception as e:
            print(f"[DB SQLite] save_chat_message error: {e}")
            return False


async def get_chat_history(user_id: str, symbol: str = None, limit: int = 20) -> List[dict]:
    """Get recent chat history for a user/symbol"""
    if _pool:
        try:
            async with _pool.acquire() as conn:
                if symbol:
                    rows = await conn.fetch("""
                        SELECT role, content, tokens_used, created_at
                        FROM ai_chat_history
                        WHERE user_id = $1 AND symbol = $2
                        ORDER BY created_at DESC
                        LIMIT $3
                    """, to_uuid(user_id), symbol.upper(), limit)
                else:
                    rows = await conn.fetch("""
                        SELECT role, content, tokens_used, created_at, symbol
                        FROM ai_chat_history
                        WHERE user_id = $1
                        ORDER BY created_at DESC
                        LIMIT $2
                    """, to_uuid(user_id), limit)
                return [dict(row) for row in reversed(rows)]
        except Exception as e:
            print(f"[DB] Error in get_chat_history: {e}")
            return []
    else:
        # SQLite fallback
        try:
            async with aiosqlite.connect(SQLITE_AI_DB) as db:
                db.row_factory = aiosqlite.Row
                if symbol:
                    cursor = await db.execute("""
                        SELECT role, content, tokens_used, created_at
                        FROM ai_chat_history
                        WHERE user_id = ? AND symbol = ?
                        ORDER BY created_at DESC
                        LIMIT ?
                    """, (user_id, symbol.upper(), limit))
                else:
                    cursor = await db.execute("""
                        SELECT role, content, tokens_used, created_at, symbol
                        FROM ai_chat_history
                        WHERE user_id = ?
                        ORDER BY created_at DESC
                        LIMIT ?
                    """, (user_id, limit))
                rows = await cursor.fetchall()
                return [dict(row) for row in reversed(rows)]
        except Exception as e:
            print(f"[DB SQLite] get_chat_history error: {e}")
            return []


async def get_all_user_chats(user_id: str, limit: int = 100) -> List[dict]:
    """Get all recent chats for a user across all symbols"""
    if _pool:
        try:
            async with _pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT symbol, role, content, tokens_used, created_at
                    FROM ai_chat_history
                    WHERE user_id = $1
                    ORDER BY created_at DESC
                    LIMIT $2
                """, to_uuid(user_id), limit)
                return [dict(row) for row in rows]
        except Exception as e:
            print(f"[DB] Error in get_all_user_chats: {e}")
            return []
    else:
        # SQLite fallback
        try:
            async with aiosqlite.connect(SQLITE_AI_DB) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute("""
                    SELECT symbol, role, content, tokens_used, created_at
                    FROM ai_chat_history
                    WHERE user_id = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                """, (user_id, limit))
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]
        except Exception as e:
            print(f"[DB SQLite] get_all_user_chats error: {e}")
            return []


async def cleanup_old_chats(days: int = 7) -> int:
    """Delete chat history older than specified days (weekly cleanup)"""
    if _pool:
        async with _pool.acquire() as conn:
            result = await conn.execute("""
                DELETE FROM ai_chat_history WHERE created_at < NOW() - INTERVAL '%s days'
            """ % days)
            count = int(result.split()[1]) if result else 0
            if count > 0:
                print(f"[DB] Cleaned up {count} old chat messages")
            return count
    else:
        # SQLite fallback
        try:
            async with aiosqlite.connect(SQLITE_AI_DB) as db:
                cutoff = (datetime.now() - timedelta(days=days)).isoformat()
                cursor = await db.execute("""
                    DELETE FROM ai_chat_history WHERE created_at < ?
                """, (cutoff,))
                await db.commit()
                count = cursor.rowcount
                if count > 0:
                    print(f"[DB SQLite] Cleaned up {count} old chat messages")
                return count
        except Exception as e:
            print(f"[DB SQLite] cleanup_old_chats error: {e}")
            return 0


async def clear_user_chat_history(user_id: str, symbol: str = None) -> int:
    """Clear chat history for a user (optionally for specific symbol)"""
    if _pool:
        try:
            async with _pool.acquire() as conn:
                if symbol:
                    result = await conn.execute("""
                        DELETE FROM ai_chat_history WHERE user_id = $1 AND symbol = $2
                    """, to_uuid(user_id), symbol.upper())
                else:
                    result = await conn.execute("""
                        DELETE FROM ai_chat_history WHERE user_id = $1
                    """, to_uuid(user_id))
                return int(result.split()[1]) if result else 0
        except Exception as e:
            print(f"[DB] Error in clear_user_chat_history: {e}")
            return 0
    else:
        # SQLite fallback
        try:
            async with aiosqlite.connect(SQLITE_AI_DB) as db:
                if symbol:
                    cursor = await db.execute("""
                        DELETE FROM ai_chat_history WHERE user_id = ? AND symbol = ?
                    """, (user_id, symbol.upper()))
                else:
                    cursor = await db.execute("""
                        DELETE FROM ai_chat_history WHERE user_id = ?
                    """, (user_id,))
                await db.commit()
                return cursor.rowcount
        except Exception as e:
            print(f"[DB SQLite] clear_user_chat_history error: {e}")
            return 0


# ============== Token Limit Operations ==============

async def get_user_token_limit(user_id: str) -> dict:
    """Get user's token limit and current usage"""
    default = {"monthly_token_limit": 500000, "tokens_used_this_month": 0, "month_start": datetime.now().strftime("%Y-%m-%d")}

    if _pool:
        try:
            user_uuid = to_uuid(user_id)
            async with _pool.acquire() as conn:
                row = await conn.fetchrow("""
                    SELECT monthly_token_limit, tokens_used_this_month, month_start, updated_at
                    FROM user_token_limits
                    WHERE user_id = $1
                """, user_uuid)

                if row:
                    return dict(row)

                # Create default entry if not exists
                await conn.execute("""
                    INSERT INTO user_token_limits (user_id)
                    VALUES ($1)
                    ON CONFLICT (user_id) DO NOTHING
                """, user_uuid)

                return default
        except Exception as e:
            print(f"[DB] Error in get_user_token_limit: {e}")
            return default
    else:
        # SQLite fallback
        try:
            async with aiosqlite.connect(SQLITE_AI_DB) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute("""
                    SELECT monthly_token_limit, tokens_used_this_month, month_start, updated_at
                    FROM user_token_limits WHERE user_id = ?
                """, (user_id,))
                row = await cursor.fetchone()
                if row:
                    return dict(row)

                # Create default entry
                await db.execute("""
                    INSERT OR IGNORE INTO user_token_limits (user_id, monthly_token_limit, tokens_used_this_month, month_start)
                    VALUES (?, 500000, 0, date('now'))
                """, (user_id,))
                await db.commit()
                return default
        except Exception as e:
            print(f"[DB SQLite] get_user_token_limit error: {e}")
            return default


async def update_user_token_usage(user_id: str, tokens: int) -> bool:
    """Add tokens to user's monthly usage"""
    if _pool:
        try:
            async with _pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO user_token_limits (user_id, tokens_used_this_month, updated_at)
                    VALUES ($1, $2, NOW())
                    ON CONFLICT (user_id) DO UPDATE SET
                        tokens_used_this_month = user_token_limits.tokens_used_this_month + $2,
                        updated_at = NOW()
                """, to_uuid(user_id), tokens)
                return True
        except Exception as e:
            print(f"[DB] Error in update_user_token_usage: {e}")
            return False
    else:
        # SQLite fallback
        try:
            async with aiosqlite.connect(SQLITE_AI_DB) as db:
                # Try update first
                cursor = await db.execute("""
                    UPDATE user_token_limits
                    SET tokens_used_this_month = tokens_used_this_month + ?,
                        updated_at = datetime('now')
                    WHERE user_id = ?
                """, (tokens, user_id))
                if cursor.rowcount == 0:
                    # Insert if not exists
                    await db.execute("""
                        INSERT INTO user_token_limits (user_id, tokens_used_this_month, monthly_token_limit, month_start)
                        VALUES (?, ?, 500000, date('now'))
                    """, (user_id, tokens))
                await db.commit()
                return True
        except Exception as e:
            print(f"[DB SQLite] update_user_token_usage error: {e}")
            return False


async def set_user_token_limit(user_id: str, limit: int) -> dict:
    """Set a user's monthly token limit (admin function)"""
    if _pool:
        try:
            async with _pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO user_token_limits (user_id, monthly_token_limit, updated_at)
                    VALUES ($1, $2, NOW())
                    ON CONFLICT (user_id) DO UPDATE SET
                        monthly_token_limit = $2,
                        updated_at = NOW()
                """, to_uuid(user_id), limit)
                return {"success": True}
        except Exception as e:
            print(f"[DB] Error in set_user_token_limit: {e}")
            return {"success": False, "error": str(e)}
    else:
        # SQLite fallback
        try:
            async with aiosqlite.connect(SQLITE_AI_DB) as db:
                await db.execute("""
                    INSERT INTO user_token_limits (user_id, monthly_token_limit, tokens_used_this_month, month_start)
                    VALUES (?, ?, 0, date('now'))
                    ON CONFLICT(user_id) DO UPDATE SET
                        monthly_token_limit = ?,
                        updated_at = datetime('now')
                """, (user_id, limit, limit))
                await db.commit()
                return {"success": True}
        except Exception as e:
            print(f"[DB SQLite] set_user_token_limit error: {e}")
            return {"success": False, "error": str(e)}


async def reset_monthly_usage_if_new_month() -> int:
    """Reset token usage for all users if we're in a new month"""
    if _pool:
        async with _pool.acquire() as conn:
            result = await conn.execute("""
                UPDATE user_token_limits
                SET tokens_used_this_month = 0,
                    month_start = CURRENT_DATE,
                    updated_at = NOW()
                WHERE month_start < DATE_TRUNC('month', CURRENT_DATE)
            """)
            count = int(result.split()[1]) if result else 0
            if count > 0:
                print(f"[DB] Reset monthly token usage for {count} users")
            return count
    else:
        # SQLite fallback
        try:
            async with aiosqlite.connect(SQLITE_AI_DB) as db:
                cursor = await db.execute("""
                    UPDATE user_token_limits
                    SET tokens_used_this_month = 0,
                        month_start = date('now'),
                        updated_at = datetime('now')
                    WHERE month_start < date('now', 'start of month')
                """)
                await db.commit()
                count = cursor.rowcount
                if count > 0:
                    print(f"[DB SQLite] Reset monthly token usage for {count} users")
                return count
        except Exception as e:
            print(f"[DB SQLite] reset_monthly_usage error: {e}")
            return 0


async def check_user_token_limit(user_id: str) -> dict:
    """Check if user has remaining tokens, returns limit info"""
    limit_info = await get_user_token_limit(user_id)
    limit_info['remaining'] = limit_info['monthly_token_limit'] - limit_info['tokens_used_this_month']
    limit_info['exceeded'] = limit_info['remaining'] <= 0
    limit_info['usage_percent'] = (limit_info['tokens_used_this_month'] / limit_info['monthly_token_limit']) * 100 if limit_info['monthly_token_limit'] > 0 else 0
    return limit_info


# ============== AI Usage Log Operations ==============

async def log_ai_usage(
    user_id: str,
    endpoint: str,
    symbol: str,
    input_tokens: int,
    output_tokens: int
) -> bool:
    """Log an AI API call for billing"""
    total_tokens = input_tokens + output_tokens

    if _pool:
        try:
            async with _pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO ai_usage_log (user_id, endpoint, symbol, input_tokens, output_tokens, total_tokens)
                    VALUES ($1, $2, $3, $4, $5, $6)
                """, to_uuid(user_id), endpoint, symbol.upper(), input_tokens, output_tokens, total_tokens)
                return True
        except Exception as e:
            print(f"[DB] Error in log_ai_usage: {e}")
            return False
    else:
        # SQLite fallback
        try:
            async with aiosqlite.connect(SQLITE_AI_DB) as db:
                await db.execute("""
                    INSERT INTO ai_usage_log (id, user_id, endpoint, symbol, input_tokens, output_tokens, total_tokens)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (str(uuid.uuid4()), user_id, endpoint, symbol.upper(), input_tokens, output_tokens, total_tokens))
                await db.commit()
                return True
        except Exception as e:
            print(f"[DB SQLite] log_ai_usage error: {e}")
            return False


async def get_user_usage_report(user_id: str, start_date: str = None, end_date: str = None) -> dict:
    """Get detailed usage report for a user"""
    # Parse dates
    if start_date and isinstance(start_date, str):
        try:
            start_dt = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
        except:
            start_dt = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        start_dt = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    if end_date and isinstance(end_date, str):
        try:
            end_dt = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
        except:
            end_dt = datetime.now(timezone.utc)
    else:
        end_dt = datetime.now(timezone.utc)

    if not _pool:
        # SQLite fallback - return basic report
        try:
            async with aiosqlite.connect(SQLITE_AI_DB) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute("""
                    SELECT COUNT(*) as request_count,
                           COALESCE(SUM(input_tokens), 0) as total_input_tokens,
                           COALESCE(SUM(output_tokens), 0) as total_output_tokens,
                           COALESCE(SUM(total_tokens), 0) as total_tokens
                    FROM ai_usage_log
                    WHERE user_id = ? AND created_at >= ? AND created_at <= ?
                """, (user_id, start_dt.isoformat(), end_dt.isoformat()))
                row = await cursor.fetchone()
                limit_info = await get_user_token_limit(user_id)
                return {
                    "user_id": str(user_id),
                    "period": {"start": start_dt.isoformat(), "end": end_dt.isoformat()},
                    "totals": dict(row) if row else {},
                    "by_endpoint": [],
                    "by_symbol": [],
                    "limits": {
                        "monthly_limit": limit_info['monthly_token_limit'],
                        "used_this_month": limit_info['tokens_used_this_month'],
                        "remaining": limit_info['monthly_token_limit'] - limit_info['tokens_used_this_month']
                    }
                }
        except Exception as e:
            print(f"[DB SQLite] get_user_usage_report error: {e}")
            return {}

    try:
        user_uuid = to_uuid(user_id)
        async with _pool.acquire() as conn:
            # Get aggregated usage
            row = await conn.fetchrow("""
                SELECT
                    COUNT(*) as request_count,
                    COALESCE(SUM(input_tokens), 0) as total_input_tokens,
                    COALESCE(SUM(output_tokens), 0) as total_output_tokens,
                    COALESCE(SUM(total_tokens), 0) as total_tokens
                FROM ai_usage_log
                WHERE user_id = $1 AND created_at >= $2 AND created_at <= $3
            """, user_uuid, start_dt, end_dt)

            # Get usage by endpoint
            endpoint_rows = await conn.fetch("""
                SELECT
                    endpoint,
                    COUNT(*) as request_count,
                    COALESCE(SUM(total_tokens), 0) as total_tokens
                FROM ai_usage_log
                WHERE user_id = $1 AND created_at >= $2 AND created_at <= $3
                GROUP BY endpoint
            """, user_uuid, start_dt, end_dt)

            # Get usage by symbol (top 10)
            symbol_rows = await conn.fetch("""
                SELECT
                    symbol,
                    COUNT(*) as request_count,
                    COALESCE(SUM(total_tokens), 0) as total_tokens
                FROM ai_usage_log
                WHERE user_id = $1 AND created_at >= $2 AND created_at <= $3
                GROUP BY symbol
                ORDER BY total_tokens DESC
                LIMIT 10
            """, user_uuid, start_dt, end_dt)

            # Get token limit info
            limit_info = await get_user_token_limit(user_id)

            return {
                "user_id": str(user_id),
                "period": {
                    "start": start_dt.isoformat(),
                    "end": end_dt.isoformat()
                },
                "totals": dict(row) if row else {},
                "by_endpoint": [dict(r) for r in endpoint_rows],
                "by_symbol": [dict(r) for r in symbol_rows],
                "limits": {
                    "monthly_limit": limit_info['monthly_token_limit'],
                    "used_this_month": limit_info['tokens_used_this_month'],
                    "remaining": limit_info['monthly_token_limit'] - limit_info['tokens_used_this_month']
                }
            }
    except Exception as e:
        print(f"[DB] Error in get_user_usage_report: {e}")
        return {}


async def get_all_users_usage_report(month: str = None) -> dict:
    """Get usage report for all users (admin function for invoicing)"""
    if not _pool:
        return {}

    # Parse month or use current
    if month:
        try:
            year, mon = map(int, month.split('-'))
            start_date = datetime(year, mon, 1, tzinfo=timezone.utc)
            if mon == 12:
                end_date = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
            else:
                end_date = datetime(year, mon + 1, 1, tzinfo=timezone.utc)
        except:
            start_date = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            end_date = datetime.now(timezone.utc)
    else:
        start_date = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end_date = datetime.now(timezone.utc)

    async with _pool.acquire() as conn:
        # Get usage per user
        rows = await conn.fetch("""
            SELECT
                u.id as user_id,
                u.email,
                COALESCE(utl.monthly_token_limit, 500000) as monthly_limit,
                COALESCE(utl.tokens_used_this_month, 0) as tokens_used_this_month,
                COUNT(aul.id) as request_count,
                COALESCE(SUM(aul.input_tokens), 0) as input_tokens,
                COALESCE(SUM(aul.output_tokens), 0) as output_tokens,
                COALESCE(SUM(aul.total_tokens), 0) as total_tokens
            FROM users u
            LEFT JOIN user_token_limits utl ON u.id = utl.user_id
            LEFT JOIN ai_usage_log aul ON u.id = aul.user_id
                AND aul.created_at >= $1 AND aul.created_at < $2
            WHERE u.is_approved = TRUE
            GROUP BY u.id, u.email, utl.monthly_token_limit, utl.tokens_used_this_month
            ORDER BY total_tokens DESC
        """, start_date, end_date)

        users = []
        total_tokens_all = 0

        for row in rows:
            user_data = dict(row)
            user_data['user_id'] = str(user_data['user_id'])
            user_data['usage_percent'] = round(
                (user_data['tokens_used_this_month'] / user_data['monthly_limit']) * 100, 2
            ) if user_data['monthly_limit'] > 0 else 0
            users.append(user_data)
            total_tokens_all += user_data['total_tokens']

        return {
            "month": month or start_date.strftime("%Y-%m"),
            "period": {
                "start": start_date.isoformat(),
                "end": end_date.isoformat()
            },
            "users": users,
            "total_tokens_all_users": total_tokens_all,
            "total_users": len(users)
        }


async def cleanup_old_usage_logs(days: int = 90) -> int:
    """Delete usage logs older than specified days"""
    if not _pool:
        return 0

    async with _pool.acquire() as conn:
        result = await conn.execute("""
            DELETE FROM ai_usage_log WHERE created_at < NOW() - INTERVAL '%s days'
        """ % days)
        count = int(result.split()[1]) if result else 0
        if count > 0:
            print(f"[DB] Cleaned up {count} old usage log entries")
        return count


# ============== Trading Journal Tables & Operations ==============

async def init_journal_tables():
    """Initialize trading journal tables in SQLite"""
    try:
        async with aiosqlite.connect(SQLITE_AI_DB) as db:
            # Trades table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    quantity REAL NOT NULL,
                    entry_price REAL NOT NULL,
                    exit_price REAL,
                    entry_time TEXT NOT NULL,
                    exit_time TEXT,
                    pnl REAL,
                    status TEXT DEFAULT 'open',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Trade notes table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS trade_notes (
                    id TEXT PRIMARY KEY,
                    trade_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (trade_id) REFERENCES trades(id) ON DELETE CASCADE
                )
            """)

            # Trade tags table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS trade_tags (
                    id TEXT PRIMARY KEY,
                    trade_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    tag TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (trade_id) REFERENCES trades(id) ON DELETE CASCADE
                )
            """)

            # Daily P&L cache
            await db.execute("""
                CREATE TABLE IF NOT EXISTS daily_pnl_cache (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    trade_date TEXT NOT NULL,
                    total_pnl REAL DEFAULT 0,
                    trade_count INTEGER DEFAULT 0,
                    win_count INTEGER DEFAULT 0,
                    loss_count INTEGER DEFAULT 0,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, trade_date)
                )
            """)

            # Create indexes
            await db.execute("CREATE INDEX IF NOT EXISTS idx_trades_user ON trades(user_id)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_trades_date ON trades(entry_time)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(user_id, symbol)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_notes_trade ON trade_notes(trade_id)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_tags_trade ON trade_tags(trade_id)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_daily_pnl ON daily_pnl_cache(user_id, trade_date)")

            await db.commit()
            print("[DB] Trading journal tables initialized")
            return True
    except Exception as e:
        print(f"[DB] Journal tables init error: {e}")
        return False


# ============== Trade CRUD Operations ==============

async def create_trade(
    user_id: str,
    symbol: str,
    side: str,
    quantity: float,
    entry_price: float,
    entry_time: str,
    exit_price: float = None,
    exit_time: str = None,
    notes: str = None,
    tags: List[str] = None
) -> dict:
    """Create a new trade entry"""
    trade_id = str(uuid.uuid4())

    # Calculate P&L if trade is closed
    pnl = None
    status = 'open'
    if exit_price is not None:
        multiplier = 1 if side.lower() == 'long' else -1
        pnl = (exit_price - entry_price) * quantity * multiplier
        status = 'closed'

    try:
        async with aiosqlite.connect(SQLITE_AI_DB) as db:
            await db.execute("""
                INSERT INTO trades (id, user_id, symbol, side, quantity, entry_price, exit_price,
                                   entry_time, exit_time, pnl, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (trade_id, user_id, symbol.upper(), side.lower(), quantity, entry_price,
                  exit_price, entry_time, exit_time, pnl, status))

            # Add notes if provided
            if notes:
                note_id = str(uuid.uuid4())
                await db.execute("""
                    INSERT INTO trade_notes (id, trade_id, user_id, content)
                    VALUES (?, ?, ?, ?)
                """, (note_id, trade_id, user_id, notes))

            # Add tags if provided
            if tags:
                for tag in tags:
                    tag_id = str(uuid.uuid4())
                    await db.execute("""
                        INSERT INTO trade_tags (id, trade_id, user_id, tag)
                        VALUES (?, ?, ?, ?)
                    """, (tag_id, trade_id, user_id, tag.strip()))

            await db.commit()

            # Update daily P&L cache if trade is closed
            if status == 'closed':
                await update_daily_pnl_cache(user_id, entry_time[:10], pnl)

            return {"id": trade_id, "status": status, "pnl": pnl}
    except Exception as e:
        print(f"[DB] create_trade error: {e}")
        return {"error": str(e)}


async def get_trades(
    user_id: str,
    symbol: str = None,
    status: str = None,
    start_date: str = None,
    end_date: str = None,
    limit: int = 100
) -> List[dict]:
    """Get user's trades with optional filters"""
    try:
        async with aiosqlite.connect(SQLITE_AI_DB) as db:
            db.row_factory = aiosqlite.Row

            query = "SELECT * FROM trades WHERE user_id = ?"
            params = [user_id]

            if symbol:
                query += " AND symbol = ?"
                params.append(symbol.upper())
            if status:
                query += " AND status = ?"
                params.append(status)
            if start_date:
                query += " AND entry_time >= ?"
                params.append(start_date)
            if end_date:
                query += " AND entry_time <= ?"
                params.append(end_date)

            query += " ORDER BY entry_time DESC LIMIT ?"
            params.append(limit)

            cursor = await db.execute(query, params)
            rows = await cursor.fetchall()

            trades = []
            for row in rows:
                trade = dict(row)
                # Get notes for this trade
                notes_cursor = await db.execute(
                    "SELECT content, created_at FROM trade_notes WHERE trade_id = ?",
                    (trade['id'],)
                )
                trade['notes'] = [dict(n) for n in await notes_cursor.fetchall()]

                # Get tags for this trade
                tags_cursor = await db.execute(
                    "SELECT tag FROM trade_tags WHERE trade_id = ?",
                    (trade['id'],)
                )
                trade['tags'] = [t['tag'] for t in await tags_cursor.fetchall()]

                trades.append(trade)

            return trades
    except Exception as e:
        print(f"[DB] get_trades error: {e}")
        return []


async def get_trade_by_id(user_id: str, trade_id: str) -> dict:
    """Get a single trade by ID"""
    try:
        async with aiosqlite.connect(SQLITE_AI_DB) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM trades WHERE id = ? AND user_id = ?",
                (trade_id, user_id)
            )
            row = await cursor.fetchone()
            if not row:
                return None

            trade = dict(row)

            # Get notes
            notes_cursor = await db.execute(
                "SELECT id, content, created_at FROM trade_notes WHERE trade_id = ?",
                (trade_id,)
            )
            trade['notes'] = [dict(n) for n in await notes_cursor.fetchall()]

            # Get tags
            tags_cursor = await db.execute(
                "SELECT tag FROM trade_tags WHERE trade_id = ?",
                (trade_id,)
            )
            trade['tags'] = [t['tag'] for t in await tags_cursor.fetchall()]

            return trade
    except Exception as e:
        print(f"[DB] get_trade_by_id error: {e}")
        return None


async def update_trade(
    user_id: str,
    trade_id: str,
    exit_price: float = None,
    exit_time: str = None,
    **kwargs
) -> dict:
    """Update a trade (close position, edit details)"""
    try:
        async with aiosqlite.connect(SQLITE_AI_DB) as db:
            # Get current trade
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM trades WHERE id = ? AND user_id = ?",
                (trade_id, user_id)
            )
            trade = await cursor.fetchone()
            if not trade:
                return {"error": "Trade not found"}

            trade = dict(trade)

            # Build update
            updates = []
            params = []

            if exit_price is not None:
                updates.append("exit_price = ?")
                params.append(exit_price)

                # Calculate P&L
                multiplier = 1 if trade['side'] == 'long' else -1
                pnl = (exit_price - trade['entry_price']) * trade['quantity'] * multiplier
                updates.append("pnl = ?")
                params.append(pnl)
                updates.append("status = 'closed'")

            if exit_time:
                updates.append("exit_time = ?")
                params.append(exit_time)

            updates.append("updated_at = datetime('now')")

            if updates:
                params.append(trade_id)
                params.append(user_id)
                await db.execute(f"""
                    UPDATE trades SET {', '.join(updates)}
                    WHERE id = ? AND user_id = ?
                """, params)
                await db.commit()

                # Update daily P&L cache
                if exit_price is not None:
                    entry_date = trade['entry_time'][:10]
                    await update_daily_pnl_cache(user_id, entry_date, pnl)

            return {"success": True, "trade_id": trade_id}
    except Exception as e:
        print(f"[DB] update_trade error: {e}")
        return {"error": str(e)}


async def delete_trade(user_id: str, trade_id: str) -> dict:
    """Delete a trade and its associated notes/tags"""
    try:
        async with aiosqlite.connect(SQLITE_AI_DB) as db:
            # Get trade for P&L cache update
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM trades WHERE id = ? AND user_id = ?",
                (trade_id, user_id)
            )
            trade = await cursor.fetchone()
            if not trade:
                return {"error": "Trade not found"}

            trade = dict(trade)

            # Delete (cascades to notes and tags)
            await db.execute("DELETE FROM trade_notes WHERE trade_id = ?", (trade_id,))
            await db.execute("DELETE FROM trade_tags WHERE trade_id = ?", (trade_id,))
            await db.execute("DELETE FROM trades WHERE id = ? AND user_id = ?", (trade_id, user_id))
            await db.commit()

            # Update daily P&L cache (subtract the deleted trade's P&L)
            if trade['pnl'] and trade['status'] == 'closed':
                entry_date = trade['entry_time'][:10]
                await update_daily_pnl_cache(user_id, entry_date, -trade['pnl'])

            return {"success": True, "deleted": trade_id}
    except Exception as e:
        print(f"[DB] delete_trade error: {e}")
        return {"error": str(e)}


# ============== Trade Notes & Tags ==============

async def add_trade_note(user_id: str, trade_id: str, content: str) -> dict:
    """Add a note to a trade"""
    try:
        async with aiosqlite.connect(SQLITE_AI_DB) as db:
            note_id = str(uuid.uuid4())
            await db.execute("""
                INSERT INTO trade_notes (id, trade_id, user_id, content)
                VALUES (?, ?, ?, ?)
            """, (note_id, trade_id, user_id, content))
            await db.commit()
            return {"id": note_id, "trade_id": trade_id}
    except Exception as e:
        print(f"[DB] add_trade_note error: {e}")
        return {"error": str(e)}


async def delete_trade_note(user_id: str, note_id: str) -> dict:
    """Delete a trade note"""
    try:
        async with aiosqlite.connect(SQLITE_AI_DB) as db:
            await db.execute(
                "DELETE FROM trade_notes WHERE id = ? AND user_id = ?",
                (note_id, user_id)
            )
            await db.commit()
            return {"success": True}
    except Exception as e:
        print(f"[DB] delete_trade_note error: {e}")
        return {"error": str(e)}


async def add_trade_tag(user_id: str, trade_id: str, tag: str) -> dict:
    """Add a tag to a trade"""
    try:
        async with aiosqlite.connect(SQLITE_AI_DB) as db:
            tag_id = str(uuid.uuid4())
            await db.execute("""
                INSERT INTO trade_tags (id, trade_id, user_id, tag)
                VALUES (?, ?, ?, ?)
            """, (tag_id, trade_id, user_id, tag.strip()))
            await db.commit()
            return {"id": tag_id, "trade_id": trade_id, "tag": tag}
    except Exception as e:
        print(f"[DB] add_trade_tag error: {e}")
        return {"error": str(e)}


async def remove_trade_tag(user_id: str, trade_id: str, tag: str) -> dict:
    """Remove a tag from a trade"""
    try:
        async with aiosqlite.connect(SQLITE_AI_DB) as db:
            await db.execute(
                "DELETE FROM trade_tags WHERE trade_id = ? AND user_id = ? AND tag = ?",
                (trade_id, user_id, tag)
            )
            await db.commit()
            return {"success": True}
    except Exception as e:
        print(f"[DB] remove_trade_tag error: {e}")
        return {"error": str(e)}


async def get_user_tags(user_id: str) -> List[str]:
    """Get all unique tags for a user"""
    try:
        async with aiosqlite.connect(SQLITE_AI_DB) as db:
            cursor = await db.execute(
                "SELECT DISTINCT tag FROM trade_tags WHERE user_id = ? ORDER BY tag",
                (user_id,)
            )
            rows = await cursor.fetchall()
            return [row[0] for row in rows]
    except Exception as e:
        print(f"[DB] get_user_tags error: {e}")
        return []


# ============== Daily P&L Cache ==============

async def update_daily_pnl_cache(user_id: str, trade_date: str, pnl_change: float):
    """Update the daily P&L cache when a trade is closed/updated/deleted"""
    try:
        async with aiosqlite.connect(SQLITE_AI_DB) as db:
            # Check if entry exists
            cursor = await db.execute(
                "SELECT total_pnl, trade_count, win_count, loss_count FROM daily_pnl_cache WHERE user_id = ? AND trade_date = ?",
                (user_id, trade_date)
            )
            row = await cursor.fetchone()

            if row:
                new_pnl = row[0] + pnl_change
                trade_count = row[1] + (1 if pnl_change != 0 else 0)
                win_count = row[2] + (1 if pnl_change > 0 else 0)
                loss_count = row[3] + (1 if pnl_change < 0 else 0)

                await db.execute("""
                    UPDATE daily_pnl_cache
                    SET total_pnl = ?, trade_count = ?, win_count = ?, loss_count = ?, updated_at = datetime('now')
                    WHERE user_id = ? AND trade_date = ?
                """, (new_pnl, trade_count, win_count, loss_count, user_id, trade_date))
            else:
                await db.execute("""
                    INSERT INTO daily_pnl_cache (id, user_id, trade_date, total_pnl, trade_count, win_count, loss_count)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (str(uuid.uuid4()), user_id, trade_date, pnl_change,
                      1 if pnl_change != 0 else 0,
                      1 if pnl_change > 0 else 0,
                      1 if pnl_change < 0 else 0))

            await db.commit()
    except Exception as e:
        print(f"[DB] update_daily_pnl_cache error: {e}")


async def get_calendar_data(user_id: str, year: int, month: int) -> List[dict]:
    """Get daily P&L data for calendar view"""
    try:
        async with aiosqlite.connect(SQLITE_AI_DB) as db:
            db.row_factory = aiosqlite.Row

            start_date = f"{year}-{month:02d}-01"
            if month == 12:
                end_date = f"{year + 1}-01-01"
            else:
                end_date = f"{year}-{month + 1:02d}-01"

            cursor = await db.execute("""
                SELECT trade_date, total_pnl, trade_count, win_count, loss_count
                FROM daily_pnl_cache
                WHERE user_id = ? AND trade_date >= ? AND trade_date < ?
                ORDER BY trade_date
            """, (user_id, start_date, end_date))

            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
    except Exception as e:
        print(f"[DB] get_calendar_data error: {e}")
        return []


# ============== Analytics ==============

async def get_trading_analytics(user_id: str, start_date: str = None, end_date: str = None) -> dict:
    """Get comprehensive trading analytics"""
    try:
        async with aiosqlite.connect(SQLITE_AI_DB) as db:
            db.row_factory = aiosqlite.Row

            # Base query conditions
            conditions = "user_id = ? AND status = 'closed'"
            params = [user_id]

            if start_date:
                conditions += " AND entry_time >= ?"
                params.append(start_date)
            if end_date:
                conditions += " AND entry_time <= ?"
                params.append(end_date)

            # Overall stats
            cursor = await db.execute(f"""
                SELECT
                    COUNT(*) as total_trades,
                    SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as winning_trades,
                    SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END) as losing_trades,
                    SUM(pnl) as total_pnl,
                    AVG(CASE WHEN pnl > 0 THEN pnl END) as avg_win,
                    AVG(CASE WHEN pnl < 0 THEN pnl END) as avg_loss,
                    MAX(pnl) as best_trade,
                    MIN(pnl) as worst_trade
                FROM trades WHERE {conditions}
            """, params)
            stats = dict(await cursor.fetchone())

            # Win rate
            if stats['total_trades'] and stats['total_trades'] > 0:
                stats['win_rate'] = round((stats['winning_trades'] or 0) / stats['total_trades'] * 100, 2)
            else:
                stats['win_rate'] = 0

            # Profit factor
            cursor = await db.execute(f"""
                SELECT
                    SUM(CASE WHEN pnl > 0 THEN pnl ELSE 0 END) as gross_profit,
                    ABS(SUM(CASE WHEN pnl < 0 THEN pnl ELSE 0 END)) as gross_loss
                FROM trades WHERE {conditions}
            """, params)
            pf_row = await cursor.fetchone()
            gross_profit = pf_row[0] or 0
            gross_loss = pf_row[1] or 0.0001  # Avoid division by zero
            stats['profit_factor'] = round(gross_profit / gross_loss, 2) if gross_loss > 0 else 0

            # P&L by symbol
            cursor = await db.execute(f"""
                SELECT symbol, SUM(pnl) as total_pnl, COUNT(*) as trade_count
                FROM trades WHERE {conditions}
                GROUP BY symbol ORDER BY total_pnl DESC
            """, params)
            stats['by_symbol'] = [dict(row) for row in await cursor.fetchall()]

            # P&L by day of week
            cursor = await db.execute(f"""
                SELECT
                    CASE strftime('%w', entry_time)
                        WHEN '0' THEN 'Sunday'
                        WHEN '1' THEN 'Monday'
                        WHEN '2' THEN 'Tuesday'
                        WHEN '3' THEN 'Wednesday'
                        WHEN '4' THEN 'Thursday'
                        WHEN '5' THEN 'Friday'
                        WHEN '6' THEN 'Saturday'
                    END as day_name,
                    SUM(pnl) as total_pnl,
                    COUNT(*) as trade_count
                FROM trades WHERE {conditions}
                GROUP BY strftime('%w', entry_time)
                ORDER BY strftime('%w', entry_time)
            """, params)
            stats['by_day'] = [dict(row) for row in await cursor.fetchall()]

            # P&L by hour
            cursor = await db.execute(f"""
                SELECT
                    strftime('%H', entry_time) as hour,
                    SUM(pnl) as total_pnl,
                    COUNT(*) as trade_count
                FROM trades WHERE {conditions}
                GROUP BY strftime('%H', entry_time)
                ORDER BY hour
            """, params)
            stats['by_hour'] = [dict(row) for row in await cursor.fetchall()]

            # Equity curve (cumulative P&L by date)
            cursor = await db.execute(f"""
                SELECT
                    date(entry_time) as trade_date,
                    SUM(pnl) as daily_pnl
                FROM trades WHERE {conditions}
                GROUP BY date(entry_time)
                ORDER BY trade_date
            """, params)

            equity = []
            cumulative = 0
            for row in await cursor.fetchall():
                cumulative += row[1]
                equity.append({"date": row[0], "daily_pnl": row[1], "cumulative_pnl": cumulative})
            stats['equity_curve'] = equity

            return stats
    except Exception as e:
        print(f"[DB] get_trading_analytics error: {e}")
        return {}


# ============== CSV Import ==============

async def import_trades_from_csv(user_id: str, trades_data: List[dict]) -> dict:
    """Import multiple trades from CSV data"""
    imported = 0
    errors = []

    for i, trade in enumerate(trades_data):
        try:
            result = await create_trade(
                user_id=user_id,
                symbol=trade.get('symbol', ''),
                side=trade.get('side', 'long'),
                quantity=float(trade.get('quantity', 0)),
                entry_price=float(trade.get('entry_price', 0)),
                entry_time=trade.get('entry_time', ''),
                exit_price=float(trade['exit_price']) if trade.get('exit_price') else None,
                exit_time=trade.get('exit_time'),
                notes=trade.get('notes'),
                tags=trade.get('tags', '').split(',') if trade.get('tags') else None
            )
            if 'error' not in result:
                imported += 1
            else:
                errors.append(f"Row {i+1}: {result['error']}")
        except Exception as e:
            errors.append(f"Row {i+1}: {str(e)}")

    return {"imported": imported, "errors": errors, "total": len(trades_data)}
