"""
Yahoo Finance Options Client

Free options chain data with no API key required.
Uses yfinance library for reliable data fetching.
"""
import sys
import asyncio
from datetime import date, datetime
from typing import List, Optional, Tuple
import math

# Fix Windows console encoding issues
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, OSError):
        pass  # Already reconfigured or not supported

try:
    import yfinance as yf
except ImportError:
    yf = None
    print("yfinance not installed. Run: pip install yfinance")

from gex_calculator import OptionContract


class YahooFinanceClient:
    """
    Client for Yahoo Finance options data.
    No API key required - completely free.
    """

    def __init__(self):
        if yf is None:
            raise ImportError("yfinance is required. Install with: pip install yfinance")

        print("[OK] Yahoo Finance client initialized (FREE - no API key needed)")

    def search_symbol(self, query: str, max_results: int = 5) -> List[dict]:
        """
        Search for symbols by name or partial ticker.
        Returns list of {symbol, name, type} matches.
        """
        try:
            import requests

            # Use Yahoo Finance search API
            url = "https://query2.finance.yahoo.com/v1/finance/search"
            params = {
                "q": query,
                "quotesCount": max_results,
                "newsCount": 0,
                "enableFuzzyQuery": True,
                "quotesQueryId": "tss_match_phrase_query"
            }
            headers = {"User-Agent": "Mozilla/5.0"}

            response = requests.get(url, params=params, headers=headers, timeout=5)
            data = response.json()

            results = []
            for quote in data.get("quotes", []):
                # Only include stocks and ETFs (skip crypto, futures, etc.)
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

    def get_quote(self, symbol: str) -> Optional[dict]:
        """Get current quote for a symbol."""
        try:
            ticker = yf.Ticker(symbol)
            info = ticker.info

            # Get current price
            price = info.get('regularMarketPrice') or info.get('currentPrice') or info.get('previousClose', 0)

            return {
                "symbol": symbol,
                "last": price,
                "bid": info.get('bid', price),
                "ask": info.get('ask', price),
                "volume": info.get('volume', 0),
                "name": info.get('shortName', symbol)
            }
        except Exception as e:
            print(f"Error fetching quote for {symbol}: {e}")
            return None

    def get_options_expirations(self, symbol: str) -> List[date]:
        """Get available options expiration dates."""
        try:
            ticker = yf.Ticker(symbol)
            expirations = ticker.options  # Returns tuple of date strings

            return [datetime.strptime(exp, "%Y-%m-%d").date() for exp in expirations]
        except Exception as e:
            print(f"Error fetching expirations for {symbol}: {e}")
            return []

    def get_options_chain(self, symbol: str, expiration: date, spot_price: float) -> List[OptionContract]:
        """Get options chain for a specific expiration."""
        contracts = []

        try:
            ticker = yf.Ticker(symbol)
            opt_chain = ticker.option_chain(expiration.strftime("%Y-%m-%d"))

            # Process calls
            for _, row in opt_chain.calls.iterrows():
                contract = self._parse_option_row(row, expiration, 'call', spot_price)
                if contract:
                    contracts.append(contract)

            # Process puts
            for _, row in opt_chain.puts.iterrows():
                contract = self._parse_option_row(row, expiration, 'put', spot_price)
                if contract:
                    contracts.append(contract)

        except Exception as e:
            print(f"Error fetching options chain for {symbol} {expiration}: {e}")

        return contracts

    def _parse_option_row(self, row, expiration: date, option_type: str, spot_price: float) -> Optional[OptionContract]:
        """Parse a single option row from yfinance."""
        try:
            strike = float(row.get('strike', 0))
            oi = int(row.get('openInterest', 0) or 0)
            volume = int(row.get('volume', 0) or 0)

            # yfinance doesn't provide Greeks directly, so we estimate them
            iv = float(row.get('impliedVolatility', 0.3) or 0.3)

            # Skip options with no open interest
            if oi == 0:
                return None

            # Estimate delta, gamma, and vanna using Black-Scholes approximation
            delta, gamma, vanna = self._estimate_greeks(strike, spot_price, iv, expiration, option_type)

            return OptionContract(
                strike=strike,
                expiration=expiration,
                option_type=option_type,
                open_interest=oi,
                gamma=gamma,
                delta=delta,
                vega=0.0,
                vanna=vanna,
                volume=volume,
                bid=float(row.get('bid', 0) or 0),
                ask=float(row.get('ask', 0) or 0)
            )
        except Exception as e:
            return None

    def _estimate_greeks(self, strike: float, spot: float, iv: float, expiration: date, option_type: str = 'call') -> tuple:
        """
        Estimate delta, gamma, and vanna using Black-Scholes approximation.

        Returns: (delta, gamma, vanna)

        Delta (call) = N(d1), Delta (put) = N(d1) - 1
        Gamma = N'(d1) / (S * sigma * sqrt(T))
        Vanna = -d2 * N'(d1) / sigma

        Key insight: Gamma peaks ATM, Vanna is positive OTM and negative ITM for calls.
        """
        if spot <= 0 or strike <= 0:
            return 0.0, 0.0, 0.0

        # Days to expiration
        dte = max(1, (expiration - date.today()).days)
        T = dte / 365.0  # Time in years

        # Ensure reasonable IV
        sigma = max(0.1, min(2.0, iv))

        # Calculate d1, d2 for Black-Scholes
        # d1 = [ln(S/K) + (r + σ²/2)T] / (σ√T)
        # d2 = d1 - σ√T
        r = 0.05  # Risk-free rate

        try:
            sqrt_T = math.sqrt(T)
            d1 = (math.log(spot / strike) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqrt_T)
            d2 = d1 - sigma * sqrt_T

            # N(d1) - cumulative normal distribution for delta
            # Using error function approximation: N(x) = 0.5 * (1 + erf(x / sqrt(2)))
            def norm_cdf(x):
                return 0.5 * (1 + math.erf(x / math.sqrt(2)))

            # N'(d1) = (1/sqrt(2*pi)) * e^(-d1^2/2) - standard normal PDF
            n_prime_d1 = math.exp(-0.5 * d1 * d1) / math.sqrt(2 * math.pi)

            # Delta: N(d1) for calls, N(d1) - 1 for puts
            if option_type == 'call':
                delta = norm_cdf(d1)
            else:
                delta = norm_cdf(d1) - 1

            # Gamma = N'(d1) / (S * sigma * sqrt(T))
            gamma = n_prime_d1 / (spot * sigma * sqrt_T)

            # Vanna = -d2 * N'(d1) / sigma
            # Vanna shows how delta changes when IV changes
            vanna = -d2 * n_prime_d1 / sigma

            return delta, gamma, vanna

        except (ValueError, ZeroDivisionError):
            return 0.0, 0.0, 0.0

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
        quote = self.get_quote(symbol)
        if not quote:
            print(f"Could not get quote for {symbol}")
            return 0, []

        spot_price = quote.get("last", 0)
        if spot_price == 0:
            return 0, []

        # Get expirations
        expirations = self.get_options_expirations(symbol)
        expirations = expirations[:max_expirations]

        if not expirations:
            print(f"No expirations found for {symbol}")
            return spot_price, []

        # Fetch chains for each expiration
        all_contracts = []

        for exp in expirations:
            contracts = self.get_options_chain(symbol, exp, spot_price)
            all_contracts.extend(contracts)
            print(f"  {symbol} {exp}: {len(contracts)} contracts")

        print(f"[Yahoo] Total: {len(all_contracts)} contracts for {symbol}")

        return spot_price, all_contracts


# Singleton instance
_yahoo_client: Optional[YahooFinanceClient] = None


def get_yahoo_client() -> YahooFinanceClient:
    """Get or create Yahoo Finance client singleton."""
    global _yahoo_client

    if _yahoo_client is None:
        _yahoo_client = YahooFinanceClient()

    return _yahoo_client


# Test
if __name__ == "__main__":
    import asyncio

    async def test():
        client = get_yahoo_client()

        # Test with SPY (liquid options)
        symbol = "SPY"
        print(f"\nTesting {symbol}...")

        spot, contracts = await client.get_full_chain_with_greeks(symbol)

        print(f"\nSpot: ${spot:.2f}")
        print(f"Total contracts: {len(contracts)}")

        if contracts:
            # Show sample
            print("\nSample contracts:")
            for c in contracts[:5]:
                print(f"  {c.option_type.upper()} {c.strike} exp:{c.expiration} OI:{c.open_interest} gamma:{c.gamma:.4f}")

    asyncio.run(test())
