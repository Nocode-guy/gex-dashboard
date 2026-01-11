"""
GEX Dashboard API Server

FastAPI server providing GEX data for the web dashboard and NinjaTrader indicator.
"""
import os
import sys

# Load environment variables from .env file
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass
import asyncio
import hashlib
import secrets

# Fix Windows console encoding issues
if sys.platform == 'win32':
    # Set stdout/stderr to use utf-8 with error replacement
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except AttributeError:
        # Python < 3.7 fallback
        import codecs
        sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'replace')
        sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'replace')
from datetime import datetime, timedelta
from typing import Dict, Optional, List
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Cookie, Response, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import uvicorn

# Session signing
try:
    from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
    SESSIONS_AVAILABLE = True
except ImportError:
    SESSIONS_AVAILABLE = False
    print("[WARNING] itsdangerous not installed - authentication disabled")

from zoneinfo import ZoneInfo

from config import (
    DEFAULT_TICKERS, DEFAULT_REFRESH_INTERVAL, REFRESH_INTERVALS,
    STALE_WARNING_MULTIPLIER, STALE_ERROR_MULTIPLIER,
    MIN_OPEN_INTEREST, MIN_GEX_VALUE
)
from gex_calculator import GEXCalculator, GEXResult

# PostgreSQL database (for user auth)
try:
    from db_postgres import init_db, close_db, get_pool
    POSTGRES_AVAILABLE = True
except ImportError:
    POSTGRES_AVAILABLE = False
    print("[WARNING] PostgreSQL module not available - using legacy auth")

# New auth system
try:
    from auth.routes import router as auth_router, user_router, admin_router, setup_router
    from auth.security import get_current_user, require_auth, require_admin
    NEW_AUTH_AVAILABLE = True
except ImportError as e:
    NEW_AUTH_AVAILABLE = False
    get_current_user = None
    require_auth = None
    require_admin = None
    print(f"[WARNING] New auth module not available: {e}")

# AI usage tracking (PostgreSQL)
try:
    from db_postgres import (
        save_chat_message, get_chat_history, cleanup_old_chats,
        get_user_token_limit, update_user_token_usage, check_user_token_limit,
        log_ai_usage, get_user_usage_report, get_all_users_usage_report,
        set_user_token_limit, reset_monthly_usage_if_new_month
    )
    AI_TRACKING_AVAILABLE = True
except ImportError:
    AI_TRACKING_AVAILABLE = False
    print("[WARNING] AI usage tracking not available (db_postgres not loaded)")

# Massive (Polygon) options flow client
try:
    from massive_client import get_massive_client, MassiveClient
    MASSIVE_AVAILABLE = True
    print("[OK] Massive options flow client available")
except ImportError as e:
    MASSIVE_AVAILABLE = False
    print(f"[WARNING] Massive client not available: {e}")

# Live options trades streaming disabled (requires Polygon WebSocket plan)
OPTIONS_WS_AVAILABLE = False

# OpenAI for AI trading analysis
try:
    from openai import OpenAI
    OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
    if OPENAI_API_KEY:
        openai_client = OpenAI(api_key=OPENAI_API_KEY)
        OPENAI_AVAILABLE = True
        print("[OK] OpenAI client available")
    else:
        openai_client = None
        OPENAI_AVAILABLE = False
        print("[WARNING] OPENAI_API_KEY not set - AI analysis disabled")
except ImportError:
    openai_client = None
    OPENAI_AVAILABLE = False
    print("[WARNING] openai package not installed - AI analysis disabled")


# =============================================================================
# AUTHENTICATION CONFIG
# =============================================================================
# Password: Set via environment variable or use default
# To change: set GEX_PASSWORD environment variable
AUTH_PASSWORD = os.environ.get("GEX_PASSWORD", "gex2024")

# Secret key for signing session cookies
# In production, set GEX_SECRET_KEY environment variable
SECRET_KEY = os.environ.get("GEX_SECRET_KEY", secrets.token_hex(32))

# Session duration (24 hours)
SESSION_MAX_AGE = 60 * 60 * 24

# Enable/disable authentication (disabled by default)
AUTH_ENABLED = os.environ.get("GEX_AUTH_ENABLED", "false").lower() == "true"


def hash_password(password: str) -> str:
    """Hash password using SHA-256."""
    return hashlib.sha256(password.encode()).hexdigest()


def verify_password(password: str, expected: str) -> bool:
    """Verify password against expected value."""
    return hash_password(password) == hash_password(expected)


# Session serializer
if SESSIONS_AVAILABLE:
    session_serializer = URLSafeTimedSerializer(SECRET_KEY)


def create_session_token(data: dict) -> str:
    """Create a signed session token."""
    if not SESSIONS_AVAILABLE:
        return ""
    return session_serializer.dumps(data)


