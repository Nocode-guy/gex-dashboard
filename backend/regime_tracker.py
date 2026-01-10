"""
Volatility Regime Tracker & Alert System

Tracks VIX levels, market regime, and generates alerts for GEX changes.
"""
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional, Tuple
from enum import Enum

try:
    import yfinance as yf
    YF_AVAILABLE = True
except ImportError:
    YF_AVAILABLE = False

from config import (
    VIX_REGIMES, GEX_RELIABILITY, ALERT_THRESHOLDS,
    PRICE_PROXIMITY_PCT, TradingContext, is_opex_week, get_next_opex,
    ALERT_DEBOUNCE_REFRESHES, ZERO_GAMMA_BUFFER_PCT,
    RELIABILITY_FACTORS, RELIABILITY_GRADES, is_event_day
)


class VolatilityRegime(Enum):
    """Market volatility regime."""
    LOW = "low"
    NORMAL = "normal"
    ELEVATED = "elevated"
    HIGH = "high"
    EXTREME = "extreme"


class AlertType(Enum):
    """Types of alerts."""
    KING_FLIP = "king_flip"              # King moved to different strike
    KING_WEAKENING = "king_weakening"    # King GEX dropped significantly
    NET_GEX_FLIP = "net_gex_flip"        # Net GEX changed sign
    ZERO_GAMMA_CROSS = "zero_gamma_cross"  # Zero gamma crossed spot
    REGIME_CHANGE = "regime_change"       # Vol regime changed
    APPROACHING_KING = "approaching_king"  # Price nearing King
    BIG_MOVE = "big_move"                 # Large % price move detected
    VOLUME_SURGE = "volume_surge"         # Unusual volume spike


class AlertSeverity(Enum):
    """Alert severity levels."""
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class Alert:
    """Single alert event."""
    alert_type: AlertType
    severity: AlertSeverity
    symbol: str
    message: str
    timestamp: datetime
    old_value: Optional[float] = None
    new_value: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "type": self.alert_type.value,
            "severity": self.severity.value,
            "symbol": self.symbol,
            "message": self.message,
            "timestamp": self.timestamp.isoformat(),
            "old_value": self.old_value,
            "new_value": self.new_value
        }


@dataclass
class GEXSnapshot:
    """Snapshot of GEX state for change detection."""
    symbol: str
    timestamp: datetime
    spot_price: float
    net_gex: float
    king_strike: Optional[float]
    king_gex: Optional[float]
    zero_gamma_level: Optional[float]
    net_vex: float = 0.0
    net_dex: float = 0.0


@dataclass
class ReliabilityScore:
    """Multi-factor reliability score."""
    score: int  # 0-100
    grade: str  # A, B, C, D, F
    reasons: List[str]  # Explanation factors

    # Individual factor scores
    vix_score: int = 0
    time_score: int = 0
    oi_freshness_score: int = 0
    zg_proximity_score: int = 0
    net_gamma_score: int = 0
    opex_score: int = 0

    def to_dict(self) -> dict:
        return {
            "score": self.score,
            "grade": self.grade,
            "reasons": self.reasons,
            "factors": {
                "vix": self.vix_score,
                "time": self.time_score,
                "oi_freshness": self.oi_freshness_score,
                "zg_proximity": self.zg_proximity_score,
                "net_gamma": self.net_gamma_score,
                "opex": self.opex_score,
            }
        }


@dataclass
class RegimeState:
    """Current market regime state."""
    vix_level: float
    vix_ma5: float
    regime: VolatilityRegime
    gex_reliability: str
    is_opex_week: bool
    is_event_day: bool
    vix_rising: bool
    timestamp: datetime
    reliability_score: Optional[ReliabilityScore] = None
    event_name: Optional[str] = None
    event_impact: Optional[str] = None

    def to_dict(self) -> dict:
        result = {
            "vix": round(self.vix_level, 2),
            "vix_ma5": round(self.vix_ma5, 2),
            "regime": self.regime.value,
            "reliability": self.gex_reliability,
            "opex_week": self.is_opex_week,
            "event_day": self.is_event_day,
            "event_name": self.event_name,
            "event_impact": self.event_impact,
            "vix_rising": self.vix_rising,
            "timestamp": self.timestamp.isoformat()
        }
        if self.reliability_score:
            result["reliability_score"] = self.reliability_score.to_dict()
        return result


