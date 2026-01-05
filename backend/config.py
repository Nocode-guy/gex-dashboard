"""
GEX Dashboard Configuration
"""
import os
from datetime import datetime, timedelta
from typing import List

# =============================================================================
# API SETTINGS
# =============================================================================
TRADIER_BASE_URL = "https://api.tradier.com/v1"
TRADIER_SANDBOX_URL = "https://sandbox.tradier.com/v1"

# =============================================================================
# MODULE TOGGLES - Enable/disable optional features
# =============================================================================
MODULES = {
    # Core (always on)
    "gex": True,              # GEX calculation - core functionality
    "vex": True,              # VEX (vanna exposure) - uses Black-Scholes
    "dex": True,              # DEX (delta exposure)

    # Optional modules (can be disabled without affecting core)
    "flow": False,            # Order flow from Unusual Whales (requires API key)
    "validation": True,       # Historical validation (runs on-demand, not in refresh loop)
    "iv_skew": True,          # IV skew calculation
    "zero_dte": True,         # 0DTE gamma explosion detection
    "proximity_alerts": True, # Price proximity to key levels
}

# =============================================================================
# CACHE TTL SETTINGS (seconds) - Different refresh rates per module
# =============================================================================
CACHE_TTL = {
    "gex": 60,                # Core GEX: 1 min (or user-configured refresh)
    "flow": 300,              # Order flow: 5 min (API rate limits)
    "validation": 86400,      # Validation: 24 hours (run daily or on-demand)
    "regime": 60,             # VIX/regime: 1 min
    "market_tide": 300,       # Market-wide flow: 5 min
}

# =============================================================================
# DATA PROVIDER SETTINGS
# =============================================================================

# Active data provider: "tradier" or "marketdata"
ACTIVE_PROVIDER = "tradier"

# Tradier API Key (for real-time options data)
# Can also set via environment variable: TRADIER_API_KEY
TRADIER_API_KEY = os.environ.get("TRADIER_API_KEY", "uB6Q87tfYQwAQdnoUpCXqNRnKVCt")
TRADIER_PAPER_TRADING = False  # False = LIVE real-time data

PROVIDERS = {
    "tradier": {
        "name": "Tradier",
        "type": "options_data",
        "realtime": True,     # Real-time data
        "delay_minutes": 0,   # No delay
        "rate_limit": 120,    # Requests per minute
    },
    "marketdata": {
        "name": "MarketData.app",
        "type": "options_data",
        "realtime": True,     # Trader plan = real-time
        "delay_minutes": 0,   # No delay on Trader plan
        "rate_limit": 10000,  # Requests per day (Trader plan)
    },
    "unusual_whales": {
        "name": "Unusual Whales",
        "type": "order_flow",
        "realtime": True,
        "enabled": False,     # Set True when API key configured
    },
}

# =============================================================================
# DEFAULT TICKERS
# =============================================================================
# MarketData.app supports index options (SPX, NDX) - included below
DEFAULT_TICKERS: List[str] = [
    "SPX",  # S&P 500 Index options (cash-settled, European-style)
    "SPY", "QQQ", "IWM",  # Index ETFs (SPY=S&P500, QQQ=Nasdaq, IWM=Russell)
    "AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "META", "GOOGL",  # Mega caps
]

# =============================================================================
# GEX CALCULATION SETTINGS
# =============================================================================
# Minimum Open Interest to include a strike
MIN_OPEN_INTEREST = 100  # Lowered from 500 to show more data for individual stocks

# Minimum absolute GEX value to include (in dollars)
MIN_GEX_VALUE = 100_000  # $100K - Lowered from $10M to show more data for individual stocks

# Maximum number of zones to return
MAX_ZONES = 20

# =============================================================================
# DTE DECAY SETTINGS
# =============================================================================
# Decay weights based on days to expiration
DTE_DECAY_RULES = {
    2: 0.2,    # < 2 days = 20% weight
    7: 0.5,    # < 7 days = 50% weight
    14: 0.8,   # < 14 days = 80% weight
    30: 1.0,   # >= 14 days = 100% weight
}

def get_dte_weight(dte: int) -> float:
    """Get the weight multiplier for a given DTE."""
    for threshold, weight in sorted(DTE_DECAY_RULES.items()):
        if dte < threshold:
            return weight
    return 1.0

# =============================================================================
# 0DTE GAMMA EXPLOSION SETTINGS
# =============================================================================
# 0DTE options have gamma that grows exponentially as expiration approaches
# These multipliers adjust GEX to reflect the increased hedging pressure

