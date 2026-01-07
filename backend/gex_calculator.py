"""
GEX (Gamma Exposure) Calculator

Core formulas:
- Call GEX = Gamma × Open Interest × 100 × Spot Price
- Put GEX = Gamma × Open Interest × 100 × Spot Price × -1
- Net GEX = Sum of Call GEX - Sum of Put GEX (puts subtract due to dealer hedge direction)

Dealer positioning:
- Positive GEX (yellow): Dealers are long gamma, will sell rallies and buy dips = STABILIZING
- Negative GEX (purple): Dealers are short gamma, will buy rallies and sell dips = AMPLIFYING
"""
from dataclasses import dataclass, field
from datetime import datetime, date
from typing import List, Dict, Optional, Tuple
from enum import Enum
import math
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo  # Python < 3.9

# Eastern timezone for market hours
ET = ZoneInfo("America/New_York")

from config import (
    MIN_OPEN_INTEREST, MIN_GEX_VALUE, MAX_ZONES,
    get_dte_weight, is_opex_week, get_next_opex,
    TradingContext, PRICE_PROXIMITY_PCT, ZONE_LABEL_RULES,
    VEX_DATA_QUALITY, get_0dte_gamma_multiplier, ZERO_DTE_CONFIG,
    get_proximity_status, IV_SKEW_CONFIG, interpret_skew
)


class NodeType(Enum):
    POSITIVE = "positive"  # Yellow - magnet/stabilizer
    NEGATIVE = "negative"  # Purple - accelerator/amplifier


class NodeRole(Enum):
    KING = "king"              # Highest absolute GEX - primary target
    GATEKEEPER = "gatekeeper"  # Guards the King - deflection zone
    SUPPORT = "support"        # Positive GEX below price
    RESISTANCE = "resistance"  # Positive GEX above price
    ACCELERATOR = "accelerator"  # Negative GEX - volatility zone


@dataclass
class OptionContract:
    """Single option contract data."""
    strike: float
    expiration: date
    option_type: str  # 'call' or 'put'
    open_interest: int
    gamma: float
    delta: float = 0.0
    vega: float = 0.0
    vanna: float = 0.0  # dDelta/dIV - for VEX calculation
    iv: float = 0.0     # Implied volatility (decimal, e.g., 0.20 = 20%)
    volume: int = 0
    bid: float = 0.0
    ask: float = 0.0


@dataclass
class StrikeGEX:
    """GEX, VEX, and DEX data aggregated at a single strike price."""
    strike: float
    call_gex: float = 0.0
    put_gex: float = 0.0
    net_gex: float = 0.0
    call_vex: float = 0.0
    put_vex: float = 0.0
    net_vex: float = 0.0
    call_dex: float = 0.0
    put_dex: float = 0.0
    net_dex: float = 0.0
    total_oi: int = 0
    call_volume: int = 0  # Daily call volume from Polygon
    put_volume: int = 0   # Daily put volume from Polygon
    expirations: Dict[str, float] = field(default_factory=dict)  # expiry -> GEX
    vex_expirations: Dict[str, float] = field(default_factory=dict)  # expiry -> VEX
    dex_expirations: Dict[str, float] = field(default_factory=dict)  # expiry -> DEX

    @property
    def gex_type(self) -> NodeType:
        return NodeType.POSITIVE if self.net_gex >= 0 else NodeType.NEGATIVE

    @property
    def vex_type(self) -> NodeType:
        return NodeType.POSITIVE if self.net_vex >= 0 else NodeType.NEGATIVE

    @property
    def dex_type(self) -> NodeType:
        return NodeType.POSITIVE if self.net_dex >= 0 else NodeType.NEGATIVE

    @property
    def abs_gex(self) -> float:
        return abs(self.net_gex)

    @property
    def abs_vex(self) -> float:
        return abs(self.net_vex)

    @property
    def abs_dex(self) -> float:
        return abs(self.net_dex)


@dataclass
class GEXZone:
    """A significant GEX zone for trading."""
    strike: float
    gex: float
    gex_formatted: str
    node_type: NodeType
    role: NodeRole
    strength: float  # 0-1, relative to max GEX
    dte_weighted_gex: float
    expirations: Dict[str, float]
    trading_context: str = "neutral"  # Executable label: absorption, breakout, etc.

    def to_dict(self) -> dict:
        return {
            "strike": self.strike,
            "gex": self.gex,
            "gex_formatted": self.gex_formatted,
            "type": self.node_type.value,
            "role": self.role.value,
            "strength": round(self.strength, 2),
            "dte_weighted_gex": self.dte_weighted_gex,
            "expirations": {k: round(v, 0) for k, v in self.expirations.items()},
            "trading_context": self.trading_context
        }


