"""
Unusual Whales Order Flow Client

Real-time options flow data from Unusual Whales API.
Provides context for GEX levels - are dealers actually hedging?

API Docs: https://api.unusualwhales.com/docs
"""
import os
import asyncio
import aiohttp
from datetime import datetime, date, timedelta
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum


class FlowSide(Enum):
    """Trade side (at bid = selling, at ask = buying)."""
    ASK = "ask"      # Bought at ask (aggressive buy)
    BID = "bid"      # Sold at bid (aggressive sell)
    MID = "mid"      # Between bid/ask
    UNKNOWN = "unknown"


class FlowSentiment(Enum):
    """Interpreted sentiment of the flow."""
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


@dataclass
class OptionsFlow:
    """Single options flow trade."""
    ticker: str
    strike: float
    expiration: date
    option_type: str  # 'call' or 'put'
    premium: float    # Total premium in dollars
    size: int         # Number of contracts
    side: FlowSide    # At bid, ask, or mid
    spot_price: float # Underlying price at time of trade
    iv: float         # Implied volatility
    timestamp: datetime
    is_sweep: bool = False    # Aggressive multi-exchange sweep
    is_block: bool = False    # Large block trade
    is_unusual: bool = False  # Flagged as unusual activity
    open_interest: int = 0
    volume: int = 0

    @property
    def sentiment(self) -> FlowSentiment:
        """Determine sentiment from flow characteristics."""
        # Calls at ask = bullish, Calls at bid = bearish
        # Puts at ask = bearish, Puts at bid = bullish
        if self.option_type == 'call':
            if self.side == FlowSide.ASK:
                return FlowSentiment.BULLISH
            elif self.side == FlowSide.BID:
                return FlowSentiment.BEARISH
        else:  # put
            if self.side == FlowSide.ASK:
                return FlowSentiment.BEARISH
            elif self.side == FlowSide.BID:
                return FlowSentiment.BULLISH
        return FlowSentiment.NEUTRAL

    @property
    def is_large(self) -> bool:
        """Check if this is a large trade (>$100K premium)."""
        return self.premium >= 100_000

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "strike": self.strike,
            "expiration": self.expiration.isoformat(),
            "type": self.option_type,
            "premium": self.premium,
            "size": self.size,
            "side": self.side.value,
            "sentiment": self.sentiment.value,
            "spot": self.spot_price,
            "iv": round(self.iv * 100, 1),
            "timestamp": self.timestamp.isoformat(),
            "is_sweep": self.is_sweep,
            "is_block": self.is_block,
            "is_unusual": self.is_unusual,
            "is_large": self.is_large,
        }


@dataclass
class FlowSummary:
    """Summary of flow activity for a symbol."""
    ticker: str
    timestamp: datetime
    total_premium: float = 0
    call_premium: float = 0
    put_premium: float = 0
    bullish_premium: float = 0
    bearish_premium: float = 0
    sweep_count: int = 0
    block_count: int = 0
    unusual_count: int = 0
    large_trades: List[OptionsFlow] = field(default_factory=list)
    flow_by_strike: Dict[float, Dict] = field(default_factory=dict)

    @property
    def put_call_ratio(self) -> float:
        """Premium-weighted put/call ratio."""
        if self.call_premium == 0:
            return float('inf') if self.put_premium > 0 else 1.0
        return self.put_premium / self.call_premium

    @property
    def net_sentiment(self) -> FlowSentiment:
        """Overall sentiment from premium flow."""
        diff = self.bullish_premium - self.bearish_premium
        total = self.bullish_premium + self.bearish_premium
        if total == 0:
            return FlowSentiment.NEUTRAL

        ratio = diff / total
        if ratio > 0.2:
            return FlowSentiment.BULLISH
        elif ratio < -0.2:
            return FlowSentiment.BEARISH
        return FlowSentiment.NEUTRAL

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "timestamp": self.timestamp.isoformat(),
            "total_premium": self.total_premium,
            "call_premium": self.call_premium,
            "put_premium": self.put_premium,
            "bullish_premium": self.bullish_premium,
            "bearish_premium": self.bearish_premium,
            "put_call_ratio": round(self.put_call_ratio, 2),
            "net_sentiment": self.net_sentiment.value,
            "sweep_count": self.sweep_count,
            "block_count": self.block_count,
            "unusual_count": self.unusual_count,
            "large_trade_count": len(self.large_trades),
            "large_trades": [t.to_dict() for t in self.large_trades[:10]],  # Top 10
            "flow_by_strike": self.flow_by_strike,
        }


