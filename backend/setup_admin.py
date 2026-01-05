#!/usr/bin/env python3
"""
Admin Setup Script for GEX Dashboard

Run this once to create the initial admin user.
Usage: python setup_admin.py

Or set environment variables before running:
  ADMIN_EMAIL=your@email.com
  ADMIN_PASSWORD=your_secure_password
"""

import os
import sys
import asyncio

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from db_postgres import init_db, close_db, get_pool, init_user_defaults
from auth.security import hash_password

# Admin configuration
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "morganwillie93@gmail.com")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "GexAdmin2024!")  # CHANGE THIS!

# Default symbols for admin
DEFAULT_SYMBOLS = ["SPX", "SPY", "QQQ", "TSLA", "NVDA", "AAPL", "AMZN"]


async def create_admin():
    """Create the initial admin user."""

    print("=" * 60)
    print("GEX Dashboard - Admin Setup")
    print("=" * 60)

    # Check if DATABASE_URL is set
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        print("\n[ERROR] DATABASE_URL environment variable not set!")
        print("\nTo set it:")
        print("  Windows: set DATABASE_URL=postgresql://user:pass@host:5432/dbname")
        print("  Linux:   export DATABASE_URL=postgresql://user:pass@host:5432/dbname")
        print("\nOr run this on Render where DATABASE_URL is auto-configured.")
        return False

    print(f"\n[INFO] Connecting to database...")

    # Initialize database
    db_ok = await init_db()
    if not db_ok:
        print("[ERROR] Failed to connect to database!")
        return False

    print("[OK] Database connected and tables created")

    pool = get_pool()
    if not pool:
        print("[ERROR] No database pool available!")
        return False

    async with pool.acquire() as conn:
        # Check if admin already exists
        existing = await conn.fetchrow(
            "SELECT id, email, is_admin FROM users WHERE email = $1",
            ADMIN_EMAIL
        )

        if existing:
            if existing['is_admin']:
                print(f"\n[INFO] Admin user already exists: {ADMIN_EMAIL}")
                print("[INFO] No changes made.")
            else:
                # Upgrade existing user to admin
                await conn.execute("""
                    UPDATE users
                    SET is_admin = TRUE, is_approved = TRUE, email_verified = TRUE
                    WHERE email = $1
                """, ADMIN_EMAIL)
                print(f"\n[OK] Upgraded existing user to admin: {ADMIN_EMAIL}")

            await close_db()
            return True

        # Create new admin user
        password_hash = hash_password(ADMIN_PASSWORD)

        result = await conn.fetchrow("""
            INSERT INTO users (
                email, password_hash, email_verified, is_approved, is_admin
            ) VALUES ($1, $2, TRUE, TRUE, TRUE)
            RETURNING id
        """, ADMIN_EMAIL, password_hash)

        user_id = str(result['id'])
        print(f"\n[OK] Created admin user: {ADMIN_EMAIL}")
        print(f"[OK] User ID: {user_id}")

        # Initialize default preferences
        await conn.execute("""
            INSERT INTO user_preferences (user_id, theme, current_symbol, refresh_interval)
            VALUES ($1, 'dark', 'SPX', 1)
            ON CONFLICT (user_id) DO NOTHING
        """, result['id'])
        print("[OK] Created default preferences")

        # Add default symbols
        for i, symbol in enumerate(DEFAULT_SYMBOLS):
            await conn.execute("""
                INSERT INTO user_symbols (user_id, symbol, display_order)
                VALUES ($1, $2, $3)
                ON CONFLICT (user_id, symbol) DO NOTHING
            """, result['id'], symbol, i)

        print(f"[OK] Added default symbols: {', '.join(DEFAULT_SYMBOLS)}")

    await close_db()

    print("\n" + "=" * 60)
    print("ADMIN SETUP COMPLETE!")
    print("=" * 60)
    print(f"\nEmail:    {ADMIN_EMAIL}")
    print(f"Password: {ADMIN_PASSWORD}")
    print("\n[!] IMPORTANT: Change your password after first login!")
    print("\nYou can now:")
    print("  1. Go to /login and sign in")
    print("  2. Access /admin to approve new users")
    print("  3. Use the dashboard at /app")
    print("=" * 60)

    return True


if __name__ == "__main__":
    success = asyncio.run(create_admin())
    sys.exit(0 if success else 1)
