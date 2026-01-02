"""
Massive (Polygon.io) Options Flow Client

Real-time options trade data for flow analysis.
Detects sweeps, blocks, and calculates pressure at each strike.
"""
import os
import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
import httpx

# API Configuration
MASSIVE_API_KEY = os.environ.get("MASSIVE_API_KEY", "yT5IQUT53OnuEE_ian4YbKQtu78rlDVX")
MASSIVE_BASE_URL = "https://api.polygon.io"

# Trade condition codes (OPRA)
CONDITION_CODES = {
    1: "regular",
    2: "average_price",
    3: "cash",
    4: "next_day",
    5: "opening",
    6: "intraday_detail",
    7: "distribution",
    8: "split",
    9: "reserved",
    10: "reserved",
    11: "reserved",
    12: "reserved",
    13: "reserved",
    14: "reserved",
    15: "cancel",
    16: "reserved",
    17: "sold_last",
    18: "reserved",
    19: "stopped",
    20: "out_of_sequence",
    21: "reserved",
    22: "reserved",
    23: "reserved",
    24: "reserved",
    25: "reserved",
    26: "reserved",
    27: "reserved",
    28: "reserved",
    29: "reserved",
    30: "reserved",
    31: "reserved",
    32: "intermarket_sweep",  # SWEEP - important for flow!
    33: "reserved",
    209: "single_leg_auction_non_iso",
    219: "single_leg_floor_trade",
}

# Exchange codes
EXCHANGES = {
    300: "NYSE_AMEX",
    301: "BOX",
    302: "CBOE",
    303: "CBOE2",
    304: "ISE",
    305: "PHLX",
    306: "NASDAQ_OM",
    307: "ARCA",
    308: "BATS",
    309: "C2",
    310: "EDGX",
    311: "ISE_GEMINI",
    312: "NASDAQ_BX",
    313: "MIAX",
    314: "MIAX_PEARL",
    315: "MIAX_EMERALD",
    316: "MEMX",
}


@dataclass
class OptionTrade:
    """Single options trade from OPRA feed."""
    symbol: str  # Option symbol (O:SPY251231C00600000)
    underlying: str  # Underlying (SPY)
    strike: float
    expiration: str  # YYMMDD
    option_type: str  # C or P
    price: float
    size: int
    premium: float  # price * size * 100
    timestamp: datetime
    exchange: str
    conditions: List[int]
    is_sweep: bool = False
    is_block: bool = False
    sentiment: str = "neutral"  # bullish, bearish, neutral


@dataclass
class StrikePressure:
    """Aggregated pressure at a single strike."""
    strike: float
    call_volume: int = 0
    put_volume: int = 0
    call_premium: float = 0.0
    put_premium: float = 0.0
    call_trades: int = 0
    put_trades: int = 0
    sweeps: int = 0
    blocks: int = 0
    net_premium: float = 0.0  # positive = bullish, negative = bearish
    pressure_pct: float = 0.0  # -100 to +100 (bearish to bullish)

    def calculate_pressure(self):
        """Calculate net pressure percentage."""
        total = self.call_premium + self.put_premium
        if total == 0:
            self.pressure_pct = 0
        else:
            # Positive = bullish (more call premium), Negative = bearish (more put premium)
            self.net_premium = self.call_premium - self.put_premium
            self.pressure_pct = (self.net_premium / total) * 100


@dataclass
class FlowSummary:
    """Overall flow summary for a symbol."""
    symbol: str
    spot_price: float
    timestamp: datetime
    total_call_volume: int = 0
    total_put_volume: int = 0
    total_call_premium: float = 0.0
    total_put_premium: float = 0.0
    net_premium: float = 0.0
    pressure_pct: float = 0.0
    sentiment: str = "neutral"
    sweeps_bullish: int = 0
    sweeps_bearish: int = 0
    blocks_bullish: int = 0
    blocks_bearish: int = 0
    strike_pressure: Dict[float, StrikePressure] = field(default_factory=dict)
    recent_trades: List[OptionTrade] = field(default_factory=list)

    def calculate_overall(self):
        """Calculate overall sentiment."""
        total = self.total_call_premium + self.total_put_premium
        if total == 0:
            self.pressure_pct = 0
            self.sentiment = "neutral"
        else:
            self.net_premium = self.total_call_premium - self.total_put_premium
            self.pressure_pct = (self.net_premium / total) * 100

            if self.pressure_pct > 20:
                self.sentiment = "bullish"
            elif self.pressure_pct < -20:
                self.sentiment = "bearish"
            else:
                self.sentiment = "neutral"

    def to_dict(self) -> dict:
        """Convert to JSON-serializable dict."""
        return {
            "symbol": self.symbol,
            "spot_price": self.spot_price,
            "timestamp": self.timestamp.isoformat(),
            "total_call_volume": self.total_call_volume,
            "total_put_volume": self.total_put_volume,
            "total_call_premium": round(self.total_call_premium, 2),
            "total_put_premium": round(self.total_put_premium, 2),
            "net_premium": round(self.net_premium, 2),
            "pressure_pct": round(self.pressure_pct, 1),
            "sentiment": self.sentiment,
            "sweeps": {
                "bullish": self.sweeps_bullish,
                "bearish": self.sweeps_bearish
            },
            "blocks": {
                "bullish": self.blocks_bullish,
                "bearish": self.blocks_bearish
            },
            "strike_pressure": {
                str(k): {
                    "strike": v.strike,
                    "call_volume": v.call_volume,
                    "put_volume": v.put_volume,
                    "call_premium": round(v.call_premium, 2),
                    "put_premium": round(v.put_premium, 2),
                    "net_premium": round(v.net_premium, 2),
                    "pressure_pct": round(v.pressure_pct, 1),
                    "sweeps": v.sweeps,
                    "blocks": v.blocks
                }
                for k, v in sorted(self.strike_pressure.items(), reverse=True)
            },
            "recent_trades": [
                {
                    "strike": t.strike,
                    "type": t.option_type,
                    "price": t.price,
                    "size": t.size,
                    "premium": round(t.premium, 2),
                    "is_sweep": t.is_sweep,
                    "is_block": t.is_block,
                    "sentiment": t.sentiment,
                    "time": t.timestamp.strftime("%H:%M:%S")
                }
                for t in self.recent_trades[-20:]  # Last 20 trades
            ]
        }