ZERO_DTE_CONFIG = {
    # Gamma multiplier based on hours to expiration (market hours)
    # At 9:30 AM on expiration day, gamma is already elevated
    # By 3:00 PM, it's explosive for ATM options
    "hours_multipliers": {
        6.5: 1.5,   # Market open (9:30 AM) - 6.5 hours left
        4.0: 2.0,   # Mid-day (12:00 PM) - 4 hours left
        2.0: 3.0,   # 2:00 PM - 2 hours left
        1.0: 5.0,   # 3:00 PM - 1 hour left
        0.5: 8.0,   # 3:30 PM - 30 min left
        0.25: 12.0, # 3:45 PM - 15 min left
    },
    # Only apply multiplier to strikes within X% of spot (ATM zone)
    "atm_range_pct": 0.02,  # 2% - gamma explosion is concentrated ATM
    # Flag threshold - when to show 0DTE warning
    "warning_threshold_hours": 6.5,  # Warn all day on expiration
}

def get_0dte_gamma_multiplier(hours_remaining: float, moneyness_pct: float) -> float:
    """
    Get gamma multiplier for 0DTE options.

    Args:
        hours_remaining: Hours until market close on expiration day
        moneyness_pct: abs(strike - spot) / spot

    Returns:
        Multiplier to apply to gamma (1.0 = no change)
    """
    # Only apply to ATM options
    if moneyness_pct > ZERO_DTE_CONFIG["atm_range_pct"]:
        return 1.0

    # Find appropriate multiplier based on time
    multiplier = 1.0
    for hours, mult in sorted(ZERO_DTE_CONFIG["hours_multipliers"].items(), reverse=True):
        if hours_remaining <= hours:
            multiplier = mult

    # Scale by how ATM the option is (ATM = full multiplier, edges = less)
    atm_factor = 1.0 - (moneyness_pct / ZERO_DTE_CONFIG["atm_range_pct"])

    return 1.0 + (multiplier - 1.0) * atm_factor

# =============================================================================
# PRICE PROXIMITY ALERTS
# =============================================================================
PROXIMITY_ALERTS = {
    # Distance thresholds (% of spot price)
    "approaching_pct": 0.005,    # 0.5% = "approaching"
    "at_level_pct": 0.002,       # 0.2% = "at level"
    "touching_pct": 0.001,       # 0.1% = "touching"

    # Alert priorities
    "priorities": {
        "touching": "critical",
        "at_level": "high",
        "approaching": "medium",
    }
}

def get_proximity_status(spot: float, level: float) -> dict:
    """
    Calculate proximity status between spot and a GEX level.

    Returns dict with:
        - distance_dollars: absolute distance
        - distance_pct: percentage distance
        - status: 'touching', 'at_level', 'approaching', or 'distant'
        - direction: 'above' or 'below' (where level is relative to spot)
    """
    if spot <= 0 or level <= 0:
        return {"distance_dollars": 0, "distance_pct": 0, "status": "unknown", "direction": "unknown"}

    distance = level - spot
    distance_pct = abs(distance) / spot

    # Determine status
    if distance_pct <= PROXIMITY_ALERTS["touching_pct"]:
        status = "touching"
    elif distance_pct <= PROXIMITY_ALERTS["at_level_pct"]:
        status = "at_level"
    elif distance_pct <= PROXIMITY_ALERTS["approaching_pct"]:
        status = "approaching"
    else:
        status = "distant"

    return {
        "distance_dollars": round(abs(distance), 2),
        "distance_pct": round(distance_pct * 100, 3),
        "status": status,
        "direction": "above" if distance > 0 else "below",
        "priority": PROXIMITY_ALERTS["priorities"].get(status, "low")
    }

# =============================================================================
# IV SKEW SETTINGS
# =============================================================================
IV_SKEW_CONFIG = {
    # Target deltas for skew calculation
    "put_delta": -0.25,    # 25-delta put
    "call_delta": 0.25,    # 25-delta call
    "delta_tolerance": 0.05,  # Accept deltas within this range

    # Skew interpretation thresholds
    "neutral_range": (0.95, 1.05),  # Skew between 0.95-1.05 = neutral
    "elevated_threshold": 1.10,     # Skew > 1.10 = elevated fear
    "extreme_threshold": 1.20,      # Skew > 1.20 = extreme fear

    # GEX reliability adjustment based on skew
    "reliability_penalty": {
        "neutral": 0,       # No penalty
        "elevated": 10,     # -10 from reliability score
        "extreme": 25,      # -25 from reliability score
    }
}