def verify_session_token(token: str) -> Optional[dict]:
    """Verify and decode a session token."""
    if not SESSIONS_AVAILABLE or not token:
        return None
    try:
        return session_serializer.loads(token, max_age=SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None


class LoginRequest(BaseModel):
    """Login request body."""
    password: str

# Eastern timezone for market hours
ET = ZoneInfo("America/New_York")
from database import save_snapshot, get_history, get_king_history
from regime_tracker import get_regime_tracker, RegimeTracker

# Data source priority: Massive > Tradier > MarketData
from config import ACTIVE_PROVIDER

# Massive/Polygon (PRIMARY - real-time OPRA data from all 17 exchanges)
try:
    from massive_gex_provider import get_massive_provider
    MASSIVE_GEX_AVAILABLE = True
    print("[OK] Massive GEX provider available (PRIMARY)")
except ImportError as e:
    MASSIVE_GEX_AVAILABLE = False
    print(f"[WARNING] Massive GEX provider not available: {e}")

# Tradier client (BACKUP - real-time options data)
try:
    from tradier_client import get_tradier_client
    TRADIER_AVAILABLE = True
except ImportError:
    TRADIER_AVAILABLE = False
    print("[WARNING] Tradier client not available (backup)")

# MarketData.app (BACKUP - real Greeks from OPRA)
try:
    from marketdata_client import get_marketdata_client
    MARKETDATA_AVAILABLE = True
except ImportError:
    MARKETDATA_AVAILABLE = False
    print("[WARNING] MarketData.app not available")


_options_client = None
_options_client_name = None

# Check if any data provider is available
DATA_PROVIDER_AVAILABLE = MASSIVE_GEX_AVAILABLE or TRADIER_AVAILABLE or MARKETDATA_AVAILABLE

def get_options_client():
    """Get the active options data client based on priority: Massive > Tradier > MarketData."""
    global _options_client, _options_client_name

    # Return cached client if already created
    if _options_client is not None:
        return _options_client

    # Priority 1: Massive (best - real-time OPRA from all exchanges)
    if MASSIVE_GEX_AVAILABLE:
        print("[OK] Data provider: Massive/Polygon (REAL-TIME OPRA)")
        _options_client = get_massive_provider()
        _options_client_name = "massive"
        return _options_client

    # Priority 2: Tradier (backup)
    if TRADIER_AVAILABLE:
        print("[OK] Data provider: Tradier (BACKUP)")
        _options_client = get_tradier_client()
        _options_client_name = "tradier"
        return _options_client

    # Priority 3: MarketData.app (backup)
    if MARKETDATA_AVAILABLE:
        print("[OK] Data provider: MarketData.app (BACKUP)")
        _options_client = get_marketdata_client()
        _options_client_name = "marketdata"
        return _options_client

    raise RuntimeError("No options data provider available")


def get_provider_name():
    """Get display name of active provider."""
    if _options_client_name == "massive":
        return "Massive (OPRA)"
    elif _options_client_name == "tradier":
        return "Tradier"
    else:
        return "MarketData"

# Quiver Quant: Congress trading, dark pool, insider trading, WSB sentiment
try:
    from quiver_client import get_quiver_client
    QUIVER_AVAILABLE = True
except ImportError:
    QUIVER_AVAILABLE = False
    print("[WARNING] Quiver Quant not available")

# Order flow: Unusual Whales (real-time options flow)
try:
    from orderflow_client import get_flow_client, enrich_gex_with_flow
    ORDERFLOW_AVAILABLE = get_flow_client() is not None
    if ORDERFLOW_AVAILABLE:
        print("[OK] Unusual Whales order flow available")
    else:
        print("[INFO] No UNUSUAL_WHALES_API_KEY - order flow disabled")
except ImportError:
    ORDERFLOW_AVAILABLE = False
    print("[INFO] Order flow client not available")

# Historical validation
try:
    from historical_validation import get_validator
    VALIDATION_AVAILABLE = True
except ImportError:
    VALIDATION_AVAILABLE = False


# =============================================================================
# CACHE
# =============================================================================
class GEXCache:
    """Simple in-memory cache for GEX results."""

    def __init__(self):
        self.data: Dict[str, GEXResult] = {}
        self.last_update: Dict[str, datetime] = {}

    def get(self, symbol: str) -> Optional[GEXResult]:
        return self.data.get(symbol.upper())

    def set(self, symbol: str, result: GEXResult):
        symbol = symbol.upper()
        self.data[symbol] = result
        self.last_update[symbol] = datetime.now()

    def is_stale(self, symbol: str, refresh_interval: int) -> tuple[bool, bool]:
        """
        Check if cached data is stale.

        Returns: (warning_stale, error_stale)
        """
        symbol = symbol.upper()
        if symbol not in self.last_update:
            return True, True

        age = (datetime.now() - self.last_update[symbol]).total_seconds()
        warning_threshold = refresh_interval * STALE_WARNING_MULTIPLIER
        error_threshold = refresh_interval * STALE_ERROR_MULTIPLIER

        return age > warning_threshold, age > error_threshold

    def get_age(self, symbol: str) -> Optional[float]:
        """Get age of cached data in seconds."""
        symbol = symbol.upper()
        if symbol not in self.last_update:
            return None
        return (datetime.now() - self.last_update[symbol]).total_seconds()

    def clear(self, symbol: str = None):
        """Clear cache for a symbol or all symbols."""
        if symbol:
            symbol = symbol.upper()
            self.data.pop(symbol, None)
            self.last_update.pop(symbol, None)
            print(f"[Cache] Cleared cache for {symbol}")
        else:
            self.data.clear()
            self.last_update.clear()
            print("[Cache] Cleared all cache")


cache = GEXCache()
calculator = GEXCalculator()
regime_tracker = get_regime_tracker()


# =============================================================================
# INTRADAY BASELINE TRACKING
# =============================================================================
class IntradayBaseline:
    """Stores baseline GEX values at market open for intraday change tracking."""

    def __init__(self, symbol: str, timestamp: datetime, spot_price: float,
                 net_gex: float, net_vex: float, net_dex: float,
                 king_strike: Optional[float], king_gex: Optional[float],
                 zone_baselines: Dict[float, Dict[str, float]]):
        self.symbol = symbol
        self.timestamp = timestamp
        self.spot_price = spot_price
        self.net_gex = net_gex
        self.net_vex = net_vex
        self.net_dex = net_dex
        self.king_strike = king_strike
        self.king_gex = king_gex
        # zone_baselines: {strike: {"gex": X, "vex": Y, "dex": Z}}
        self.zone_baselines = zone_baselines


class DailyBaselineTracker:
    """
    Tracks intraday GEX changes from market open baseline.

    Stores first snapshot of each trading day as baseline,
    then calculates deltas for subsequent refreshes.
    """

    def __init__(self):
        self.baselines: Dict[str, IntradayBaseline] = {}
        self.current_date: Optional[str] = None

    def _get_trading_date(self) -> str:
        """Get current trading date in ET."""
        now_et = datetime.now(ET)
        # If before 4 AM ET, consider it previous trading day's extended session
        if now_et.hour < 4:
            now_et = now_et - timedelta(days=1)
        return now_et.strftime("%Y-%m-%d")

    def _is_market_hours(self) -> bool:
        """Check if currently in market hours (9:00 AM - 4:30 PM ET)."""
        now_et = datetime.now(ET)
        market_open = now_et.replace(hour=9, minute=0, second=0, microsecond=0)
        market_close = now_et.replace(hour=16, minute=30, second=0, microsecond=0)
        return market_open <= now_et <= market_close

    def _is_weekend(self) -> bool:
        """Check if today is Saturday (5) or Sunday (6)."""
        now_et = datetime.now(ET)
        return now_et.weekday() >= 5

    def _should_refresh(self) -> bool:
        """Check if we should be refreshing (market hours on weekdays only)."""
        if self._is_weekend():
            return False
        return self._is_market_hours()

    def _clear_old_baselines(self):
        """Clear baselines from previous trading days."""
        today = self._get_trading_date()
        if self.current_date != today:
            print(f"[Baseline] New trading day: {today} - clearing old baselines")
            self.baselines.clear()
            self.current_date = today

    def update_baseline(self, symbol: str, result: GEXResult):
        """
        Update baseline for a symbol if this is first snapshot of the day.
        Called on each refresh.
        """
        self._clear_old_baselines()
        symbol = symbol.upper()

        # Only set baseline if we don't have one for today
        if symbol not in self.baselines:
            # Build zone baselines (GEX only - zones don't have vex/dex)
            zone_baselines = {}
            for zone in result.zones:
                zone_baselines[zone.strike] = {
                    "gex": zone.gex
                }

            baseline = IntradayBaseline(
                symbol=symbol,
                timestamp=datetime.now(ET),
                spot_price=result.spot_price,
                net_gex=result.net_gex,
                net_vex=result.net_vex,
                net_dex=result.net_dex,
                king_strike=result.king_node.strike if result.king_node else None,
                king_gex=result.king_node.gex if result.king_node else None,
                zone_baselines=zone_baselines
            )
            self.baselines[symbol] = baseline
            print(f"[Baseline] Set baseline for {symbol}: GEX=${result.net_gex/1e9:.2f}B")

    def get_deltas(self, symbol: str, result: GEXResult) -> Optional[dict]:
        """
        Calculate deltas from baseline for current result.
        Returns dict with intraday changes.
        """
        symbol = symbol.upper()
        baseline = self.baselines.get(symbol)

        if not baseline:
            return None

        # Calculate zone-level deltas (GEX only for zones)
        zone_deltas = {}
        for zone in result.zones:
            if zone.strike in baseline.zone_baselines:
                base = baseline.zone_baselines[zone.strike]
                delta_gex = zone.gex - base["gex"]
                zone_deltas[zone.strike] = {
                    "delta_gex": delta_gex,
                    "pct_gex": ((delta_gex) / abs(base["gex"]) * 100) if base["gex"] != 0 else 0
                }

        return {
            "baseline_time": baseline.timestamp.strftime("%H:%M:%S ET"),
            "baseline_date": baseline.timestamp.strftime("%Y-%m-%d"),
            "delta_net_gex": result.net_gex - baseline.net_gex,
            "delta_net_vex": result.net_vex - baseline.net_vex,
            "delta_net_dex": result.net_dex - baseline.net_dex,
            "delta_spot": result.spot_price - baseline.spot_price,
            "baseline_net_gex": baseline.net_gex,
            "baseline_spot": baseline.spot_price,
            "baseline_king_strike": baseline.king_strike,
            "king_changed": (result.king_node.strike if result.king_node else None) != baseline.king_strike,
            "zone_deltas": zone_deltas
        }

    def get_baseline(self, symbol: str) -> Optional[IntradayBaseline]:
        """Get baseline for a symbol."""
        return self.baselines.get(symbol.upper())


baseline_tracker = DailyBaselineTracker()


# =============================================================================
# BACKGROUND REFRESH
# =============================================================================
import json

# File to persist user's symbol list
SYMBOLS_FILE = os.path.join(os.path.dirname(__file__), "user_symbols.json")

def load_saved_symbols() -> List[str]:
    """Load symbols from file, or return defaults if not found."""
    try:
        if os.path.exists(SYMBOLS_FILE):
            with open(SYMBOLS_FILE, 'r') as f:
                data = json.load(f)
                if isinstance(data, list) and len(data) > 0:
                    print(f"[OK] Loaded {len(data)} saved symbols: {', '.join(data)}")
                    return data
    except Exception as e:
        print(f"[WARN] Could not load symbols file: {e}")
    return list(DEFAULT_TICKERS)

def save_symbols(symbols: List[str]):
    """Save symbols to file."""
    try:
        print(f"[SAVE] Attempting to save to: {SYMBOLS_FILE}")
        with open(SYMBOLS_FILE, 'w') as f:
            json.dump(symbols, f)
            f.flush()
        print(f"[OK] Saved {len(symbols)} symbols: {', '.join(symbols)}")
    except Exception as e:
        import traceback
        print(f"[ERROR] Could not save symbols: {e}")
        traceback.print_exc()


class RefreshManager:
    """Manages background refresh of GEX data."""

    def __init__(self):
        self.refresh_interval = DEFAULT_REFRESH_INTERVAL * 60  # Convert to seconds
        self.active_symbols: List[str] = load_saved_symbols()
        self.running = False
        self._paused = False
        self._task: Optional[asyncio.Task] = None

    def _is_market_hours(self) -> bool:
        """Check if currently in market hours (9:00 AM - 4:30 PM ET)."""
        now_et = datetime.now(ET)
        market_open = now_et.replace(hour=9, minute=0, second=0, microsecond=0)
        market_close = now_et.replace(hour=16, minute=30, second=0, microsecond=0)
        return market_open <= now_et <= market_close

    def _is_weekend(self) -> bool:
        """Check if today is Saturday (5) or Sunday (6)."""
        now_et = datetime.now(ET)
        return now_et.weekday() >= 5

    def _should_refresh(self) -> bool:
        """Check if we should be refreshing (market hours on weekdays only)."""
        if self._is_weekend():
            return False
        return self._is_market_hours()

    async def refresh_symbol(self, symbol: str):
        """Refresh GEX data for a single symbol."""
        try:
            if not DATA_PROVIDER_AVAILABLE:
                print(f"[ERROR] No data provider available for {symbol}")
                return

            client = get_options_client()
            spot, contracts = await client.get_full_chain_with_greeks(symbol)

            if spot == 0 or not contracts:
                print(f"No data returned for {symbol}")
                return

            result = calculator.calculate(
                symbol=symbol,
                spot_price=spot,
                contracts=contracts,
                refresh_interval=self.refresh_interval
            )

            # Detect changes from previous snapshot
            changes = regime_tracker.detect_changes(
                symbol=symbol,
                spot_price=result.spot_price,
                net_gex=result.net_gex,
                king_strike=result.king_node.strike if result.king_node else None,
                king_gex=result.king_node.gex if result.king_node else None,
                zero_gamma_level=result.zero_gamma_level,
                net_vex=result.net_vex,
                net_dex=result.net_dex
            )

            # Check for big price moves
            regime_tracker.check_big_move(symbol, result.spot_price)

            cache.set(symbol, result)

            # Update intraday baseline (stores first snapshot of the day)
            baseline_tracker.update_baseline(symbol, result)

            # Save to historical database
            try:
                save_snapshot(
                    symbol=symbol,
                    spot_price=result.spot_price,
                    net_gex=result.net_gex,
                    total_call_gex=result.total_call_gex,
                    total_put_gex=result.total_put_gex,
                    net_vex=result.net_vex,
                    king_strike=result.king_node.strike if result.king_node else None,
                    king_gex=result.king_node.gex if result.king_node else None,
                    gatekeeper_strike=result.gatekeeper_node.strike if result.gatekeeper_node else None,
                    gatekeeper_gex=result.gatekeeper_node.gex if result.gatekeeper_node else None,
                    zero_gamma_level=result.zero_gamma_level,
                    opex_warning=result.opex_warning,
                    zones=[z.to_dict() for z in result.zones]
                )
            except Exception as db_err:
                print(f"[DB] Error saving snapshot: {db_err}")

            # Save intraday snapshot for playback (every 5 min during market hours)
            try:
                from database import save_intraday_snapshot
                # Prepare heatmap data for storage
                heatmap_data = None
                if result.heatmap_strikes:
                    heatmap_data = {
                        "strikes": result.heatmap_strikes,
                        "expirations": result.heatmap_expirations,
                        "data": result.heatmap_data
                    }

                saved = save_intraday_snapshot(
                    symbol=symbol,
                    spot_price=result.spot_price,
                    net_gex=result.net_gex,
                    net_vex=result.net_vex,
                    net_dex=result.net_dex,
                    king_strike=result.king_node.strike if result.king_node else None,
                    king_gex=result.king_node.gex if result.king_node else None,
                    gatekeeper_strike=result.gatekeeper_node.strike if result.gatekeeper_node else None,
                    zero_gamma_level=result.zero_gamma_level,
                    zones=[z.to_dict() for z in result.zones],
                    heatmap_data=heatmap_data
                )
                if saved:
                    print(f"[Playback] Saved intraday snapshot for {symbol}")
            except Exception as pb_err:
                print(f"[Playback] Error saving snapshot for {symbol}: {pb_err}")

            print(f"[{datetime.now().strftime('%H:%M:%S')}] [{get_provider_name()}] Refreshed {symbol}: "
                  f"${spot:.2f}, {len(result.zones)} zones, "
                  f"King: {result.king_node.strike if result.king_node else 'N/A'}")

            # Update Flow Service for WAVE indicator (runs in background)
            try:
                from flow_service import get_flow_service
                if MASSIVE_AVAILABLE:
                    from massive_client import get_massive_client
                    client = get_massive_client()
                    flow_summary = await client.get_flow_summary(symbol=symbol, spot_price=spot)
                    service = get_flow_service()
                    await service.process_flow_summary(symbol, flow_summary.to_dict())
            except Exception as flow_err:
                pass  # Silent fail - flow is supplementary

        except Exception as e:
            print(f"Error refreshing {symbol}: {e}")

    async def refresh_all(self):
        """Refresh all active symbols."""
        tasks = [self.refresh_symbol(sym) for sym in self.active_symbols]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def run(self):
        """Background refresh loop with market hours awareness."""
        self.running = True
        self._paused = False
        print(f"Starting refresh loop: {self.refresh_interval}s interval, "
              f"symbols: {', '.join(self.active_symbols)}")

        while self.running:
            if self._should_refresh():
                # Market is open - refresh normally
                if self._paused:
                    print(f"[{datetime.now(ET).strftime('%H:%M:%S ET')}] Market open - resuming refresh")
                    self._paused = False
                await self.refresh_all()
                await asyncio.sleep(self.refresh_interval)
            else:
                # Outside market hours - pause refreshing
                if not self._paused:
                    now_et = datetime.now(ET)
                    if self._is_weekend():
                        reason = "Weekend"
                    else:
                        reason = "After hours (4:30pm-9:00am ET)"
                    print(f"[{now_et.strftime('%H:%M:%S ET')}] {reason} - pausing refresh (data cached)")
                    self._paused = True
                # Check every 60 seconds if market has opened
                await asyncio.sleep(60)

    def start(self):
        """Start background refresh."""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.run())

    def stop(self):
        """Stop background refresh."""
        self.running = False
        if self._task:
            self._task.cancel()

    def set_interval(self, minutes: int):
        """Set refresh interval in minutes."""
        if minutes in REFRESH_INTERVALS:
            self.refresh_interval = minutes * 60
            print(f"Refresh interval set to {minutes} minutes")

    def add_symbol(self, symbol: str):
        """Add a symbol to refresh list."""
        symbol = symbol.upper()
        print(f"[ADD] Adding {symbol} to watchlist...")
        if symbol not in self.active_symbols:
            self.active_symbols.append(symbol)
            print(f"[ADD] Current symbols: {self.active_symbols}")
            save_symbols(self.active_symbols)
        else:
            print(f"[ADD] {symbol} already in list")

    def remove_symbol(self, symbol: str):
        """Remove a symbol from refresh list."""
        symbol = symbol.upper()
        if symbol in self.active_symbols:
            self.active_symbols.remove(symbol)
            save_symbols(self.active_symbols)


refresh_manager = RefreshManager()


# =============================================================================
# BIG MOVER SCANNER
# =============================================================================
async def run_big_mover_scanner():
    """
    Background task that scans top symbols for big price moves.
    Uses Tradier API for fast quote fetching.
    """
    await asyncio.sleep(30)  # Wait 30s after startup before first scan

    while True:
        try:
            # Get Tradier client for fast quotes
            if TRADIER_AVAILABLE:
                from tradier_client import get_tradier_client
                tradier = get_tradier_client()

                # Scan all symbols in the SCAN_SYMBOLS list
                for symbol in regime_tracker.SCAN_SYMBOLS:
                    try:
                        # Get current price from Tradier (fast)
                        price = await tradier.get_spot_price(symbol)
                        if price and price > 0:
                            # Check for big move
                            regime_tracker.check_big_move(symbol, price)
                    except Exception as e:
                        pass  # Silently skip failed symbols

                print(f"[BigMover] Scanned {len(regime_tracker.SCAN_SYMBOLS)} symbols")

        except Exception as e:
            print(f"[BigMover] Scanner error: {e}")

        await asyncio.sleep(60)  # Scan every 60 seconds


# =============================================================================
# FASTAPI APP
# =============================================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    # Startup: Initial data fetch
    print("GEX Dashboard starting...")

    # Initialize PostgreSQL database (for user auth)
    if POSTGRES_AVAILABLE:
        db_ok = await init_db()
        if db_ok:
            print("[OK] PostgreSQL database connected")
        else:
            print("[WARNING] PostgreSQL not configured - using legacy auth")

    # Initialize regime tracker (fetch VIX)
    print("Fetching VIX for regime detection...")
    regime_tracker.update_regime()

    await refresh_manager.refresh_all()
    refresh_manager.start()

    # Initialize Flow Service (WAVE indicator background tasks)
    from flow_service import init_flow_service, get_flow_service
    await init_flow_service()
    print("[OK] Flow Service started (WAVE snapshots every 10s)")

    # Initialize Alert Service (monitors for trading opportunities)
    from alert_service import init_alert_service, get_alert_service
    from massive_gex_provider import get_massive_provider
    flow_svc = get_flow_service()
    gex_provider = get_massive_provider()
    alert_svc = init_alert_service(gex_provider=gex_provider, flow_service=flow_svc)
    asyncio.create_task(alert_svc.start())
    print("[OK] Alert Service started (monitoring top 30 symbols)")

    # Start Big Mover Scanner
    asyncio.create_task(run_big_mover_scanner())
    print("[OK] Big Mover Scanner started (checking top 30 symbols every 60s)")

    # Initialize trading journal tables
    try:
        from db_postgres import init_journal_tables
        await init_journal_tables()
    except Exception as e:
        print(f"[WARNING] Journal tables init: {e}")

    # Start AI usage tracking background tasks
    if AI_TRACKING_AVAILABLE and POSTGRES_AVAILABLE:
        async def weekly_chat_cleanup():
            """Delete chat history older than 7 days (runs daily at midnight)."""
            while True:
                try:
                    await asyncio.sleep(86400)  # Wait 24 hours
                    deleted = await cleanup_old_chats(days=7)
                    print(f"[AI Cleanup] Deleted {deleted} old chat messages")
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    print(f"[AI Cleanup] Error: {e}")

        async def monthly_usage_reset():
            """Reset monthly token usage on the 1st of each month (checks hourly)."""
            while True:
                try:
                    await asyncio.sleep(3600)  # Check every hour
                    reset_count = await reset_monthly_usage_if_new_month()
                    if reset_count > 0:
                        print(f"[AI Reset] Reset monthly usage for {reset_count} users")
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    print(f"[AI Reset] Error: {e}")

        asyncio.create_task(weekly_chat_cleanup())
        asyncio.create_task(monthly_usage_reset())
        print("[OK] AI usage tracking tasks started (cleanup & monthly reset)")

    yield

    # Shutdown
    refresh_manager.stop()

    # Stop Alert Service
    try:
        from alert_service import get_alert_service
        alert_svc = get_alert_service()
        if alert_svc:
            alert_svc.stop()
    except Exception as e:
        print(f"[WARNING] Alert service shutdown error: {e}")

    # Stop Flow Service
    try:
        from flow_service import get_flow_service
        service = get_flow_service()
        await service.stop()
    except Exception as e:
        print(f"[WARNING] Flow service shutdown error: {e}")

    if POSTGRES_AVAILABLE:
        await close_db()
    print("GEX Dashboard stopped")


