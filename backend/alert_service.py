"""
Alert Service - Monitors top symbols for trading opportunities and big moves.

Alert Types:
1. GEX Regime Change - Price crossing 0γ flip
2. Level Break - Price breaking support/resistance/magnet
3. Volume Surge - Unusual volume spike
4. Flow Flip - Put/call ratio reversing
5. Acceleration Zone - Price entering negative GEX zone
6. Squeeze Setup - Walls tightening around price
"""

import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from dataclasses import dataclass, field
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class AlertType(Enum):
    GEX_REGIME_CHANGE = "gex_regime"      # Crossed 0γ flip
    LEVEL_BREAK = "level_break"            # Broke support/resistance
    VOLUME_SURGE = "volume_surge"          # 2x+ normal volume
    FLOW_FLIP = "flow_flip"                # Put/call ratio reversed
    ACCELERATION = "acceleration"          # Entered negative GEX zone
    SQUEEZE_SETUP = "squeeze"              # Walls tightening
    BIG_MOVE = "big_move"                  # Large % move detected
    APPROACHING_LEVEL = "approaching"      # Price near key level


class AlertSeverity(Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class Alert:
    id: str
    symbol: str
    alert_type: AlertType
    severity: AlertSeverity
    title: str
    message: str
    price: float
    level: Optional[float] = None
    timestamp: datetime = field(default_factory=datetime.now)
    dismissed: bool = False

    def to_dict(self):
        return {
            "id": self.id,
            "symbol": self.symbol,
            "type": self.alert_type.value,
            "severity": self.severity.value,
            "title": self.title,
            "message": self.message,
            "price": self.price,
            "level": self.level,
            "timestamp": self.timestamp.isoformat(),
            "dismissed": self.dismissed,
            "age_seconds": (datetime.now() - self.timestamp).total_seconds()
        }


@dataclass
class SymbolState:
    """Track state for each symbol to detect changes."""
    symbol: str
    last_price: float = 0
    last_gex_regime: str = "unknown"  # "positive" or "negative"
    last_flow_bias: str = "neutral"   # "bullish", "bearish", "neutral"
    last_check: datetime = field(default_factory=datetime.now)
    price_history: List[float] = field(default_factory=list)  # Last N prices
    volume_baseline: float = 0  # Average volume for comparison


class AlertService:
    """
    Background service that monitors symbols and generates alerts.
    """

    # Top 30 liquid symbols to monitor
    MONITORED_SYMBOLS = [
        # Major ETFs
        "SPY", "QQQ", "IWM", "DIA",
        # Mag 7
        "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA",
        # Other liquid tech
        "AMD", "NFLX", "CRM", "ORCL", "INTC",
        # Finance
        "JPM", "BAC", "GS", "V", "MA",
        # Other popular
        "XOM", "CVX", "COST", "WMT", "HD",
        # Crypto-adjacent
        "COIN", "MSTR",
        # Indices
        "SPX", "NDX"
    ]

    def __init__(self, gex_provider=None, flow_service=None):
        self.gex_provider = gex_provider
        self.flow_service = flow_service
        self.alerts: List[Alert] = []
        self.max_alerts = 100  # Keep last 100 alerts
        self.symbol_states: Dict[str, SymbolState] = {}
        self.running = False
        self.alert_counter = 0
        self.check_interval = 30  # Check every 30 seconds

        # Alert cooldowns (prevent spam)
        self.cooldowns: Dict[str, datetime] = {}
        self.cooldown_minutes = 5  # Min time between same alert type for same symbol

    def _generate_alert_id(self) -> str:
        self.alert_counter += 1
        return f"alert_{datetime.now().strftime('%Y%m%d%H%M%S')}_{self.alert_counter}"

    def _is_on_cooldown(self, symbol: str, alert_type: AlertType) -> bool:
        key = f"{symbol}_{alert_type.value}"
        if key in self.cooldowns:
            if datetime.now() - self.cooldowns[key] < timedelta(minutes=self.cooldown_minutes):
                return True
        return False

    def _set_cooldown(self, symbol: str, alert_type: AlertType):
        key = f"{symbol}_{alert_type.value}"
        self.cooldowns[key] = datetime.now()

    def add_alert(self, alert: Alert):
        """Add alert if not on cooldown."""
        if self._is_on_cooldown(alert.symbol, alert.alert_type):
            return

        self.alerts.insert(0, alert)  # Newest first
        self._set_cooldown(alert.symbol, alert.alert_type)

        # Trim old alerts
        if len(self.alerts) > self.max_alerts:
            self.alerts = self.alerts[:self.max_alerts]

        logger.info(f"[Alert] {alert.severity.value.upper()}: {alert.symbol} - {alert.title}")

    def get_alerts(self, limit: int = 20, include_dismissed: bool = False) -> List[dict]:
        """Get recent alerts."""
        alerts = self.alerts if include_dismissed else [a for a in self.alerts if not a.dismissed]
        return [a.to_dict() for a in alerts[:limit]]

    def dismiss_alert(self, alert_id: str):
        """Mark alert as dismissed."""
        for alert in self.alerts:
            if alert.id == alert_id:
                alert.dismissed = True
                break

    def clear_alerts(self, symbol: Optional[str] = None):
        """Clear alerts, optionally for specific symbol."""
        if symbol:
            self.alerts = [a for a in self.alerts if a.symbol != symbol]
        else:
            self.alerts = []

    async def check_symbol(self, symbol: str, gex_data: dict, flow_data: dict):
        """Check a symbol for alert conditions."""

        if not gex_data:
            return

        spot = gex_data.get("spot_price", 0)
        if not spot:
            return

        # Get or create symbol state
        if symbol not in self.symbol_states:
            self.symbol_states[symbol] = SymbolState(symbol=symbol, last_price=spot)

        state = self.symbol_states[symbol]

        # Update price history (keep last 20 data points = ~10 min at 30s intervals)
        state.price_history.append(spot)
        if len(state.price_history) > 20:
            state.price_history = state.price_history[-20:]

        levels = gex_data.get("levels", {})
        zones = gex_data.get("zones", [])

        # === CHECK 1: GEX Regime Change (0γ flip cross) ===
        zero_gamma = levels.get("zero_gamma")
        if zero_gamma and state.last_price:
            current_regime = "positive" if spot > zero_gamma else "negative"

            if state.last_gex_regime != "unknown" and current_regime != state.last_gex_regime:
                if current_regime == "negative":
                    self.add_alert(Alert(
                        id=self._generate_alert_id(),
                        symbol=symbol,
                        alert_type=AlertType.GEX_REGIME_CHANGE,
                        severity=AlertSeverity.CRITICAL,
                        title=f"NEGATIVE GAMMA",
                        message=f"Dropped below 0γ flip ({zero_gamma:.2f}). Moves will AMPLIFY.",
                        price=spot,
                        level=zero_gamma
                    ))
                else:
                    self.add_alert(Alert(
                        id=self._generate_alert_id(),
                        symbol=symbol,
                        alert_type=AlertType.GEX_REGIME_CHANGE,
                        severity=AlertSeverity.WARNING,
                        title=f"POSITIVE GAMMA",
                        message=f"Rose above 0γ flip ({zero_gamma:.2f}). Moves will stabilize.",
                        price=spot,
                        level=zero_gamma
                    ))

            state.last_gex_regime = current_regime

        # === CHECK 2: Level Breaks ===
        support = levels.get("support")
        resistance = levels.get("resistance")
        magnet = levels.get("magnet")

        if support and state.last_price:
            # Broke below support
            if state.last_price > support and spot < support:
                self.add_alert(Alert(
                    id=self._generate_alert_id(),
                    symbol=symbol,
                    alert_type=AlertType.LEVEL_BREAK,
                    severity=AlertSeverity.CRITICAL,
                    title=f"BROKE SUPPORT",
                    message=f"Dropped below support at {support:.2f}. Next support lower.",
                    price=spot,
                    level=support
                ))

        if resistance and state.last_price:
            # Broke above resistance
            if state.last_price < resistance and spot > resistance:
                self.add_alert(Alert(
                    id=self._generate_alert_id(),
                    symbol=symbol,
                    alert_type=AlertType.LEVEL_BREAK,
                    severity=AlertSeverity.CRITICAL,
                    title=f"BROKE RESISTANCE",
                    message=f"Broke above resistance at {resistance:.2f}. Could run higher.",
                    price=spot,
                    level=resistance
                ))

        # === CHECK 3: Big Move Detection ===
        if len(state.price_history) >= 5:
            # Check 5-period move (2.5 min at 30s intervals)
            old_price = state.price_history[-5]
            pct_move = ((spot - old_price) / old_price) * 100

            # Alert on 1%+ move in 2.5 minutes
            threshold = 1.0 if symbol in ["SPY", "QQQ", "IWM", "DIA"] else 2.0

            if abs(pct_move) >= threshold:
                direction = "UP" if pct_move > 0 else "DOWN"
                self.add_alert(Alert(
                    id=self._generate_alert_id(),
                    symbol=symbol,
                    alert_type=AlertType.BIG_MOVE,
                    severity=AlertSeverity.CRITICAL,
                    title=f"BIG MOVE {direction}",
                    message=f"Moved {pct_move:+.2f}% in last 2.5 min. From {old_price:.2f} to {spot:.2f}",
                    price=spot,
                    level=old_price
                ))

        # === CHECK 4: Approaching Key Levels ===
        if magnet and magnet != support and magnet != resistance:
            distance_pct = abs(spot - magnet) / spot * 100
            if distance_pct < 0.3:  # Within 0.3% of magnet
                self.add_alert(Alert(
                    id=self._generate_alert_id(),
                    symbol=symbol,
                    alert_type=AlertType.APPROACHING_LEVEL,
                    severity=AlertSeverity.WARNING,
                    title=f"NEAR MAGNET",
                    message=f"Approaching magnet at {magnet:.2f}. Price tends to gravitate here.",
                    price=spot,
                    level=magnet
                ))

        # === CHECK 5: Acceleration Zone ===
        accelerator = levels.get("accelerator")
        if accelerator and state.last_price:
            # Entered acceleration zone
            if spot < accelerator and state.last_price >= accelerator:
                self.add_alert(Alert(
                    id=self._generate_alert_id(),
                    symbol=symbol,
                    alert_type=AlertType.ACCELERATION,
                    severity=AlertSeverity.CRITICAL,
                    title=f"ACCELERATION ZONE",
                    message=f"Entered negative GEX zone below {accelerator:.2f}. Moves amplify!",
                    price=spot,
                    level=accelerator
                ))

        # === CHECK 6: Flow Flip ===
        if flow_data and "strike_pressure" in flow_data:
            total_calls = 0
            total_puts = 0

            for strike, data in flow_data["strike_pressure"].items():
                strike_f = float(strike)
                # Only count strikes near spot (within 2%)
                if abs(strike_f - spot) / spot < 0.02:
                    total_calls += data.get("call_volume", 0)
                    total_puts += data.get("put_volume", 0)

            if total_calls + total_puts > 0:
                call_ratio = total_calls / (total_calls + total_puts)

                if call_ratio > 0.6:
                    current_bias = "bullish"
                elif call_ratio < 0.4:
                    current_bias = "bearish"
                else:
                    current_bias = "neutral"

                if state.last_flow_bias != "neutral" and current_bias != "neutral":
                    if state.last_flow_bias == "bullish" and current_bias == "bearish":
                        self.add_alert(Alert(
                            id=self._generate_alert_id(),
                            symbol=symbol,
                            alert_type=AlertType.FLOW_FLIP,
                            severity=AlertSeverity.WARNING,
                            title=f"FLOW FLIPPED BEARISH",
                            message=f"Put volume now dominating. Calls: {total_calls:,}, Puts: {total_puts:,}",
                            price=spot
                        ))
                    elif state.last_flow_bias == "bearish" and current_bias == "bullish":
                        self.add_alert(Alert(
                            id=self._generate_alert_id(),
                            symbol=symbol,
                            alert_type=AlertType.FLOW_FLIP,
                            severity=AlertSeverity.WARNING,
                            title=f"FLOW FLIPPED BULLISH",
                            message=f"Call volume now dominating. Calls: {total_calls:,}, Puts: {total_puts:,}",
                            price=spot
                        ))

                state.last_flow_bias = current_bias

        # Update state
        state.last_price = spot
        state.last_check = datetime.now()

    async def run_check_cycle(self):
        """Run one check cycle for all monitored symbols."""
        if not self.gex_provider:
            logger.warning("[AlertService] No GEX provider configured")
            return

        for symbol in self.MONITORED_SYMBOLS:
            try:
                # Get cached GEX data (don't force refresh to avoid rate limits)
                gex_data = None
                if hasattr(self.gex_provider, 'cache') and symbol in self.gex_provider.cache:
                    cached = self.gex_provider.cache[symbol]
                    gex_data = {
                        "spot_price": cached.spot_price,
                        "levels": {
                            "support": cached.levels.get("support") if cached.levels else None,
                            "resistance": cached.levels.get("resistance") if cached.levels else None,
                            "magnet": cached.levels.get("magnet") if cached.levels else None,
                            "accelerator": cached.levels.get("accelerator") if cached.levels else None,
                            "zero_gamma": cached.levels.get("zero_gamma") if cached.levels else None,
                        },
                        "zones": cached.zones
                    }

                # Get flow data if available
                flow_data = None
                if self.flow_service and hasattr(self.flow_service, 'flow_cache'):
                    flow_data = self.flow_service.flow_cache.get(symbol)

                if gex_data:
                    await self.check_symbol(symbol, gex_data, flow_data)

            except Exception as e:
                logger.error(f"[AlertService] Error checking {symbol}: {e}")

        logger.debug(f"[AlertService] Checked {len(self.MONITORED_SYMBOLS)} symbols, {len(self.alerts)} active alerts")

    async def start(self):
        """Start the alert monitoring loop."""
        self.running = True
        logger.info(f"[AlertService] Started monitoring {len(self.MONITORED_SYMBOLS)} symbols")

        while self.running:
            try:
                await self.run_check_cycle()
            except Exception as e:
                logger.error(f"[AlertService] Error in check cycle: {e}")

            await asyncio.sleep(self.check_interval)

    def stop(self):
        """Stop the alert monitoring loop."""
        self.running = False
        logger.info("[AlertService] Stopped")


# Global instance
alert_service: Optional[AlertService] = None


def get_alert_service() -> Optional[AlertService]:
    return alert_service


def init_alert_service(gex_provider=None, flow_service=None) -> AlertService:
    global alert_service
    alert_service = AlertService(gex_provider=gex_provider, flow_service=flow_service)
    return alert_service
