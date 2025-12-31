"""
GEX Historical Database

Stores daily GEX snapshots for historical tracking and analysis.
"""
import sqlite3
from datetime import datetime, date
from typing import List, Optional, Dict
from pathlib import Path

# Database file location
DB_PATH = Path(__file__).parent / "gex_history.db"


def get_connection():
    """Get database connection."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_database():
    """Initialize database tables."""
    conn = get_connection()
    cursor = conn.cursor()

    # Daily GEX snapshots
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS gex_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            snapshot_date DATE NOT NULL,
            snapshot_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            spot_price REAL NOT NULL,
            net_gex REAL NOT NULL,
            total_call_gex REAL,
            total_put_gex REAL,
            net_vex REAL,
            king_strike REAL,
            king_gex REAL,
            gatekeeper_strike REAL,
            gatekeeper_gex REAL,
            zero_gamma_level REAL,
            opex_warning BOOLEAN DEFAULT FALSE,
            UNIQUE(symbol, snapshot_date)
        )
    """)

    # Zone history (top zones for each snapshot)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS zone_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_id INTEGER NOT NULL,
            strike REAL NOT NULL,
            gex REAL NOT NULL,
            gex_type TEXT NOT NULL,
            role TEXT NOT NULL,
            strength REAL,
            FOREIGN KEY (snapshot_id) REFERENCES gex_snapshots(id)
        )
    """)

    # Create indexes for fast queries
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_snapshots_symbol_date
        ON gex_snapshots(symbol, snapshot_date DESC)
    """)

    conn.commit()
    conn.close()
    print("[DB] Database initialized")


def save_snapshot(
    symbol: str,
    spot_price: float,
    net_gex: float,
    total_call_gex: float,
    total_put_gex: float,
    net_vex: float,
    king_strike: Optional[float],
    king_gex: Optional[float],
    gatekeeper_strike: Optional[float],
    gatekeeper_gex: Optional[float],
    zero_gamma_level: Optional[float],
    opex_warning: bool,
    zones: List[Dict]
) -> int:
    """
    Save a GEX snapshot to the database.
    Updates if snapshot for today already exists.

    Returns: snapshot_id
    """
    conn = get_connection()
    cursor = conn.cursor()
    today = date.today()

    try:
        # Insert or replace today's snapshot
        cursor.execute("""
            INSERT INTO gex_snapshots (
                symbol, snapshot_date, spot_price, net_gex,
                total_call_gex, total_put_gex, net_vex,
                king_strike, king_gex, gatekeeper_strike, gatekeeper_gex,
                zero_gamma_level, opex_warning
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, snapshot_date) DO UPDATE SET
                snapshot_time = CURRENT_TIMESTAMP,
                spot_price = excluded.spot_price,
                net_gex = excluded.net_gex,
                total_call_gex = excluded.total_call_gex,
                total_put_gex = excluded.total_put_gex,
                net_vex = excluded.net_vex,
                king_strike = excluded.king_strike,
                king_gex = excluded.king_gex,
                gatekeeper_strike = excluded.gatekeeper_strike,
                gatekeeper_gex = excluded.gatekeeper_gex,
                zero_gamma_level = excluded.zero_gamma_level,
                opex_warning = excluded.opex_warning
        """, (
            symbol, today, spot_price, net_gex,
            total_call_gex, total_put_gex, net_vex,
            king_strike, king_gex, gatekeeper_strike, gatekeeper_gex,
            zero_gamma_level, opex_warning
        ))

        # Get the snapshot ID
        cursor.execute("""
            SELECT id FROM gex_snapshots
            WHERE symbol = ? AND snapshot_date = ?
        """, (symbol, today))
        snapshot_id = cursor.fetchone()[0]

        # Delete old zones for this snapshot
        cursor.execute("DELETE FROM zone_history WHERE snapshot_id = ?", (snapshot_id,))

        # Save top zones (limit to 10)
        for zone in zones[:10]:
            cursor.execute("""
                INSERT INTO zone_history (
                    snapshot_id, strike, gex, gex_type, role, strength
                ) VALUES (?, ?, ?, ?, ?, ?)
            """, (
                snapshot_id,
                zone.get('strike'),
                zone.get('gex'),
                zone.get('type'),
                zone.get('role'),
                zone.get('strength')
            ))

        conn.commit()
        return snapshot_id

    finally:
        conn.close()


def get_history(symbol: str, days: int = 30) -> List[Dict]:
    """
    Get historical GEX data for a symbol.

    Returns list of daily snapshots, newest first.
    """
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            snapshot_date,
            spot_price,
            net_gex,
            net_vex,
            king_strike,
            king_gex,
            gatekeeper_strike,
            gatekeeper_gex,
            zero_gamma_level,
            opex_warning
        FROM gex_snapshots
        WHERE symbol = ?
        ORDER BY snapshot_date DESC
        LIMIT ?
    """, (symbol, days))

    rows = cursor.fetchall()
    conn.close()

    return [dict(row) for row in rows]


def get_king_history(symbol: str, days: int = 30) -> List[Dict]:
    """
    Get history of King strike movements.
    Useful for seeing how the King level shifts over time.
    """
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            snapshot_date,
            spot_price,
            king_strike,
            king_gex,
            gatekeeper_strike
        FROM gex_snapshots
        WHERE symbol = ? AND king_strike IS NOT NULL
        ORDER BY snapshot_date DESC
        LIMIT ?
    """, (symbol, days))

    rows = cursor.fetchall()
    conn.close()

    return [dict(row) for row in rows]


def get_all_symbols_latest() -> List[Dict]:
    """Get the latest snapshot for all tracked symbols."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            symbol,
            snapshot_date,
            spot_price,
            net_gex,
            king_strike
        FROM gex_snapshots
        WHERE snapshot_date = (
            SELECT MAX(snapshot_date) FROM gex_snapshots s2
            WHERE s2.symbol = gex_snapshots.symbol
        )
        ORDER BY symbol
    """)

    rows = cursor.fetchall()
    conn.close()

    return [dict(row) for row in rows]


# Initialize database on import
init_database()