app = FastAPI(
    title="GEX Dashboard API",
    description="Gamma Exposure (GEX) calculation and visualization API",
    version="1.0.0",
    lifespan=lifespan
)

# CORS - allow all origins for local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount new auth routers (if available and PostgreSQL configured)
if NEW_AUTH_AVAILABLE:
    app.include_router(auth_router)      # /auth/* endpoints
    app.include_router(user_router)      # /api/me/* endpoints
    app.include_router(admin_router)     # /admin/* endpoints
    app.include_router(setup_router)     # /setup/* endpoints (one-time admin setup)
    print("[OK] New auth system mounted (/auth, /api/me, /admin, /setup)")


# =============================================================================
# API ROUTES
# =============================================================================
@app.get("/health")
async def health_check():
    """Health check and API info."""
    return {
        "status": "ok",
        "service": "GEX Dashboard API",
        "version": "1.0.0",
        "active_symbols": refresh_manager.active_symbols,
        "refresh_interval_min": refresh_manager.refresh_interval // 60
    }


@app.get("/")
async def root(refresh_token: Optional[str] = Cookie(None)):
    """Root redirect - sends to /app or /login based on auth status."""
    # If new auth is enabled and no refresh token, go to login
    if NEW_AUTH_AVAILABLE and AUTH_ENABLED:
        if not refresh_token:
            return RedirectResponse(url="/login", status_code=302)
    # Otherwise go to app (frontend will handle auth check)
    return RedirectResponse(url="/app", status_code=302)


@app.get("/status")
async def get_status():
    """
    Provider and module status dashboard.
    Shows what's active, last refresh times, and any issues.
    """
    from config import MODULES, PROVIDERS, CACHE_TTL

    # Calculate provider statuses
    provider_status = {}

    # Data provider status (Tradier or MarketData)
    data_last_refresh = None
    data_symbols_loaded = 0
    for sym in refresh_manager.active_symbols:
        age = cache.get_age(sym)
        if age is not None:
            data_symbols_loaded += 1
            if data_last_refresh is None or age < data_last_refresh:
                data_last_refresh = age

    active_provider = PROVIDERS.get(ACTIVE_PROVIDER, PROVIDERS["marketdata"])
    provider_status["options_data"] = {
        "name": active_provider["name"],
        "provider": ACTIVE_PROVIDER,
        "status": "ok" if data_symbols_loaded > 0 else "no_data",
        "realtime": active_provider["realtime"],
        "delay_minutes": active_provider.get("delay_minutes", 0),
        "symbols_loaded": data_symbols_loaded,
        "last_refresh_sec": round(data_last_refresh, 1) if data_last_refresh else None,
    }

    # Unusual Whales status
    provider_status["unusual_whales"] = {
        "name": PROVIDERS["unusual_whales"]["name"],
        "status": "enabled" if ORDERFLOW_AVAILABLE else "disabled",
        "realtime": PROVIDERS["unusual_whales"]["realtime"],
        "note": "Set UNUSUAL_WHALES_API_KEY to enable" if not ORDERFLOW_AVAILABLE else "Active",
    }

    # Module status
    module_status = {
        name: {
            "enabled": enabled,
            "cache_ttl_sec": CACHE_TTL.get(name, CACHE_TTL.get("gex", 60)),
        }
        for name, enabled in MODULES.items()
    }

    # Regime status
    regime = regime_tracker.current_regime
    regime_info = regime.to_dict() if regime else {"status": "not_loaded"}

    return {
        "timestamp": datetime.now().isoformat(),
        "providers": provider_status,
        "modules": module_status,
        "regime": regime_info,
        "cache": {
            "symbols_cached": len([s for s in refresh_manager.active_symbols if cache.get(s)]),
            "total_symbols": len(refresh_manager.active_symbols),
        },
        "refresh_loop": {
            "running": refresh_manager.running,
            "paused": refresh_manager._paused,
            "interval_sec": refresh_manager.refresh_interval,
            "market_open": refresh_manager._is_market_hours(),
            "is_weekend": refresh_manager._is_weekend(),
        }
    }


@app.get("/gex/{symbol}")
async def get_gex(
    symbol: str,
    refresh: bool = Query(False, description="Force refresh from API")
):
    """
    Get full GEX data for a symbol.

    Returns complete heatmap data for the dashboard.
    """
    symbol = symbol.upper()

    # Force refresh if requested or no cached data
    if refresh or cache.get(symbol) is None:
        await refresh_manager.refresh_symbol(symbol)

    result = cache.get(symbol)
    if result is None:
        raise HTTPException(status_code=404, detail=f"No data for {symbol}")

    # Add staleness info
    response = result.to_dict()
    warning_stale, error_stale = cache.is_stale(symbol, refresh_manager.refresh_interval)
    data_age = cache.get_age(symbol)

    response["stale_warning"] = warning_stale
    response["stale_error"] = error_stale
    response["data_age_sec"] = data_age

    # Trading-grade fields
    response["timestamp_utc"] = result.timestamp.strftime("%Y-%m-%dT%H:%M:%SZ")
    response["data_latency_seconds"] = round(data_age, 1) if data_age else 0
    from config import PROVIDERS, ACTIVE_PROVIDER
    active_prov = PROVIDERS.get(ACTIVE_PROVIDER, PROVIDERS["marketdata"])
    response["delayed"] = not active_prov["realtime"]
    response["data_provider"] = ACTIVE_PROVIDER

    # VEX data quality flag (vanna is derived, not from OPRA)
    from config import VEX_DATA_QUALITY
    response["vex_quality"] = VEX_DATA_QUALITY

    # Add regime and change detection data
    regime = regime_tracker.current_regime
    if regime:
        response["regime"] = regime.to_dict()

        # Calculate per-symbol reliability score
        reliability_score = regime_tracker.calculate_reliability_score(
            symbol=symbol,
            regime=regime.regime,
            spot_price=result.spot_price,
            zero_gamma_level=result.zero_gamma_level,
            net_gex=result.net_gex
        )
        response["reliability"] = reliability_score.to_dict()

    # Get recent alerts for this symbol
    response["alerts"] = regime_tracker.get_recent_alerts(symbol, limit=5)

    # Get change detection data (since last refresh)
    prev_snapshot = regime_tracker.previous_snapshots.get(symbol)
    if prev_snapshot:
        response["changes"] = {
            "delta_gex": round(result.net_gex - prev_snapshot.net_gex, 0),
            "delta_vex": round(result.net_vex - prev_snapshot.net_vex, 0),
            "delta_dex": round(result.net_dex - prev_snapshot.net_dex, 0),
            "previous_king": prev_snapshot.king_strike,
            "previous_update": prev_snapshot.timestamp.isoformat()
        }

    # Get intraday deltas (since market open / first snapshot of day)
    intraday = baseline_tracker.get_deltas(symbol, result)
    if intraday:
        response["intraday"] = intraday

    return response


@app.get("/candles/{symbol}")
async def get_candles(
    symbol: str,
    resolution: str = Query("5", description="Candle resolution: 1, 5, 15, 30, 60, D, W, M"),
    count: int = Query(100, description="Number of candles to return")
):
    """
    Get historical price candles for charting.

    Returns OHLCV data for the specified symbol.
    Also includes GEX levels for overlay on the chart.
    """
    symbol = symbol.upper()

    if not DATA_PROVIDER_AVAILABLE:
        raise HTTPException(status_code=503, detail="Market data not available")

    # Use Tradier for candles (MassiveGEXProvider doesn't support candles)
    try:
        from tradier_client import get_tradier_client
        tradier = get_tradier_client()
        candles = await tradier.get_candles(symbol, resolution=resolution, count=count)
    except Exception as e:
        print(f"[Candles] Tradier client error: {e}")
        # Fallback to empty candles with just levels
        candles = []

    if not candles:
        raise HTTPException(status_code=404, detail=f"No candle data for {symbol}")

    # Get current GEX levels for chart overlay - MATCHING KEY ZONES LOGIC
    result = cache.get(symbol)
    levels = {}
    if result:
        spot_price = result.spot_price or 0

        # Find levels from ALL zones for accurate chart overlay
        # This matches what's visible on the heatmap (not just Key Zones top 10)
        if result.zones:
            # Use ALL zones to find the most significant levels
            all_zones = result.zones  # Full list (20 zones)
            positive_zones = [z for z in all_zones if z.gex > 0]
            negative_zones = [z for z in all_zones if z.gex < 0]

            # MAGNET = Highest positive GEX (price target / absorption)
            magnet = max(positive_zones, key=lambda z: z.gex) if positive_zones else None

            # RESISTANCE = Highest positive GEX ABOVE spot price
            zones_above = [z for z in positive_zones if z.strike > spot_price]
            resistance = max(zones_above, key=lambda z: z.gex) if zones_above else None

            # SUPPORT = Highest positive GEX BELOW spot price
            zones_below = [z for z in positive_zones if z.strike < spot_price]
            support = max(zones_below, key=lambda z: z.gex) if zones_below else None

            # ACCELERATOR = Highest negative GEX (vol expansion zone)
            accelerator = min(negative_zones, key=lambda z: z.gex) if negative_zones else None

            levels = {
                "magnet": magnet.strike if magnet else None,
                "magnet_gex": magnet.gex if magnet else None,
                "resistance": resistance.strike if resistance else None,
                "resistance_gex": resistance.gex if resistance else None,
                "support": support.strike if support else None,
                "support_gex": support.gex if support else None,
                "accelerator": accelerator.strike if accelerator else None,
                "accelerator_gex": accelerator.gex if accelerator else None,
                "zero_gamma": result.zero_gamma_level,
                "expected_move": result.expected_move,
                "spot_price": spot_price
            }
        else:
            levels = {
                "magnet": result.king_node.strike if result.king_node else None,
                "zero_gamma": result.zero_gamma_level,
                "expected_move": result.expected_move,
                "spot_price": spot_price
            }

    # Include all zones for heatmap rendering (with strength for liquidity visualization)
    zones_data = []
    if result and result.zones:
        zones_data = [{"strike": z.strike, "gex": z.gex, "strength": z.strength} for z in result.zones]

    return {
        "symbol": symbol,
        "resolution": resolution,
        "candles": candles,
        "levels": levels,
        "zones": zones_data
    }


@app.get("/gex/{symbol}/levels")
async def get_gex_levels(
    symbol: str,
    refresh: bool = Query(False, description="Force refresh from API")
):
    """
    Get compact GEX levels for NinjaTrader indicator.

    Returns only the essential data needed for chart overlay.
    """
    symbol = symbol.upper()

    # Force refresh if requested or no cached data
    if refresh or cache.get(symbol) is None:
        await refresh_manager.refresh_symbol(symbol)

    result = cache.get(symbol)
    if result is None:
        raise HTTPException(status_code=404, detail=f"No data for {symbol}")

    response = result.to_levels_dict()

    # Add staleness info
    warning_stale, error_stale = cache.is_stale(symbol, refresh_manager.refresh_interval)
    data_age = cache.get_age(symbol)
    response["stale"] = error_stale
    response["stale_warning"] = warning_stale
    response["data_age_sec"] = data_age
    response["data_latency_seconds"] = round(data_age, 1) if data_age else 0
    from config import PROVIDERS, ACTIVE_PROVIDER
    active_prov = PROVIDERS.get(ACTIVE_PROVIDER, PROVIDERS["marketdata"])
    response["delayed"] = not active_prov["realtime"]
    response["data_provider"] = ACTIVE_PROVIDER

    # Add reliability score for NinjaTrader
    regime = regime_tracker.current_regime
    if regime:
        reliability_score = regime_tracker.calculate_reliability_score(
            symbol=symbol,
            regime=regime.regime,
            spot_price=result.spot_price,
            zero_gamma_level=result.zero_gamma_level,
            net_gex=result.net_gex
        )
        response["reliability"] = {
            "score": reliability_score.score,
            "grade": reliability_score.grade
        }

    return response


@app.get("/symbols")
async def get_symbols():
    """Get list of available symbols with cached data."""
    symbols = []
    for sym in refresh_manager.active_symbols:
        result = cache.get(sym)
        if result:
            symbols.append({
                "symbol": sym,
                "spot_price": result.spot_price,
                "net_gex": result.net_gex,
                "king_strike": result.king_node.strike if result.king_node else None,
                "last_update": result.timestamp.isoformat(),
                "opex_warning": result.opex_warning
            })
    return {"symbols": symbols}


@app.post("/symbols/{symbol}")
async def add_symbol(symbol: str):
    """Add a symbol to the active refresh list."""
    symbol = symbol.upper()

    # Check if already added
    if symbol in refresh_manager.active_symbols:
        return {
            "status": "ok",
            "symbol": symbol,
            "message": f"{symbol} already in watchlist"
        }

    # Quick validation - just check if we can get a quote
    try:
        if not DATA_PROVIDER_AVAILABLE:
            raise HTTPException(status_code=503, detail="MarketData.app client not available")

        client = get_options_client()
        quote = await client.get_quote(symbol)

        if not quote or quote.get("last", 0) == 0:
            raise HTTPException(
                status_code=404,
                detail=f"Symbol {symbol} not found or has no price data."
            )

        spot = quote.get("last", 0)

        # Add symbol immediately
        refresh_manager.add_symbol(symbol)

        # Fetch full data in background (don't wait)
        asyncio.create_task(refresh_manager.refresh_symbol(symbol))

        return {
            "status": "ok",
            "symbol": symbol,
            "message": f"{symbol} added at ${spot:.2f} - loading data..."
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to add {symbol}: {str(e)}")


@app.delete("/symbols/{symbol}")
async def remove_symbol(symbol: str):
    """Remove a symbol from the active refresh list."""
    symbol = symbol.upper()
    refresh_manager.remove_symbol(symbol)
    return {"status": "ok", "symbol": symbol, "message": "Symbol removed"}


@app.post("/settings/refresh")
async def set_refresh_interval(minutes: int = Query(..., description="Refresh interval in minutes")):
    """Set the refresh interval."""
    if minutes not in REFRESH_INTERVALS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid interval. Must be one of: {REFRESH_INTERVALS}"
        )
    refresh_manager.set_interval(minutes)
    return {"status": "ok", "refresh_interval_min": minutes}


