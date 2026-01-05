# Flow Service - WAVE Indicator and Trade Tape Management
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional
from dataclasses import dataclass, field
from collections import defaultdict, deque

# Popular liquid symbols to track for the leaderboard (top movers across the market)
POPULAR_SYMBOLS = [
    'SPY', 'QQQ', 'IWM', 'DIA',           # Major ETFs
    'AAPL', 'MSFT', 'GOOGL', 'AMZN',      # Mega caps
    'META', 'NVDA', 'TSLA', 'AMD',        # Tech
    'JPM', 'BAC', 'GS', 'V',              # Financials
    'XOM', 'CVX',                          # Energy
    'COIN', 'MSTR',                        # Crypto-related
    'SPX', 'NDX'                           # Indices
]

# Try to import PostgreSQL functions, fall back to in-memory storage
try:
    from db_postgres import (
        save_wave_data, get_wave_history, get_latest_wave,
        save_flow_trade, get_recent_trades,
        update_leaderboard, get_leaderboard,
        cleanup_old_wave_data, cleanup_old_trades
    )
    HAS_POSTGRES = True
except ImportError:
    HAS_POSTGRES = False
    print("[FlowService] PostgreSQL not available - using in-memory storage")


@dataclass
class WaveAccumulator:
    """Tracks cumulative call/put premium for a symbol"""
    symbol: str
    cumulative_call: float = 0.0
    cumulative_put: float = 0.0
    last_call_premium: float = 0.0
    last_put_premium: float = 0.0
    last_update: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def wave_value(self) -> float:
        """Net WAVE value (call - put)"""
        return self.cumulative_call - self.cumulative_put

    @property
    def wave_pct(self) -> float:
        """WAVE as percentage (-100 to +100)"""
        total = self.cumulative_call + self.cumulative_put
        if total == 0:
            return 0.0
        return ((self.cumulative_call - self.cumulative_put) / total) * 100

    def update(self, call_premium: float, put_premium: float):
        """Update with new premium values"""
        # Calculate delta from last update
        call_delta = call_premium - self.last_call_premium
        put_delta = put_premium - self.last_put_premium

        # Only add positive deltas (premium increases)
        if call_delta > 0:
            self.cumulative_call += call_delta
        if put_delta > 0:
            self.cumulative_put += put_delta

        self.last_call_premium = call_premium
        self.last_put_premium = put_premium
        self.last_update = datetime.now(timezone.utc)

    def reset_daily(self):
        """Reset accumulators for new trading day"""
        self.cumulative_call = 0.0
        self.cumulative_put = 0.0
        self.last_call_premium = 0.0
        self.last_put_premium = 0.0