class UnusualWhalesClient:
    """
    Client for Unusual Whales API.
    Provides real-time options flow data.
    """

    BASE_URL = "https://api.unusualwhales.com"

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json"
        }
        print("[OK] Unusual Whales client initialized")

    async def _request(self, endpoint: str, params: dict = None) -> dict:
        """Make an async API request."""
        url = f"{self.BASE_URL}{endpoint}"

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=self.headers, params=params) as response:
                if response.status == 200:
                    return await response.json()
                elif response.status == 401:
                    raise Exception("Invalid API key")
                elif response.status == 429:
                    raise Exception("Rate limit exceeded")
                else:
                    text = await response.text()
                    raise Exception(f"API error {response.status}: {text}")

    async def get_flow_alerts(self, limit: int = 50) -> List[OptionsFlow]:
        """
        Get recent flow alerts across all tickers.
        These are trades flagged as significant by UW's algorithms.
        """
        try:
            data = await self._request("/api/option-trades/flow-alerts", {"limit": limit})
            flows = []

            for item in data.get("data", []):
                try:
                    flow = self._parse_flow(item)
                    if flow:
                        flows.append(flow)
                except Exception as e:
                    continue

            return flows

        except Exception as e:
            print(f"Error fetching flow alerts: {e}")
            return []

    async def get_ticker_flow(
        self,
        ticker: str,
        min_premium: int = 25000,
        limit: int = 100
    ) -> List[OptionsFlow]:
        """
        Get recent options flow for a specific ticker.
        """
        try:
            params = {
                "min_premium": min_premium,
                "limit": limit
            }
            data = await self._request(f"/api/stock/{ticker}/flow-recent", params)
            flows = []

            for item in data.get("data", []):
                try:
                    flow = self._parse_flow(item)
                    if flow:
                        flows.append(flow)
                except Exception as e:
                    continue

            return flows

        except Exception as e:
            print(f"Error fetching flow for {ticker}: {e}")
            return []

    async def get_flow_by_strike(self, ticker: str) -> Dict[float, Dict]:
        """
        Get flow aggregated by strike price.
        Useful for seeing flow at specific GEX levels.
        """
        try:
            data = await self._request(f"/api/stock/{ticker}/flow-per-strike")

            result = {}
            for item in data.get("data", []):
                strike = float(item.get("strike", 0))
                if strike > 0:
                    result[strike] = {
                        "call_premium": float(item.get("call_premium", 0)),
                        "put_premium": float(item.get("put_premium", 0)),
                        "call_volume": int(item.get("call_volume", 0)),
                        "put_volume": int(item.get("put_volume", 0)),
                        "net_premium": float(item.get("call_premium", 0)) - float(item.get("put_premium", 0)),
                    }

            return result

        except Exception as e:
            print(f"Error fetching flow by strike for {ticker}: {e}")
            return {}

    async def get_flow_by_strike_intraday(self, ticker: str) -> Dict[float, Dict]:
        """
        Get REAL-TIME intraday flow by strike (live during trading session).
        This is the freshest data - updates as orders come in.
        """
        try:
            data = await self._request(f"/api/stock/{ticker}/flow-per-strike-intraday")

            result = {}
            for item in data.get("data", []):
                strike = float(item.get("strike", 0))
                if strike > 0:
                    result[strike] = {
                        "call_premium": float(item.get("call_premium", 0)),
                        "put_premium": float(item.get("put_premium", 0)),
                        "call_volume": int(item.get("call_volume", 0)),
                        "put_volume": int(item.get("put_volume", 0)),
                        "call_trades": int(item.get("call_trades", 0)),
                        "put_trades": int(item.get("put_trades", 0)),
                        "call_volume_ask": int(item.get("call_volume_ask_side", 0)),
                        "call_volume_bid": int(item.get("call_volume_bid_side", 0)),
                        "put_volume_ask": int(item.get("put_volume_ask_side", 0)),
                        "put_volume_bid": int(item.get("put_volume_bid_side", 0)),
                        "net_premium": float(item.get("call_premium", 0)) - float(item.get("put_premium", 0)),
                        "timestamp": item.get("date") or datetime.now().isoformat(),
                    }

            return result

        except Exception as e:
            print(f"Error fetching intraday flow by strike for {ticker}: {e}")
            # Fallback to regular flow-per-strike
            return await self.get_flow_by_strike(ticker)

    async def get_flow_summary(self, ticker: str, use_realtime: bool = True) -> FlowSummary:
        """
        Get complete flow summary for a ticker.
        Aggregates recent flow data into actionable insights.

        Args:
            ticker: Stock symbol
            use_realtime: If True, uses intraday data (real-time). Default True.
        """
        summary = FlowSummary(ticker=ticker, timestamp=datetime.now())

        try:
            # Get recent flows
            flows = await self.get_ticker_flow(ticker, min_premium=10000, limit=200)

            for flow in flows:
                summary.total_premium += flow.premium

                if flow.option_type == 'call':
                    summary.call_premium += flow.premium
                else:
                    summary.put_premium += flow.premium

                if flow.sentiment == FlowSentiment.BULLISH:
                    summary.bullish_premium += flow.premium
                elif flow.sentiment == FlowSentiment.BEARISH:
                    summary.bearish_premium += flow.premium

                if flow.is_sweep:
                    summary.sweep_count += 1
                if flow.is_block:
                    summary.block_count += 1
                if flow.is_unusual:
                    summary.unusual_count += 1

                if flow.is_large:
                    summary.large_trades.append(flow)

            # Sort large trades by premium
            summary.large_trades.sort(key=lambda x: x.premium, reverse=True)

            # Get flow by strike - use REAL-TIME intraday data if available
            if use_realtime:
                summary.flow_by_strike = await self.get_flow_by_strike_intraday(ticker)
            else:
                summary.flow_by_strike = await self.get_flow_by_strike(ticker)

        except Exception as e:
            print(f"Error building flow summary for {ticker}: {e}")

        return summary

    async def get_net_premium_ticks(self, ticker: str) -> List[Dict]:
        """
        Get REAL-TIME net premium ticks for WAVE indicator.
        Shows call/put volumes, bid/ask side breakdown, net delta over time.
        Perfect for building the WAVE chart.
        """
        try:
            data = await self._request(f"/api/stock/{ticker}/net-prem-ticks")

            ticks = []
            for item in data.get("data", []):
                ticks.append({
                    "timestamp": item.get("date") or item.get("timestamp"),
                    "call_premium": float(item.get("call_premium", 0)),
                    "put_premium": float(item.get("put_premium", 0)),
                    "net_premium": float(item.get("net_premium", 0)),
                    "call_volume": int(item.get("call_volume", 0)),
                    "put_volume": int(item.get("put_volume", 0)),
                    "call_volume_ask": int(item.get("call_volume_ask_side", 0)),
                    "call_volume_bid": int(item.get("call_volume_bid_side", 0)),
                    "put_volume_ask": int(item.get("put_volume_ask_side", 0)),
                    "put_volume_bid": int(item.get("put_volume_bid_side", 0)),
                    "net_delta": float(item.get("net_delta", 0)),
                })

            return ticks

        except Exception as e:
            print(f"Error fetching net premium ticks for {ticker}: {e}")
            return []

    async def get_greek_flow(self, ticker: str) -> Dict:
        """
        Get real-time Greek flow data (delta/gamma/theta flow).
        """
        try:
            data = await self._request(f"/api/stock/{ticker}/greek-flow")
            return data.get("data", {})
        except Exception as e:
            print(f"Error fetching greek flow for {ticker}: {e}")
            return {}

    async def get_market_tide(self) -> Dict:
        """
        Get overall market flow sentiment (market tide).
        Shows aggregate bullish/bearish flow across the market.
        """
        try:
            data = await self._request("/api/market/market-tide")
            return data.get("data", {})
        except Exception as e:
            print(f"Error fetching market tide: {e}")
            return {}

    async def get_dark_pool_flow(self, ticker: str) -> List[Dict]:
        """
        Get dark pool trades for a ticker.
        Large institutional trades that don't show on public exchanges.
        """
        try:
            data = await self._request(f"/api/darkpool/{ticker}/recent")
            return data.get("data", [])
        except Exception as e:
            print(f"Error fetching dark pool for {ticker}: {e}")
            return []

    def _parse_flow(self, item: dict) -> Optional[OptionsFlow]:
        """Parse API response into OptionsFlow object."""
        try:
            # Parse expiration date
            exp_str = item.get("expiry") or item.get("expiration")
            if exp_str:
                expiration = datetime.strptime(exp_str[:10], "%Y-%m-%d").date()
            else:
                return None

            # Parse timestamp
            ts_str = item.get("executed_at") or item.get("timestamp")
            if ts_str:
                timestamp = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            else:
                timestamp = datetime.now()

            # Parse side
            side_str = item.get("side", "").lower()
            if side_str == "ask" or side_str == "above_ask":
                side = FlowSide.ASK
            elif side_str == "bid" or side_str == "below_bid":
                side = FlowSide.BID
            elif side_str == "mid":
                side = FlowSide.MID
            else:
                side = FlowSide.UNKNOWN

            return OptionsFlow(
                ticker=item.get("underlying_symbol") or item.get("ticker", ""),
                strike=float(item.get("strike", 0)),
                expiration=expiration,
                option_type=item.get("option_type", "call").lower(),
                premium=float(item.get("premium", 0)),
                size=int(item.get("size", 0)),
                side=side,
                spot_price=float(item.get("underlying_price", 0)),
                iv=float(item.get("iv", 0)),
                timestamp=timestamp,
                is_sweep=item.get("is_sweep", False),
                is_block=item.get("is_block", False),
                is_unusual=item.get("is_unusual", False),
                open_interest=int(item.get("open_interest", 0)),
                volume=int(item.get("volume", 0)),
            )

        except Exception as e:
            return None