@dataclass
class GEXResult:
    """Complete GEX, VEX, and DEX analysis result."""
    symbol: str
    spot_price: float
    timestamp: datetime
    refresh_interval_sec: int
    opex_warning: bool
    opex_date: Optional[date]
    king_node: Optional[GEXZone]
    gatekeeper_node: Optional[GEXZone]
    zones: List[GEXZone]
    heatmap_strikes: List[float]
    heatmap_expirations: List[str]
    heatmap_data: List[List[float]]
    vex_heatmap_data: List[List[float]]  # VEX heatmap
    dex_heatmap_data: List[List[float]]  # DEX heatmap
    total_call_gex: float
    total_put_gex: float
    net_gex: float
    total_call_vex: float
    total_put_vex: float
    net_vex: float
    total_call_dex: float
    total_put_dex: float
    net_dex: float
    zero_gamma_level: Optional[float]
    # New fields
    zero_dte_status: dict = field(default_factory=dict)
    iv_skew: dict = field(default_factory=dict)
    king_proximity: dict = field(default_factory=dict)
    gatekeeper_proximity: dict = field(default_factory=dict)
    zero_gamma_proximity: dict = field(default_factory=dict)
    # Feature additions
    gex_flip_level: Optional[float] = None
    expected_move: dict = field(default_factory=dict)
    put_call_walls: dict = field(default_factory=dict)
    # Volume by strike from Polygon
    volume_by_strike: Dict[float, Dict[str, int]] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "spot_price": self.spot_price,
            "timestamp": self.timestamp.isoformat(),
            "refresh_interval_sec": self.refresh_interval_sec,
            "opex_warning": self.opex_warning,
            "opex_date": self.opex_date.isoformat() if self.opex_date else None,
            "king_node": self.king_node.to_dict() if self.king_node else None,
            "gatekeeper_node": self.gatekeeper_node.to_dict() if self.gatekeeper_node else None,
            "zones": [z.to_dict() for z in self.zones],
            "heatmap": {
                "strikes": [float(s) for s in self.heatmap_strikes],
                "expirations": self.heatmap_expirations,
                "data": [[int(round(v, 0)) for v in row] for row in self.heatmap_data]
            },
            "vex_heatmap": {
                "strikes": [float(s) for s in self.heatmap_strikes],
                "expirations": self.heatmap_expirations,
                "data": [[int(round(v, 0)) for v in row] for row in self.vex_heatmap_data]
            },
            "dex_heatmap": {
                "strikes": [float(s) for s in self.heatmap_strikes],
                "expirations": self.heatmap_expirations,
                "data": [[int(round(v, 0)) for v in row] for row in self.dex_heatmap_data]
            },
            "meta": {
                "total_call_gex": round(self.total_call_gex, 0),
                "total_put_gex": round(self.total_put_gex, 0),
                "net_gex": round(self.net_gex, 0),
                "total_call_vex": round(self.total_call_vex, 0),
                "total_put_vex": round(self.total_put_vex, 0),
                "net_vex": round(self.net_vex, 0),
                "total_call_dex": round(self.total_call_dex, 0),
                "total_put_dex": round(self.total_put_dex, 0),
                "net_dex": round(self.net_dex, 0),
                "zero_gamma_level": round(self.zero_gamma_level, 2) if self.zero_gamma_level else None,
                "filters_applied": {
                    "min_oi": MIN_OPEN_INTEREST,
                    "min_gex": MIN_GEX_VALUE,
                    "dte_decay": True
                }
            },
            # New trading intel fields
            "zero_dte": self.zero_dte_status,
            "iv_skew": self.iv_skew,
            "proximity": {
                "king": self.king_proximity,
                "gatekeeper": self.gatekeeper_proximity,
                "zero_gamma": self.zero_gamma_proximity,
            },
            # New features
            "gex_flip_level": round(self.gex_flip_level, 2) if self.gex_flip_level else None,
            "expected_move": self.expected_move,
            "put_call_walls": self.put_call_walls
        }

    def to_levels_dict(self) -> dict:
        """
        Compact format for NinjaTrader indicator.

        Includes all fields needed for proper chart overlay:
        - strike, type, polarity, strength, context label
        """
        return {
            "symbol": self.symbol,
            "spot": self.spot_price,
            "timestamp": self.timestamp.isoformat(),
            "timestamp_utc": self.timestamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "stale": False,  # Will be set by API based on age
            "opex_warning": self.opex_warning,
            "zero_gamma": self.zero_gamma_level,
            "net_gex_billions": round(self.net_gex / 1_000_000_000, 3),
            "levels": [
                {
                    "strike": z.strike,
                    "gex": round(z.gex / 1_000_000_000, 3),  # In billions
                    "type": z.role.value,  # king, gatekeeper, support, resistance, accelerator
                    "polarity": z.node_type.value,  # positive or negative
                    "strength": round(z.strength, 2),
                    "context": z.trading_context,  # magnet, absorption, acceleration, etc.
                }
                for z in self.zones[:10]  # Top 10 for NT
            ]
        }


