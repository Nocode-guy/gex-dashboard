"""
Quiver Quant API Client

Alternative data: Congress trading, dark pool, insider trading, WSB sentiment, etc.
https://api.quiverquant.com/docs/
"""
import aiohttp
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import asyncio


class QuiverClient:
    """
    Client for Quiver Quant API.
    Provides congress trading, dark pool, insider trading, and social sentiment data.
    """

    BASE_URL = "https://api.quiverquant.com/beta"

    def __init__(self, api_token: str):
        self.api_token = api_token
        self.headers = {
            "Authorization": f"Bearer {api_token}",
            "Accept": "application/json"
        }
        print("[OK] Quiver Quant client initialized")

    async def _request(self, endpoint: str, params: dict = None) -> list:
        """Make an async API request."""
        url = f"{self.BASE_URL}{endpoint}"

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=self.headers, params=params) as response:
                if response.status == 200:
                    return await response.json()
                elif response.status == 401:
                    raise Exception("Invalid Quiver API token")
                elif response.status == 403:
                    raise Exception("Quiver API access forbidden - check tier")
                elif response.status == 429:
                    raise Exception("Quiver API rate limit exceeded")
                else:
                    text = await response.text()
                    raise Exception(f"Quiver API error {response.status}: {text}")

    # =========================================================================
    # CONGRESS TRADING
    # =========================================================================

    async def get_congress_trades(self, limit: int = 100) -> List[Dict]:
        """
        Get most recent Congress trades (House + Senate).

        Returns list of trades with: Representative, Transaction, Ticker, Amount, Date
        """
        try:
            data = await self._request("/live/congresstrading")

            trades = []
            for trade in data[:limit]:
                trades.append({
                    "representative": trade.get("Representative", "Unknown"),
                    "house": trade.get("House", ""),  # House or Senate
                    "transaction": trade.get("Transaction", ""),  # Purchase or Sale
                    "ticker": trade.get("Ticker", ""),
                    "amount": trade.get("Amount", ""),
                    "date": trade.get("TransactionDate", ""),
                    "report_date": trade.get("ReportDate", ""),
                    "district": trade.get("District", ""),
                    "party": trade.get("Party", "")
                })

            return trades
        except Exception as e:
            print(f"Error fetching Congress trades: {e}")
            return []

    async def get_congress_trades_for_ticker(self, ticker: str) -> List[Dict]:
        """Get Congress trades for a specific ticker."""
        try:
            data = await self._request(f"/historical/congresstrading/{ticker.upper()}")

            trades = []
            for trade in data:
                trades.append({
                    "representative": trade.get("Representative", "Unknown"),
                    "house": trade.get("House", ""),
                    "transaction": trade.get("Transaction", ""),
                    "ticker": ticker.upper(),
                    "amount": trade.get("Amount", ""),
                    "date": trade.get("TransactionDate", ""),
                    "party": trade.get("Party", "")
                })

            return trades
        except Exception as e:
            print(f"Error fetching Congress trades for {ticker}: {e}")
            return []

    # =========================================================================
    # DARK POOL / OFF-EXCHANGE
    # =========================================================================

    async def get_dark_pool_data(self, limit: int = 100) -> List[Dict]:
        """
        Get off-exchange (dark pool) activity.

        Returns: Ticker, Short Volume, Total Volume, Short % of Volume, DPI
        Note: API field names are OTC_Short, OTC_Total, DPI
        """
        try:
            data = await self._request("/live/offexchange")

            results = []
            for item in data[:limit]:
                # API uses OTC_Short and OTC_Total field names
                short_vol = item.get("OTC_Short", 0) or item.get("ShortVolume", 0)
                total_vol = item.get("OTC_Total", 0) or item.get("TotalVolume", 0)
                dpi = item.get("DPI", 0)  # Dark Pool Index
                short_pct = dpi * 100 if dpi else ((short_vol / total_vol * 100) if total_vol > 0 else 0)

                results.append({
                    "ticker": item.get("Ticker", ""),
                    "date": item.get("Date", ""),
                    "short_volume": short_vol,
                    "total_volume": total_vol,
                    "short_percent": round(short_pct, 2),
                    "dpi": round(dpi, 4) if dpi else 0
                })

            return results
        except Exception as e:
            print(f"Error fetching dark pool data: {e}")
            return []

    async def get_dark_pool_for_ticker(self, ticker: str, days: int = 30) -> List[Dict]:
        """Get historical dark pool data for a specific ticker."""
        try:
            data = await self._request(f"/historical/offexchange/{ticker.upper()}")

            results = []
            for item in data[:days]:
                short_vol = item.get("ShortVolume", 0)
                total_vol = item.get("TotalVolume", 0)
                short_pct = (short_vol / total_vol * 100) if total_vol > 0 else 0

                results.append({
                    "ticker": ticker.upper(),
                    "date": item.get("Date", ""),
                    "short_volume": short_vol,
                    "total_volume": total_vol,
                    "short_percent": round(short_pct, 2)
                })

            return results
        except Exception as e:
            print(f"Error fetching dark pool for {ticker}: {e}")
            return []

    # =========================================================================
    # INSIDER TRADING
    # =========================================================================

    async def get_insider_trades(self, limit: int = 100) -> List[Dict]:
        """
        Get most recent insider trades (Form 4 filings).

        Returns: Name, Ticker, Transaction Type, Shares, Price, Value, Date
        """
        try:
            data = await self._request("/live/insiders")

            trades = []
            for trade in data[:limit]:
                trades.append({
                    "name": trade.get("Name", "Unknown"),
                    "ticker": trade.get("Ticker", ""),
                    "title": trade.get("Title", ""),  # CEO, CFO, Director, etc.
                    "transaction_type": trade.get("TransactionType", ""),  # P-Purchase, S-Sale
                    "shares": trade.get("Shares", 0),
                    "price": trade.get("Price", 0),
                    "value": trade.get("Value", 0),
                    "date": trade.get("FilingDate", ""),
                    "owned_after": trade.get("OwnedAfter", 0)
                })

            return trades
        except Exception as e:
            print(f"Error fetching insider trades: {e}")
            return []

    # =========================================================================
    # WSB / REDDIT SENTIMENT
    # =========================================================================

    async def get_wsb_mentions(self, limit: int = 50) -> List[Dict]:
        """
        Get Wall Street Bets ticker mentions and sentiment.
        """
        try:
            data = await self._request("/live/wsbcomments")

            # Aggregate by ticker
            ticker_counts = {}
            for comment in data:
                ticker = comment.get("Ticker", "")
                if ticker:
                    if ticker not in ticker_counts:
                        ticker_counts[ticker] = {
                            "ticker": ticker,
                            "mentions": 0,
                            "sentiment_sum": 0,
                            "comments": []
                        }
                    ticker_counts[ticker]["mentions"] += 1
                    ticker_counts[ticker]["sentiment_sum"] += comment.get("Sentiment", 0)
                    if len(ticker_counts[ticker]["comments"]) < 3:
                        ticker_counts[ticker]["comments"].append(comment.get("Body", "")[:100])

            # Calculate average sentiment and sort by mentions
            results = []
            for ticker, data in ticker_counts.items():
                avg_sentiment = data["sentiment_sum"] / data["mentions"] if data["mentions"] > 0 else 0
                results.append({
                    "ticker": ticker,
                    "mentions": data["mentions"],
                    "sentiment": round(avg_sentiment, 3),
                    "sentiment_label": "Bullish" if avg_sentiment > 0.1 else "Bearish" if avg_sentiment < -0.1 else "Neutral",
                    "sample_comments": data["comments"]
                })

            results.sort(key=lambda x: x["mentions"], reverse=True)
            return results[:limit]
        except Exception as e:
            print(f"Error fetching WSB mentions: {e}")
            return []

    # =========================================================================
    # GOVERNMENT CONTRACTS
    # =========================================================================

    async def get_gov_contracts(self, limit: int = 50) -> List[Dict]:
        """Get recently announced government contracts."""
        try:
            data = await self._request("/live/govcontractsall")

            contracts = []
            for item in data[:limit]:
                contracts.append({
                    "ticker": item.get("Ticker", ""),
                    "agency": item.get("Agency", ""),
                    "amount": item.get("Amount", 0),
                    "description": item.get("Description", "")[:200],
                    "date": item.get("Date", "")
                })

            return contracts
        except Exception as e:
            print(f"Error fetching gov contracts: {e}")
            return []

    # =========================================================================
    # 13F HEDGE FUND HOLDINGS
    # =========================================================================

    async def get_13f_changes(self, limit: int = 50) -> List[Dict]:
        """Get recent changes in hedge fund holdings (13F filings)."""
        try:
            data = await self._request("/live/sec13fchanges")

            changes = []
            for item in data[:limit]:
                changes.append({
                    "fund": item.get("Fund", ""),
                    "ticker": item.get("Ticker", ""),
                    "change_type": item.get("ChangeType", ""),  # New, Increased, Decreased, Sold
                    "shares": item.get("Shares", 0),
                    "value": item.get("Value", 0),
                    "change_percent": item.get("ChangePercent", 0),
                    "date": item.get("FilingDate", "")
                })

            return changes
        except Exception as e:
            print(f"Error fetching 13F changes: {e}")
            return []

    # =========================================================================
    # WATCHLIST ALERTS - Check if any watchlist symbols have activity
    # =========================================================================

    async def check_watchlist_alerts(self, watchlist: List[str]) -> Dict:
        """
        Check all data sources for activity on watchlist symbols.
        Returns categorized alerts for the notification bell.
        """
        watchlist_upper = [s.upper() for s in watchlist]
        alerts = {
            "congress": [],
            "insider": [],
            "dark_pool": [],
            "wsb": []
        }

        try:
            # Check Congress trades
            congress = await self.get_congress_trades(200)
            for trade in congress:
                if trade["ticker"] in watchlist_upper:
                    alerts["congress"].append({
                        "type": "congress",
                        "icon": "🏛️",
                        "title": f"Congress: {trade['representative']}",
                        "message": f"{trade['transaction']} {trade['ticker']} - {trade['amount']}",
                        "date": trade["date"],
                        "ticker": trade["ticker"],
                        "party": trade["party"]
                    })

            # Check Insider trades
            insiders = await self.get_insider_trades(200)
            for trade in insiders:
                if trade["ticker"] in watchlist_upper:
                    action = "bought" if trade["transaction_type"] == "P" else "sold"
                    alerts["insider"].append({
                        "type": "insider",
                        "icon": "👔",
                        "title": f"Insider: {trade['name']}",
                        "message": f"{trade['title']} {action} ${trade['value']:,.0f} of {trade['ticker']}",
                        "date": trade["date"],
                        "ticker": trade["ticker"]
                    })

            # Check Dark Pool unusual activity
            dark_pool = await self.get_dark_pool_data(500)
            for dp in dark_pool:
                if dp["ticker"] in watchlist_upper and dp["short_percent"] > 50:
                    alerts["dark_pool"].append({
                        "type": "dark_pool",
                        "icon": "🌑",
                        "title": f"Dark Pool: {dp['ticker']}",
                        "message": f"High short volume: {dp['short_percent']:.1f}% ({dp['short_volume']:,} shares)",
                        "date": dp["date"],
                        "ticker": dp["ticker"]
                    })

            # Check WSB buzz
            wsb = await self.get_wsb_mentions(100)
            for item in wsb:
                if item["ticker"] in watchlist_upper and item["mentions"] >= 5:
                    alerts["wsb"].append({
                        "type": "wsb",
                        "icon": "🦍",
                        "title": f"WSB Buzz: {item['ticker']}",
                        "message": f"{item['mentions']} mentions - {item['sentiment_label']}",
                        "date": datetime.now().strftime("%Y-%m-%d"),
                        "ticker": item["ticker"]
                    })

        except Exception as e:
            print(f"Error checking watchlist alerts: {e}")

        return alerts