# ===================
# GEX + FLOW INTEGRATION
# ===================
@dataclass
class GEXFlowContext:
    """
    Combined GEX level with flow context.
    Shows if dealers are actually hedging at this level.
    """
    strike: float
    gex: float
    gex_type: str  # positive/negative

    # Flow data at this strike
    call_premium: float = 0
    put_premium: float = 0
    net_premium: float = 0
    call_volume: int = 0
    put_volume: int = 0

    # Recent large trades at/near this strike
    recent_sweeps: int = 0
    recent_blocks: int = 0

    # Interpretation
    flow_confirms_gex: bool = False
    flow_strength: str = "none"  # none, weak, moderate, strong

    def to_dict(self) -> dict:
        return {
            "strike": self.strike,
            "gex": self.gex,
            "gex_type": self.gex_type,
            "call_premium": self.call_premium,
            "put_premium": self.put_premium,
            "net_premium": self.net_premium,
            "call_volume": self.call_volume,
            "put_volume": self.put_volume,
            "recent_sweeps": self.recent_sweeps,
            "recent_blocks": self.recent_blocks,
            "flow_confirms_gex": self.flow_confirms_gex,
            "flow_strength": self.flow_strength,
        }


async def enrich_gex_with_flow(
    ticker: str,
    gex_zones: List[dict],
    flow_client: UnusualWhalesClient
) -> List[GEXFlowContext]:
    """
    Enrich GEX zones with flow context.

    This answers: "Are dealers actually trading at these levels?"
    """
    enriched = []

    # Get flow by strike
    flow_by_strike = await flow_client.get_flow_by_strike(ticker)

    # Get recent flows for sweep/block detection
    recent_flows = await flow_client.get_ticker_flow(ticker, min_premium=50000, limit=100)

    for zone in gex_zones:
        strike = zone.get("strike", 0)
        gex = zone.get("gex", 0)
        gex_type = zone.get("type", "positive")

        # Get flow at this strike
        strike_flow = flow_by_strike.get(strike, {})

        # Count recent sweeps/blocks near this strike (within $1)
        sweeps = 0
        blocks = 0
        for flow in recent_flows:
            if abs(flow.strike - strike) <= 1:
                if flow.is_sweep:
                    sweeps += 1
                if flow.is_block:
                    blocks += 1

        # Determine if flow confirms GEX
        net_premium = strike_flow.get("net_premium", 0)
        # Positive GEX = dealers long gamma = expect mean reversion
        # If we see aggressive call buying at positive GEX levels, flow confirms
        flow_confirms = False
        flow_strength = "none"

        total_premium = strike_flow.get("call_premium", 0) + strike_flow.get("put_premium", 0)
        if total_premium > 100_000:
            flow_strength = "weak"
        if total_premium > 500_000:
            flow_strength = "moderate"
        if total_premium > 1_000_000:
            flow_strength = "strong"

        if gex_type == "positive" and net_premium > 0:
            # Positive GEX + net call buying = flow confirms
            flow_confirms = True
        elif gex_type == "negative" and net_premium < 0:
            # Negative GEX + net put buying = flow confirms acceleration risk
            flow_confirms = True

        enriched.append(GEXFlowContext(
            strike=strike,
            gex=gex,
            gex_type=gex_type,
            call_premium=strike_flow.get("call_premium", 0),
            put_premium=strike_flow.get("put_premium", 0),
            net_premium=net_premium,
            call_volume=strike_flow.get("call_volume", 0),
            put_volume=strike_flow.get("put_volume", 0),
            recent_sweeps=sweeps,
            recent_blocks=blocks,
            flow_confirms_gex=flow_confirms,
            flow_strength=flow_strength,
        ))

    return enriched


