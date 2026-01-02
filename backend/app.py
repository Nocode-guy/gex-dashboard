"""
GEX Dashboard API Server

FastAPI server providing GEX data for the web dashboard and NinjaTrader indicator.
"""
import os
import sys
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

from fastapi import FastAPI, HTTPException, Query, Cookie, Response, Request
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

# Massive (Polygon) options flow client
try:
    from massive_client import get_massive_client, MassiveClient
    MASSIVE_AVAILABLE = True
    print("[OK] Massive options flow client available")
except ImportError as e:
    MASSIVE_AVAILABLE = False
    print(f"[WARNING] Massive client not available: {e}")


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

            print(f"[{datetime.now().strftime('%H:%M:%S')}] [{get_provider_name()}] Refreshed {symbol}: "
                  f"${spot:.2f}, {len(result.zones)} zones, "
                  f"King: {result.king_node.strike if result.king_node else 'N/A'}")

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
# FASTAPI APP
# =============================================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    # Startup: Initial data fetch
    print("GEX Dashboard starting...")

    # Initialize regime tracker (fetch VIX)
    print("Fetching VIX for regime detection...")
    regime_tracker.update_regime()

    await refresh_manager.refresh_all()
    refresh_manager.start()
    yield
    # Shutdown
    refresh_manager.stop()
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


# =============================================================================
# API ROUTES
# =============================================================================
@app.get("/")
async def root():
    """Health check and API info."""
    return {
        "status": "ok",
        "service": "GEX Dashboard API",
        "version": "1.0.0",
        "active_symbols": refresh_manager.active_symbols,
        "refresh_interval_min": refresh_manager.refresh_interval // 60
    }


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

    client = get_options_client()

    # Fetch candles
    candles = await client.get_candles(symbol, resolution=resolution, count=count)

    if not candles:
        raise HTTPException(status_code=404, detail=f"No candle data for {symbol}")

    # Get current GEX levels for chart overlay
    result = cache.get(symbol)
    levels = {}
    if result:
        levels = {
            "king": result.king_node.strike if result.king_node else None,
            "king_gex": result.king_node.gex if result.king_node else None,
            "gatekeeper": result.gatekeeper_node.strike if result.gatekeeper_node else None,
            "zero_gamma": result.zero_gamma_level,
            "gex_flip": result.gex_flip_level,
            "expected_move": result.expected_move,
            "spot_price": result.spot_price
        }

    return {
        "symbol": symbol,
        "resolution": resolution,
        "candles": candles,
        "levels": levels
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
            return result
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error fetching flow: {str(e)}")

    raise HTTPException(
        status_code=503,
        detail="Options flow not available. Configure MASSIVE_API_KEY or UNUSUAL_WHALES_API_KEY."
    )


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

    @app.get("/app", response_class=FileResponse)
    @app.get("/app/{path:path}")
    async def serve_frontend(
        request: Request,
        path: str = "",
        gex_session: Optional[str] = Cookie(None)
    ):
        """Serve the frontend application (protected by authentication)."""
        # Check authentication
        if AUTH_ENABLED and not is_authenticated(gex_session):
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