def interpret_skew(skew: float) -> dict:
    """
    Interpret IV skew value.

    Skew = 25-delta Put IV / 25-delta Call IV
    - Skew > 1: Puts are more expensive (fear/hedging demand)
    - Skew < 1: Calls are more expensive (bullish speculation)

    Returns interpretation dict.
    """
    neutral_low, neutral_high = IV_SKEW_CONFIG["neutral_range"]

    if skew < neutral_low:
        regime = "call_premium"
        description = "Calls expensive - bullish speculation"
        gex_reliability = "normal"
    elif skew <= neutral_high:
        regime = "neutral"
        description = "Balanced skew"
        gex_reliability = "normal"
    elif skew <= IV_SKEW_CONFIG["elevated_threshold"]:
        regime = "mild_fear"
        description = "Mild put premium - some hedging"
        gex_reliability = "normal"
    elif skew <= IV_SKEW_CONFIG["extreme_threshold"]:
        regime = "elevated_fear"
        description = "Elevated put premium - active hedging"
        gex_reliability = "reduced"
    else:
        regime = "extreme_fear"
        description = "Extreme put skew - panic hedging"
        gex_reliability = "unreliable"

    return {
        "skew": round(skew, 3),
        "regime": regime,
        "description": description,
        "gex_reliability": gex_reliability,
        "reliability_penalty": IV_SKEW_CONFIG["reliability_penalty"].get(
            "extreme" if regime == "extreme_fear" else
            "elevated" if regime == "elevated_fear" else "neutral", 0
        )
    }

# =============================================================================
# OPEX DETECTION
# =============================================================================
def get_monthly_opex(year: int, month: int) -> datetime:
    """Get the monthly OPEX date (3rd Friday) for a given month."""
    # Find the first day of the month
    first_day = datetime(year, month, 1)
    # Find the first Friday (weekday 4)
    days_until_friday = (4 - first_day.weekday()) % 7
    first_friday = first_day + timedelta(days=days_until_friday)
    # Third Friday is 14 days after first Friday
    third_friday = first_friday + timedelta(days=14)
    return third_friday

def is_opex_week(check_date: datetime = None) -> bool:
    """Check if the given date is within OPEX week."""
    if check_date is None:
        check_date = datetime.now()

    opex = get_monthly_opex(check_date.year, check_date.month)
    # OPEX week is Monday through Friday of OPEX week
    opex_monday = opex - timedelta(days=4)
    opex_friday = opex

    return opex_monday.date() <= check_date.date() <= opex_friday.date()

def get_next_opex() -> datetime:
    """Get the next OPEX date."""
    now = datetime.now()
    opex = get_monthly_opex(now.year, now.month)

    if now.date() > opex.date():
        # Move to next month
        if now.month == 12:
            opex = get_monthly_opex(now.year + 1, 1)
        else:
            opex = get_monthly_opex(now.year, now.month + 1)

    return opex

# =============================================================================
# REFRESH SETTINGS
# =============================================================================
REFRESH_INTERVALS = [1, 5, 15, 30, 60]  # Available intervals in minutes
DEFAULT_REFRESH_INTERVAL = 5  # Default 5 minutes

# Stale data thresholds (multiples of refresh interval)
STALE_WARNING_MULTIPLIER = 2  # Yellow badge
STALE_ERROR_MULTIPLIER = 5    # Red badge

# =============================================================================
# NODE CLASSIFICATION
# =============================================================================
# Minimum distance (in strikes) between King and Gatekeeper
MIN_KING_GATEKEEPER_DISTANCE = 1

# Maximum distance for a node to be considered "near" another
MAX_CLUSTER_DISTANCE = 3  # strikes

# =============================================================================
# CHANGE DETECTION & ALERTS
# =============================================================================
# Thresholds for triggering alerts
ALERT_THRESHOLDS = {
    "king_gex_drop_pct": 0.20,      # Alert if King GEX drops >20%
    "net_gex_flip": True,           # Alert on net GEX sign change
    "zero_gamma_cross": True,       # Alert when zero gamma crosses spot
    "king_strike_change": True,     # Alert when King moves to different strike
    "delta_gex_spike_pct": 0.15,    # Alert if ΔGEX > 15% of total GEX
    "delta_gex_spike_notional": 100_000_000,  # Or if ΔGEX > $100M absolute
}

# Alert debouncing - require N consecutive refreshes before alerting
ALERT_DEBOUNCE_REFRESHES = 2

# Price proximity threshold (how close is "near" a level)
PRICE_PROXIMITY_PCT = 0.005  # 0.5% of spot price

