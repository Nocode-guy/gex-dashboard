# PostgreSQL Database Module for GEX Dashboard
import os
import asyncpg
from typing import Optional, List, Any
from datetime import datetime, timezone, timedelta
from contextlib import asynccontextmanager

# Database URL from environment (Render provides this)
DATABASE_URL = os.environ.get("DATABASE_URL", "")

# Connection pool
_pool: Optional[asyncpg.Pool] = None


async def init_db():
    """Initialize database connection pool and create tables"""
    global _pool

    if not DATABASE_URL:
        print("[DB] DATABASE_URL not set - using SQLite fallback")
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
                created_at TIMESTAMPTZ DEFAULT NOW(),
                last_login TIMESTAMPTZ,
                failed_login_attempts INTEGER DEFAULT 0,
                locked_until TIMESTAMPTZ,
                password_reset_token VARCHAR(255),
                password_reset_expires TIMESTAMPTZ
            )
        """)

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
                refresh_interval INTEGER DEFAULT 5,
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

        # Create indexes
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_users_approval ON users(is_approved)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_user_symbols_user ON user_symbols(user_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_refresh_tokens_user ON refresh_tokens(user_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_refresh_tokens_hash ON refresh_tokens(token_hash)")


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
    if not _pool:
        return None

    try:
        async with _pool.acquire() as conn:
            expires = datetime.now(timezone.utc) + timedelta(hours=24)
            result = await conn.fetchrow("""
                INSERT INTO users (email, password_hash, email_verification_token,
                                   email_verification_expires, is_admin, is_approved)
                VALUES ($1, $2, $3, $4, $5, $5)
                RETURNING id
            """, email, password_hash, verification_token, expires, is_admin)
            return str(result['id'])
    except asyncpg.UniqueViolationError:
        return None  # Email already exists
    except Exception as e:
        print(f"[DB] Error creating user: {e}")
        return None


async def get_user_by_email(email: str) -> Optional[dict]:
    """Get user by email"""
    if not _pool:
        return None

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


async def get_user_by_id(user_id: str) -> Optional[dict]:
    """Get user by ID"""
    if not _pool:
        return None

    async with _pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT id, email, password_hash, email_verified, is_approved, is_admin,
                   created_at, last_login, failed_login_attempts, locked_until
            FROM users WHERE id = $1
        """, user_id)

        if row:
            return dict(row)
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
    if not _pool:
        return False

    async with _pool.acquire() as conn:
        await conn.execute("""
            UPDATE users
            SET email_verified = TRUE, email_verification_token = NULL
            WHERE id = $1
        """, user_id)
        return True


async def update_login_success(user_id: str) -> bool:
    """Update user after successful login"""
    if not _pool:
        return False

    async with _pool.acquire() as conn:
        await conn.execute("""
            UPDATE users
            SET last_login = NOW(), failed_login_attempts = 0, locked_until = NULL
            WHERE id = $1
        """, user_id)
        return True


async def update_login_failure(user_id: str, lock_until: Optional[datetime] = None) -> bool:
    """Update user after failed login"""
    if not _pool:
        return False

    async with _pool.acquire() as conn:
        if lock_until:
            await conn.execute("""
                UPDATE users
                SET failed_login_attempts = failed_login_attempts + 1, locked_until = $2
                WHERE id = $1
            """, user_id, lock_until)
        else:
            await conn.execute("""
                UPDATE users
                SET failed_login_attempts = failed_login_attempts + 1
                WHERE id = $1
            """, user_id)
        return True


async def get_all_users(include_pending: bool = True) -> List[dict]:
    """Get all users for admin"""
    if not _pool:
        return []

    async with _pool.acquire() as conn:
        if include_pending:
            rows = await conn.fetch("""
                SELECT id, email, email_verified, is_approved, is_admin, created_at, last_login
                FROM users ORDER BY created_at DESC
            """)
        else:
            rows = await conn.fetch("""
                SELECT id, email, email_verified, is_approved, is_admin, created_at, last_login
                FROM users WHERE is_approved = TRUE ORDER BY created_at DESC
            """)

        return [dict(row) for row in rows]


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

    async with _pool.acquire() as conn:
        result = await conn.execute("""
            UPDATE users SET is_approved = TRUE WHERE id = $1
        """, user_id)
        return result == "UPDATE 1"


async def delete_user(user_id: str) -> bool:
    """Delete a user"""
    if not _pool:
        return False

    async with _pool.acquire() as conn:
        result = await conn.execute("DELETE FROM users WHERE id = $1", user_id)
        return result == "DELETE 1"