class MassiveClient:
    """Client for Massive/Polygon options flow data."""

    def __init__(self, api_key: str = None):
        self.api_key = api_key or MASSIVE_API_KEY
        self.base_url = MASSIVE_BASE_URL
        self._client: Optional[httpx.AsyncClient] = None

        # Cache for flow data
        self._flow_cache: Dict[str, FlowSummary] = {}
        self._cache_time: Dict[str, datetime] = {}
        self._cache_ttl = 30  # seconds

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    async def close(self):
        """Close HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    def _parse_option_symbol(self, symbol: str) -> Tuple[str, str, float, str]:
        """
        Parse option symbol like O:SPY251231C00600000
        Returns: (underlying, expiration, strike, type)
        """
        # Remove O: prefix
        if symbol.startswith("O:"):
            symbol = symbol[2:]

        # Find where the underlying ends (first digit after letters)
        i = 0
        while i < len(symbol) and not symbol[i].isdigit():
            i += 1

        underlying = symbol[:i]
        rest = symbol[i:]

        # Next 6 chars are expiration (YYMMDD)
        expiration = rest[:6]

        # Next char is type (C or P)
        option_type = rest[6]

        # Rest is strike (8 digits, implied 3 decimal places)
        strike_str = rest[7:]
        strike = int(strike_str) / 1000

        return underlying, expiration, strike, option_type

    async def get_options_snapshot(
        self,
        symbol: str,
        limit: int = 250,
        min_strike: float = None,
        max_strike: float = None
    ) -> List[dict]:
        """
        Fetch options snapshot with volume, OI, and last trade data.

        Args:
            symbol: Underlying symbol (e.g., SPY)
            limit: Max contracts to return
            min_strike: Minimum strike price filter
            max_strike: Maximum strike price filter
        """
        client = await self._get_client()
        symbol = symbol.upper()

        # Index symbols need I: prefix for options API
        index_symbols = {"SPX", "NDX", "RUT", "VIX", "DJX", "OEX"}
        api_symbol = f"I:{symbol}" if symbol in index_symbols else symbol

        params = {
            "apiKey": self.api_key,
            "limit": limit
        }

        # Add strike filters if provided (critical for getting both calls AND puts)
        if min_strike is not None:
            params["strike_price.gte"] = min_strike
        if max_strike is not None:
            params["strike_price.lte"] = max_strike

        url = f"{self.base_url}/v3/snapshot/options/{api_symbol}"

        results = []
        try:
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()

            if data.get("status") != "OK":
                print(f"[Massive] API error: {data}")
                return results

            results = data.get("results", [])
            print(f"[Massive] Got {len(results)} options for {symbol}")

        except httpx.HTTPError as e:
            print(f"[Massive] HTTP error: {e}")
        except Exception as e:
            print(f"[Massive] Error: {e}")

        return results

    async def get_flow_summary(
        self,
        symbol: str,
        spot_price: float = 0,
        strike_range: int = 20,
        use_cache: bool = True
    ) -> FlowSummary:
        """
        Get aggregated flow summary for a symbol using snapshot data.

        Args:
            symbol: Underlying symbol
            spot_price: Current price (for context)
            strike_range: How many strikes above/below spot to include
            use_cache: Whether to use cached data
        """
        symbol = symbol.upper()

        # Check cache
        if use_cache and symbol in self._flow_cache:
            cache_age = (datetime.now() - self._cache_time.get(symbol, datetime.min)).total_seconds()
            if cache_age < self._cache_ttl:
                return self._flow_cache[symbol]

        now = datetime.utcnow()

        # Calculate strike range for API filtering
        min_strike = spot_price - strike_range if spot_price else None
        max_strike = spot_price + strike_range if spot_price else None

        # Fetch options snapshot with strike filter (to get both calls AND puts)
        options = await self.get_options_snapshot(
            symbol=symbol,
            limit=250,
            min_strike=min_strike,
            max_strike=max_strike
        )

        # Build summary
        summary = FlowSummary(
            symbol=symbol,
            spot_price=spot_price,
            timestamp=now
        )

        for opt in options:
            try:
                details = opt.get("details", {})
                day = opt.get("day", {})
                last_trade = opt.get("last_trade", {})

                contract_type = details.get("contract_type", "").lower()
                strike = details.get("strike_price", 0)

                # Strike filtering is now done in the API call

                volume = day.get("volume", 0) or 0
                open_interest = opt.get("open_interest", 0) or 0

                # Get last trade info
                trade_price = last_trade.get("price", 0) or 0
                trade_size = last_trade.get("size", 0) or 0

                # Estimate premium from volume
                # Use midpoint if available, else last trade price
                quote = opt.get("last_quote", {})
                mid_price = quote.get("midpoint", 0) or trade_price

                if mid_price > 0 and volume > 0:
                    premium = mid_price * volume * 100
                else:
                    premium = 0

                # Update totals
                if contract_type == "call":
                    summary.total_call_volume += volume
                    summary.total_call_premium += premium
                elif contract_type == "put":
                    summary.total_put_volume += volume
                    summary.total_put_premium += premium

                # Update strike pressure
                if strike not in summary.strike_pressure:
                    summary.strike_pressure[strike] = StrikePressure(strike=strike)

                sp = summary.strike_pressure[strike]
                if contract_type == "call":
                    sp.call_volume += volume
                    sp.call_premium += premium
                    sp.call_trades += 1 if volume > 0 else 0
                elif contract_type == "put":
                    sp.put_volume += volume
                    sp.put_premium += premium
                    sp.put_trades += 1 if volume > 0 else 0

                # Track high volume as potential blocks
                if volume >= 1000:
                    sp.blocks += 1
                    if contract_type == "call":
                        summary.blocks_bullish += 1
                    else:
                        summary.blocks_bearish += 1

            except Exception as e:
                print(f"[Massive] Error processing option: {e}")
                continue

        # Calculate pressures
        for sp in summary.strike_pressure.values():
            sp.calculate_pressure()

        summary.calculate_overall()

        # Cache result
        self._flow_cache[symbol] = summary
        self._cache_time[symbol] = datetime.now()

        return summary

    async def get_strike_pressure_bars(
        self,
        symbol: str,
        spot_price: float,
        strike_range: int = 10
    ) -> List[dict]:
        """
        Get pressure bars for strikes around spot price.

        Returns list of {strike, pressure_pct, call_premium, put_premium}
        for easy visualization.
        """
        summary = await self.get_flow_summary(symbol, spot_price)

        # Filter to strikes around spot
        min_strike = spot_price - strike_range
        max_strike = spot_price + strike_range

        bars = []
        for strike in sorted(summary.strike_pressure.keys(), reverse=True):
            if min_strike <= strike <= max_strike:
                sp = summary.strike_pressure[strike]
                bars.append({
                    "strike": strike,
                    "pressure_pct": round(sp.pressure_pct, 1),
                    "call_premium": round(sp.call_premium, 2),
                    "put_premium": round(sp.put_premium, 2),
                    "net_premium": round(sp.net_premium, 2),
                    "total_volume": sp.call_volume + sp.put_volume,
                    "sweeps": sp.sweeps,
                    "blocks": sp.blocks,
                    "sentiment": "bullish" if sp.pressure_pct > 20 else "bearish" if sp.pressure_pct < -20 else "neutral"
                })

        return bars


# Singleton instance
_massive_client: Optional[MassiveClient] = None


def get_massive_client() -> MassiveClient:
    """Get or create Massive client singleton."""
    global _massive_client
    if _massive_client is None:
        _massive_client = MassiveClient()
    return _massive_client


# Quick test
if __name__ == "__main__":
    async def test():
        client = get_massive_client()
        print("Testing Massive API...")

        # Test flow summary
        summary = await client.get_flow_summary("SPY", spot_price=596.0, minutes=60)
        print(f"\nSPY Flow Summary:")
        print(f"  Call Volume: {summary.total_call_volume:,}")
        print(f"  Put Volume: {summary.total_put_volume:,}")
        print(f"  Call Premium: ${summary.total_call_premium:,.0f}")
        print(f"  Put Premium: ${summary.total_put_premium:,.0f}")
        print(f"  Net Premium: ${summary.net_premium:,.0f}")
        print(f"  Pressure: {summary.pressure_pct:.1f}%")
        print(f"  Sentiment: {summary.sentiment}")
        print(f"  Sweeps: {summary.sweeps_bullish} bullish, {summary.sweeps_bearish} bearish")

        await client.close()

    asyncio.run(test())