@dataclass
class ChangeDetection:
    """Detected changes between snapshots."""
    delta_gex: float = 0.0
    delta_vex: float = 0.0
    delta_dex: float = 0.0
    delta_gex_pct: float = 0.0
    king_changed: bool = False
    old_king_strike: Optional[float] = None
    new_king_strike: Optional[float] = None
    net_gex_flipped: bool = False
    zero_gamma_crossed_spot: bool = False
    spot_crossed_direction: Optional[str] = None  # "above" or "below"

    def to_dict(self) -> dict:
        return {
            "delta_gex": round(self.delta_gex, 0),
            "delta_vex": round(self.delta_vex, 0),
            "delta_dex": round(self.delta_dex, 0),
            "delta_gex_pct": round(self.delta_gex_pct * 100, 1),
            "king_changed": self.king_changed,
            "old_king_strike": self.old_king_strike,
            "new_king_strike": self.new_king_strike,
            "net_gex_flipped": self.net_gex_flipped,
            "zero_gamma_crossed_spot": self.zero_gamma_crossed_spot,
            "spot_crossed_direction": self.spot_crossed_direction
        }


class RegimeTracker:
    """
    Tracks volatility regime and detects GEX changes.
    """

    # Top symbols to scan for big moves
    SCAN_SYMBOLS = [
        # Major ETFs
        "SPY", "QQQ", "IWM", "DIA",
        # Mag 7
        "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA",
        # Popular movers
        "AMD", "NFLX", "CRM", "ORCL", "INTC", "MU", "SMCI",
        # Finance
        "JPM", "BAC", "GS", "V", "MA",
        # Retail/Consumer
        "COST", "WMT", "HD", "TGT", "NKE",
        # Energy
        "XOM", "CVX",
        # Crypto-adjacent
        "COIN", "MSTR"
    ]

    def __init__(self):
        self.vix_history: List[float] = []
        self.previous_snapshots: Dict[str, GEXSnapshot] = {}
        self.alerts: List[Alert] = []
        self.current_regime: Optional[RegimeState] = None
        self._last_vix_fetch: Optional[datetime] = None

        # Alert debouncing - track consecutive triggers
        self._pending_alerts: Dict[str, int] = {}  # key -> consecutive count
        self._last_reliability_calc: Dict[str, ReliabilityScore] = {}

        # Price tracking for big move detection
        self._price_history: Dict[str, List[Tuple[datetime, float]]] = {}  # symbol -> [(time, price), ...]
        self._last_big_move_alert: Dict[str, datetime] = {}  # Cooldown tracking

    def fetch_vix(self) -> Tuple[float, float]:
        """
        Fetch current VIX level and 5-day MA.
        Returns: (current_vix, vix_ma5)
        """
        if not YF_AVAILABLE:
            return 20.0, 20.0  # Default to normal regime

        try:
            vix = yf.Ticker("^VIX")
            hist = vix.history(period="10d")

            if hist.empty:
                return 20.0, 20.0

            current_vix = float(hist['Close'].iloc[-1])
            vix_ma5 = float(hist['Close'].tail(5).mean())

            # Update history
            self.vix_history = hist['Close'].tolist()
            self._last_vix_fetch = datetime.now()

            return current_vix, vix_ma5

        except Exception as e:
            print(f"Error fetching VIX: {e}")
            return 20.0, 20.0

    def determine_regime(self, vix: float) -> VolatilityRegime:
        """Determine volatility regime from VIX level."""
        if vix < VIX_REGIMES["low"]:
            return VolatilityRegime.LOW
        elif vix < VIX_REGIMES["normal"]:
            return VolatilityRegime.NORMAL
        elif vix < VIX_REGIMES["elevated"]:
            return VolatilityRegime.ELEVATED
        elif vix < VIX_REGIMES["high"]:
            return VolatilityRegime.HIGH
        else:
            return VolatilityRegime.EXTREME

    def get_reliability(self, regime: VolatilityRegime, is_opex: bool, is_event: bool) -> str:
        """Get GEX reliability rating based on conditions."""
        if is_event:
            return GEX_RELIABILITY["event"]
        if is_opex:
            # Combine with regime
            base = GEX_RELIABILITY.get(regime.value, "MEDIUM")
            if base == "HIGH":
                return "MEDIUM"  # Downgrade during OPEX
            return base
        return GEX_RELIABILITY.get(regime.value, "MEDIUM")

    def calculate_reliability_score(
        self,
        symbol: str,
        regime: VolatilityRegime,
        spot_price: float,
        zero_gamma_level: Optional[float],
        net_gex: float
    ) -> ReliabilityScore:
        """
        Calculate multi-factor reliability score (0-100).

        Factors:
        - VIX regime (30%)
        - Time of day (10%)
        - OI freshness (15%)
        - Spot proximity to zero gamma (15%)
        - Net gamma magnitude (20%)
        - OPEX proximity (10%)
        """
        weights = RELIABILITY_FACTORS["weights"]
        reasons = []

        # 1. VIX regime score
        vix_scores = RELIABILITY_FACTORS["vix_scores"]
        vix_score = vix_scores.get(regime.value, 50)
        if regime == VolatilityRegime.EXTREME:
            reasons.append("VIX extreme")
        elif regime == VolatilityRegime.HIGH:
            reasons.append("VIX high")
        elif regime == VolatilityRegime.LOW:
            reasons.append("VIX low")
        else:
            reasons.append(f"VIX {regime.value}")

        # 2. Time of day score
        now = datetime.now()
        hour = now.hour
        time_scores = RELIABILITY_FACTORS["time_scores"]

        if hour < 4:
            time_score = time_scores["overnight"]
            reasons.append("overnight")
        elif hour < 9 or (hour == 9 and now.minute < 30):
            time_score = time_scores["pre_market"]
            reasons.append("pre-market")
        elif hour == 9 and now.minute >= 30:
            time_score = time_scores["open"]
            reasons.append("market open")
        elif hour < 15:
            time_score = time_scores["mid_session"]
            reasons.append("RTH session")
        elif hour < 16:
            time_score = time_scores["close"]
            reasons.append("close approaching")
        elif hour < 20:
            time_score = time_scores["after_hours"]
            reasons.append("after hours")
        else:
            time_score = time_scores["overnight"]
            reasons.append("overnight")

        # 3. OI freshness score (decays from 100 through the day)
        # OI updates at market open, so freshness decays
        hours_since_open = max(0, hour - 9) if hour >= 9 else 0
        decay_rate = RELIABILITY_FACTORS["oi_freshness_decay_per_hour"]
        oi_freshness_score = max(40, 100 - (hours_since_open * decay_rate))

        # 4. Spot proximity to zero gamma
        if zero_gamma_level and spot_price > 0:
            zg_distance_pct = abs(spot_price - zero_gamma_level) / spot_price
            threshold = RELIABILITY_FACTORS["zg_proximity_threshold_pct"]
            if zg_distance_pct < threshold:
                zg_proximity_score = 30  # Very close = fragile
                reasons.append("near zero gamma")
            elif zg_distance_pct < threshold * 2:
                zg_proximity_score = 60
            elif zg_distance_pct < threshold * 4:
                zg_proximity_score = 80
            else:
                zg_proximity_score = 100
        else:
            zg_proximity_score = 70  # Unknown = neutral

        # 5. Net gamma magnitude (small = fragile)
        fragile_threshold = RELIABILITY_FACTORS["net_gamma_fragile_threshold"]
        abs_net_gex = abs(net_gex)
        if abs_net_gex < fragile_threshold:
            net_gamma_score = 40
            reasons.append("net GEX fragile")
        elif abs_net_gex < fragile_threshold * 2:
            net_gamma_score = 60
        elif abs_net_gex < fragile_threshold * 5:
            net_gamma_score = 80
        else:
            net_gamma_score = 100
            reasons.append("net GEX high")

        # 6. OPEX proximity
        opex_date = get_next_opex()
        days_to_opex = (opex_date.date() - datetime.now().date()).days
        if days_to_opex <= 0:
            opex_score = 40  # OPEX day
            reasons.append("OPEX day")
        elif days_to_opex <= 2:
            opex_score = 60
            reasons.append("near OPEX")
        elif days_to_opex <= 5:
            opex_score = 80
        else:
            opex_score = 100

        # Calculate weighted total
        total_score = int(
            vix_score * weights["vix_regime"] +
            time_score * weights["time_of_day"] +
            oi_freshness_score * weights["oi_freshness"] +
            zg_proximity_score * weights["spot_zg_proximity"] +
            net_gamma_score * weights["net_gamma_magnitude"] +
            opex_score * weights["opex_proximity"]
        )

        # Determine grade
        grade = "F"
        for threshold, g in sorted(RELIABILITY_GRADES.items(), reverse=True):
            if total_score >= threshold:
                grade = g
                break

        reliability = ReliabilityScore(
            score=total_score,
            grade=grade,
            reasons=reasons[:4],  # Limit to top 4 reasons
            vix_score=vix_score,
            time_score=time_score,
            oi_freshness_score=int(oi_freshness_score),
            zg_proximity_score=zg_proximity_score,
            net_gamma_score=net_gamma_score,
            opex_score=opex_score
        )

        self._last_reliability_calc[symbol] = reliability
        return reliability

    def _should_fire_alert(self, alert_key: str) -> bool:
        """
        Check if alert should fire based on debouncing.
        Requires ALERT_DEBOUNCE_REFRESHES consecutive triggers.
        """
        self._pending_alerts[alert_key] = self._pending_alerts.get(alert_key, 0) + 1

        if self._pending_alerts[alert_key] >= ALERT_DEBOUNCE_REFRESHES:
            # Reset counter and fire
            self._pending_alerts[alert_key] = 0
            return True
        return False

    def _reset_alert_counter(self, alert_key: str):
        """Reset debounce counter when condition no longer holds."""
        if alert_key in self._pending_alerts:
            self._pending_alerts[alert_key] = 0

    def update_regime(self) -> RegimeState:
        """Update and return current regime state."""
        vix, vix_ma5 = self.fetch_vix()
        regime = self.determine_regime(vix)
        is_opex = is_opex_week()
        event_info = is_event_day()
        is_event = event_info["is_event"]
        vix_rising = vix > vix_ma5

        reliability = self.get_reliability(regime, is_opex, is_event)

        # Check for regime change
        if self.current_regime and self.current_regime.regime != regime:
            self._create_alert(
                AlertType.REGIME_CHANGE,
                AlertSeverity.WARNING,
                "MARKET",
                f"Regime changed: {self.current_regime.regime.value} -> {regime.value}",
                old_value=self.current_regime.vix_level,
                new_value=vix
            )

        self.current_regime = RegimeState(
            vix_level=vix,
            vix_ma5=vix_ma5,
            regime=regime,
            gex_reliability=reliability,
            is_opex_week=is_opex,
            is_event_day=is_event,
            vix_rising=vix_rising,
            timestamp=datetime.now(),
            event_name=event_info["name"],
            event_impact=event_info["impact"]
        )

        return self.current_regime

    def detect_changes(
        self,
        symbol: str,
        spot_price: float,
        net_gex: float,
        king_strike: Optional[float],
        king_gex: Optional[float],
        zero_gamma_level: Optional[float],
        net_vex: float = 0.0,
        net_dex: float = 0.0
    ) -> ChangeDetection:
        """
        Detect changes from previous snapshot.
        Returns ChangeDetection with deltas and flags.
        """
        changes = ChangeDetection()

        # Get previous snapshot
        prev = self.previous_snapshots.get(symbol)

        if prev is None:
            # First snapshot - store and return empty changes
            self._store_snapshot(symbol, spot_price, net_gex, king_strike,
                                king_gex, zero_gamma_level, net_vex, net_dex)
            return changes

        # Calculate deltas
        changes.delta_gex = net_gex - prev.net_gex
        changes.delta_vex = net_vex - prev.net_vex
        changes.delta_dex = net_dex - prev.net_dex

        if prev.net_gex != 0:
            changes.delta_gex_pct = changes.delta_gex / abs(prev.net_gex)

        # Check King change (with debouncing)
        king_flip_key = f"{symbol}_king_flip"
        if king_strike and prev.king_strike and king_strike != prev.king_strike:
            changes.king_changed = True
            changes.old_king_strike = prev.king_strike
            changes.new_king_strike = king_strike

            if self._should_fire_alert(king_flip_key):
                self._create_alert(
                    AlertType.KING_FLIP,
                    AlertSeverity.CRITICAL,
                    symbol,
                    f"King flipped: {prev.king_strike} -> {king_strike}",
                    old_value=prev.king_strike,
                    new_value=king_strike
                )
        else:
            self._reset_alert_counter(king_flip_key)

        # Check King weakening
        if king_gex and prev.king_gex:
            gex_drop_pct = (prev.king_gex - king_gex) / abs(prev.king_gex)
            if gex_drop_pct > ALERT_THRESHOLDS["king_gex_drop_pct"]:
                self._create_alert(
                    AlertType.KING_WEAKENING,
                    AlertSeverity.WARNING,
                    symbol,
                    f"King GEX dropped {gex_drop_pct*100:.1f}%",
                    old_value=prev.king_gex,
                    new_value=king_gex
                )

        # Check net GEX flip (with debouncing - require 2 consecutive)
        gex_flip_key = f"{symbol}_gex_flip"
        if (prev.net_gex > 0 and net_gex < 0) or (prev.net_gex < 0 and net_gex > 0):
            changes.net_gex_flipped = True
            direction = "positive" if net_gex > 0 else "negative"

            if self._should_fire_alert(gex_flip_key):
                self._create_alert(
                    AlertType.NET_GEX_FLIP,
                    AlertSeverity.CRITICAL,
                    symbol,
                    f"Net GEX flipped to {direction}",
                    old_value=prev.net_gex,
                    new_value=net_gex
                )
        else:
            self._reset_alert_counter(gex_flip_key)

        # Check zero gamma crossing spot (with buffer to prevent noise)
        zg_cross_key = f"{symbol}_zg_cross"
        if zero_gamma_level and prev.zero_gamma_level and spot_price > 0:
            # Apply buffer: only trigger if crossed by more than buffer amount
            buffer = spot_price * ZERO_GAMMA_BUFFER_PCT

            prev_above = prev.spot_price > (prev.zero_gamma_level + buffer)
            prev_below = prev.spot_price < (prev.zero_gamma_level - buffer)
            curr_above = spot_price > (zero_gamma_level + buffer)
            curr_below = spot_price < (zero_gamma_level - buffer)

            # Only trigger if clearly crossed (was above+buffer, now below-buffer or vice versa)
            crossed = (prev_above and curr_below) or (prev_below and curr_above)

            if crossed:
                changes.zero_gamma_crossed_spot = True
                changes.spot_crossed_direction = "above" if curr_above else "below"

                if self._should_fire_alert(zg_cross_key):
                    self._create_alert(
                        AlertType.ZERO_GAMMA_CROSS,
                        AlertSeverity.CRITICAL,
                        symbol,
                        f"Spot crossed zero gamma ({changes.spot_crossed_direction})",
                        old_value=prev.zero_gamma_level,
                        new_value=zero_gamma_level
                    )
            else:
                self._reset_alert_counter(zg_cross_key)

        # Store new snapshot
        self._store_snapshot(symbol, spot_price, net_gex, king_strike,
                            king_gex, zero_gamma_level, net_vex, net_dex)

        return changes

    def _store_snapshot(
        self,
        symbol: str,
        spot_price: float,
        net_gex: float,
        king_strike: Optional[float],
        king_gex: Optional[float],
        zero_gamma_level: Optional[float],
        net_vex: float,
        net_dex: float
    ):
        """Store a GEX snapshot for future comparison."""
        self.previous_snapshots[symbol] = GEXSnapshot(
            symbol=symbol,
            timestamp=datetime.now(),
            spot_price=spot_price,
            net_gex=net_gex,
            king_strike=king_strike,
            king_gex=king_gex,
            zero_gamma_level=zero_gamma_level,
            net_vex=net_vex,
            net_dex=net_dex
        )

    def track_price(self, symbol: str, price: float):
        """Track price for big move detection."""
        now = datetime.now()

        if symbol not in self._price_history:
            self._price_history[symbol] = []

        self._price_history[symbol].append((now, price))

        # Keep only last 30 minutes of data (at ~1 min intervals = ~30 points)
        cutoff = now - timedelta(minutes=30)
        self._price_history[symbol] = [
            (t, p) for t, p in self._price_history[symbol] if t > cutoff
        ]

    def check_big_move(self, symbol: str, current_price: float) -> Optional[dict]:
        """
        Check if symbol has made a big move.
        Returns alert info if big move detected, None otherwise.

        Thresholds:
        - ETFs (SPY, QQQ, etc): 1% in 15 min
        - Stocks: 2% in 15 min or 3% in 30 min
        """
        now = datetime.now()

        # Check cooldown (10 min between alerts for same symbol)
        if symbol in self._last_big_move_alert:
            if now - self._last_big_move_alert[symbol] < timedelta(minutes=10):
                return None

        # Track current price
        self.track_price(symbol, current_price)

        history = self._price_history.get(symbol, [])
        if len(history) < 3:  # Need at least 3 data points
            return None

        # Determine thresholds based on symbol type
        is_etf = symbol in ["SPY", "QQQ", "IWM", "DIA", "XLF", "XLE", "XLK"]
        threshold_15min = 1.0 if is_etf else 2.0  # % move threshold
        threshold_30min = 1.5 if is_etf else 3.0

        # Check 15 min move
        cutoff_15 = now - timedelta(minutes=15)
        prices_15 = [p for t, p in history if t > cutoff_15]

        if len(prices_15) >= 2:
            start_price = prices_15[0]
            pct_move = ((current_price - start_price) / start_price) * 100

            if abs(pct_move) >= threshold_15min:
                direction = "UP" if pct_move > 0 else "DOWN"
                dollar_move = current_price - start_price

                self._last_big_move_alert[symbol] = now
                self._create_alert(
                    AlertType.BIG_MOVE,
                    AlertSeverity.CRITICAL,
                    symbol,
                    f"BIG MOVE {direction}: {pct_move:+.1f}% (${dollar_move:+.2f}) in 15 min",
                    old_value=start_price,
                    new_value=current_price
                )
                return {
                    "symbol": symbol,
                    "direction": direction,
                    "pct_move": pct_move,
                    "dollar_move": dollar_move,
                    "timeframe": "15min"
                }

        # Check 30 min move (only if 15 min didn't trigger)
        prices_30 = [p for t, p in history]  # Full history is already 30 min max

        if len(prices_30) >= 5:
            start_price = prices_30[0]
            pct_move = ((current_price - start_price) / start_price) * 100

            if abs(pct_move) >= threshold_30min:
                direction = "UP" if pct_move > 0 else "DOWN"
                dollar_move = current_price - start_price

                self._last_big_move_alert[symbol] = now
                self._create_alert(
                    AlertType.BIG_MOVE,
                    AlertSeverity.WARNING,
                    symbol,
                    f"MOVING {direction}: {pct_move:+.1f}% (${dollar_move:+.2f}) in 30 min",
                    old_value=start_price,
                    new_value=current_price
                )
                return {
                    "symbol": symbol,
                    "direction": direction,
                    "pct_move": pct_move,
                    "dollar_move": dollar_move,
                    "timeframe": "30min"
                }

        return None

    def _create_alert(
        self,
        alert_type: AlertType,
        severity: AlertSeverity,
        symbol: str,
        message: str,
        old_value: Optional[float] = None,
        new_value: Optional[float] = None
    ):
        """Create and store an alert."""
        alert = Alert(
            alert_type=alert_type,
            severity=severity,
            symbol=symbol,
            message=message,
            timestamp=datetime.now(),
            old_value=old_value,
            new_value=new_value
        )
        self.alerts.append(alert)

        # Keep only last 100 alerts
        if len(self.alerts) > 100:
            self.alerts = self.alerts[-100:]

        # Log alert
        print(f"[ALERT] [{severity.value.upper()}] {symbol}: {message}")

    def get_recent_alerts(self, symbol: Optional[str] = None, limit: int = 10) -> List[dict]:
        """Get recent alerts, optionally filtered by symbol."""
        alerts = self.alerts
        if symbol:
            alerts = [a for a in alerts if a.symbol == symbol or a.symbol == "MARKET"]

        return [a.to_dict() for a in alerts[-limit:]]

    def get_trading_context(
        self,
        strike: float,
        gex: float,
        spot_price: float,
        king_strike: Optional[float],
        strength: float
    ) -> str:
        """
        Determine trading context for a zone.

        Returns actionable label like 'absorption', 'breakout', etc.
        """
        is_positive = gex > 0
        is_above_spot = strike > spot_price
        is_below_spot = strike < spot_price
        proximity = abs(strike - spot_price) / spot_price

        is_near = proximity < PRICE_PROXIMITY_PCT * 2  # Within 1% of spot
        is_very_near = proximity < PRICE_PROXIMITY_PCT  # Within 0.5%
        is_king = king_strike and strike == king_strike

        # Negative GEX zones = acceleration
        if not is_positive:
            return TradingContext.ACCELERATION

        # King node = magnet
        if is_king and strength > 0.8:
            return TradingContext.MAGNET

        # Near zones with high positive GEX = absorption
        if is_near and is_positive and strength > 0.5:
            return TradingContext.ABSORPTION

        # Standard support/resistance
        if is_above_spot and is_positive:
            return TradingContext.RESISTANCE

        if is_below_spot and is_positive:
            return TradingContext.SUPPORT

        return TradingContext.NEUTRAL


# Singleton instance
_regime_tracker: Optional[RegimeTracker] = None


def get_regime_tracker() -> RegimeTracker:
    """Get or create regime tracker singleton."""
    global _regime_tracker
    if _regime_tracker is None:
        _regime_tracker = RegimeTracker()
    return _regime_tracker