async def set_admin(user_id: str, is_admin: bool) -> bool:
    """Set admin status for a user"""
    if not _pool:
        return False

    async with _pool.acquire() as conn:
        await conn.execute("""
            UPDATE users SET is_admin = $2 WHERE id = $1
        """, user_id, is_admin)
        return True


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
    if not _pool:
        return False

    async with _pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO refresh_tokens (user_id, token_hash, expires_at)
            VALUES ($1, $2, $3)
        """, user_id, token_hash, expires_at)
        return True


async def get_refresh_token(token_hash: str) -> Optional[dict]:
    """Get refresh token by hash"""
    if not _pool:
        return None

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


async def revoke_refresh_token(token_id: str) -> bool:
    """Revoke a refresh token"""
    if not _pool:
        return False

    async with _pool.acquire() as conn:
        await conn.execute("""
            UPDATE refresh_tokens SET revoked = TRUE WHERE id = $1
        """, token_id)
        return True


async def revoke_all_user_tokens(user_id: str) -> bool:
    """Revoke all refresh tokens for a user"""
    if not _pool:
        return False

    async with _pool.acquire() as conn:
        await conn.execute("""
            UPDATE refresh_tokens SET revoked = TRUE WHERE user_id = $1
        """, user_id)
        return True


async def cleanup_expired_tokens() -> int:
    """Remove expired refresh tokens"""
    if not _pool:
        return 0

    async with _pool.acquire() as conn:
        result = await conn.execute("""
            DELETE FROM refresh_tokens WHERE expires_at < NOW()
        """)
        # Parse "DELETE N" to get count
        return int(result.split()[1]) if result else 0


# ============== User Symbols Operations ==============

async def get_user_symbols(user_id: str) -> List[str]:
    """Get user's saved symbols"""
    if not _pool:
        return []

    async with _pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT symbol FROM user_symbols
            WHERE user_id = $1 ORDER BY display_order, added_at
        """, user_id)
        return [row['symbol'] for row in rows]


async def add_user_symbol(user_id: str, symbol: str) -> bool:
    """Add a symbol to user's watchlist"""
    if not _pool:
        return False

    try:
        async with _pool.acquire() as conn:
            # Get max display order
            max_order = await conn.fetchval("""
                SELECT COALESCE(MAX(display_order), 0) + 1 FROM user_symbols WHERE user_id = $1
            """, user_id)

            await conn.execute("""
                INSERT INTO user_symbols (user_id, symbol, display_order)
                VALUES ($1, $2, $3)
                ON CONFLICT (user_id, symbol) DO NOTHING
            """, user_id, symbol.upper(), max_order)
            return True
    except Exception as e:
        print(f"[DB] Error adding symbol: {e}")
        return False


async def remove_user_symbol(user_id: str, symbol: str) -> bool:
    """Remove a symbol from user's watchlist"""
    if not _pool:
        return False

    async with _pool.acquire() as conn:
        result = await conn.execute("""
            DELETE FROM user_symbols WHERE user_id = $1 AND symbol = $2
        """, user_id, symbol.upper())
        return result == "DELETE 1"


async def reorder_user_symbols(user_id: str, symbols: List[str]) -> bool:
    """Reorder user's symbols"""
    if not _pool:
        return False

    async with _pool.acquire() as conn:
        for i, symbol in enumerate(symbols):
            await conn.execute("""
                UPDATE user_symbols SET display_order = $3
                WHERE user_id = $1 AND symbol = $2
            """, user_id, symbol.upper(), i)
        return True


# ============== User Preferences Operations ==============

async def get_user_preferences(user_id: str) -> Optional[dict]:
    """Get user's preferences"""
    if not _pool:
        return None

    async with _pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT theme, current_symbol, refresh_interval, current_view,
                   expiration_mode, view_mode, trinity_symbols, trend_filter
            FROM user_preferences WHERE user_id = $1
        """, user_id)

        if row:
            result = dict(row)
            # Parse JSON fields
            if result.get('trinity_symbols'):
                import json
                result['trinity_symbols'] = json.loads(result['trinity_symbols'])
            return result
        return None


async def save_user_preferences(user_id: str, preferences: dict) -> bool:
    """Save user's preferences (upsert)"""
    if not _pool:
        return False

    import json

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
            user_id,
            preferences.get('theme', 'dark'),
            preferences.get('current_symbol', 'SPX'),
            preferences.get('refresh_interval', 5),
            preferences.get('current_view', 'gex'),
            preferences.get('expiration_mode', 'all'),
            preferences.get('view_mode', 'single'),
            trinity,
            preferences.get('trend_filter', 'all')
        )
        return True


async def init_user_defaults(user_id: str, default_symbols: List[str] = None) -> bool:
    """Initialize default preferences and symbols for new user"""
    if not _pool:
        return False

    if default_symbols is None:
        default_symbols = ["SPX", "SPY", "QQQ"]

    # Create default preferences
    await save_user_preferences(user_id, {})

    # Add default symbols
    for i, symbol in enumerate(default_symbols):
        await add_user_symbol(user_id, symbol)

    return True
