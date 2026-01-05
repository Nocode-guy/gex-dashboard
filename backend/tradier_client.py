"""
Tradier API Client

Handles fetching options chain data from Tradier API.
Falls back to mock data if no API key is configured.
"""
import os
import asyncio
from datetime import date, datetime
from typing import List, Optional, Dict, Any
import httpx

from gex_calculator import OptionContract
from mock_data import get_mock_options_chain, get_mock_spot_price
from config import TRADIER_BASE_URL, TRADIER_SANDBOX_URL, TRADIER_API_KEY, TRADIER_PAPER_TRADING
from greeks_calculator import calculate_greeks


class TradierClient:
    """
    Client for Tradier API.

    Supports both live and sandbox environments.
    Falls back to mock data if no API key is provided.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        paper_trading: Optional[bool] = None
    ):
        # Use config settings if not provided
        self.api_key = api_key or os.environ.get("TRADIER_API_KEY") or TRADIER_API_KEY
        self.paper_trading = paper_trading if paper_trading is not None else TRADIER_PAPER_TRADING

        if self.paper_trading:
            self.base_url = TRADIER_SANDBOX_URL
        else:
            self.base_url = TRADIER_BASE_URL

        self.use_mock = self.api_key is None or self.api_key == ""

        if self.use_mock:
            print("[WARN] No Tradier API key found - using mock data")
        else:
            mode = "SANDBOX" if self.paper_trading else "LIVE REAL-TIME"
            print(f"[OK] Tradier client initialized ({mode}) - {self.base_url}")

    def _get_headers(self) -> Dict[str, str]:
        """Get API request headers."""
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json"
        }

    async def get_quote(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Get current quote for a symbol.

        Returns dict with: last, bid, ask, volume, etc.
        """
        if self.use_mock:
            return {
                "symbol": symbol,
                "last": get_mock_spot_price(symbol),
                "bid": get_mock_spot_price(symbol) - 0.05,
                "ask": get_mock_spot_price(symbol) + 0.05,
                "volume": 1000000,
            }

        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(
                    f"{self.base_url}/markets/quotes",
                    headers=self._get_headers(),
                    params={"symbols": symbol}
                )
                response.raise_for_status()
                data = response.json()

                quotes = data.get("quotes", {}).get("quote", {})
                if isinstance(quotes, list):
                    return quotes[0] if quotes else None
                return quotes

            except Exception as e:
                print(f"Error fetching quote for {symbol}: {e}")
                return None

    async def get_candles(
        self,
        symbol: str,
        resolution: str = "5",
        count: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Get historical candle data for a symbol.

        Args:
            symbol: Stock symbol
            resolution: Candle resolution (1, 5, 15, 60, D, W, M)
            count: Number of candles to fetch
        """
        if self.use_mock:
            return []

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                # Determine if this is intraday or daily+ data
                is_intraday = resolution in ["1", "5", "15", "60"]

                if is_intraday:
                    # Use /markets/timesales for intraday data
                    interval_map = {"1": "1min", "5": "5min", "15": "15min", "60": "1min"}
                    interval = interval_map.get(resolution, "5min")

                    # Timesales requires start/end as timestamps or dates
                    # Get last 5 trading days of data
                    end_time = datetime.now()
                    start_time = end_time - timedelta(days=5)

                    params = {
                        "symbol": symbol,
                        "interval": interval,
                        "start": start_time.strftime("%Y-%m-%d %H:%M"),
                        "end": end_time.strftime("%Y-%m-%d %H:%M"),
                        "session_filter": "all"  # Include pre/post market
                    }

                    response = await client.get(
                        f"{self.base_url}/markets/timesales",
                        headers=self._get_headers(),
                        params=params
                    )
                    response.raise_for_status()
                    data = response.json()

                    series = data.get("series", {})
                    if not series:
                        print(f"[Candles] No timesales data for {symbol}")
                        return []

                    ticks = series.get("data", [])
                    if isinstance(ticks, dict):
                        ticks = [ticks]

                    # For 60-minute candles, aggregate 1-min data
                    if resolution == "60":
                        ticks = self._aggregate_to_hourly(ticks)

                    # Convert to candle format (timesales returns OHLCV)
                    candles = []
                    for tick in ticks[-count:]:
                        time_str = tick.get("time", "")
                        # Parse timestamp to Unix for lightweight-charts
                        try:
                            dt = datetime.strptime(time_str, "%Y-%m-%dT%H:%M:%S")
                            unix_time = int(dt.timestamp())
                        except:
                            unix_time = time_str

                        candles.append({
                            "time": unix_time,
                            "open": float(tick.get("open", 0)),
                            "high": float(tick.get("high", 0)),
                            "low": float(tick.get("low", 0)),
                            "close": float(tick.get("close", tick.get("price", 0))),
                            "volume": int(tick.get("volume", 0))
                        })
                    return candles

                else:
                    # Use /markets/history for daily, weekly, monthly data
                    interval_map = {"D": "daily", "W": "weekly", "M": "monthly"}
                    interval = interval_map.get(resolution, "daily")

                    params = {
                        "symbol": symbol,
                        "interval": interval,
                        "start": (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d"),
                        "end": datetime.now().strftime("%Y-%m-%d")
                    }

                    response = await client.get(
                        f"{self.base_url}/markets/history",
                        headers=self._get_headers(),
                        params=params
                    )
                    response.raise_for_status()
                    data = response.json()

                    history = data.get("history", {})
                    if not history:
                        print(f"[Candles] No history data for {symbol}")
                        return []

                    days = history.get("day", [])
                    if isinstance(days, dict):
                        days = [days]

                    # Convert to candle format
                    candles = []
                    for day in days[-count:]:
                        date_str = day.get("date", "")
                        try:
                            dt = datetime.strptime(date_str, "%Y-%m-%d")
                            unix_time = int(dt.timestamp())
                        except:
                            unix_time = date_str

                        candles.append({
                            "time": unix_time,
                            "open": float(day.get("open", 0)),
                            "high": float(day.get("high", 0)),
                            "low": float(day.get("low", 0)),
                            "close": float(day.get("close", 0)),
                            "volume": int(day.get("volume", 0))
                        })
                    return candles

            except Exception as e:
                print(f"Error fetching candles for {symbol}: {e}")
                return []

    def _aggregate_to_hourly(self, ticks: List[Dict]) -> List[Dict]:
        """Aggregate 1-minute ticks to hourly candles."""
        if not ticks:
            return []

        hourly = {}
        for tick in ticks:
            time_str = tick.get("time", "")
            try:
                dt = datetime.strptime(time_str, "%Y-%m-%dT%H:%M:%S")
                hour_key = dt.strftime("%Y-%m-%dT%H:00:00")

                if hour_key not in hourly:
                    hourly[hour_key] = {
                        "time": hour_key,
                        "open": float(tick.get("open", 0)),
                        "high": float(tick.get("high", 0)),
                        "low": float(tick.get("low", 0)),
                        "close": float(tick.get("close", tick.get("price", 0))),
                        "volume": int(tick.get("volume", 0))
                    }
                else:
                    hourly[hour_key]["high"] = max(hourly[hour_key]["high"], float(tick.get("high", 0)))
                    hourly[hour_key]["low"] = min(hourly[hour_key]["low"], float(tick.get("low", 0)))
                    hourly[hour_key]["close"] = float(tick.get("close", tick.get("price", 0)))
                    hourly[hour_key]["volume"] += int(tick.get("volume", 0))
            except:
                continue

        return list(hourly.values())

    def search_symbol(self, query: str, max_results: int = 8) -> List[Dict[str, Any]]:
        """
        Search for symbols by name or ticker.
        Note: This is synchronous as it's a simple lookup.
        """
        import requests

        if self.use_mock:
            # Return the query as a result
            return [{"symbol": query.upper(), "name": query.upper(), "type": "stock"}]

        try:
            response = requests.get(
                f"{self.base_url}/markets/lookup",
                headers=self._get_headers(),
                params={"q": query}
            )
            response.raise_for_status()
            data = response.json()

            securities = data.get("securities", {}).get("security", [])
            if isinstance(securities, dict):
                securities = [securities]

            results = []
            for sec in securities[:max_results]:
                results.append({
                    "symbol": sec.get("symbol", ""),
                    "name": sec.get("description", ""),
                    "type": sec.get("type", "stock"),
                    "exchange": sec.get("exchange", "")
                })
            return results

        except Exception as e:
            print(f"Error searching symbols: {e}")
            # Fallback - return the query as a stock symbol
            return [{"symbol": query.upper(), "name": query.upper(), "type": "stock"}]

    async def get_options_expirations(self, symbol: str) -> List[date]:
        """Get available options expiration dates for a symbol."""
        if self.use_mock:
            # Return mock expirations
            today = date.today()
            expirations = []
            for i in range(1, 8):
                # Weekly expirations
                days_ahead = 7 * i - (today.weekday() - 4) % 7
                exp_date = today + timedelta(days=days_ahead)
                expirations.append(exp_date)
            return expirations[:6]

        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(
                    f"{self.base_url}/markets/options/expirations",
                    headers=self._get_headers(),
                    params={
                        "symbol": symbol,
                        "includeAllRoots": "true"  # Include 0DTE/weekly for SPX (SPXW)
                    }
                )
                response.raise_for_status()
                data = response.json()

                expirations = data.get("expirations", {})
                if expirations is None:
                    return []
                dates = expirations.get("date", [])
                if isinstance(dates, str):
                    dates = [dates]
                return [datetime.strptime(d, "%Y-%m-%d").date() for d in dates]

            except Exception as e:
                print(f"Error fetching expirations for {symbol}: {e}")
                return []

    async def get_options_chain(
        self,
        symbol: str,
        expiration: Optional[date] = None
    ) -> List[Dict[str, Any]]:
        """
        Get options chain for a symbol.

        If expiration is None, fetches all available expirations.
        """
        if self.use_mock:
            return []  # Mock data handled separately

        async with httpx.AsyncClient() as client:
            try:
                params = {
                    "symbol": symbol,
                    "greeks": "true"  # Include Greeks
                }
                if expiration:
                    params["expiration"] = expiration.isoformat()

                response = await client.get(
                    f"{self.base_url}/markets/options/chains",
                    headers=self._get_headers(),
                    params=params
                )
                response.raise_for_status()
                data = response.json()

                options = data.get("options", {}).get("option", [])
                if isinstance(options, dict):
                    options = [options]
                return options

            except Exception as e:
                print(f"Error fetching options chain for {symbol}: {e}")
                return []

    async def get_full_chain_with_greeks(
        self,
        symbol: str,
        max_expirations: int = 6
    ) -> tuple[float, List[OptionContract]]:
        """
        Get complete options chain with Greeks for GEX calculation.

        Returns: (spot_price, list of OptionContract)
        """
        if self.use_mock:
            return get_mock_options_chain(symbol)

        # Get spot price
        quote = await self.get_quote(symbol)
        if not quote:
            print(f"Could not get quote for {symbol}, using mock data")
            return get_mock_options_chain(symbol)

        spot_price = quote.get("last", 0)
        if spot_price == 0:
            spot_price = (quote.get("bid", 0) + quote.get("ask", 0)) / 2

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
            print(f"[{symbol}] Market closed - skipping today's expiration, showing {expirations[0] if expirations else 'N/A'}")

        expirations = expirations[:max_expirations]

        # Fetch chains for each expiration
        contracts: List[OptionContract] = []

        for exp in expirations:
            chain = await self.get_options_chain(symbol, exp)

            for opt in chain:
                try:
                    strike = float(opt.get("strike", 0))
                    exp_date = datetime.strptime(
                        opt.get("expiration_date", ""),
                        "%Y-%m-%d"
                    ).date()
                    opt_type = opt.get("option_type", "call").lower()

                    # Get Greeks from Tradier
                    greeks_data = opt.get("greeks", {}) or {}
                    gamma = float(greeks_data.get("gamma", 0) or 0)
                    delta = float(greeks_data.get("delta", 0) or 0)
                    vega = float(greeks_data.get("vega", 0) or 0)

                    # Get IV from Tradier (mid_iv is most accurate)
                    iv = float(greeks_data.get("mid_iv", 0) or greeks_data.get("smv_vol", 0) or 0)

                    # Calculate vanna using Black-Scholes (Tradier doesn't provide it)
                    vanna = 0.0
                    if iv > 0 and spot_price > 0:
                        try:
                            calculated_greeks = calculate_greeks(
                                spot=spot_price,
                                strike=strike,
                                expiration=exp_date,
                                iv=iv,
                                option_type=opt_type
                            )
                            vanna = calculated_greeks.vanna
                        except Exception:
                            # Fallback: approximate vanna from delta, gamma, IV
                            if gamma != 0 and iv > 0:
                                vanna = -delta * gamma / iv

                    contract = OptionContract(
                        strike=strike,
                        expiration=exp_date,
                        option_type=opt_type,
                        open_interest=int(opt.get("open_interest", 0) or 0),
                        gamma=gamma,
                        delta=delta,
                        vega=vega,
                        vanna=vanna,
                        volume=int(opt.get("volume", 0) or 0),
                        bid=float(opt.get("bid", 0) or 0),
                        ask=float(opt.get("ask", 0) or 0)
                    )
                    contracts.append(contract)
                except (ValueError, TypeError) as e:
                    print(f"Error parsing option: {e}")
                    continue

        if not contracts:
            print(f"No contracts fetched for {symbol}, using mock data")
            return get_mock_options_chain(symbol)

        return spot_price, contracts


# Singleton instance
_client: Optional[TradierClient] = None


def get_tradier_client(
    api_key: Optional[str] = None,
    paper_trading: Optional[bool] = None
) -> TradierClient:
    """Get or create Tradier client singleton."""
    global _client

    if _client is None or api_key is not None:
        _client = TradierClient(api_key, paper_trading)

    return _client


# Import timedelta that was missing
from datetime import timedelta