@app.get("/settings")
async def get_settings():
    """Get current settings."""
    return {
        "refresh_interval_min": refresh_manager.refresh_interval // 60,
        "available_intervals": REFRESH_INTERVALS,
        "active_symbols": refresh_manager.active_symbols,
        "min_oi": MIN_OPEN_INTEREST,
        "min_gex": MIN_GEX_VALUE
    }


@app.post("/cache/clear")
async def clear_cache(symbol: str = Query(None, description="Symbol to clear, or all if not specified")):
    """Clear cache and reset baselines for fresh data."""
    # Clear cache
    cache.clear(symbol)

    # Reset baselines
    if symbol:
        baseline_tracker.baselines.pop(symbol.upper(), None)
        print(f"[Baseline] Reset baseline for {symbol.upper()}")
    else:
        baseline_tracker.baselines.clear()
        print("[Baseline] Reset all baselines")

    return {
        "status": "ok",
        "cleared": symbol.upper() if symbol else "all",
        "message": "Cache and baselines cleared. Next fetch will use fresh data."
    }


@app.get("/search")
async def search_symbols(q: str = Query(..., min_length=1, description="Search query")):
    """
    Search for symbols by name or ticker.
    Returns matching stocks/ETFs with their symbols.
    """
    if not DATA_PROVIDER_AVAILABLE:
        return {"query": q, "results": [{"symbol": q.upper(), "name": q.upper(), "type": "UNKNOWN"}]}

    client = get_options_client()
    results = client.search_symbol(q, max_results=8)
    return {"query": q, "results": results}


# =============================================================================
# HISTORICAL DATA ENDPOINTS
# =============================================================================
@app.get("/history/{symbol}")
async def get_symbol_history(
    symbol: str,
    days: int = Query(30, description="Number of days of history")
):
    """
    Get historical GEX data for a symbol.
    Returns daily snapshots for charting and analysis.
    """
    symbol = symbol.upper()
    history = get_history(symbol, days)

    if not history:
        return {"symbol": symbol, "history": [], "message": "No historical data yet"}

    return {
        "symbol": symbol,
        "days": days,
        "history": history
    }


@app.get("/history/{symbol}/king")
async def get_symbol_king_history(
    symbol: str,
    days: int = Query(30, description="Number of days of history")
):
    """
    Get history of King strike movements for a symbol.
    Useful for seeing how key levels shift over time.
    """
    symbol = symbol.upper()
    history = get_king_history(symbol, days)

    return {
        "symbol": symbol,
        "days": days,
        "king_history": history
    }


# =============================================================================
# PLAYBACK ENDPOINTS
# =============================================================================
@app.get("/playback/{symbol}/dates")
async def get_playback_dates(
    symbol: str,
    days: int = Query(30, description="Number of days to look back")
):
    """
    Get list of dates with playback data available.
    """
    from database import get_available_playback_dates
    symbol = symbol.upper()
    dates = get_available_playback_dates(symbol, days)

    return {
        "symbol": symbol,
        "available_dates": dates,
        "count": len(dates)
    }


@app.get("/playback/{symbol}/{date}")
async def get_playback_data(
    symbol: str,
    date: str
):
    """
    Get all intraday snapshots for playback on a specific date.
    Returns data in chronological order for timeline scrubbing.

    Args:
        symbol: Stock symbol
        date: Date in YYYY-MM-DD format
    """
    from database import get_intraday_snapshots
    symbol = symbol.upper()
    snapshots = get_intraday_snapshots(symbol, date)

    if not snapshots:
        return {
            "symbol": symbol,
            "date": date,
            "snapshots": [],
            "count": 0,
            "message": "No playback data for this date"
        }

    # Format timestamps for frontend
    formatted = []
    for snap in snapshots:
        formatted.append({
            "time": snap['timestamp'],
            "spot_price": snap['spot_price'],
            "net_gex": snap['net_gex'],
            "net_vex": snap.get('net_vex'),
            "net_dex": snap.get('net_dex'),
            "king_strike": snap.get('king_strike'),
            "king_gex": snap.get('king_gex'),
            "gatekeeper_strike": snap.get('gatekeeper_strike'),
            "zero_gamma_level": snap.get('zero_gamma_level'),
            "zones": snap.get('zones', []),
            "heatmap": snap.get('heatmap')
        })

    return {
        "symbol": symbol,
        "date": date,
        "snapshots": formatted,
        "count": len(formatted),
        "first_time": formatted[0]['time'] if formatted else None,
        "last_time": formatted[-1]['time'] if formatted else None
    }


# =============================================================================
# REGIME & ALERTS ENDPOINTS
# =============================================================================
@app.get("/regime")
async def get_regime():
    """
    Get current market regime and VIX data.
    Returns volatility regime, GEX reliability, and VIX levels.
    """
    # Update regime (fetches latest VIX)
    regime = regime_tracker.update_regime()

    return {
        "regime": regime.to_dict(),
        "description": {
            "low": "Low volatility - GEX levels very reliable, expect mean reversion",
            "normal": "Normal volatility - GEX levels reliable",
            "elevated": "Elevated volatility - GEX levels somewhat reliable",
            "high": "High volatility - GEX levels less reliable, expect larger moves",
            "extreme": "Extreme volatility - GEX levels unreliable, risk management critical"
        }.get(regime.regime.value, "")
    }


@app.get("/alerts")
async def get_alerts(
    symbol: Optional[str] = Query(None, description="Filter by symbol"),
    limit: int = Query(20, description="Max alerts to return")
):
    """
    Get recent alerts for all symbols or a specific symbol.
    Alerts include King flips, net GEX flips, zero gamma crosses, etc.
    """
    alerts = regime_tracker.get_recent_alerts(symbol, limit)

    return {
        "alerts": alerts,
        "total": len(alerts),
        "filter": symbol
    }


# =============================================================================
# ORDER FLOW ENDPOINTS
# =============================================================================

# NOTE: Specific routes must be defined BEFORE dynamic {symbol} routes

@app.get("/flow/tape")
async def get_trade_tape(
    symbol: str = Query(default=None, description="Filter by symbol (optional)"),
    min_premium: float = Query(default=10000, description="Minimum premium filter"),
    limit: int = Query(default=50, description="Max trades to return")
):
    """
    Get recent large trades for the live tape.
    """
    from flow_service import get_flow_service

    service = get_flow_service()
    trades = await service.get_recent_trades(symbol=symbol, min_premium=int(min_premium), limit=limit)

    # Convert datetime objects to ISO strings
    for trade in trades:
        if 'timestamp' in trade and hasattr(trade['timestamp'], 'isoformat'):
            trade['timestamp'] = trade['timestamp'].isoformat()
        if 'expiration' in trade and hasattr(trade['expiration'], 'isoformat'):
            trade['expiration'] = trade['expiration'].isoformat()

    return {"trades": trades, "count": len(trades)}


@app.get("/flow/leaderboard")
async def get_flow_leaderboard(
    limit: int = Query(default=20, description="Max symbols to return")
):
    """
    Get top symbols by flow activity.
    """
    from flow_service import get_flow_service

    service = get_flow_service()
    entries = await service.get_leaderboard(limit=limit)

    # Convert datetime objects
    for entry in entries:
        if 'timestamp' in entry and hasattr(entry['timestamp'], 'isoformat'):
            entry['timestamp'] = entry['timestamp'].isoformat()

    return {"leaderboard": entries, "count": len(entries)}


@app.get("/flow/{symbol}")
async def get_flow(
    symbol: str,
    strike_range: int = Query(default=20, description="Number of strikes above/below spot to include")
):
    """
    Get options flow summary for a symbol.
    Uses Massive (Polygon) if available, falls back to Unusual Whales.
    Returns flow summary with bullish/bearish premium and pressure at each strike.
    """
    symbol = symbol.upper()

    # Try Massive first (preferred)
    if MASSIVE_AVAILABLE:
        # Get current spot price from cache
        spot_price = 0
        cached = cache.get(symbol)
        if cached:
            spot_price = cached.spot_price

        try:
            client = get_massive_client()
            summary = await client.get_flow_summary(
                symbol=symbol,
                spot_price=spot_price,
                strike_range=strike_range
            )
            result = summary.to_dict()
            result["data_source"] = "massive"

            # Update WAVE service with flow data
            try:
                from flow_service import get_flow_service
                service = get_flow_service()
                await service.process_flow_summary(symbol, result)
            except Exception as wave_err:
                print(f"[WAVE] Update error: {wave_err}")

            return result
        except Exception as e:
            print(f"[Massive] Error: {e} - trying Unusual Whales fallback")

    # Fallback to Unusual Whales
    if ORDERFLOW_AVAILABLE:
        from orderflow_client import get_flow_client
        client = get_flow_client()

        try:
            summary = await client.get_flow_summary(symbol)
            result = summary.to_dict()
            result["data_source"] = "unusual_whales"

            # Update WAVE service with flow data
            try:
                from flow_service import get_flow_service
                service = get_flow_service()
                await service.process_flow_summary(symbol, result)
            except Exception as wave_err:
                print(f"[WAVE] Update error: {wave_err}")

            return result
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error fetching flow: {str(e)}")

    raise HTTPException(
        status_code=503,
        detail="Options flow not available. Configure MASSIVE_API_KEY or UNUSUAL_WHALES_API_KEY."
    )


@app.get("/flow/{symbol}/wave")
async def get_wave_indicator(
    symbol: str,
    minutes: int = Query(default=60, description="Minutes of history to return")
):
    """
    Get WAVE indicator data for charting.
    Shows cumulative call vs put premium over time.
    """
    from flow_service import get_flow_service

    symbol = symbol.upper()
    service = get_flow_service()

    # Get WAVE data
    data = await service.get_wave_data(symbol, minutes)

    # If no history, fetch current flow to populate
    if not data.get('wave_history') and not data.get('current_wave'):
        # Trigger a flow fetch to populate WAVE data
        if MASSIVE_AVAILABLE:
            try:
                spot_price = 0
                cached = cache.get(symbol)
                if cached:
                    spot_price = cached.spot_price

                client = get_massive_client()
                summary = await client.get_flow_summary(symbol=symbol, spot_price=spot_price)
                await service.process_flow_summary(symbol, summary.to_dict())
                data = await service.get_wave_data(symbol, minutes)
            except Exception as e:
                print(f"[WAVE] Error fetching initial flow: {e}")

    return data