# Singleton instance
_quiver_client: Optional[QuiverClient] = None

# API Token
QUIVER_API_TOKEN = "fcdea37de8cb07711a5938e8d28413b9032d912c"


def get_quiver_client() -> QuiverClient:
    """Get or create Quiver Quant client singleton."""
    global _quiver_client

    if _quiver_client is None:
        import os
        token = os.environ.get("QUIVER_API_TOKEN", QUIVER_API_TOKEN)
        _quiver_client = QuiverClient(token)

    return _quiver_client


# Test
if __name__ == "__main__":
    async def test():
        client = get_quiver_client()

        print("\n=== Congress Trades ===")
        congress = await client.get_congress_trades(5)
        for t in congress:
            print(f"  {t['representative']} ({t['party']}): {t['transaction']} {t['ticker']} - {t['amount']}")

        print("\n=== Dark Pool ===")
        dp = await client.get_dark_pool_data(5)
        for d in dp:
            print(f"  {d['ticker']}: {d['short_percent']:.1f}% short ({d['total_volume']:,} total)")

        print("\n=== Insider Trades ===")
        insiders = await client.get_insider_trades(5)
        for t in insiders:
            print(f"  {t['name']} ({t['title']}): {t['transaction_type']} {t['ticker']} ${t['value']:,.0f}")

        print("\n=== WSB Mentions ===")
        wsb = await client.get_wsb_mentions(5)
        for w in wsb:
            print(f"  {w['ticker']}: {w['mentions']} mentions ({w['sentiment_label']})")

    asyncio.run(test())