# Zero gamma buffer (% of spot to consider "crossed")
ZERO_GAMMA_BUFFER_PCT = 0.0015  # 0.15% buffer

# =============================================================================
# TRADING ZONE LABELS - DETERMINISTIC RULES
# =============================================================================
class TradingContext:
    """Trading context labels for executable zones."""
    ABSORPTION = "absorption"           # High +GEX, price approaching - expect fade
    BREAKOUT = "breakout"               # Price broke through level
    REJECTION = "rejection"             # Price touched and reversed
    ACCELERATION = "acceleration"       # Negative GEX - vol expansion zone
    MAGNET = "magnet"                   # Strong +GEX pulling price
    SUPPORT = "support"                 # +GEX below price
    RESISTANCE = "resistance"           # +GEX above price
    NEUTRAL = "neutral"                 # No strong signal

# Deterministic thresholds for zone labeling (locked rules)
ZONE_LABEL_RULES = {
    # MAGNET: strike == King AND |gex| > magnet_threshold
    "magnet_min_strength": 0.70,        # Must be >= 70% of max GEX

    # ABSORPTION: positive gex AND within ±X% of spot AND strength > Y
    "absorption_proximity_pct": 0.0035,  # Within 0.35% of spot
    "absorption_min_strength": 0.40,     # Must be >= 40% of max GEX

    # ACCELERATION: negative gex AND within ±X% of spot
    "acceleration_proximity_pct": 0.0050,  # Within 0.50% of spot

    # SUPPORT/RESISTANCE: positive gex, top N by magnitude
    "support_resistance_top_n": 5,       # Top 5 zones per side
}

# =============================================================================
# VOLATILITY REGIME
# =============================================================================
# VIX thresholds for regime detection
VIX_REGIMES = {
    "low": 15,          # VIX < 15 = low vol regime
    "normal": 20,       # VIX 15-20 = normal
    "elevated": 25,     # VIX 20-25 = elevated
    "high": 30,         # VIX 25-30 = high
    "extreme": 30,      # VIX > 30 = extreme
}

# GEX reliability by regime (base scores)
GEX_RELIABILITY = {
    "low": "HIGH",          # Low VIX = GEX very reliable
    "normal": "HIGH",       # Normal = reliable
    "elevated": "MEDIUM",   # Elevated = somewhat reliable
    "high": "LOW",          # High VIX = less reliable
    "extreme": "LOW",       # Extreme = unreliable
    "opex": "MEDIUM",       # OPEX week = near-term less reliable
    "event": "LOW",         # Event day = unreliable
}

# =============================================================================
# MULTI-FACTOR RELIABILITY SCORING
# =============================================================================
# Reliability = weighted sum of factors, each 0-100, final score 0-100
RELIABILITY_FACTORS = {
    # Factor weights (must sum to 1.0)
    "weights": {
        "vix_regime": 0.30,         # VIX level impact
        "time_of_day": 0.10,        # Market hours behavior
        "oi_freshness": 0.15,       # How fresh is OI data
        "spot_zg_proximity": 0.15,  # Distance to zero gamma
        "net_gamma_magnitude": 0.20,  # Size of net gamma (fragility)
        "opex_proximity": 0.10,     # Days to OPEX
    },

    # VIX regime scores (0-100)
    "vix_scores": {
        "low": 100,
        "normal": 90,
        "elevated": 65,
        "high": 40,
        "extreme": 20,
    },

    # Time of day scores (0-100)
    "time_scores": {
        "pre_market": 60,       # 4:00-9:30 ET
        "open": 50,             # 9:30-10:00 ET (volatile)
        "mid_session": 85,      # 10:00-15:00 ET
        "close": 60,            # 15:00-16:00 ET (volatile)
        "after_hours": 40,      # 16:00-20:00 ET
        "overnight": 30,        # 20:00-4:00 ET
    },

    # OI freshness (decays during day)
    "oi_freshness_decay_per_hour": 2,  # Lose 2 points per hour from open

    # Spot proximity to zero gamma (closer = less reliable)
    "zg_proximity_threshold_pct": 0.005,  # Within 0.5% = fragile

    # Net gamma magnitude (small = fragile)
    "net_gamma_fragile_threshold": 50_000_000,  # < $50M = fragile
}

# Reliability grade mapping
RELIABILITY_GRADES = {
    90: "A",   # 90-100
    75: "B",   # 75-89
    55: "C",   # 55-74
    35: "D",   # 35-54
    0: "F",    # 0-34
}