# ===================
# SINGLETON
# ===================
_flow_client: Optional[UnusualWhalesClient] = None

# Get API key from environment (with default key)
UNUSUAL_WHALES_API_KEY = os.environ.get("UNUSUAL_WHALES_API_KEY", "d1a5672b-c6fd-4de8-8e24-c50bfd82ce98")


def get_flow_client() -> Optional[UnusualWhalesClient]:
    """Get or create Unusual Whales client singleton."""
    global _flow_client

    if not UNUSUAL_WHALES_API_KEY:
        return None

    if _flow_client is None:
        _flow_client = UnusualWhalesClient(UNUSUAL_WHALES_API_KEY)

    return _flow_client


# ===================
# TEST
# ===================
if __name__ == "__main__":
    async def test():
        api_key = os.environ.get("UNUSUAL_WHALES_API_KEY")
        if not api_key:
            print("Set UNUSUAL_WHALES_API_KEY environment variable to test")
            return

        client = UnusualWhalesClient(api_key)

        print("Testing Unusual Whales Client")
        print("=" * 50)

        # Get flow alerts
        print("\nRecent Flow Alerts:")
        alerts = await client.get_flow_alerts(limit=5)
        for alert in alerts:
            print(f"  {alert.ticker} {alert.option_type.upper()} ${alert.strike} "
                  f"| ${alert.premium:,.0f} | {alert.sentiment.value}")

        # Get SPY flow summary
        print("\nSPY Flow Summary:")
        summary = await client.get_flow_summary("SPY")
        print(f"  Total Premium: ${summary.total_premium:,.0f}")
        print(f"  Call/Put Ratio: {summary.put_call_ratio:.2f}")
        print(f"  Net Sentiment: {summary.net_sentiment.value}")
        print(f"  Sweeps: {summary.sweep_count}, Blocks: {summary.block_count}")

    asyncio.run(test())