class FlowService:
    """
    Service for managing WAVE indicator and trade tape data.
    Runs background tasks to collect and store flow data.
    """

    def __init__(self):
        self.wave_accumulators: Dict[str, WaveAccumulator] = {}
        self.running = False
        self._snapshot_task = None
        self._cleanup_task = None
        self._last_market_date: Optional[datetime] = None

        # In-memory storage for when PostgreSQL is not available
        self._wave_history: Dict[str, deque] = defaultdict(lambda: deque(maxlen=500))
        self._trade_history: deque = deque(maxlen=1000)

    def get_accumulator(self, symbol: str) -> WaveAccumulator:
        """Get or create wave accumulator for symbol"""
        if symbol not in self.wave_accumulators:
            self.wave_accumulators[symbol] = WaveAccumulator(symbol=symbol)
        return self.wave_accumulators[symbol]

    async def update_wave(self, symbol: str, call_premium: float, put_premium: float):
        """Update WAVE data for a symbol"""
        acc = self.get_accumulator(symbol)

        # Check if we need to reset for new day
        now = datetime.now(timezone.utc)
        if self._last_market_date and now.date() != self._last_market_date.date():
            # New trading day - reset all accumulators
            for a in self.wave_accumulators.values():
                a.reset_daily()
            self._last_market_date = now

        acc.update(call_premium, put_premium)

    async def get_wave_data(self, symbol: str, minutes: int = 60) -> dict:
        """Get WAVE data for charting"""
        # Get historical data from database or in-memory
        if HAS_POSTGRES:
            history = await get_wave_history(symbol, minutes)
        else:
            # Use in-memory history
            cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
            history = [
                h for h in self._wave_history.get(symbol, [])
                if h['timestamp'] > cutoff
            ]

        # Get current accumulator state
        acc = self.wave_accumulators.get(symbol)
        current = None
        if acc:
            current = {
                'cumulative_call': acc.cumulative_call,
                'cumulative_put': acc.cumulative_put,
                'wave_value': acc.wave_value,
                'wave_pct': acc.wave_pct,
                'last_update': acc.last_update.isoformat()
            }

        return {
            'symbol': symbol,
            'wave_history': history,
            'current_wave': current,
            'minutes': minutes
        }

    async def process_flow_summary(self, symbol: str, flow_data: dict):
        """Process flow summary and update WAVE + store trades"""
        if not flow_data:
            return

        call_premium = flow_data.get('total_call_premium', 0) or 0
        put_premium = flow_data.get('total_put_premium', 0) or 0

        # Update WAVE accumulator
        await self.update_wave(symbol, call_premium, put_premium)

        # Store large trades from flow data
        recent_trades = flow_data.get('recent_trades', [])
        for trade in recent_trades:
            if trade.get('premium', 0) >= 10000:  # Only store trades >= $10K
                trade_record = {
                    'symbol': symbol,
                    'strike': trade.get('strike', 0),
                    'expiration': trade.get('expiration', datetime.now().date()),
                    'contract_type': trade.get('contract_type', 'call'),
                    'trade_type': trade.get('trade_type', 'normal'),
                    'size': trade.get('size', 0),
                    'premium': trade.get('premium', 0),
                    'sentiment': trade.get('sentiment'),
                    'timestamp': datetime.now(timezone.utc)
                }
                if HAS_POSTGRES:
                    await save_flow_trade(trade_record)
                else:
                    self._trade_history.append(trade_record)

    async def save_snapshot(self, symbol: str):
        """Save current WAVE state to database or in-memory"""
        acc = self.wave_accumulators.get(symbol)
        if not acc:
            return

        now = datetime.now(timezone.utc)
        # Round to nearest minute for consistent time series
        rounded = now.replace(second=0, microsecond=0)

        snapshot = {
            'symbol': symbol,
            'timestamp': rounded,
            'cumulative_call': acc.cumulative_call,
            'cumulative_put': acc.cumulative_put,
            'wave_value': acc.wave_value,
            'call_premium': acc.last_call_premium,
            'put_premium': acc.last_put_premium
        }

        if HAS_POSTGRES:
            await save_wave_data(
                symbol=symbol,
                timestamp=rounded,
                cumulative_call=acc.cumulative_call,
                cumulative_put=acc.cumulative_put,
                call_premium=acc.last_call_premium,
                put_premium=acc.last_put_premium
            )
        else:
            self._wave_history[symbol].append(snapshot)

    async def start(self):
        """Start background tasks"""
        if self.running:
            return

        self.running = True
        self._last_market_date = datetime.now(timezone.utc)
        self._snapshot_task = asyncio.create_task(self._snapshot_loop())
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        self._popular_symbols_task = asyncio.create_task(self._refresh_popular_symbols_loop())
        print("[FlowService] Started background tasks")

    async def stop(self):
        """Stop background tasks"""
        self.running = False
        if self._snapshot_task:
            self._snapshot_task.cancel()
        if self._cleanup_task:
            self._cleanup_task.cancel()
        if hasattr(self, '_popular_symbols_task') and self._popular_symbols_task:
            self._popular_symbols_task.cancel()
        print("[FlowService] Stopped background tasks")

    async def _snapshot_loop(self):
        """Save WAVE snapshots frequently for real-time updates"""
        while self.running:
            try:
                for symbol in list(self.wave_accumulators.keys()):
                    await self.save_snapshot(symbol)

                # Wait 10 seconds for more real-time updates
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[FlowService] Snapshot error: {e}")
                await asyncio.sleep(10)

    async def _cleanup_loop(self):
        """Clean up old data daily (only when PostgreSQL is available)"""
        if not HAS_POSTGRES:
            # In-memory storage uses deques with maxlen, auto-cleans
            return

        while self.running:
            try:
                # Run cleanup at 4am UTC
                now = datetime.now(timezone.utc)
                next_cleanup = now.replace(hour=4, minute=0, second=0, microsecond=0)
                if now >= next_cleanup:
                    next_cleanup += timedelta(days=1)

                wait_seconds = (next_cleanup - now).total_seconds()
                await asyncio.sleep(wait_seconds)

                # Perform cleanup
                wave_deleted = await cleanup_old_wave_data(days=7)
                trades_deleted = await cleanup_old_trades(days=7)
                print(f"[FlowService] Cleanup: removed {wave_deleted} wave records, {trades_deleted} trades")

            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[FlowService] Cleanup error: {e}")
                await asyncio.sleep(3600)

    async def _refresh_popular_symbols_loop(self):
        """Periodically refresh flow data for popular liquid symbols (for leaderboard)"""
        # Import here to avoid circular imports
        try:
            from massive_client import get_massive_client
            has_massive = True
        except ImportError:
            has_massive = False
            print("[FlowService] Massive client not available - popular symbols refresh disabled")
            return

        # Short delay on startup to let other services initialize
        await asyncio.sleep(2)

        while self.running:
            try:
                client = get_massive_client()
                refreshed_count = 0

                for symbol in POPULAR_SYMBOLS:
                    try:
                        # Fetch flow summary for this symbol
                        flow_summary = await client.get_flow_summary(symbol=symbol, spot_price=0)
                        await self.process_flow_summary(symbol, flow_summary.to_dict())
                        refreshed_count += 1
                        # Small delay between symbols to avoid rate limits
                        await asyncio.sleep(0.5)
                    except Exception as sym_err:
                        # Individual symbol failure shouldn't stop the loop
                        pass

                if refreshed_count > 0:
                    print(f"[FlowService] Refreshed flow for {refreshed_count}/{len(POPULAR_SYMBOLS)} popular symbols")

                # Wait 2 minutes before next refresh cycle
                await asyncio.sleep(120)

            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[FlowService] Popular symbols refresh error: {e}")
                await asyncio.sleep(120)


    async def get_recent_trades(self, symbol: Optional[str] = None, min_premium: int = 10000, limit: int = 50) -> List[dict]:
        """Get recent trades from database or in-memory"""
        if HAS_POSTGRES:
            return await get_recent_trades(symbol=symbol, min_premium=min_premium, limit=limit)
        else:
            # Filter in-memory trades
            trades = list(self._trade_history)
            if symbol:
                trades = [t for t in trades if t['symbol'] == symbol]
            trades = [t for t in trades if t.get('premium', 0) >= min_premium]
            # Sort by timestamp descending and limit
            trades.sort(key=lambda x: x['timestamp'], reverse=True)
            return trades[:limit]

    async def get_leaderboard(self, limit: int = 20, market_only: bool = True) -> List[dict]:
        """Get flow leaderboard from database or calculate from in-memory

        Args:
            limit: Max symbols to return
            market_only: If True, only return POPULAR_SYMBOLS (market-wide top movers)
                        If False, return all symbols including user's watchlist
        """
        if HAS_POSTGRES:
            return await get_leaderboard(limit=limit)
        else:
            # Calculate from current accumulators
            leaderboard = []
            for symbol, acc in self.wave_accumulators.items():
                # Filter to only market-wide popular symbols if requested
                if market_only and symbol not in POPULAR_SYMBOLS:
                    continue

                total = acc.cumulative_call + acc.cumulative_put
                if total > 0:
                    leaderboard.append({
                        'symbol': symbol,
                        'total_premium': total,
                        'net_premium': acc.wave_value,
                        'wave_pct': acc.wave_pct,
                        'sentiment': 'bullish' if acc.wave_pct > 10 else ('bearish' if acc.wave_pct < -10 else 'neutral'),
                        'last_update': acc.last_update.isoformat()
                    })
            # Sort by absolute net premium (most bullish or bearish activity)
            leaderboard.sort(key=lambda x: abs(x['net_premium']), reverse=True)
            return leaderboard[:limit]


# Singleton instance
_flow_service: Optional[FlowService] = None


def get_flow_service() -> FlowService:
    """Get the flow service singleton"""
    global _flow_service
    if _flow_service is None:
        _flow_service = FlowService()
    return _flow_service


async def init_flow_service() -> FlowService:
    """Initialize and start the flow service"""
    service = get_flow_service()
    await service.start()
    return service