# Economic event dates for 2025 (update annually)
# Format: (month, day, name, impact)
# Impact: "high" (FOMC, CPI, Jobs), "medium" (PPI, Retail), "low" (other)
EVENT_DATES = [
    # January 2025
    (1, 3, "Jobs Report", "high"),
    (1, 14, "PPI", "medium"),
    (1, 15, "CPI", "high"),
    (1, 29, "FOMC Decision", "high"),
    (1, 30, "GDP", "medium"),
    # February 2025
    (2, 7, "Jobs Report", "high"),
    (2, 12, "CPI", "high"),
    (2, 13, "PPI", "medium"),
    (2, 14, "Retail Sales", "medium"),
    # March 2025
    (3, 7, "Jobs Report", "high"),
    (3, 12, "CPI", "high"),
    (3, 13, "PPI", "medium"),
    (3, 19, "FOMC Decision", "high"),
    # April 2025
    (4, 4, "Jobs Report", "high"),
    (4, 10, "CPI", "high"),
    (4, 11, "PPI", "medium"),
    # May 2025
    (5, 2, "Jobs Report", "high"),
    (5, 7, "FOMC Decision", "high"),
    (5, 13, "CPI", "high"),
    (5, 14, "PPI", "medium"),
    # June 2025
    (6, 6, "Jobs Report", "high"),
    (6, 11, "CPI", "high"),
    (6, 12, "PPI", "medium"),
    (6, 18, "FOMC Decision", "high"),
    # July 2025
    (7, 3, "Jobs Report", "high"),
    (7, 10, "CPI", "high"),
    (7, 11, "PPI", "medium"),
    (7, 30, "FOMC Decision", "high"),
    # August 2025
    (8, 1, "Jobs Report", "high"),
    (8, 13, "CPI", "high"),
    (8, 14, "PPI", "medium"),
    # September 2025
    (9, 5, "Jobs Report", "high"),
    (9, 10, "CPI", "high"),
    (9, 11, "PPI", "medium"),
    (9, 17, "FOMC Decision", "high"),
    # October 2025
    (10, 3, "Jobs Report", "high"),
    (10, 10, "CPI", "high"),
    (10, 14, "PPI", "medium"),
    # November 2025
    (11, 7, "Jobs Report", "high"),
    (11, 5, "FOMC Decision", "high"),
    (11, 13, "CPI", "high"),
    (11, 14, "PPI", "medium"),
    # December 2025
    (12, 5, "Jobs Report", "high"),
    (12, 10, "CPI", "high"),
    (12, 11, "PPI", "medium"),
    (12, 17, "FOMC Decision", "high"),
]

def is_event_day(check_date: datetime = None) -> dict:
    """Check if the given date is an economic event day."""
    if check_date is None:
        check_date = datetime.now()

    for month, day, name, impact in EVENT_DATES:
        if check_date.month == month and check_date.day == day:
            return {"is_event": True, "name": name, "impact": impact}

    return {"is_event": False, "name": None, "impact": None}

# =============================================================================
# DTE WEIGHTING POLICY
# =============================================================================
# Weighting strategy for expirations
DTE_WEIGHTING_POLICY = {
    # Near-term (0-7 DTE): Intraday dealer fight - highest weight
    # Mid-term (7-30 DTE): Structural positioning
    # Long-term (30+ DTE): Background bias

    "0dte_weight": 1.0,      # 0 DTE = full weight (day of)
    "1-2dte_weight": 0.85,   # 1-2 DTE = near full
    "3-7dte_weight": 0.70,   # Week out
    "8-14dte_weight": 0.55,  # 2 weeks
    "15-30dte_weight": 0.40, # Monthly
    "31-60dte_weight": 0.25, # Quarterly
    "60+dte_weight": 0.15,   # Long-dated (structural only)
}

# =============================================================================
# VEX DATA QUALITY FLAGS
# =============================================================================
# Vanna is calculated using Black-Scholes with real IV from OPRA
VEX_DATA_QUALITY = {
    "source": "calculated",        # "real" (from feed), "calculated" (Black-Scholes), "derived" (proxy)
    "method": "black_scholes",     # Proper BS formula with d1/d2
    "confidence": "high",          # "high", "medium", "low"
    "show_in_ui": True,            # Whether to show VEX
    "inputs": ["spot", "strike", "iv", "dte", "rate"],  # What we use for calculation
    # Assumptions & limitations
    "assumptions": {
        "risk_free_rate": 0.045,   # 4.5% - 10Y Treasury (update periodically)
        "dividend_yield": 0.0,     # NOT included - affects accuracy for high-div stocks
        "note": "Dividend yield ignored. For SPY (~1.3% yield), vanna slightly overstated.",
    },
}