def format_gex(value: float) -> str:
    """Format GEX value as human-readable string."""
    abs_val = abs(value)
    sign = "+" if value >= 0 else "-"

    if abs_val >= 1_000_000_000:
        return f"{sign}${abs_val / 1_000_000_000:.1f}B"
    elif abs_val >= 1_000_000:
        return f"{sign}${abs_val / 1_000_000:.1f}M"
    elif abs_val >= 1_000:
        return f"{sign}${abs_val / 1_000:.1f}K"
    else:
        return f"{sign}${abs_val:.0f}"


class GEXCalculator:
    """
    Calculates Gamma Exposure (GEX) from options chain data.
    """

    def __init__(self, min_oi: int = MIN_OPEN_INTEREST, min_gex: float = MIN_GEX_VALUE):
        self.min_oi = min_oi
        self.min_gex = min_gex

    def calculate_contract_gex(
        self,
        contract: OptionContract,
        spot_price: float,
        apply_dte_decay: bool = True,
        apply_0dte_multiplier: bool = True
    ) -> float:
        """
        Calculate GEX for a single option contract.

        Formula: GEX = Gamma × OI × 100 × Spot
        - Calls: positive (dealers buy stock when price rises)
        - Puts: negative (dealers sell stock when price rises)

        For 0DTE options, applies gamma explosion multiplier for ATM strikes.
        """
        if contract.open_interest < self.min_oi:
            return 0.0

        # Base GEX calculation
        gex = contract.gamma * contract.open_interest * 100 * spot_price

        # Puts have opposite hedge direction
        if contract.option_type == 'put':
            gex *= -1

        # Calculate DTE
        dte = (contract.expiration - date.today()).days

        # Apply 0DTE gamma explosion multiplier
        if apply_0dte_multiplier and dte == 0:
            # Calculate hours remaining until 4 PM ET market close
            now_et = datetime.now(ET)
            market_close_hour = 16  # 4 PM ET
            hours_remaining = max(0, market_close_hour - now_et.hour - now_et.minute / 60)

            # Calculate moneyness
            moneyness_pct = abs(contract.strike - spot_price) / spot_price if spot_price > 0 else 1

            # Get multiplier
            multiplier = get_0dte_gamma_multiplier(hours_remaining, moneyness_pct)
            gex *= multiplier

        # Apply DTE decay if enabled (but not for 0DTE - we use multiplier instead)
        if apply_dte_decay and dte > 0:
            weight = get_dte_weight(dte)
            gex *= weight

        return gex

    def calculate_contract_vex(
        self,
        contract: OptionContract,
        spot_price: float,
        apply_dte_decay: bool = True
    ) -> float:
        """
        Calculate VEX (Vanna Exposure) for a single option contract.

        Formula: VEX = Vanna × OI × 100 × Spot
        - Positive VEX: When IV rises, dealers need to buy (bullish pressure)
        - Negative VEX: When IV rises, dealers need to sell (bearish pressure)
        """
        if contract.open_interest < self.min_oi:
            return 0.0

        # Base VEX calculation
        vex = contract.vanna * contract.open_interest * 100 * spot_price

        # Puts have opposite effect
        if contract.option_type == 'put':
            vex *= -1

        # Apply DTE decay if enabled
        if apply_dte_decay:
            dte = (contract.expiration - date.today()).days
            weight = get_dte_weight(dte)
            vex *= weight

        return vex

    def calculate_contract_dex(
        self,
        contract: OptionContract,
        spot_price: float,
        apply_dte_decay: bool = True
    ) -> float:
        """
        Calculate DEX (Delta Exposure) for a single option contract.

        Formula: DEX = Delta × OI × 100 × Spot
        - Positive DEX: Dealers are long delta (need to sell on rallies)
        - Negative DEX: Dealers are short delta (need to buy on rallies)
        """
        if contract.open_interest < self.min_oi:
            return 0.0

        # Base DEX calculation
        dex = contract.delta * contract.open_interest * 100 * spot_price

        # Apply DTE decay if enabled
        if apply_dte_decay:
            dte = (contract.expiration - date.today()).days
            weight = get_dte_weight(dte)
            dex *= weight

        return dex

    def aggregate_by_strike(
        self,
        contracts: List[OptionContract],
        spot_price: float
    ) -> Dict[float, StrikeGEX]:
        """Aggregate GEX, VEX, and DEX by strike price."""
        strikes: Dict[float, StrikeGEX] = {}

        for contract in contracts:
            strike = contract.strike

            if strike not in strikes:
                strikes[strike] = StrikeGEX(strike=strike)

            gex = self.calculate_contract_gex(contract, spot_price)
            vex = self.calculate_contract_vex(contract, spot_price)
            dex = self.calculate_contract_dex(contract, spot_price)
            exp_key = contract.expiration.isoformat()

            if contract.option_type == 'call':
                strikes[strike].call_gex += gex
                strikes[strike].call_vex += vex
                strikes[strike].call_dex += dex
                strikes[strike].call_volume += contract.volume
            else:
                strikes[strike].put_gex += gex
                strikes[strike].put_vex += vex
                strikes[strike].put_dex += dex
                strikes[strike].put_volume += contract.volume

            strikes[strike].total_oi += contract.open_interest

            # Track GEX by expiration
            if exp_key not in strikes[strike].expirations:
                strikes[strike].expirations[exp_key] = 0
            strikes[strike].expirations[exp_key] += gex

            # Track VEX by expiration
            if exp_key not in strikes[strike].vex_expirations:
                strikes[strike].vex_expirations[exp_key] = 0
            strikes[strike].vex_expirations[exp_key] += vex

            # Track DEX by expiration
            if exp_key not in strikes[strike].dex_expirations:
                strikes[strike].dex_expirations[exp_key] = 0
            strikes[strike].dex_expirations[exp_key] += dex

        # Calculate net GEX, VEX, and DEX per strike
        for strike_gex in strikes.values():
            strike_gex.net_gex = strike_gex.call_gex + strike_gex.put_gex
            strike_gex.net_vex = strike_gex.call_vex + strike_gex.put_vex
            strike_gex.net_dex = strike_gex.call_dex + strike_gex.put_dex

        return strikes

    def classify_node_role(
        self,
        strike: float,
        gex: float,
        spot_price: float,
        is_king: bool = False,
        is_gatekeeper: bool = False
    ) -> NodeRole:
        """Classify a node's role based on its characteristics."""
        if is_king:
            return NodeRole.KING
        if is_gatekeeper:
            return NodeRole.GATEKEEPER

        node_type = NodeType.POSITIVE if gex >= 0 else NodeType.NEGATIVE

        if node_type == NodeType.NEGATIVE:
            return NodeRole.ACCELERATOR
        elif strike > spot_price:
            return NodeRole.RESISTANCE
        else:
            return NodeRole.SUPPORT

    def get_trading_context(
        self,
        strike: float,
        gex: float,
        spot_price: float,
        king_strike: Optional[float],
        strength: float
    ) -> str:
        """
        Determine trading context for a zone using DETERMINISTIC rules.

        Rules are locked in config.ZONE_LABEL_RULES for consistency:
        - MAGNET: strike == King AND strength >= 0.70
        - ABSORPTION: positive GEX AND within 0.35% of spot AND strength >= 0.40
        - ACCELERATION: negative GEX AND within 0.50% of spot
        - SUPPORT: positive GEX below spot
        - RESISTANCE: positive GEX above spot
        """
        is_positive = gex > 0
        is_above_spot = strike > spot_price
        proximity = abs(strike - spot_price) / spot_price if spot_price > 0 else 1

        # Get locked thresholds
        magnet_min_strength = ZONE_LABEL_RULES["magnet_min_strength"]
        absorption_proximity = ZONE_LABEL_RULES["absorption_proximity_pct"]
        absorption_min_strength = ZONE_LABEL_RULES["absorption_min_strength"]
        acceleration_proximity = ZONE_LABEL_RULES["acceleration_proximity_pct"]

        # Check if this is the King strike
        is_king = king_strike is not None and abs(strike - king_strike) < 0.01

        # Rule 1: MAGNET - King node with high strength
        if is_king and strength >= magnet_min_strength:
            return TradingContext.MAGNET

        # Rule 2: ACCELERATION - Negative GEX near spot (vol expansion zone)
        if not is_positive and proximity <= acceleration_proximity:
            return TradingContext.ACCELERATION

        # Rule 3: ABSORPTION - High positive GEX very near spot (expect fade)
        if is_positive and proximity <= absorption_proximity and strength >= absorption_min_strength:
            return TradingContext.ABSORPTION

        # Rule 4/5: SUPPORT/RESISTANCE - Positive GEX levels
        if is_positive:
            if is_above_spot:
                return TradingContext.RESISTANCE
            else:
                return TradingContext.SUPPORT

        # Default for negative GEX not near spot
        if not is_positive:
            return TradingContext.ACCELERATION

        return TradingContext.NEUTRAL

    def find_zero_gamma_level(self, strikes: Dict[float, StrikeGEX]) -> Optional[float]:
        """
        Find the price level where net gamma exposure crosses zero.
        This is often a key inflection point.
        """
        sorted_strikes = sorted(strikes.items(), key=lambda x: x[0])

        for i in range(len(sorted_strikes) - 1):
            s1, gex1 = sorted_strikes[i]
            s2, gex2 = sorted_strikes[i + 1]

            # Check for sign change
            if gex1.net_gex * gex2.net_gex < 0:
                # Linear interpolation
                total_range = abs(gex1.net_gex) + abs(gex2.net_gex)
                if total_range > 0:
                    ratio = abs(gex1.net_gex) / total_range
                    zero_level = s1 + (s2 - s1) * ratio
                    return zero_level

        return None

    def find_gex_flip_level(self, strikes: Dict[float, StrikeGEX], spot_price: float) -> Optional[float]:
        """
        Find the GEX Flip Level - where cumulative GEX crosses from negative to positive.

        This level represents where dealer positioning changes from amplifying
        (short gamma, chase moves) to stabilizing (long gamma, fade moves).

        Calculated by accumulating GEX from lowest strike upward until cumulative
        crosses from negative to positive.
        """
        sorted_strikes = sorted(strikes.items(), key=lambda x: x[0])

        if not sorted_strikes:
            return None

        cumulative = 0.0
        prev_cumulative = 0.0

        for i, (strike, strike_gex) in enumerate(sorted_strikes):
            prev_cumulative = cumulative
            cumulative += strike_gex.net_gex

            # Check for flip from negative to positive
            if prev_cumulative < 0 and cumulative >= 0:
                # Interpolate the exact level
                if strike_gex.net_gex != 0:
                    # How much of this strike's GEX was needed to flip
                    needed = abs(prev_cumulative)
                    ratio = needed / abs(strike_gex.net_gex)
                    # If we have previous strike, interpolate
                    if i > 0:
                        prev_strike = sorted_strikes[i-1][0]
                        flip_level = prev_strike + (strike - prev_strike) * ratio
                        return flip_level
                return strike

        # If no flip found, check if overall is positive (flip below all strikes)
        # or negative (flip above all strikes)
        if cumulative > 0 and sorted_strikes:
            return sorted_strikes[0][0]  # Flip is below lowest strike

        return None

    def calculate_expected_move(
        self,
        contracts: List[OptionContract],
        spot_price: float
    ) -> dict:
        """
        Calculate Expected Move range based on ATM implied volatility.

        Expected Move = Price × IV × √(DTE/365)

        Returns 1-day and 1-week expected move ranges.
        """
        if spot_price <= 0:
            return {"iv": 0, "daily": {"low": 0, "high": 0}, "weekly": {"low": 0, "high": 0}}

        # Find ATM IV from nearest expiration
        today = date.today()
        expirations = sorted(set(c.expiration for c in contracts))

        # Use first expiration with decent liquidity
        atm_iv = 0.0
        for exp in expirations:
            exp_contracts = [c for c in contracts if c.expiration == exp]

            # Find contracts near ATM (within 1%)
            atm_contracts = [
                c for c in exp_contracts
                if abs(c.strike - spot_price) / spot_price < 0.01 and c.iv and c.iv > 0
            ]

            if atm_contracts:
                # Average IV of ATM contracts
                atm_iv = sum(c.iv for c in atm_contracts) / len(atm_contracts)
                break

        # If no ATM IV found, try wider range
        if atm_iv == 0:
            for exp in expirations[:3]:
                exp_contracts = [c for c in contracts if c.expiration == exp]
                near_atm = [
                    c for c in exp_contracts
                    if abs(c.strike - spot_price) / spot_price < 0.05 and c.iv and c.iv > 0
                ]
                if near_atm:
                    atm_iv = sum(c.iv for c in near_atm) / len(near_atm)
                    break

        if atm_iv == 0:
            return {"iv": 0, "daily": {"low": 0, "high": 0}, "weekly": {"low": 0, "high": 0}}

        # Calculate expected moves
        # Daily: √(1/365) ≈ 0.0523
        daily_factor = math.sqrt(1 / 365)
        daily_move = spot_price * atm_iv * daily_factor

        # Weekly: √(7/365) ≈ 0.1385
        weekly_factor = math.sqrt(7 / 365)
        weekly_move = spot_price * atm_iv * weekly_factor

        return {
            "iv": round(atm_iv * 100, 2),  # As percentage
            "daily": {
                "low": round(spot_price - daily_move, 2),
                "high": round(spot_price + daily_move, 2),
                "move": round(daily_move, 2)
            },
            "weekly": {
                "low": round(spot_price - weekly_move, 2),
                "high": round(spot_price + weekly_move, 2),
                "move": round(weekly_move, 2)
            }
        }

    def build_put_call_walls(
        self,
        strikes: Dict[float, StrikeGEX],
        spot_price: float,
        num_strikes: int = 20
    ) -> dict:
        """
        Build Put/Call Wall data showing call GEX vs put GEX at each strike.

        Returns strikes centered around spot with separate call/put GEX values
        for visualization as a horizontal bar chart.
        """
        if not strikes:
            return {"strikes": [], "walls": []}

        # Sort strikes and center around spot
        sorted_strikes = sorted(strikes.keys())

        # Find closest strike to spot
        spot_idx = 0
        min_diff = float('inf')
        for i, s in enumerate(sorted_strikes):
            diff = abs(s - spot_price)
            if diff < min_diff:
                min_diff = diff
                spot_idx = i

        # Get strikes around spot
        half = num_strikes // 2
        start = max(0, spot_idx - half)
        end = min(len(sorted_strikes), spot_idx + half + 1)

        selected_strikes = sorted_strikes[start:end]

        walls = []
        for strike in selected_strikes:
            strike_data = strikes[strike]
            walls.append({
                "strike": strike,
                "call_gex": round(strike_data.call_gex, 0),
                "put_gex": round(strike_data.put_gex, 0),
                "net_gex": round(strike_data.net_gex, 0),
                "total_oi": strike_data.total_oi
            })

        return {
            "strikes": selected_strikes,
            "walls": walls,
            "spot_price": spot_price
        }

    def calculate_iv_skew(
        self,
        contracts: List[OptionContract],
        spot_price: float
    ) -> dict:
        """
        Calculate IV skew from the options chain.

        Skew = 25-delta Put IV / 25-delta Call IV
        - Skew > 1: Puts more expensive (fear)
        - Skew < 1: Calls more expensive (greed)
        """
        target_put_delta = IV_SKEW_CONFIG["put_delta"]
        target_call_delta = IV_SKEW_CONFIG["call_delta"]
        tolerance = IV_SKEW_CONFIG["delta_tolerance"]

        # Find nearest expiration for cleaner skew reading
        today = date.today()
        expirations = sorted(set(c.expiration for c in contracts))

        # Use first expiration that's at least 7 days out (avoid 0DTE noise)
        target_exp = None
        for exp in expirations:
            dte = (exp - today).days
            if 7 <= dte <= 45:  # Sweet spot for skew
                target_exp = exp
                break

        if target_exp is None and expirations:
            target_exp = expirations[0]

        if target_exp is None:
            return {"skew": 1.0, "regime": "unknown", "description": "No data", "put_iv": 0, "call_iv": 0}

        # Filter to target expiration
        exp_contracts = [c for c in contracts if c.expiration == target_exp]

        # Find 25-delta put (delta around -0.25)
        puts = [c for c in exp_contracts if c.option_type == 'put' and c.delta != 0]
        put_25d = None
        put_25d_diff = float('inf')

        for p in puts:
            diff = abs(p.delta - target_put_delta)
            if diff < put_25d_diff and diff <= tolerance:
                put_25d = p
                put_25d_diff = diff

        # Find 25-delta call (delta around 0.25)
        calls = [c for c in exp_contracts if c.option_type == 'call' and c.delta != 0]
        call_25d = None
        call_25d_diff = float('inf')

        for c in calls:
            diff = abs(c.delta - target_call_delta)
            if diff < call_25d_diff and diff <= tolerance:
                call_25d = c
                call_25d_diff = diff

        # Calculate skew
        if put_25d is None or call_25d is None:
            # Fallback: use ATM IV comparison
            atm_strikes = [c for c in exp_contracts if abs(c.strike - spot_price) / spot_price < 0.02]
            atm_puts = [c for c in atm_strikes if c.option_type == 'put']
            atm_calls = [c for c in atm_strikes if c.option_type == 'call']

            if atm_puts and atm_calls:
                # Use average IV near ATM
                put_iv = sum(getattr(c, 'iv', 0) or 0 for c in atm_puts) / len(atm_puts) if atm_puts else 0
                call_iv = sum(getattr(c, 'iv', 0) or 0 for c in atm_calls) / len(atm_calls) if atm_calls else 0
            else:
                return {"skew": 1.0, "regime": "unknown", "description": "Insufficient data", "put_iv": 0, "call_iv": 0}
        else:
            # Get IV directly from contract (comes as decimal from MarketData.app, e.g. 0.20 = 20%)
            put_iv = getattr(put_25d, 'iv', 0) or 0 if put_25d else 0
            call_iv = getattr(call_25d, 'iv', 0) or 0 if call_25d else 0

        # Handle missing IV with reasonable default
        if put_iv == 0 and put_25d:
            put_iv = 0.20  # Default 20% IV
        if call_iv == 0 and call_25d:
            call_iv = 0.20

        # Calculate skew (ratio of put IV to call IV)
        if call_iv > 0:
            skew = put_iv / call_iv
        else:
            skew = 1.0

        # Interpret skew
        result = interpret_skew(skew)
        # IV comes as decimal (0.20), convert to percentage for display (20.0)
        # MarketData.app returns IV as decimal, so always multiply by 100
        result["put_iv"] = round(put_iv * 100, 1)
        result["call_iv"] = round(call_iv * 100, 1)
        result["expiration"] = target_exp.isoformat()

        return result

    def detect_0dte_status(self, contracts: List[OptionContract]) -> dict:
        """
        Detect if 0DTE options are present and their impact.
        """
        today = date.today()
        now = datetime.now()

        # Find 0DTE contracts
        zero_dte_contracts = [c for c in contracts if c.expiration == today]

        if not zero_dte_contracts:
            return {
                "active": False,
                "contract_count": 0,
                "total_oi": 0,
                "hours_remaining": 0,
                "gamma_multiplier": 1.0,
                "warning": None
            }

        # Calculate hours remaining
        market_close_hour = 16
        hours_remaining = max(0, market_close_hour - now.hour - now.minute / 60)

        # Total OI in 0DTE
        total_oi = sum(c.open_interest for c in zero_dte_contracts)

        # Get current multiplier (for ATM)
        multiplier = get_0dte_gamma_multiplier(hours_remaining, 0)

        # Generate warning
        if hours_remaining <= 1:
            warning = "CRITICAL: 0DTE gamma explosion imminent - levels may shift rapidly"
        elif hours_remaining <= 2:
            warning = "HIGH: 0DTE gamma elevated - expect increased volatility at key levels"
        elif hours_remaining <= ZERO_DTE_CONFIG["warning_threshold_hours"]:
            warning = "0DTE options active - gamma effects amplified"
        else:
            warning = None

        return {
            "active": True,
            "contract_count": len(zero_dte_contracts),
            "total_oi": total_oi,
            "hours_remaining": round(hours_remaining, 2),
            "gamma_multiplier": round(multiplier, 1),
            "warning": warning
        }

    def calculate(
        self,
        symbol: str,
        spot_price: float,
        contracts: List[OptionContract],
        refresh_interval: int = 300
    ) -> GEXResult:
        """
        Full GEX calculation and analysis.

        Returns complete GEX result with zones, heatmap, and metadata.
        """
        # Aggregate by strike
        strikes = self.aggregate_by_strike(contracts, spot_price)

        # Filter by minimum GEX
        significant_strikes = {
            k: v for k, v in strikes.items()
            if abs(v.net_gex) >= self.min_gex
        }

        # Sort by absolute GEX
        sorted_strikes = sorted(
            significant_strikes.values(),
            key=lambda x: x.abs_gex,
            reverse=True
        )

        # Identify King and Gatekeeper
        king_strike = sorted_strikes[0] if sorted_strikes else None
        gatekeeper_strike = None

        if king_strike and len(sorted_strikes) > 1:
            # Find gatekeeper - second largest that's near the king
            for s in sorted_strikes[1:]:
                if s.gex_type != king_strike.gex_type:  # Different type
                    gatekeeper_strike = s
                    break

        # Calculate max GEX for strength normalization
        max_gex = king_strike.abs_gex if king_strike else 1

        # Build zones
        zones: List[GEXZone] = []
        king_strike_price = king_strike.strike if king_strike else None

        for strike_gex in sorted_strikes[:MAX_ZONES]:
            is_king = strike_gex == king_strike
            is_gatekeeper = strike_gex == gatekeeper_strike
            strength = strike_gex.abs_gex / max_gex if max_gex > 0 else 0

            # Get trading context (executable label)
            trading_ctx = self.get_trading_context(
                strike=strike_gex.strike,
                gex=strike_gex.net_gex,
                spot_price=spot_price,
                king_strike=king_strike_price,
                strength=strength
            )

            zone = GEXZone(
                strike=strike_gex.strike,
                gex=strike_gex.net_gex,
                gex_formatted=format_gex(strike_gex.net_gex),
                node_type=strike_gex.gex_type,
                role=self.classify_node_role(
                    strike_gex.strike,
                    strike_gex.net_gex,
                    spot_price,
                    is_king,
                    is_gatekeeper
                ),
                strength=strength,
                dte_weighted_gex=strike_gex.net_gex,  # Already weighted
                expirations=strike_gex.expirations,
                trading_context=trading_ctx
            )
            zones.append(zone)

        # Sort zones by strike for display
        zones.sort(key=lambda z: z.strike, reverse=True)

        # Build heatmap data (GEX and VEX)
        # Filter strikes to only those within reasonable range of spot (±30%)
        if spot_price > 0:
            min_strike = spot_price * 0.70
            max_strike = spot_price * 1.30
            filtered_strikes = {k: v for k, v in strikes.items() if min_strike <= k <= max_strike}
        else:
            # Fallback if spot price is invalid
            filtered_strikes = strikes

        # If filtering removed everything, use unfiltered
        if not filtered_strikes:
            filtered_strikes = strikes

        all_strikes_sorted = sorted(filtered_strikes.keys(), reverse=True)
        all_expirations = sorted(set(
            exp for s in filtered_strikes.values() for exp in s.expirations.keys()
        ))

        # Center strikes around SPOT PRICE for better heatmap display
        # This ensures current price is always visible in the grid
        HEATMAP_ROWS = 60  # More rows to show full range like Skylit

        # Find spot price index in sorted strikes
        spot_idx = 0
        for i, s in enumerate(all_strikes_sorted):
            if s <= spot_price:
                spot_idx = i
                break

        # Center around spot: show HEATMAP_ROWS/2 above and HEATMAP_ROWS/2 below spot
        half_rows = HEATMAP_ROWS // 2
        start_idx = max(0, spot_idx - half_rows)
        end_idx = min(len(all_strikes_sorted), start_idx + HEATMAP_ROWS)

        # Adjust start if we're near the end
        if end_idx - start_idx < HEATMAP_ROWS:
            start_idx = max(0, end_idx - HEATMAP_ROWS)

        all_strikes = all_strikes_sorted[start_idx:end_idx]

        heatmap_data = []
        vex_heatmap_data = []
        dex_heatmap_data = []
        for strike in all_strikes:  # Already limited and centered
            gex_row = []
            vex_row = []
            dex_row = []
            for exp in all_expirations[:8]:  # Limit to 8 expirations (can increase with Tradier)
                gex_val = filtered_strikes[strike].expirations.get(exp, 0)
                vex_val = filtered_strikes[strike].vex_expirations.get(exp, 0)
                dex_val = filtered_strikes[strike].dex_expirations.get(exp, 0)
                gex_row.append(gex_val)
                vex_row.append(vex_val)
                dex_row.append(dex_val)
            heatmap_data.append(gex_row)
            vex_heatmap_data.append(vex_row)
            dex_heatmap_data.append(dex_row)

        # Calculate GEX totals
        total_call_gex = sum(s.call_gex for s in strikes.values())
        total_put_gex = sum(s.put_gex for s in strikes.values())
        net_gex = total_call_gex + total_put_gex

        # Calculate VEX totals
        total_call_vex = sum(s.call_vex for s in strikes.values())
        total_put_vex = sum(s.put_vex for s in strikes.values())
        net_vex = total_call_vex + total_put_vex

        # Calculate DEX totals
        total_call_dex = sum(s.call_dex for s in strikes.values())
        total_put_dex = sum(s.put_dex for s in strikes.values())
        net_dex = total_call_dex + total_put_dex

        # Find zero gamma level
        zero_gamma = self.find_zero_gamma_level(strikes)

        # OPEX detection
        opex_warning = is_opex_week()
        next_opex = get_next_opex()

        # Find king and gatekeeper zones
        king_zone = next((z for z in zones if z.role == NodeRole.KING), None)
        gatekeeper_zone = next((z for z in zones if z.role == NodeRole.GATEKEEPER), None)

        # NEW: Calculate 0DTE status
        zero_dte_status = self.detect_0dte_status(contracts)

        # NEW: Calculate IV skew
        iv_skew = self.calculate_iv_skew(contracts, spot_price)

        # NEW: Calculate proximity alerts
        king_proximity = {}
        if king_zone:
            king_proximity = get_proximity_status(spot_price, king_zone.strike)
            king_proximity["strike"] = king_zone.strike

        gatekeeper_proximity = {}
        if gatekeeper_zone:
            gatekeeper_proximity = get_proximity_status(spot_price, gatekeeper_zone.strike)
            gatekeeper_proximity["strike"] = gatekeeper_zone.strike

        zero_gamma_proximity = {}
        if zero_gamma:
            zero_gamma_proximity = get_proximity_status(spot_price, zero_gamma)
            zero_gamma_proximity["level"] = round(zero_gamma, 2)

        # NEW FEATURES: GEX Flip, Expected Move, Put/Call Walls
        gex_flip = self.find_gex_flip_level(strikes, spot_price)
        expected_move = self.calculate_expected_move(contracts, spot_price)
        put_call_walls = self.build_put_call_walls(strikes, spot_price)

        # Build volume by strike dict from aggregated data
        volume_by_strike = {
            strike: {"call_volume": s.call_volume, "put_volume": s.put_volume}
            for strike, s in strikes.items()
        }

        return GEXResult(
            symbol=symbol,
            spot_price=spot_price,
            timestamp=datetime.now(),
            refresh_interval_sec=refresh_interval,
            opex_warning=opex_warning,
            opex_date=next_opex.date() if opex_warning else None,
            king_node=king_zone,
            gatekeeper_node=gatekeeper_zone,
            zones=zones,
            heatmap_strikes=all_strikes,
            heatmap_expirations=all_expirations[:8],
            heatmap_data=heatmap_data,
            vex_heatmap_data=vex_heatmap_data,
            dex_heatmap_data=dex_heatmap_data,
            total_call_gex=total_call_gex,
            total_put_gex=total_put_gex,
            net_gex=net_gex,
            total_call_vex=total_call_vex,
            total_put_vex=total_put_vex,
            net_vex=net_vex,
            total_call_dex=total_call_dex,
            total_put_dex=total_put_dex,
            net_dex=net_dex,
            zero_gamma_level=zero_gamma,
            # New fields
            zero_dte_status=zero_dte_status,
            iv_skew=iv_skew,
            king_proximity=king_proximity,
            gatekeeper_proximity=gatekeeper_proximity,
            zero_gamma_proximity=zero_gamma_proximity,
            # New features
            gex_flip_level=gex_flip,
            expected_move=expected_move,
            put_call_walls=put_call_walls,
            volume_by_strike=volume_by_strike,
        )
