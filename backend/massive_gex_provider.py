"""
Massive (Polygon) GEX Data Provider

Fetches options data from Massive/Polygon for GEX calculations.
Replaces Tradier as primary data source.
"""
import os
import asyncio
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from zoneinfo import ZoneInfo
import httpx

# Import gex_calculator's OptionContract for compatibility with existing code
from gex_calculator import OptionContract as GEXOptionContract

ET = ZoneInfo("America/New_York")

# API Configuration
MASSIVE_API_KEY = os.environ.get("MASSIVE_API_KEY", "yT5IQUT53OnuEE_ian4YbKQtu78rlDVX")
MASSIVE_BASE_URL = "https://api.polygon.io"


@dataclass
class OptionContract:
    """Single option contract with all data needed for GEX."""
    symbol: str  # O:SPY260102C00600000
    underlying: str
    strike: float
    expiration: str  # YYYY-MM-DD
    contract_type: str  # call or put
    open_interest: int
    volume: int
    delta: float
    gamma: float
    vega: float
    iv: float
    bid: float
    ask: float
    mid: float
    last_price: float


class MassiveGEXProvider:
    """Fetch options data from Massive for GEX calculations."""

    def __init__(self, api_key: str = None):
        self.api_key = api_key or MASSIVE_API_KEY
        self.base_url = MASSIVE_BASE_URL
        self._client: Optional[httpx.AsyncClient] = None

        # Cache for spot prices
        self._spot_cache: Dict[str, Tuple[float, datetime]] = {}
        self._spot_cache_ttl = 60  # seconds

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

    async def get_spot_price(self, symbol: str) -> float:
        """Get current spot price for underlying."""
        symbol = symbol.upper()

        # Check cache
        if symbol in self._spot_cache:
            price, ts = self._spot_cache[symbol]
            if (datetime.now() - ts).total_seconds() < self._spot_cache_ttl:
                return price

        client = await self._get_client()

        try:
            # Get stock snapshot
            url = f"{self.base_url}/v2/snapshot/locale/us/markets/stocks/tickers/{symbol}"
            response = await client.get(url, params={"apiKey": self.api_key})
            response.raise_for_status()
            data = response.json()

            ticker = data.get("ticker", {})
            # Try different price fields
            price = (
                ticker.get("lastTrade", {}).get("p", 0) or
                ticker.get("prevDay", {}).get("c", 0) or
                ticker.get("min", {}).get("c", 0) or
                0
            )

            if price > 0:
                self._spot_cache[symbol] = (price, datetime.now())
                return price

        except Exception as e:
            print(f"[Massive] Error getting spot price for {symbol}: {e}")

        # Fallback: get from options snapshot
        try:
            url = f"{self.base_url}/v3/snapshot/options/{symbol}"
            response = await client.get(url, params={"apiKey": self.api_key, "limit": 1})
            response.raise_for_status()
            data = response.json()

            if data.get("results"):
                price = data["results"][0].get("underlying_asset", {}).get("price", 0)
                if price > 0:
                    self._spot_cache[symbol] = (price, datetime.now())
                    return price

        except Exception as e:
            print(f"[Massive] Error getting spot from options: {e}")

        return 0

    async def get_options_chain(
        self,
        symbol: str,
        min_dte: int = 0,
        max_dte: int = 45,
        min_oi: int = 100
    ) -> List[OptionContract]:
        """
        Fetch full options chain for a symbol.

        Args:
            symbol: Underlying symbol
            min_dte: Minimum days to expiration
            max_dte: Maximum days to expiration
            min_oi: Minimum open interest filter
        """
        symbol = symbol.upper()
        client = await self._get_client()

        # Calculate date range
        today = datetime.now()
        min_exp = (today + timedelta(days=min_dte)).strftime("%Y-%m-%d")
        max_exp = (today + timedelta(days=max_dte)).strftime("%Y-%m-%d")

        contracts = []
        next_url = None
        page = 0
        max_pages = 20  # Safety limit

        while page < max_pages:
            try:
                if next_url:
                    response = await client.get(next_url)
                else:
                    url = f"{self.base_url}/v3/snapshot/options/{symbol}"
                    params = {
                        "apiKey": self.api_key,
                        "limit": 250,
                        "expiration_date.gte": min_exp,
                        "expiration_date.lte": max_exp
                    }
                    response = await client.get(url, params=params)

                response.raise_for_status()
                data = response.json()

                if data.get("status") != "OK":
                    print(f"[Massive] API error: {data}")
                    break

                results = data.get("results", [])
                if not results:
                    break

                for opt in results:
                    try:
                        details = opt.get("details", {})
                        day = opt.get("day", {})
                        greeks = opt.get("greeks", {})
                        quote = opt.get("last_quote", {})
                        trade = opt.get("last_trade", {})

                        oi = opt.get("open_interest", 0) or 0
                        if oi < min_oi:
                            continue

                        contract = OptionContract(
                            symbol=details.get("ticker", ""),
                            underlying=symbol,
                            strike=details.get("strike_price", 0),
                            expiration=details.get("expiration_date", ""),
                            contract_type=details.get("contract_type", "").lower(),
                            open_interest=oi,
                            volume=day.get("volume", 0) or 0,
                            delta=greeks.get("delta", 0) or 0,
                            gamma=greeks.get("gamma", 0) or 0,
                            vega=greeks.get("vega", 0) or 0,
                            iv=opt.get("implied_volatility", 0) or 0,
                            bid=quote.get("bid", 0) or 0,
                            ask=quote.get("ask", 0) or 0,
                            mid=quote.get("midpoint", 0) or 0,
                            last_price=trade.get("price", 0) or 0
                        )
                        contracts.append(contract)

                    except Exception as e:
                        print(f"[Massive] Error parsing option: {e}")
                        continue

                # Check for pagination
                next_url = data.get("next_url")
                if next_url:
                    # Add API key to next_url
                    if "?" in next_url:
                        next_url += f"&apiKey={self.api_key}"
                    else:
                        next_url += f"?apiKey={self.api_key}"
                    page += 1
                else:
                    break

            except httpx.HTTPError as e:
                print(f"[Massive] HTTP error: {e}")
                break
            except Exception as e:
                print(f"[Massive] Error: {e}")
                break

        print(f"[Massive] Got {len(contracts)} options for {symbol}")
        return contracts

    async def get_gex_data(
        self,
        symbol: str,
        min_oi: int = 100
    ) -> Dict:
        """
        Get all data needed for GEX calculation.

        Returns dict with:
        - spot_price: Current underlying price
        - contracts: List of OptionContract objects
        - timestamp: Data timestamp
        """
        symbol = symbol.upper()

        # Fetch spot price and options chain in parallel
        spot_task = self.get_spot_price(symbol)
        chain_task = self.get_options_chain(symbol, min_oi=min_oi)

        spot_price, contracts = await asyncio.gather(spot_task, chain_task)

        return {
            "symbol": symbol,
            "spot_price": spot_price,
            "contracts": contracts,
            "timestamp": datetime.now(),
            "contract_count": len(contracts),
            "data_source": "massive"
        }

    async def get_full_chain_with_greeks(
        self,
        symbol: str,
        max_expirations: int = 6
    ) -> Tuple[float, List[GEXOptionContract]]:
        """
        Get complete options chain with Greeks for GEX calculation.
        Compatible with Tradier's interface for drop-in replacement.

        Returns: (spot_price, list of GEXOptionContract)
        """
        symbol = symbol.upper()

        # Get spot price and raw options chain
        spot_task = self.get_spot_price(symbol)
        chain_task = self.get_options_chain(symbol, min_oi=50)

        spot_price, raw_contracts = await asyncio.gather(spot_task, chain_task)

        if spot_price == 0:
            print(f"[Massive] Could not get spot price for {symbol}")
            return 0, []

        # After market close (4pm EST), skip today's expiration
        now_et = datetime.now(ET)
        today = now_et.date()
        market_closed = now_et.hour >= 16  # 4pm EST

        # Convert to GEX calculator format and filter expirations
        contracts: List[GEXOptionContract] = []
        expirations_seen = set()

        for raw in raw_contracts:
            try:
                # Parse expiration date
                exp_str = raw.expiration  # YYYY-MM-DD format
                exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()

                # Skip expired options after market close
                if market_closed and exp_date == today:
                    continue

                # Track unique expirations
                expirations_seen.add(exp_date)

                # Skip if we already have enough expirations
                if len(expirations_seen) > max_expirations:
                    # Check if this expiration is one of our first N
                    sorted_exps = sorted(expirations_seen)[:max_expirations]
                    if exp_date not in sorted_exps:
                        continue

                # Get greeks from Polygon
                greeks = {
                    'gamma': raw.gamma or 0,
                    'delta': raw.delta or 0,
                    'vega': raw.vega or 0,
                }

                # Estimate vanna from delta and IV
                # vanna = d(delta)/d(sigma) - estimate as delta * (1 - abs(delta)) / IV
                vanna = 0.0
                if raw.iv > 0 and abs(raw.delta) > 0:
                    # Rough approximation: vanna peaks at ATM
                    vanna = raw.delta * (1 - abs(raw.delta)) * 2 / raw.iv

                # Create GEX-compatible contract
                contract = GEXOptionContract(
                    strike=raw.strike,
                    expiration=exp_date,
                    option_type=raw.contract_type,  # 'call' or 'put'
                    open_interest=raw.open_interest,
                    gamma=greeks['gamma'],
                    delta=greeks['delta'],
                    vega=greeks['vega'],
                    vanna=vanna,
                    iv=raw.iv,
                    volume=raw.volume,
                    bid=raw.bid,
                    ask=raw.ask
                )
                contracts.append(contract)

            except Exception as e:
                print(f"[Massive] Error converting contract: {e}")
                continue

        # Sort by expiration, then strike
        contracts.sort(key=lambda c: (c.expiration, c.strike))

        print(f"[Massive] Converted {len(contracts)} contracts for {symbol} "
              f"(expirations: {len(expirations_seen)})")

        return spot_price, contracts

    def calculate_gex_for_contract(
        self,
        contract: OptionContract,
        spot_price: float,
        shares_per_contract: int = 100
    ) -> float:
        """
        Calculate GEX for a single contract.

        GEX = Gamma × Open Interest × Spot² × 0.01 × Shares per Contract

        For puts, gamma effect is inverted (dealers are long puts = short gamma)
        """
        if contract.gamma == 0 or contract.open_interest == 0:
            return 0

        gex = (
            contract.gamma *
            contract.open_interest *
            (spot_price ** 2) *
            0.01 *
            shares_per_contract
        )

        # Puts have inverse effect on dealer hedging
        if contract.contract_type == "put":
            gex = -gex

        return gex


# Singleton instance
_massive_provider: Optional[MassiveGEXProvider] = None


def get_massive_provider() -> MassiveGEXProvider:
    """Get or create Massive provider singleton."""
    global _massive_provider
    if _massive_provider is None:
        _massive_provider = MassiveGEXProvider()
    return _massive_provider


# Test
if __name__ == "__main__":
    async def test():
        provider = get_massive_provider()
        print("Testing Massive GEX Provider...")

        data = await provider.get_gex_data("SPY", min_oi=500)
        print(f"\nSPY Data:")
        print(f"  Spot Price: ${data['spot_price']:.2f}")
        print(f"  Contracts: {data['contract_count']}")

        # Calculate total GEX
        total_gex = 0
        for c in data['contracts']:
            gex = provider.calculate_gex_for_contract(c, data['spot_price'])
            total_gex += gex

        print(f"  Total GEX: ${total_gex/1e9:.2f}B")

        await provider.close()

    asyncio.run(test())
