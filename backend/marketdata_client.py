"""
MarketData.app Options Client

Real options data with actual Greeks (no estimation needed).
https://www.marketdata.app/docs/api/options/chain
"""
import sys
import asyncio
import aiohttp
from datetime import date, datetime, timedelta
from typing import List, Optional, Tuple

# Fix Windows console encoding issues
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, OSError):
        pass

from gex_calculator import OptionContract


class MarketDataClient:
    """
    Client for MarketData.app API.
    Provides real Greeks directly from OPRA data.
    """

    BASE_URL = "https://api.marketdata.app/v1"

    def __init__(self, api_token: str):
        self.api_token = api_token
        self.headers = {
            "Authorization": f"Token {api_token}",
            "Accept": "application/json"
        }
        print("[OK] MarketData.app client initialized (Real Greeks from OPRA)")

    async def _request(self, endpoint: str, params: dict = None) -> dict:
        """Make an async API request."""
        url = f"{self.BASE_URL}{endpoint}"

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=self.headers, params=params) as response:
                # 200 = OK, 203 = Non-Authoritative (cached data - still valid)
                if response.status in (200, 203):
                    return await response.json()
                elif response.status == 401:
                    raise Exception("Invalid API token")
                elif response.status == 429:
                    raise Exception("Rate limit exceeded")
                else:
                    text = await response.text()
                    raise Exception(f"API error {response.status}: {text}")

    # Index symbols that need special handling
    INDEX_SYMBOLS = {"SPX", "NDX", "RUT", "DJX"}

    async def get_quote(self, symbol: str) -> Optional[dict]:
        """Get current quote for a symbol (stock or index)."""
        try:
            # Try stock quote first
            data = await self._request(f"/stocks/quotes/{symbol}/")

            if data.get("s") == "ok":
                return {
                    "symbol": symbol,
                    "last": data.get("last", [0])[0],
                    "bid": data.get("bid", [0])[0] if data.get("bid") else data.get("last", [0])[0],
                    "ask": data.get("ask", [0])[0] if data.get("ask") else data.get("last", [0])[0],
                    "volume": data.get("volume", [0])[0] if data.get("volume") else 0,
                    "name": symbol
                }

            # For index symbols, estimate price from options chain
            if symbol.upper() in self.INDEX_SYMBOLS:
                spot = await self._estimate_index_price(symbol)
                if spot:
                    return {
                        "symbol": symbol,
                        "last": spot,
                        "bid": spot,
                        "ask": spot,
                        "volume": 0,
                        "name": symbol
                    }

            return None
        except Exception as e:
            print(f"Error fetching quote for {symbol}: {e}")
            # Fallback for indices
            if symbol.upper() in self.INDEX_SYMBOLS:
                spot = await self._estimate_index_price(symbol)
                if spot:
                    return {"symbol": symbol, "last": spot, "bid": spot, "ask": spot, "volume": 0, "name": symbol}
            return None

    async def get_candles(
        self,
        symbol: str,
        resolution: str = "5",  # 1, 5, 15, 30, 60, D, W, M
        from_date: str = None,
        to_date: str = None,
        count: int = 100
    ) -> List[dict]:
        """
        Get historical candle data for charting.

        Args:
            symbol: Stock/ETF symbol
            resolution: Candle resolution (1, 5, 15, 30, 60 minutes, or D/W/M)
            from_date: Start date (YYYY-MM-DD)
            to_date: End date (YYYY-MM-DD)
            count: Number of candles to return

        Returns:
            List of candle dicts with time, open, high, low, close, volume
        """
        try:
            params = {"resolution": resolution}

            if from_date:
                params["from"] = from_date
            if to_date:
                params["to"] = to_date
            if count:
                params["countback"] = count

            data = await self._request(f"/stocks/candles/{resolution}/{symbol}/", params)

            if data.get("s") != "ok":
                print(f"Candles error for {symbol}: {data.get('errmsg', 'Unknown error')}")
                return []

            # Parse parallel arrays into candle objects
            candles = []
            timestamps = data.get("t", [])
            opens = data.get("o", [])
            highs = data.get("h", [])
            lows = data.get("l", [])
            closes = data.get("c", [])
            volumes = data.get("v", [])

            for i in range(len(timestamps)):
                candles.append({
                    "time": timestamps[i],
                    "open": opens[i] if i < len(opens) else 0,
                    "high": highs[i] if i < len(highs) else 0,
                    "low": lows[i] if i < len(lows) else 0,
                    "close": closes[i] if i < len(closes) else 0,
                    "volume": volumes[i] if i < len(volumes) else 0
                })

            return candles

        except Exception as e:
            print(f"Error fetching candles for {symbol}: {e}")
            return []

    async def _estimate_index_price(self, symbol: str) -> Optional[float]:
        """Estimate index price from ATM options (put-call parity)."""
        try:
            # Get nearest expiration
            expirations = await self.get_options_expirations(symbol)
            if not expirations:
                return None

            # Get options chain for nearest expiration
            params = {"expiration": expirations[0].strftime("%Y-%m-%d")}
            data = await self._request(f"/options/chain/{symbol}/", params)

            if data.get("s") != "ok":
                return None

            # Find ATM options and estimate spot from mid prices
            strikes = data.get("strike", [])
            sides = data.get("side", [])
            bids = data.get("bid", [])
            asks = data.get("ask", [])

            if not strikes:
                return None

            # Group by strike
            strike_data = {}
            for i in range(len(strikes)):
                strike = strikes[i]
                side = sides[i] if i < len(sides) else None
                bid = bids[i] if i < len(bids) else 0
                ask = asks[i] if i < len(asks) else 0

                if strike not in strike_data:
                    strike_data[strike] = {}
                if side:
                    strike_data[strike][side] = (bid or 0, ask or 0)

            # Find strike where call and put prices are closest (ATM)
            best_strike = None
            min_diff = float('inf')

            for strike, options in strike_data.items():
                if 'call' in options and 'put' in options:
                    call_mid = (options['call'][0] + options['call'][1]) / 2
                    put_mid = (options['put'][0] + options['put'][1]) / 2
                    diff = abs(call_mid - put_mid)
                    if diff < min_diff:
                        min_diff = diff
                        best_strike = strike

            if best_strike:
                print(f"[{symbol}] Estimated spot price from options: {best_strike}")
                return float(best_strike)

            # Fallback: return middle strike
            sorted_strikes = sorted(set(strikes))
            mid_strike = sorted_strikes[len(sorted_strikes) // 2]
            print(f"[{symbol}] Using middle strike as spot estimate: {mid_strike}")
            return float(mid_strike)

        except Exception as e:
            print(f"Error estimating index price for {symbol}: {e}")
            return None

    async def get_options_expirations(self, symbol: str) -> List[date]:
        """Get available options expiration dates."""
        try:
            data = await self._request(f"/options/expirations/{symbol}/")

            if data.get("s") != "ok":
                return []

            expirations = data.get("expirations", [])
            return [datetime.strptime(exp, "%Y-%m-%d").date() for exp in expirations]

        except Exception as e:
            print(f"Error fetching expirations for {symbol}: {e}")
            return []

    async def get_options_chain(
        self,
        symbol: str,
        expiration: date,
        spot_price: float
    ) -> List[OptionContract]:
        """
        Get options chain for a specific expiration.
        Returns contracts with REAL Greeks from OPRA data.
        """
        contracts = []
        gamma_provided = 0
        gamma_calculated = 0
        gamma_zero = 0

        try:
            # Fetch options chain with Greeks
            params = {
                "expiration": expiration.strftime("%Y-%m-%d"),
                "minOpenInterest": 100,  # Filter low OI
            }

            data = await self._request(f"/options/chain/{symbol}/", params)

            if data.get("s") != "ok":
                print(f"Options chain error for {symbol} {expiration}: {data.get('errmsg', 'Unknown error')}")
                return []

            # Parse response arrays
            # MarketData returns parallel arrays for each field
            num_contracts = len(data.get("optionSymbol", []))

            for i in range(num_contracts):
                try:
                    # Extract data for this contract
                    side = data.get("side", [])[i] if i < len(data.get("side", [])) else None
                    strike = data.get("strike", [])[i] if i < len(data.get("strike", [])) else None
                    oi = data.get("openInterest", [])[i] if i < len(data.get("openInterest", [])) else 0
                    volume = data.get("volume", [])[i] if i < len(data.get("volume", [])) else 0
                    bid = data.get("bid", [])[i] if i < len(data.get("bid", [])) else 0
                    ask = data.get("ask", [])[i] if i < len(data.get("ask", [])) else 0

                    # REAL Greeks from OPRA!
                    delta = data.get("delta", [])[i] if i < len(data.get("delta", [])) else 0
                    gamma = data.get("gamma", [])[i] if i < len(data.get("gamma", [])) else 0
                    theta = data.get("theta", [])[i] if i < len(data.get("theta", [])) else 0
                    vega = data.get("vega", [])[i] if i < len(data.get("vega", [])) else 0
                    iv = data.get("iv", [])[i] if i < len(data.get("iv", [])) else 0

                    # Skip invalid contracts
                    if not side or not strike or oi is None:
                        continue

                    # Handle None values
                    oi = int(oi) if oi else 0
                    volume = int(volume) if volume else 0
                    delta = float(delta) if delta else 0.0
                    gamma = float(gamma) if gamma else 0.0
                    theta = float(theta) if theta else 0.0
                    vega = float(vega) if vega else 0.0
                    bid = float(bid) if bid else 0.0
                    ask = float(ask) if ask else 0.0
                    iv_val = float(iv) if iv else 0.0

                    # Skip zero OI
                    if oi == 0:
                        continue

                    # Track if gamma came from API
                    vanna = 0.0
                    api_had_gamma = gamma != 0.0

                    if api_had_gamma:
                        gamma_provided += 1

                    # Calculate additional Greeks (vanna) and fill in missing values
                    if iv_val > 0 and spot_price > 0:
                        try:
                            from greeks_calculator import calculate_greeks
                            greeks = calculate_greeks(
                                spot=spot_price,
                                strike=float(strike),
                                expiration=expiration,
                                iv=iv_val,
                                option_type=side.lower()
                            )
                            # Use calculated gamma if API didn't provide it
                            if not api_had_gamma:
                                gamma = greeks.gamma
                                if gamma != 0.0:
                                    gamma_calculated += 1
                                else:
                                    gamma_zero += 1
                            # Use calculated delta if API didn't provide it
                            if delta == 0.0:
                                delta = greeks.delta
                            vanna = greeks.vanna
                        except Exception as calc_err:
                            # Fallback to approximation only if calculator fails
                            vanna = -delta * gamma / iv_val if gamma else 0.0
                            if not api_had_gamma:
                                gamma_zero += 1
                    else:
                        if not api_had_gamma:
                            gamma_zero += 1

                    contract = OptionContract(
                        strike=float(strike),
                        expiration=expiration,
                        option_type=side.lower(),  # 'call' or 'put'
                        open_interest=oi,
                        gamma=gamma,
                        delta=delta,
                        vega=vega,
                        vanna=vanna,
                        iv=iv_val,  # IV for skew calculation
                        volume=volume,
                        bid=bid,
                        ask=ask
                    )
                    contracts.append(contract)

                except (IndexError, TypeError, ValueError) as e:
                    continue

        except Exception as e:
            print(f"Error fetching options chain for {symbol} {expiration}: {e}")

        # Log gamma statistics for debugging
        if contracts:
            print(f"  {symbol} {expiration}: {len(contracts)} contracts "
                  f"(gamma: {gamma_provided} API, {gamma_calculated} calc, {gamma_zero} zero)")

        return contracts

    async def get_full_chain_with_greeks(
        self,
        symbol: str,
        max_expirations: int = 8
    ) -> Tuple[float, List[OptionContract]]:
        """
        Get complete options chain for GEX calculation.

        Returns: (spot_price, list of OptionContract)
        """
        # Get spot price
        quote = await self.get_quote(symbol)
        if not quote:
            print(f"Could not get quote for {symbol}")
            return 0, []

        spot_price = quote.get("last", 0)
        if spot_price == 0:
            return 0, []

        # Get expirations
        expirations = await self.get_options_expirations(symbol)

        # After market close (4pm EST), skip today's expiration - it's expired
        from zoneinfo import ZoneInfo
        ET = ZoneInfo("America/New_York")
        now_et = datetime.now(ET)
        today = now_et.date()
        market_closed = now_et.hour >= 16  # 4pm EST

        if market_closed and expirations and expirations[0] == today:
            expirations = expirations[1:]  # Skip today's expired options
            print(f"[{symbol}] Market closed - showing next day: {expirations[0] if expirations else 'N/A'}")

        expirations = expirations[:max_expirations]

        if not expirations:
            print(f"No expirations found for {symbol}")
            return spot_price, []

        # Fetch chains for each expiration
        all_contracts = []

        for exp in expirations:
            contracts = await self.get_options_chain(symbol, exp, spot_price)
            all_contracts.extend(contracts)

        print(f"[MarketData] Total: {len(all_contracts)} contracts for {symbol}")

        return spot_price, all_contracts

    def search_symbol(self, query: str, max_results: int = 5) -> List[dict]:
        """
        Search for symbols by name or partial ticker.
        Note: MarketData.app doesn't have a search endpoint,
        so we fall back to Yahoo Finance for search.
        """
        try:
            import requests

            # Use Yahoo Finance search API (free, no key needed)
            url = "https://query2.finance.yahoo.com/v1/finance/search"
            params = {
                "q": query,
                "quotesCount": max_results,
                "newsCount": 0,
                "enableFuzzyQuery": True,
            }
            headers = {"User-Agent": "Mozilla/5.0"}

            response = requests.get(url, params=params, headers=headers, timeout=5)
            data = response.json()

            results = []
            for quote in data.get("quotes", []):
                quote_type = quote.get("quoteType", "")
                if quote_type in ["EQUITY", "ETF"]:
                    results.append({
                        "symbol": quote.get("symbol", ""),
                        "name": quote.get("shortname") or quote.get("longname", ""),
                        "type": quote_type,
                        "exchange": quote.get("exchange", "")
                    })

            return results

        except Exception as e:
            print(f"Search error: {e}")
            return []


# Singleton instance
_marketdata_client: Optional[MarketDataClient] = None

# API Token - set via environment or directly
MARKETDATA_API_TOKEN = "RHFRclMtZEQyZVdXSk10eVFmcWJ3Z0Y5a1JZN0o1ZlpKMEx1ZWVWUnhtND0"


def get_marketdata_client() -> MarketDataClient:
    """Get or create MarketData.app client singleton."""
    global _marketdata_client

    if _marketdata_client is None:
        import os
        token = os.environ.get("MARKETDATA_API_TOKEN", MARKETDATA_API_TOKEN)
        _marketdata_client = MarketDataClient(token)

    return _marketdata_client


# Test
if __name__ == "__main__":
    async def test():
        client = get_marketdata_client()

        # Test with SPY
        symbol = "SPY"
        print(f"\nTesting {symbol}...")

        spot, contracts = await client.get_full_chain_with_greeks(symbol)

        print(f"\nSpot: ${spot:.2f}")
        print(f"Total contracts: {len(contracts)}")

        if contracts:
            # Show sample with REAL Greeks
            print("\nSample contracts (REAL Greeks from OPRA):")
            for c in contracts[:5]:
                print(f"  {c.option_type.upper()} {c.strike} exp:{c.expiration} "
                      f"OI:{c.open_interest} delta:{c.delta:.3f} gamma:{c.gamma:.6f}")

    asyncio.run(test())