@app.get("/flow/{symbol}/levels")
async def get_flow_levels(symbol: str):
    """
    Get GEX levels enriched with order flow context.
    Shows if dealers are actually trading at GEX levels.
    """
    symbol = symbol.upper()

    if not ORDERFLOW_AVAILABLE:
        raise HTTPException(
            status_code=503,
            detail="Order flow not available. Set UNUSUAL_WHALES_API_KEY environment variable."
        )

    # Get cached GEX data
    result = cache.get(symbol)
    if result is None:
        raise HTTPException(status_code=404, detail=f"No GEX data for {symbol}. Fetch /gex/{symbol} first.")

    from orderflow_client import get_flow_client, enrich_gex_with_flow
    client = get_flow_client()

    try:
        # Convert zones to dict format for enrichment
        gex_zones = [z.to_dict() for z in result.zones]

        # Enrich with flow data
        enriched = await enrich_gex_with_flow(symbol, gex_zones, client)

        return {
            "symbol": symbol,
            "spot_price": result.spot_price,
            "timestamp": result.timestamp.isoformat(),
            "levels": [e.to_dict() for e in enriched],
            "flow_available": True
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error enriching with flow: {str(e)}")


@app.get("/flow/market/tide")
async def get_market_tide():
    """
    Get overall market flow sentiment (market tide).
    Shows aggregate bullish/bearish flow across the market.
    """
    if not ORDERFLOW_AVAILABLE:
        raise HTTPException(
            status_code=503,
            detail="Order flow not available. Set UNUSUAL_WHALES_API_KEY environment variable."
        )

    from orderflow_client import get_flow_client
    client = get_flow_client()

    try:
        tide = await client.get_market_tide()
        return {"market_tide": tide, "timestamp": datetime.now().isoformat()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching market tide: {str(e)}")


@app.get("/flow/{symbol}/realtime")
async def get_realtime_flow(symbol: str):
    """
    Get options volume by strike from Polygon (via cached GEX data).
    Uses daily volume from options chain for ALL strikes.
    Filters to 15 strikes above and 15 below spot price.
    """
    symbol = symbol.upper()
    STRIKES_ABOVE = 15
    STRIKES_BELOW = 15

    try:
        # Get cached GEX data which includes volume_by_strike from Polygon
        cached = cache.get(symbol)
        if not cached:
            raise HTTPException(
                status_code=404,
                detail=f"No cached data for {symbol}. Trigger a GEX refresh first."
            )

        spot_price = cached.spot_price
        polygon_volume = cached.volume_by_strike  # Dict[float, {call_volume, put_volume}]

        # Only major ETFs have true $1 strike intervals across all expirations
        # Stocks like TSLA have $5 intervals in aggregated options chains
        ETFS_1_DOLLAR = {'SPY', 'QQQ', 'IWM', 'DIA'}

        if symbol in ETFS_1_DOLLAR:
            strike_interval = 1
        else:
            # Detect interval from Polygon volume data
            polygon_strikes = sorted(polygon_volume.keys())
            if len(polygon_strikes) >= 3:
                intervals = [polygon_strikes[i+1] - polygon_strikes[i] for i in range(len(polygon_strikes)-1)]
                small_intervals = [i for i in intervals if 0.5 <= i <= 10]
                if small_intervals:
                    from collections import Counter
                    interval_counts = Counter([round(i, 1) for i in small_intervals])
                    strike_interval = interval_counts.most_common(1)[0][0]
                else:
                    strike_interval = 1
            else:
                strike_interval = 1

        # Round spot to nearest strike
        rounded_spot = round(spot_price / strike_interval) * strike_interval
        print(f"[Flow Realtime] {symbol}: spot={spot_price:.2f}, interval={strike_interval}, rounded={rounded_spot}")

        # Generate ALL strikes in range (15 below to 15 above)
        strike_pressure = {}
        total_call_vol = 0
        total_put_vol = 0

        for i in range(-STRIKES_BELOW, STRIKES_ABOVE + 1):
            strike = rounded_spot + (i * strike_interval)
            strike_key = str(int(strike)) if strike == int(strike) else str(strike)

            # Get Polygon volume for this strike
            vol_data = polygon_volume.get(strike, {})
            call_vol = vol_data.get("call_volume", 0)
            put_vol = vol_data.get("put_volume", 0)

            strike_pressure[strike_key] = {
                "call_premium": call_vol,  # Using volume as "premium" for display
                "put_premium": put_vol,
                "call_volume": call_vol,
                "put_volume": put_vol,
                "call_volume_ask": 0,
                "call_volume_bid": 0,
                "put_volume_ask": 0,
                "put_volume_bid": 0,
                "net_premium": call_vol - put_vol,
            }
            total_call_vol += call_vol
            total_put_vol += put_vol

        print(f"[Flow Realtime] Generated {len(strike_pressure)} strikes from Polygon volume")

        net_volume = total_call_vol - total_put_vol
        sentiment = "bullish" if net_volume > 0 else "bearish" if net_volume < 0 else "neutral"

        return {
            "symbol": symbol,
            "spot_price": spot_price,
            "timestamp": datetime.now().isoformat(),
            "data_source": "polygon_volume",
            "strike_pressure": strike_pressure,
            "total_call_premium": total_call_vol,
            "total_put_premium": total_put_vol,
            "net_premium": net_volume,
            "sentiment": sentiment,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching volume data: {str(e)}")


@app.get("/flow/{symbol}/volume-uw")
async def get_volume_uw(symbol: str, strikes_above: int = 5, strikes_below: int = 5):
    """
    REAL-TIME intraday flow by strike from Unusual Whales.
    Shows CONTINUOUS strikes around current price (no gaps).
    Strikes with no activity show as 0.
    """
    symbol = symbol.upper()

    if not ORDERFLOW_AVAILABLE:
        raise HTTPException(status_code=503, detail="UW API not available")

    try:
        from orderflow_client import get_flow_client
        client = get_flow_client()

        # Get real-time intraday flow from UW
        uw_data = await client.get_flow_by_strike_intraday(symbol)

        # Get spot price from Tradier (real-time)
        spot_price = 0
        try:
            from tradier_client import get_tradier_client
            tradier = get_tradier_client()
            quote = await tradier.get_quote(symbol)
            if quote:
                spot_price = quote.get("last", 0) or quote.get("close", 0)
        except:
            pass

        # Fallback to cache
        if spot_price == 0:
            cached = cache.get(symbol)
            if cached:
                spot_price = cached.spot_price

        if spot_price == 0:
            raise HTTPException(status_code=404, detail=f"Could not get spot price for {symbol}")

        # Determine strike interval based on symbol type
        # ETFs have $1 strikes, stocks typically have $2.50
        etfs = {"SPY", "QQQ", "IWM", "DIA", "XLF", "XLE", "XLK", "GLD", "SLV", "TLT", "EEM", "VXX", "UVXY", "SQQQ", "TQQQ"}
        if symbol in etfs:
            strike_interval = 1.0
        else:
            # For stocks, use $2.50 intervals (standard for liquid stocks)
            strike_interval = 2.5

        # Generate continuous strikes around spot price
        # Round spot to nearest strike
        nearest_strike = round(spot_price / strike_interval) * strike_interval

        # Generate strikes above and below
        generated_strikes = []
        for i in range(-strikes_below, strikes_above + 1):
            strike = nearest_strike + (i * strike_interval)
            if strike > 0:
                generated_strikes.append(strike)

        # Build response with all generated strikes
        strike_pressure = {}
        total_call = 0
        total_put = 0

        for strike in generated_strikes:
            # Check if UW has data for this strike
            if uw_data and strike in uw_data:
                data = uw_data[strike]
                strike_pressure[str(int(strike)) if strike == int(strike) else str(strike)] = {
                    "call_premium": data.get("call_premium", 0),
                    "put_premium": data.get("put_premium", 0),
                    "call_volume": data.get("call_volume", 0),
                    "put_volume": data.get("put_volume", 0),
                    "call_volume_ask": data.get("call_volume_ask", 0),
                    "call_volume_bid": data.get("call_volume_bid", 0),
                    "put_volume_ask": data.get("put_volume_ask", 0),
                    "put_volume_bid": data.get("put_volume_bid", 0),
                    "net_premium": data.get("net_premium", 0),
                }
                total_call += data.get("call_premium", 0)
                total_put += data.get("put_premium", 0)
            else:
                # No data for this strike - show zeros
                strike_pressure[str(int(strike)) if strike == int(strike) else str(strike)] = {
                    "call_premium": 0,
                    "put_premium": 0,
                    "call_volume": 0,
                    "put_volume": 0,
                    "call_volume_ask": 0,
                    "call_volume_bid": 0,
                    "put_volume_ask": 0,
                    "put_volume_bid": 0,
                    "net_premium": 0,
                }

        print(f"[UW Volume] {symbol}: spot=${spot_price:.2f}, interval=${strike_interval}, showing {len(strike_pressure)} continuous strikes")

        return {
            "symbol": symbol,
            "spot_price": spot_price,
            "strike_interval": strike_interval,
            "timestamp": datetime.now().isoformat(),
            "data_source": "unusual_whales_intraday",
            "strike_pressure": strike_pressure,
            "total_call_premium": total_call,
            "total_put_premium": total_put,
            "net_premium": total_call - total_put,
            "sentiment": "bullish" if total_call > total_put else "bearish",
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching UW volume: {str(e)}")


@app.get("/flow/{symbol}/volume-live")
async def get_volume_live(symbol: str, source: str = Query("polygon", description="Data source: polygon, uw, auto")):
    """
    Real-time intraday volume by strike.
    - source=polygon: Polygon data (default - shows all trades)
    - source=uw: Unusual Whales intraday (large trades only)
    - source=auto: Try UW first, fallback to Polygon

    Returns current strike ±2 strikes (5 rows total) for focused view.
    """
    symbol = symbol.upper()
    WINDOW_SIZE = 2  # ±2 strikes from current

    try:
        # Get spot price from Tradier (real-time)
        spot_price = 0
        try:
            from tradier_client import get_tradier_client
            tradier = get_tradier_client()
            quote = await tradier.get_quote(symbol)
            if quote:
                spot_price = quote.get("last", 0) or quote.get("close", 0)
        except:
            pass

        # Fallback to cache
        if spot_price == 0:
            cached = cache.get(symbol)
            if cached:
                spot_price = cached.spot_price

        if spot_price == 0:
            raise HTTPException(status_code=404, detail=f"Cannot get spot price for {symbol}")

        # Try UW first (if source is auto or uw)
        uw_data = None
        data_source = "polygon_live"

        if source in ("auto", "uw") and ORDERFLOW_AVAILABLE:
            try:
                from orderflow_client import get_flow_client
                client = get_flow_client()
                uw_data = await client.get_flow_by_strike_intraday(symbol)
                if uw_data:
                    data_source = "unusual_whales_intraday"
            except Exception as e:
                print(f"[Volume] UW failed for {symbol}: {e}")
                uw_data = None

        # If UW failed or source=polygon, use Polygon
        if uw_data is None or source == "polygon":
            from massive_gex_provider import get_massive_provider
            provider = get_massive_provider()
            polygon_volume = await provider.get_volume_by_strike_fast(symbol, spot_price)
            data_source = "polygon_live"
        else:
            polygon_volume = None

        # Determine strike interval
        etfs = {"SPY", "QQQ", "IWM", "DIA", "XLF", "XLE", "XLK", "GLD", "SLV", "TLT"}
        if symbol in etfs:
            strike_interval = 1.0
        else:
            strike_interval = 2.5  # $2.50 for stocks

        # Find nearest strike to spot
        nearest_strike = round(spot_price / strike_interval) * strike_interval

        # Generate window: ±WINDOW_SIZE strikes
        window_strikes = []
        for i in range(-WINDOW_SIZE, WINDOW_SIZE + 1):
            strike = nearest_strike + (i * strike_interval)
            if strike > 0:
                window_strikes.append(strike)

        # Build response
        strike_pressure = {}
        total_call = 0
        total_put = 0

        for strike in window_strikes:
            strike_key = str(int(strike)) if strike == int(strike) else str(strike)

            if uw_data and strike in uw_data:
                # Use UW intraday data
                data = uw_data[strike]
                strike_pressure[strike_key] = {
                    "call_premium": data.get("call_premium", 0),
                    "put_premium": data.get("put_premium", 0),
                    "call_volume": data.get("call_volume", 0),
                    "put_volume": data.get("put_volume", 0),
                    "call_volume_ask": data.get("call_volume_ask", 0),
                    "call_volume_bid": data.get("call_volume_bid", 0),
                    "put_volume_ask": data.get("put_volume_ask", 0),
                    "put_volume_bid": data.get("put_volume_bid", 0),
                    "net_premium": data.get("net_premium", 0),
                }
                total_call += data.get("call_premium", 0)
                total_put += data.get("put_premium", 0)
            elif polygon_volume and strike in polygon_volume:
                # Use Polygon volume
                vol = polygon_volume[strike]
                call_vol = vol.get("call_volume", 0)
                put_vol = vol.get("put_volume", 0)
                strike_pressure[strike_key] = {
                    "call_premium": call_vol,
                    "put_premium": put_vol,
                    "call_volume": call_vol,
                    "put_volume": put_vol,
                    "net_premium": call_vol - put_vol,
                }
                total_call += call_vol
                total_put += put_vol
            else:
                # No data - show zeros
                strike_pressure[strike_key] = {
                    "call_premium": 0,
                    "put_premium": 0,
                    "call_volume": 0,
                    "put_volume": 0,
                    "net_premium": 0,
                }

        print(f"[Volume] {symbol}: spot=${spot_price:.2f}, nearest={nearest_strike}, window={len(window_strikes)} strikes, source={data_source}")

        return {
            "symbol": symbol,
            "spot_price": spot_price,
            "nearest_strike": nearest_strike,
            "strike_interval": strike_interval,
            "timestamp": datetime.now().isoformat(),
            "data_source": data_source,
            "strike_pressure": strike_pressure,
            "total_call_premium": total_call,
            "total_put_premium": total_put,
            "net_premium": total_call - total_put,
            "sentiment": "bullish" if total_call > total_put else "bearish",
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching live volume: {str(e)}")


@app.get("/flow/{symbol}/signals")
async def get_confluence_signals(symbol: str):
    """
    Generate LONG/SHORT signals based on GEX + Volume confluence.

    LONG signal: Bullish volume flow + price near positive GEX support
    SHORT signal: Bearish volume flow + price near negative GEX resistance
    """
    symbol = symbol.upper()

    try:
        # Get GEX data
        gex_data = cache.get(symbol)
        if not gex_data:
            raise HTTPException(status_code=404, detail=f"No GEX data for {symbol}")

        spot_price = gex_data.spot_price

        # Get volume data
        volume_response = await get_volume_live(symbol, source="polygon")
        volume_data = volume_response.get("strike_pressure", {})
        nearest_strike = volume_response.get("nearest_strike", 0)

        # Get GEX key levels from nodes
        king_strike = gex_data.king_strike if hasattr(gex_data, 'king_strike') else 0
        king_gex = gex_data.king_gex if hasattr(gex_data, 'king_gex') else 0

        # Find support/resistance from zones
        support_level = 0
        resistance_level = 0
        magnet_level = king_strike  # King often acts as magnet

        # Get zones for detailed GEX at each strike
        zones = {}
        if gex_data.zones:
            for z in gex_data.zones:
                zones[z.strike] = z.gex
                # Find nearest support (positive GEX below spot)
                if z.gex > 0 and z.strike < spot_price:
                    if support_level == 0 or z.strike > support_level:
                        support_level = z.strike
                # Find nearest resistance (positive GEX above spot)
                if z.gex > 0 and z.strike > spot_price:
                    if resistance_level == 0 or z.strike < resistance_level:
                        resistance_level = z.strike

        signals = []

        # Analyze each strike in volume data
        for strike_str, vol in volume_data.items():
            strike = float(strike_str)
            call_vol = vol.get("call_volume", 0)
            put_vol = vol.get("put_volume", 0)
            net = vol.get("net_premium", 0)

            # Skip if no meaningful volume
            total_vol = call_vol + put_vol
            if total_vol < 1000:
                continue

            # Calculate flow bias percentage
            if total_vol > 0:
                flow_pct = (call_vol - put_vol) / total_vol * 100
            else:
                flow_pct = 0

            # Get GEX at this strike
            strike_gex = zones.get(strike, 0)

            # Determine signal
            signal = None
            confidence = 0
            reason = []

            # LONG conditions
            if flow_pct > 10:  # Calls winning by 10%+
                reason.append(f"Bullish flow ({flow_pct:+.0f}%)")
                confidence += 30

                # Check GEX confluence
                if strike_gex > 0:
                    reason.append(f"Positive GEX (+${abs(strike_gex)/1e6:.0f}M)")
                    confidence += 30

                # Near support level
                if support_level and abs(strike - support_level) <= 2:
                    reason.append(f"Near support ({support_level})")
                    confidence += 20

                # Price below this strike (support below)
                if strike < spot_price:
                    reason.append("Support below price")
                    confidence += 10

                if confidence >= 50:
                    signal = "LONG"

            # SHORT conditions
            elif flow_pct < -10:  # Puts winning by 10%+
                reason.append(f"Bearish flow ({flow_pct:+.0f}%)")
                confidence += 30

                # Check GEX confluence (negative GEX = acceleration zone)
                if strike_gex < 0:
                    reason.append(f"Negative GEX (-${abs(strike_gex)/1e6:.0f}M)")
                    confidence += 30

                # Near resistance/magnet level
                if resistance_level and abs(strike - resistance_level) <= 2:
                    reason.append(f"Near resistance ({resistance_level})")
                    confidence += 20
                if magnet_level and abs(strike - magnet_level) <= 2:
                    reason.append(f"Near magnet ({magnet_level})")
                    confidence += 15

                # Price above this strike (resistance above)
                if strike > spot_price:
                    reason.append("Resistance above price")
                    confidence += 10

                if confidence >= 50:
                    signal = "SHORT"

            if signal:
                signals.append({
                    "strike": strike,
                    "signal": signal,
                    "confidence": min(confidence, 100),
                    "flow_pct": round(flow_pct, 1),
                    "call_volume": call_vol,
                    "put_volume": put_vol,
                    "gex": strike_gex,
                    "reasons": reason,
                })

        # Sort by confidence
        signals.sort(key=lambda x: x["confidence"], reverse=True)

        # Determine overall bias
        long_signals = [s for s in signals if s["signal"] == "LONG"]
        short_signals = [s for s in signals if s["signal"] == "SHORT"]

        if long_signals and not short_signals:
            overall = "BULLISH"
        elif short_signals and not long_signals:
            overall = "BEARISH"
        elif long_signals and short_signals:
            overall = "MIXED"
        else:
            overall = "NEUTRAL"

        return {
            "symbol": symbol,
            "spot_price": spot_price,
            "timestamp": datetime.now().isoformat(),
            "overall_bias": overall,
            "signals": signals[:5],  # Top 5 signals
            "gex_levels": {
                "support": support_level,
                "resistance": resistance_level,
                "magnet": magnet_level,
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generating signals: {str(e)}")


@app.get("/flow/{symbol}/wave-realtime")
async def get_wave_realtime(symbol: str):
    """
    Get REAL-TIME WAVE indicator data from Unusual Whales.
    Returns net premium ticks for building the WAVE chart.
    """
    symbol = symbol.upper()

    if not ORDERFLOW_AVAILABLE:
        raise HTTPException(
            status_code=503,
            detail="Real-time WAVE not available. Unusual Whales API required."
        )

    from orderflow_client import get_flow_client
    client = get_flow_client()

    try:
        # Get net premium ticks for WAVE
        ticks = await client.get_net_premium_ticks(symbol)

        # Calculate cumulative values for WAVE chart
        cumulative_call = 0
        cumulative_put = 0
        wave_data = []

        for tick in ticks:
            cumulative_call += tick.get("call_premium", 0)
            cumulative_put += tick.get("put_premium", 0)

            wave_data.append({
                "timestamp": tick.get("timestamp"),
                "call_premium": tick.get("call_premium", 0),
                "put_premium": tick.get("put_premium", 0),
                "net_premium": tick.get("net_premium", 0),
                "cumulative_call": cumulative_call,
                "cumulative_put": cumulative_put,
                "cumulative_net": cumulative_call - cumulative_put,
                "net_delta": tick.get("net_delta", 0),
            })

        # Current WAVE values
        current_wave = {
            "cumulative_call": cumulative_call,
            "cumulative_put": cumulative_put,
            "cumulative_net": cumulative_call - cumulative_put,
            "sentiment": "bullish" if cumulative_call > cumulative_put else "bearish",
        }

        return {
            "symbol": symbol,
            "timestamp": datetime.now().isoformat(),
            "data_source": "unusual_whales_realtime",
            "current_wave": current_wave,
            "wave_history": wave_data,
            "tick_count": len(wave_data),
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching WAVE data: {str(e)}")


# =============================================================================
# ALERTS ENDPOINTS
# =============================================================================

@app.get("/alerts")
async def get_alerts(
    limit: int = Query(20, description="Number of alerts to return"),
    include_dismissed: bool = Query(False, description="Include dismissed alerts")
):
    """
    Get recent trading alerts.
    Monitors top 30 symbols for:
    - GEX regime changes (0γ flip crosses)
    - Level breaks (support/resistance)
    - Big moves (1%+ in short time)
    - Flow flips (put/call ratio reversal)
    - Acceleration zone entries
    """
    from alert_service import get_alert_service

    service = get_alert_service()
    if not service:
        return {
            "alerts": [],
            "total": 0,
            "message": "Alert service not initialized"
        }

    alerts = service.get_alerts(limit=limit, include_dismissed=include_dismissed)

    return {
        "alerts": alerts,
        "total": len(alerts),
        "monitored_symbols": service.MONITORED_SYMBOLS,
        "check_interval_sec": service.check_interval
    }


@app.post("/alerts/{alert_id}/dismiss")
async def dismiss_alert(alert_id: str):
    """Dismiss an alert."""
    from alert_service import get_alert_service

    service = get_alert_service()
    if not service:
        raise HTTPException(status_code=503, detail="Alert service not available")

    service.dismiss_alert(alert_id)
    return {"status": "ok", "dismissed": alert_id}


@app.delete("/alerts")
async def clear_alerts(symbol: Optional[str] = Query(None, description="Clear alerts for specific symbol")):
    """Clear all alerts or alerts for a specific symbol."""
    from alert_service import get_alert_service

    service = get_alert_service()
    if not service:
        raise HTTPException(status_code=503, detail="Alert service not available")

    service.clear_alerts(symbol)
    return {"status": "ok", "cleared": symbol or "all"}


# =============================================================================
# AI TRADING ANALYSIS ENDPOINTS
# =============================================================================

# System prompt for trading analysis
AI_TRADING_SYSTEM_PROMPT = """You are an expert options trader and gamma exposure (GEX) analyst. Analyze the provided market data and give a clear, actionable trading read.

Your analysis should follow this structure:
1. **Big Picture (Gamma + Structure)** - Key levels (King/Magnet, Accel, Support/Resistance)
2. **Price Action** - Current price behavior, consolidation vs trend, MA alignment
3. **Flow Analysis** - Call vs Put premium, net sentiment, WAVE indicator interpretation
4. **GEX Table Interpretation** - Key strikes with high GEX, walls, low-friction zones
5. **Scenarios (Playbook)** - Bullish continuation (with triggers), Pullback zones, Bearish invalidation

Key concepts:
- King/Magnet: Highest positive GEX strike - price gravitates here, acts as resistance when above, support when below
- Zero Gamma (0γ): Level where total GEX flips sign. Above = dealers hedge WITH price (trending), Below = dealers hedge AGAINST (mean-reverting)
- Accelerator: Large negative GEX zone - price accelerates when it breaks through
- Call Wall: Strike where dealers sold calls - resistance
- Put Wall: Strike where dealers sold puts - support

Be direct and trading-focused. No fluff. Give probability-weighted scenarios."""


class ChatMessage(BaseModel):
    """Chat message for AI conversation."""
    role: str  # "user" or "assistant"
    content: str


class ChatRequest(BaseModel):
    """Chat request with conversation history."""
    symbol: str
    message: str
    history: Optional[List[ChatMessage]] = None


@app.get("/ai/analyze/{symbol}")
async def analyze_symbol(
    symbol: str,
    request: Request,
    current_user: dict = Depends(get_current_user) if get_current_user else None
):
    """
    Get AI-powered trading analysis for a symbol.
    Gathers all GEX, flow, and price data and sends to OpenAI for analysis.
    Requires authentication. Tracks token usage per user.
    """
    if not OPENAI_AVAILABLE:
        raise HTTPException(
            status_code=503,
            detail="AI analysis not available. Set OPENAI_API_KEY environment variable."
        )

    # Check authentication
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required for AI analysis")

    user_id = str(current_user.get('sub') or current_user.get('id'))
    is_admin = current_user.get('is_admin', False)

    # Check if user has AI access enabled (admins bypass this check)
    if AI_TRACKING_AVAILABLE and POSTGRES_AVAILABLE and not is_admin:
        from db_postgres import get_user_ai_enabled
        ai_enabled = await get_user_ai_enabled(user_id)
        if not ai_enabled:
            raise HTTPException(
                status_code=403,
                detail="AI access not enabled for your account. Contact admin to enable AI."
            )

    # Check token limit
    if AI_TRACKING_AVAILABLE and POSTGRES_AVAILABLE:
        limit_info = await check_user_token_limit(user_id)
        if limit_info.get('exceeded'):
            raise HTTPException(
                status_code=429,
                detail=f"Monthly token limit exceeded. Used: {limit_info['tokens_used_this_month']:,} / {limit_info['monthly_token_limit']:,}"
            )

    symbol = symbol.upper()

    # Gather all data for the symbol
    try:
        # Get GEX data from cache
        gex_data = cache.get(symbol)
        if not gex_data:
            # Try to refresh
            await refresh_manager.refresh_symbol(symbol)
            gex_data = cache.get(symbol)

        if not gex_data:
            raise HTTPException(status_code=404, detail=f"No data available for {symbol}")

        # Get flow data
        flow_data = None
        if MASSIVE_AVAILABLE:
            try:
                client = get_massive_client()
                flow_summary = await client.get_flow_summary(
                    symbol=symbol,
                    spot_price=gex_data.spot_price,
                    strike_range=20
                )
                flow_data = flow_summary.to_dict()
            except Exception as e:
                print(f"[AI] Flow data error: {e}")

        # Get regime info
        regime = regime_tracker.current_regime

        # Format data for AI prompt
        data_context = f"""
## {symbol} Market Data (Real-time)

### Current Price
- Spot: ${gex_data.spot_price:.2f}
- Timestamp: {gex_data.timestamp.strftime("%Y-%m-%d %H:%M:%S ET")}

### Key GEX Levels
- King/Magnet (highest +GEX): ${gex_data.king_node.strike if gex_data.king_node else 'N/A'}
- Zero Gamma (0γ): ${gex_data.zero_gamma_level:.2f}
- Net GEX: ${gex_data.net_gex:,.0f}
- Price vs 0γ: {"ABOVE (trending regime)" if gex_data.spot_price > gex_data.zero_gamma_level else "BELOW (mean-reverting regime)"}

### Top GEX Strikes
"""
        # Add top strikes by GEX
        sorted_strikes = sorted(gex_data.zones, key=lambda z: abs(z.gex), reverse=True)[:10]
        for zone in sorted_strikes:
            gex_sign = "+" if zone.gex > 0 else ""
            data_context += f"- ${zone.strike}: {gex_sign}{zone.gex/1e6:.1f}M GEX\n"

        # Add Put/Call Walls (Liquidity Zones)
        if gex_data.put_call_walls and gex_data.put_call_walls.get('walls'):
            data_context += "\n### Liquidity Zones (Put/Call Walls)\n"
            walls = gex_data.put_call_walls['walls']
            # Sort by total OI to find highest liquidity
            high_liquidity = sorted(walls, key=lambda w: w.get('total_oi', 0), reverse=True)[:5]
            for wall in high_liquidity:
                call_gex = wall.get('call_gex', 0) / 1e6
                put_gex = wall.get('put_gex', 0) / 1e6
                total_oi = wall.get('total_oi', 0)
                dominant = "CALL WALL" if call_gex > abs(put_gex) else "PUT WALL"
                data_context += f"- ${wall['strike']}: {dominant} | Call GEX: {call_gex:+.1f}M, Put GEX: {put_gex:+.1f}M, OI: {total_oi:,}\n"

        # Add Gatekeeper (support/resistance from -GEX)
        if gex_data.gatekeeper_node:
            data_context += f"\n### Key Support/Resistance\n"
            data_context += f"- Gatekeeper (highest -GEX): ${gex_data.gatekeeper_node.strike} ({gex_data.gatekeeper_node.gex/1e6:.1f}M)\n"

        # Add GEX Flip Level if available
        if gex_data.gex_flip_level:
            data_context += f"- GEX Flip Level: ${gex_data.gex_flip_level:.2f}\n"

        # Add flow data if available
        if flow_data:
            data_context += f"""
### Options Flow
- Call Premium: ${flow_data.get('total_call_premium', 0)/1e6:.2f}M
- Put Premium: ${flow_data.get('total_put_premium', 0)/1e6:.2f}M
- Net: ${(flow_data.get('total_call_premium', 0) - flow_data.get('total_put_premium', 0))/1e6:.2f}M ({"bullish" if flow_data.get('total_call_premium', 0) > flow_data.get('total_put_premium', 0) else "bearish"})
- Put/Call Ratio: {flow_data.get('put_call_ratio', 0):.2f}
"""
            # Add pressure by strike
            if flow_data.get('pressure_by_strike'):
                data_context += "\n### Flow by Strike (Top 5)\n"
                pressure_sorted = sorted(
                    flow_data['pressure_by_strike'].items(),
                    key=lambda x: abs(x[1].get('net_pressure', 0)),
                    reverse=True
                )[:5]
                for strike, pressure in pressure_sorted:
                    net = pressure.get('net_pressure', 0)
                    data_context += f"- ${strike}: {'📈' if net > 0 else '📉'} {'+' if net > 0 else ''}{net/1e3:.1f}K net\n"

        # Add regime info
        if regime:
            data_context += f"""
### Market Regime
- Current Regime: {regime.regime.value}
- VIX: {regime.vix_level:.2f}
- GEX Reliability: {regime.gex_reliability}
"""

        # Call OpenAI
        response = openai_client.chat.completions.create(
            model="gpt-5-mini",
            messages=[
                {"role": "system", "content": AI_TRADING_SYSTEM_PROMPT},
                {"role": "user", "content": f"Analyze {symbol} based on this data:\n{data_context}"}
            ],
            max_completion_tokens=4000
        )

        analysis = response.choices[0].message.content or ""

        # Track token usage
        input_tokens = response.usage.prompt_tokens if response.usage else 0
        output_tokens = response.usage.completion_tokens if response.usage else 0
        total_tokens = input_tokens + output_tokens

        if AI_TRACKING_AVAILABLE and POSTGRES_AVAILABLE:
            # Log usage for billing
            await log_ai_usage(user_id, 'analyze', symbol, input_tokens, output_tokens)
            # Update user's monthly token count
            await update_user_token_usage(user_id, total_tokens)

        print(f"[AI] Generated analysis for {symbol}: {len(analysis)} chars, {total_tokens} tokens (user: {current_user.get('email', 'unknown')})")

        return {
            "symbol": symbol,
            "analysis": analysis,
            "timestamp": datetime.now().isoformat(),
            "spot_price": gex_data.spot_price,
            "king_strike": gex_data.king_node.strike if gex_data.king_node else None,
            "zero_gamma": gex_data.zero_gamma_level,
            "model": "gpt-5-mini",
            "tokens_used": total_tokens
        }

    except HTTPException:
        raise
    except Exception as e:
        print(f"[AI] Analysis error: {e}")
        raise HTTPException(status_code=500, detail=f"Analysis error: {str(e)}")


@app.post("/ai/chat")
async def chat_with_ai(
    request: ChatRequest,
    http_request: Request,
    current_user: dict = Depends(get_current_user) if get_current_user else None
):
    """
    Chat with AI about a specific symbol.
    Maintains conversation context in database for follow-up questions.
    Requires authentication. Tracks token usage per user.
    """
    print(f"[AI Chat] Received request for {request.symbol}: {request.message[:50]}...")

    if not OPENAI_AVAILABLE:
        raise HTTPException(
            status_code=503,
            detail="AI chat not available. Set OPENAI_API_KEY environment variable."
        )

    # Check authentication
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required for AI chat")

    user_id = str(current_user.get('sub') or current_user.get('id'))
    is_admin = current_user.get('is_admin', False)

    # Check if user has AI access enabled (admins bypass this check)
    if AI_TRACKING_AVAILABLE and POSTGRES_AVAILABLE and not is_admin:
        from db_postgres import get_user_ai_enabled
        ai_enabled = await get_user_ai_enabled(user_id)
        if not ai_enabled:
            raise HTTPException(
                status_code=403,
                detail="AI access not enabled for your account. Contact admin to enable AI."
            )

    # Check token limit
    if AI_TRACKING_AVAILABLE and POSTGRES_AVAILABLE:
        limit_info = await check_user_token_limit(user_id)
        if limit_info.get('exceeded'):
            raise HTTPException(
                status_code=429,
                detail=f"Monthly token limit exceeded. Used: {limit_info['tokens_used_this_month']:,} / {limit_info['monthly_token_limit']:,}"
            )

    symbol = request.symbol.upper()

    try:
        # Get current market data for context
        gex_data = cache.get(symbol)

        # Build market context
        market_context = ""
        if gex_data:
            market_context = f"""
Current {symbol} data:
- Price: ${gex_data.spot_price:.2f}
- King (Magnet): ${gex_data.king_node.strike if gex_data.king_node else 'N/A'}
- Gatekeeper (Resistance): ${gex_data.gatekeeper_node.strike if gex_data.gatekeeper_node else 'N/A'}
- Zero Gamma: ${gex_data.zero_gamma_level:.2f}
- Net GEX: ${gex_data.net_gex:,.0f}
- Regime: {"ABOVE 0γ (trending)" if gex_data.spot_price > gex_data.zero_gamma_level else "BELOW 0γ (mean-reverting)"}
"""
            # Add liquidity zones
            if gex_data.put_call_walls and gex_data.put_call_walls.get('walls'):
                walls = gex_data.put_call_walls['walls']
                high_liq = sorted(walls, key=lambda w: w.get('total_oi', 0), reverse=True)[:3]
                market_context += "Key Liquidity Zones:\n"
                for w in high_liq:
                    market_context += f"- ${w['strike']}: OI {w.get('total_oi', 0):,}\n"

        # Load chat history from database (instead of request.history)
        conversation = ""
        if AI_TRACKING_AVAILABLE and POSTGRES_AVAILABLE:
            db_history = await get_chat_history(user_id, symbol, limit=20)
            for msg in db_history:
                role_label = "User" if msg['role'] == "user" else "Assistant"
                conversation += f"{role_label}: {msg['content']}\n\n"

            # Save user message to DB
            await save_chat_message(user_id, symbol, 'user', request.message, 0)
        else:
            # Fallback to request.history if DB not available
            if request.history:
                for msg in request.history:
                    role_label = "User" if msg.role == "user" else "Assistant"
                    conversation += f"{role_label}: {msg.content}\n\n"

        conversation += f"User: {request.message}"

        # Call OpenAI
        response = openai_client.chat.completions.create(
            model="gpt-5-mini",
            messages=[
                {"role": "system", "content": AI_TRADING_SYSTEM_PROMPT + f"\n\n{market_context}"},
                {"role": "user", "content": conversation}
            ],
            max_completion_tokens=2000
        )

        reply = response.choices[0].message.content or ""

        # Track token usage
        input_tokens = response.usage.prompt_tokens if response.usage else 0
        output_tokens = response.usage.completion_tokens if response.usage else 0
        total_tokens = input_tokens + output_tokens

        if AI_TRACKING_AVAILABLE and POSTGRES_AVAILABLE:
            # Save assistant response to DB
            await save_chat_message(user_id, symbol, 'assistant', reply, output_tokens)
            # Log usage for billing
            await log_ai_usage(user_id, 'chat', symbol, input_tokens, output_tokens)
            # Update user's monthly token count
            await update_user_token_usage(user_id, total_tokens)

        # Log for debugging
        if not reply:
            print(f"[AI Chat] WARNING: Empty reply for {symbol}. Status: {response.status if hasattr(response, 'status') else 'unknown'}")
        else:
            print(f"[AI Chat] Generated reply for {symbol}: {len(reply)} chars, {total_tokens} tokens (user: {current_user.get('email', 'unknown')})")

        return {
            "symbol": symbol,
            "reply": reply,
            "timestamp": datetime.now().isoformat(),
            "tokens_used": total_tokens
        }

    except HTTPException:
        raise
    except Exception as e:
        import traceback
        print(f"[AI Chat] ERROR: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Chat error: {str(e)}")


@app.get("/ai/status")
async def get_ai_status():
    """Check if AI analysis is available."""
    return {
        "available": OPENAI_AVAILABLE,
        "model": "o1-mini" if OPENAI_AVAILABLE else None,
        "message": "AI analysis ready" if OPENAI_AVAILABLE else "Set OPENAI_API_KEY to enable"
    }


# =============================================================================
# ADMIN USAGE TRACKING ENDPOINTS
# =============================================================================
@app.get("/admin/users/usage")
async def get_all_users_usage(
    month: str = Query(None, description="Month in YYYY-MM format, defaults to current month"),
    http_request: Request = None,
    current_user: dict = Depends(get_current_user) if get_current_user else None
):
    """
    Get all users' token usage for invoicing (admin only).
    Returns monthly usage stats for billing purposes.
    """
    # Check admin privileges
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")
    if not current_user.get('is_admin'):
        raise HTTPException(status_code=403, detail="Admin privileges required")

    if not AI_TRACKING_AVAILABLE or not POSTGRES_AVAILABLE:
        raise HTTPException(status_code=503, detail="Usage tracking not available")

    try:
        report = await get_all_users_usage_report(month)
        return report
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get usage report: {str(e)}")


@app.get("/admin/users/{user_id}/usage")
async def get_user_usage(
    user_id: str,
    start_date: str = Query(None, description="Start date YYYY-MM-DD"),
    end_date: str = Query(None, description="End date YYYY-MM-DD"),
    http_request: Request = None,
    current_user: dict = Depends(get_current_user) if get_current_user else None
):
    """
    Get specific user's detailed usage (admin only).
    """
    # Check admin privileges
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")
    if not current_user.get('is_admin'):
        raise HTTPException(status_code=403, detail="Admin privileges required")

    if not AI_TRACKING_AVAILABLE or not POSTGRES_AVAILABLE:
        raise HTTPException(status_code=503, detail="Usage tracking not available")

    try:
        report = await get_user_usage_report(user_id, start_date, end_date)
        return report
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get user usage: {str(e)}")


@app.put("/admin/users/{user_id}/token-limit")
async def update_user_token_limit(
    user_id: str,
    limit: int = Query(..., description="New monthly token limit"),
    http_request: Request = None,
    current_user: dict = Depends(get_current_user) if get_current_user else None
):
    """
    Set a user's monthly token limit (admin only).
    """
    # Check admin privileges
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")
    if not current_user.get('is_admin'):
        raise HTTPException(status_code=403, detail="Admin privileges required")

    if not AI_TRACKING_AVAILABLE or not POSTGRES_AVAILABLE:
        raise HTTPException(status_code=503, detail="Usage tracking not available")

    if limit < 0:
        raise HTTPException(status_code=400, detail="Token limit must be non-negative")

    try:
        result = await set_user_token_limit(user_id, limit)
        return {"success": True, "user_id": user_id, "new_limit": limit, **result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to set token limit: {str(e)}")


@app.get("/admin/users/{user_id}/chat-history")
async def get_user_chat_history(
    user_id: str,
    symbol: str = Query(None, description="Filter by symbol"),
    limit: int = Query(100, description="Max messages to return"),
    http_request: Request = None,
    current_user: dict = Depends(get_current_user) if get_current_user else None
):
    """
    Get a user's chat history (admin only).
    """
    # Check admin privileges
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")
    if not current_user.get('is_admin'):
        raise HTTPException(status_code=403, detail="Admin privileges required")

    if not AI_TRACKING_AVAILABLE or not POSTGRES_AVAILABLE:
        raise HTTPException(status_code=503, detail="Chat history not available")

    try:
        history = await get_chat_history(user_id, symbol, limit)
        return {"user_id": user_id, "symbol": symbol, "messages": history, "count": len(history)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get chat history: {str(e)}")


# =============================================================================
# TRADING JOURNAL ENDPOINTS
# =============================================================================

# Import journal functions
try:
    from db_postgres import (
        init_journal_tables, create_trade, get_trades, get_trade_by_id,
        update_trade, delete_trade, add_trade_note, delete_trade_note,
        add_trade_tag, remove_trade_tag, get_user_tags,
        get_calendar_data, get_trading_analytics, import_trades_from_csv
    )
    JOURNAL_AVAILABLE = True
except ImportError:
    JOURNAL_AVAILABLE = False


class TradeCreate(BaseModel):
    symbol: str
    side: str  # 'long' or 'short'
    quantity: float
    entry_price: float
    entry_time: str
    exit_price: Optional[float] = None
    exit_time: Optional[str] = None
    notes: Optional[str] = None
    tags: Optional[List[str]] = None


class TradeUpdate(BaseModel):
    exit_price: Optional[float] = None
    exit_time: Optional[str] = None


class NoteCreate(BaseModel):
    content: str


class CSVImport(BaseModel):
    trades: List[dict]


@app.get("/journal/trades")
async def list_trades(
    symbol: str = Query(None),
    status: str = Query(None),
    start_date: str = Query(None),
    end_date: str = Query(None),
    limit: int = Query(100),
    current_user: dict = Depends(get_current_user) if get_current_user else None
):
    """Get user's trades with optional filters"""
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    if not JOURNAL_AVAILABLE:
        raise HTTPException(status_code=503, detail="Trading journal not available")

    user_id = str(current_user.get('sub') or current_user.get('id'))
    trades = await get_trades(user_id, symbol, status, start_date, end_date, limit)
    return {"trades": trades, "count": len(trades)}


@app.post("/journal/trades")
async def create_new_trade(
    trade: TradeCreate,
    current_user: dict = Depends(get_current_user) if get_current_user else None
):
    """Create a new trade entry"""
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    if not JOURNAL_AVAILABLE:
        raise HTTPException(status_code=503, detail="Trading journal not available")

    user_id = str(current_user.get('sub') or current_user.get('id'))
    result = await create_trade(
        user_id=user_id,
        symbol=trade.symbol,
        side=trade.side,
        quantity=trade.quantity,
        entry_price=trade.entry_price,
        entry_time=trade.entry_time,
        exit_price=trade.exit_price,
        exit_time=trade.exit_time,
        notes=trade.notes,
        tags=trade.tags
    )

    if 'error' in result:
        raise HTTPException(status_code=400, detail=result['error'])
    return result


@app.get("/journal/trades/{trade_id}")
async def get_single_trade(
    trade_id: str,
    current_user: dict = Depends(get_current_user) if get_current_user else None
):
    """Get a single trade by ID"""
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    user_id = str(current_user.get('sub') or current_user.get('id'))
    trade = await get_trade_by_id(user_id, trade_id)

    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")
    return trade


@app.put("/journal/trades/{trade_id}")
async def update_existing_trade(
    trade_id: str,
    trade_update: TradeUpdate,
    current_user: dict = Depends(get_current_user) if get_current_user else None
):
    """Update a trade (close position, edit details)"""
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    user_id = str(current_user.get('sub') or current_user.get('id'))
    result = await update_trade(
        user_id=user_id,
        trade_id=trade_id,
        exit_price=trade_update.exit_price,
        exit_time=trade_update.exit_time
    )

    if 'error' in result:
        raise HTTPException(status_code=400, detail=result['error'])
    return result


@app.delete("/journal/trades/{trade_id}")
async def delete_existing_trade(
    trade_id: str,
    current_user: dict = Depends(get_current_user) if get_current_user else None
):
    """Delete a trade"""
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    user_id = str(current_user.get('sub') or current_user.get('id'))
    result = await delete_trade(user_id, trade_id)

    if 'error' in result:
        raise HTTPException(status_code=400, detail=result['error'])
    return result


@app.post("/journal/trades/{trade_id}/notes")
async def add_note_to_trade(
    trade_id: str,
    note: NoteCreate,
    current_user: dict = Depends(get_current_user) if get_current_user else None
):
    """Add a note to a trade"""
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    user_id = str(current_user.get('sub') or current_user.get('id'))
    result = await add_trade_note(user_id, trade_id, note.content)

    if 'error' in result:
        raise HTTPException(status_code=400, detail=result['error'])
    return result


@app.delete("/journal/notes/{note_id}")
async def delete_note(
    note_id: str,
    current_user: dict = Depends(get_current_user) if get_current_user else None
):
    """Delete a note"""
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    user_id = str(current_user.get('sub') or current_user.get('id'))
    return await delete_trade_note(user_id, note_id)


@app.post("/journal/trades/{trade_id}/tags/{tag}")
async def add_tag_to_trade(
    trade_id: str,
    tag: str,
    current_user: dict = Depends(get_current_user) if get_current_user else None
):
    """Add a tag to a trade"""
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    user_id = str(current_user.get('sub') or current_user.get('id'))
    return await add_trade_tag(user_id, trade_id, tag)


@app.delete("/journal/trades/{trade_id}/tags/{tag}")
async def remove_tag_from_trade(
    trade_id: str,
    tag: str,
    current_user: dict = Depends(get_current_user) if get_current_user else None
):
    """Remove a tag from a trade"""
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    user_id = str(current_user.get('sub') or current_user.get('id'))
    return await remove_trade_tag(user_id, trade_id, tag)


@app.get("/journal/tags")
async def get_all_tags(
    current_user: dict = Depends(get_current_user) if get_current_user else None
):
    """Get all user's tags"""
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    user_id = str(current_user.get('sub') or current_user.get('id'))
    tags = await get_user_tags(user_id)
    return {"tags": tags}


@app.get("/journal/calendar")
async def get_calendar(
    year: int = Query(...),
    month: int = Query(...),
    current_user: dict = Depends(get_current_user) if get_current_user else None
):
    """Get daily P&L for calendar view"""
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    user_id = str(current_user.get('sub') or current_user.get('id'))
    data = await get_calendar_data(user_id, year, month)
    return {"year": year, "month": month, "days": data}


@app.get("/journal/analytics")
async def get_analytics(
    start_date: str = Query(None),
    end_date: str = Query(None),
    current_user: dict = Depends(get_current_user) if get_current_user else None
):
    """Get comprehensive trading analytics"""
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    user_id = str(current_user.get('sub') or current_user.get('id'))
    analytics = await get_trading_analytics(user_id, start_date, end_date)
    return analytics


@app.post("/journal/trades/import")
async def import_csv_trades(
    data: CSVImport,
    current_user: dict = Depends(get_current_user) if get_current_user else None
):
    """Import trades from CSV data"""
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    user_id = str(current_user.get('sub') or current_user.get('id'))
    result = await import_trades_from_csv(user_id, data.trades)
    return result


# =============================================================================
# HISTORICAL VALIDATION ENDPOINTS
# =============================================================================
@app.get("/validation/{symbol}")
async def get_validation(
    symbol: str,
    days: int = Query(30, description="Days of history to validate")
):
    """
    Run historical validation on GEX levels vs price action.
    Returns statistics on how often King/Gatekeeper levels held.
    """
    symbol = symbol.upper()

    if not VALIDATION_AVAILABLE:
        raise HTTPException(
            status_code=503,
            detail="Historical validation not available"
        )

    try:
        from historical_validation import get_validator

        validator = get_validator()
        result = await validator.validate_symbol(symbol, days)

        return result.to_dict() if result else {"symbol": symbol, "error": "Not enough data"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Validation error: {str(e)}")


# =============================================================================
# QUIVER QUANT ENDPOINTS - Congress, Dark Pool, Insider, WSB
# =============================================================================
@app.get("/quiver/congress")
async def get_congress_trades(
    limit: int = Query(50, description="Number of trades to return"),
    ticker: Optional[str] = Query(None, description="Filter by ticker")
):
    """
    Get recent Congress trading activity.
    Shows what members of Congress are buying/selling.
    """
    if not QUIVER_AVAILABLE:
        raise HTTPException(status_code=503, detail="Quiver Quant not available")

    client = get_quiver_client()

    try:
        if ticker:
            trades = await client.get_congress_trades_for_ticker(ticker.upper())
        else:
            trades = await client.get_congress_trades(limit)

        return {
            "type": "congress",
            "trades": trades,
            "count": len(trades),
            "filter": ticker
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching Congress trades: {str(e)}")


@app.get("/quiver/darkpool")
async def get_dark_pool(
    limit: int = Query(50, description="Number of records to return"),
    ticker: Optional[str] = Query(None, description="Filter by ticker")
):
    """
    Get dark pool (off-exchange) trading activity.
    Shows short volume and total volume by ticker.
    """
    if not QUIVER_AVAILABLE:
        raise HTTPException(status_code=503, detail="Quiver Quant not available")

    client = get_quiver_client()

    try:
        if ticker:
            data = await client.get_dark_pool_for_ticker(ticker.upper())
        else:
            data = await client.get_dark_pool_data(limit)

        return {
            "type": "darkpool",
            "data": data,
            "count": len(data),
            "filter": ticker
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching dark pool: {str(e)}")


@app.get("/quiver/insider")
async def get_insider_trades(
    limit: int = Query(50, description="Number of trades to return")
):
    """
    Get recent insider trading activity (Form 4 filings).
    Shows CEO, CFO, Director buys and sells.
    """
    if not QUIVER_AVAILABLE:
        raise HTTPException(status_code=503, detail="Quiver Quant not available")

    client = get_quiver_client()

    try:
        trades = await client.get_insider_trades(limit)

        return {
            "type": "insider",
            "trades": trades,
            "count": len(trades)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching insider trades: {str(e)}")


@app.get("/quiver/wsb")
async def get_wsb_sentiment(
    limit: int = Query(30, description="Number of tickers to return")
):
    """
    Get Wall Street Bets mentions and sentiment.
    Shows which tickers are trending on Reddit WSB.
    """
    if not QUIVER_AVAILABLE:
        raise HTTPException(status_code=503, detail="Quiver Quant not available")

    client = get_quiver_client()

    try:
        data = await client.get_wsb_mentions(limit)

        return {
            "type": "wsb",
            "data": data,
            "count": len(data)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching WSB data: {str(e)}")


@app.get("/quiver/govcontracts")
async def get_gov_contracts(
    limit: int = Query(30, description="Number of contracts to return")
):
    """
    Get recent government contract awards.
    Shows companies receiving federal contracts.
    """
    if not QUIVER_AVAILABLE:
        raise HTTPException(status_code=503, detail="Quiver Quant not available")

    client = get_quiver_client()

    try:
        contracts = await client.get_gov_contracts(limit)

        return {
            "type": "govcontracts",
            "contracts": contracts,
            "count": len(contracts)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching gov contracts: {str(e)}")


@app.get("/quiver/13f")
async def get_13f_changes(
    limit: int = Query(30, description="Number of changes to return")
):
    """
    Get recent hedge fund 13F filing changes.
    Shows what institutional investors are buying/selling.
    """
    if not QUIVER_AVAILABLE:
        raise HTTPException(status_code=503, detail="Quiver Quant not available")

    client = get_quiver_client()

    try:
        changes = await client.get_13f_changes(limit)

        return {
            "type": "13f",
            "changes": changes,
            "count": len(changes)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching 13F changes: {str(e)}")


@app.get("/quiver/alerts")
async def get_quiver_alerts(
    symbols: str = Query(..., description="Comma-separated list of symbols to watch")
):
    """
    Check all Quiver data sources for alerts on watchlist symbols.
    Returns Congress trades, insider activity, dark pool, and WSB buzz.
    """
    if not QUIVER_AVAILABLE:
        raise HTTPException(status_code=503, detail="Quiver Quant not available")

    client = get_quiver_client()
    watchlist = [s.strip().upper() for s in symbols.split(",")]

    try:
        alerts = await client.check_watchlist_alerts(watchlist)

        # Flatten alerts into a single list
        all_alerts = []
        for category, items in alerts.items():
            all_alerts.extend(items)

        # Sort by date (most recent first)
        all_alerts.sort(key=lambda x: x.get("date", ""), reverse=True)

        return {
            "type": "quiver_alerts",
            "watchlist": watchlist,
            "alerts": all_alerts,
            "by_category": alerts,
            "total": len(all_alerts)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error checking alerts: {str(e)}")


# =============================================================================
# AUTHENTICATION ENDPOINTS
# =============================================================================
@app.post("/auth/login")
async def login(request: LoginRequest, response: Response):
    """
    Login with password.
    Sets a session cookie on success.
    """
    if not AUTH_ENABLED:
        # Auth disabled - always succeed
        return {"status": "ok", "message": "Authentication disabled"}

    if not verify_password(request.password, AUTH_PASSWORD):
        raise HTTPException(status_code=401, detail="Invalid password")

    # Create session token
    session_data = {
        "authenticated": True,
        "login_time": datetime.now().isoformat()
    }
    token = create_session_token(session_data)

    # Set cookie
    response.set_cookie(
        key="gex_session",
        value=token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=False  # Set to True in production with HTTPS
    )

    return {"status": "ok", "message": "Login successful"}


@app.post("/auth/logout")
async def logout(response: Response):
    """
    Logout - clears the session cookie.
    """
    response.delete_cookie("gex_session")
    return {"status": "ok", "message": "Logged out"}


@app.get("/auth/check")
async def check_auth(gex_session: Optional[str] = Cookie(None)):
    """
    Check if user is authenticated.
    Returns auth status without requiring login.
    """
    if not AUTH_ENABLED:
        return {"authenticated": True, "auth_required": False}

    session = verify_session_token(gex_session) if gex_session else None
    return {
        "authenticated": session is not None,
        "auth_required": AUTH_ENABLED
    }


def is_authenticated(gex_session: Optional[str]) -> bool:
    """Check if request has valid session."""
    if not AUTH_ENABLED:
        return True
    if not gex_session:
        return False
    session = verify_session_token(gex_session)
    return session is not None and session.get("authenticated", False)


# =============================================================================
# STATIC FILES (Frontend)
# =============================================================================
# Serve frontend files from ../frontend directory
FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")

# Check if frontend directory exists
if os.path.exists(FRONTEND_DIR):
    # Serve static files (js, css, images)
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

    @app.get("/login")
    async def serve_login():
        """Serve the login page."""
        return FileResponse(os.path.join(FRONTEND_DIR, "login.html"))

    @app.get("/register")
    async def serve_register():
        """Serve the registration page."""
        register_path = os.path.join(FRONTEND_DIR, "register.html")
        if os.path.exists(register_path):
            return FileResponse(register_path)
        # Fallback to login if register.html doesn't exist yet
        return FileResponse(os.path.join(FRONTEND_DIR, "login.html"))

    @app.get("/reset-password")
    async def serve_reset_password():
        """Serve the password reset page."""
        reset_path = os.path.join(FRONTEND_DIR, "reset-password.html")
        if os.path.exists(reset_path):
            return FileResponse(reset_path)
        return FileResponse(os.path.join(FRONTEND_DIR, "login.html"))

    @app.get("/guide")
    async def serve_trading_guide():
        """Serve the trading guide page."""
        guide_path = os.path.join(FRONTEND_DIR, "trading-guide.html")
        if os.path.exists(guide_path):
            return FileResponse(guide_path)
        return RedirectResponse(url="/app", status_code=302)

    @app.get("/admin")
    async def serve_admin(
        gex_session: Optional[str] = Cookie(None),
        refresh_token: Optional[str] = Cookie(None)
    ):
        """Serve the admin dashboard (requires admin privileges)."""
        # Check authentication
        if AUTH_ENABLED:
            if NEW_AUTH_AVAILABLE:
                if not refresh_token:
                    return RedirectResponse(url="/login", status_code=302)
            else:
                if not is_authenticated(gex_session):
                    return RedirectResponse(url="/login", status_code=302)
        admin_path = os.path.join(FRONTEND_DIR, "admin.html")
        if os.path.exists(admin_path):
            return FileResponse(admin_path)
        return RedirectResponse(url="/app", status_code=302)

    @app.get("/reset-password")
    async def serve_reset_password():
        """Serve the password reset page."""
        reset_path = os.path.join(FRONTEND_DIR, "reset-password.html")
        if os.path.exists(reset_path):
            return FileResponse(reset_path)
        return RedirectResponse(url="/login", status_code=302)

    @app.get("/app", response_class=FileResponse)
    @app.get("/app/{path:path}")
    async def serve_frontend(
        request: Request,
        path: str = "",
        gex_session: Optional[str] = Cookie(None),
        refresh_token: Optional[str] = Cookie(None)
    ):
        """Serve the frontend application (protected by authentication)."""
        # Check authentication - use new JWT auth if available
        if AUTH_ENABLED:
            if NEW_AUTH_AVAILABLE:
                # New auth: check for refresh token cookie
                if not refresh_token:
                    return RedirectResponse(url="/login", status_code=302)
            else:
                # Legacy auth: check session cookie
                if not is_authenticated(gex_session):
                    return RedirectResponse(url="/login", status_code=302)

        # Try to serve the requested file
        file_path = os.path.join(FRONTEND_DIR, path) if path else os.path.join(FRONTEND_DIR, "index.html")
        if os.path.isfile(file_path):
            return FileResponse(file_path)
        # Fall back to index.html for SPA routing
        return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))

    print(f"[OK] Frontend mounted at /app from {FRONTEND_DIR}")
    if AUTH_ENABLED:
        print(f"[OK] Authentication enabled - login at /login")


# =============================================================================
# MAIN
# =============================================================================
if __name__ == "__main__":
    print("=" * 60)
    print("GEX Dashboard API - MarketData.app (Real Greeks from OPRA)")
    print("=" * 60)

    if DATA_PROVIDER_AVAILABLE:
        print("[OK] Data provider available")
    else:
        print("[ERROR] No data provider available!")

    # Run server (PORT from environment for Render, default 8000)
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=port,
        reload=False
    )
