"""
Polygon WebSocket client for real-time options trades.
Connects to Polygon's options WebSocket and streams trades to the frontend via SSE.
"""
import asyncio
import json
import os
from datetime import datetime
from typing import Dict, List, Optional, Set, Callable
from dataclasses import dataclass, field
from collections import deque
import websockets
from websockets.exceptions import ConnectionClosed

# Use same API key as Massive client
MASSIVE_API_KEY = os.environ.get("MASSIVE_API_KEY", os.environ.get("POLYGON_API_KEY", ""))

# Massive WebSocket endpoints (built on Polygon)
MASSIVE_OPTIONS_WS = "wss://socket.massive.com/options"
MASSIVE_OPTIONS_WS_DELAYED = "wss://delayed.massive.com/options"  # 15-min delayed data


@dataclass
class OptionsTrade:
    """Represents a single options trade."""
    symbol: str  # Underlying symbol (e.g., SPY)
    contract: str  # Full contract symbol (e.g., O:SPY251219C00500000)
    strike: float
    expiration: str
    contract_type: str  # 'call' or 'put'
    price: float
    size: int
    premium: float  # price * size * 100
    timestamp: datetime
    exchange: int
    conditions: List[int] = field(default_factory=list)

    # Derived fields
    trade_type: str = "normal"  # 'sweep', 'block', or 'normal'

    def __post_init__(self):
        # Classify trade type
        if self.size >= 100:
            self.trade_type = "block"
        # Sweeps are identified by exchange patterns (multiple exchanges in quick succession)
        # This is a simplified heuristic

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "contract": self.contract,
            "strike": self.strike,
            "expiration": self.expiration,
            "contract_type": self.contract_type,
            "price": self.price,
            "contracts": self.size,
            "premium": self.premium,
            "timestamp": self.timestamp.isoformat(),
            "trade_type": self.trade_type,
            "exchange": self.exchange,
        }


def parse_contract_symbol(contract: str) -> Optional[dict]:
    """
    Parse Polygon options contract symbol.
    Format: O:SPY251219C00500000
    - O: prefix
    - SPY: underlying
    - 251219: expiration (YYMMDD)
    - C/P: call/put
    - 00500000: strike * 1000 (8 digits)
    """
    try:
        if not contract.startswith("O:"):
            return None

        rest = contract[2:]  # Remove O: prefix

        # Find where the date starts (6 digits before C/P)
        # The underlying can be 1-5 characters
        for i in range(1, 6):
            if rest[i:i+6].isdigit():
                underlying = rest[:i]
                date_str = rest[i:i+6]
                option_type = rest[i+6]
                strike_str = rest[i+7:]
                break
        else:
            return None

        # Parse expiration
        year = 2000 + int(date_str[:2])
        month = int(date_str[2:4])
        day = int(date_str[4:6])
        expiration = f"{year}-{month:02d}-{day:02d}"

        # Parse strike (divide by 1000)
        strike = int(strike_str) / 1000

        return {
            "underlying": underlying,
            "expiration": expiration,
            "contract_type": "call" if option_type == "C" else "put",
            "strike": strike,
        }
    except Exception:
        return None


class MassiveOptionsWSClient:
    """
    WebSocket client for Massive/Polygon options trades.

    Usage:
        client = MassiveOptionsWSClient(api_key)
        await client.connect()
        await client.subscribe(["SPY", "QQQ", "TSLA"])

        # Trades are stored in client.trades[symbol]
        # Or register a callback: client.on_trade = my_callback
    """

    def __init__(self, api_key: str = None, use_delayed: bool = True):
        self.api_key = api_key or MASSIVE_API_KEY
        self.use_delayed = use_delayed
        self.ws_url = MASSIVE_OPTIONS_WS_DELAYED if use_delayed else MASSIVE_OPTIONS_WS

        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._connected = False
        self._authenticated = False
        self._subscribed_symbols: Set[str] = set()
        self._running = False
        self._task: Optional[asyncio.Task] = None

        # Trade storage - last N trades per symbol
        self.max_trades_per_symbol = 100
        self.trades: Dict[str, deque] = {}

        # Callbacks
        self.on_trade: Optional[Callable[[OptionsTrade], None]] = None
        self.on_connect: Optional[Callable[[], None]] = None
        self.on_disconnect: Optional[Callable[[], None]] = None

        # Stats
        self.total_trades_received = 0
        self.last_trade_time: Optional[datetime] = None

    @property
    def connected(self) -> bool:
        return self._connected and self._authenticated

    async def connect(self) -> bool:
        """Connect and authenticate to Polygon WebSocket."""
        try:
            print(f"[Polygon WS] Connecting to {self.ws_url}...")
            self._ws = await websockets.connect(
                self.ws_url,
                ping_interval=30,
                ping_timeout=10,
                close_timeout=5,
            )
            self._connected = True

            # Wait for connection message
            msg = await self._ws.recv()
            data = json.loads(msg)
            if data[0].get("status") == "connected":
                print("[Polygon WS] Connected, authenticating...")

            # Authenticate - Massive/Polygon uses API key directly as params
            auth_msg = {"action": "auth", "params": self.api_key}
            await self._ws.send(json.dumps(auth_msg))

            msg = await self._ws.recv()
            data = json.loads(msg)
            if data[0].get("status") == "auth_success":
                print("[Polygon WS] Authenticated successfully")
                self._authenticated = True
                if self.on_connect:
                    self.on_connect()
                return True
            else:
                error_msg = data[0].get("message", "unknown error")
                print(f"[Polygon WS] Auth failed: {error_msg}")
                print(f"[Polygon WS] Make sure your API key has WebSocket access")
                # Close connection but don't crash
                await self._ws.close()
                self._ws = None
                self._connected = False
                return False

        except Exception as e:
            print(f"[Polygon WS] Connection error: {e}")
            self._connected = False
            return False

    async def subscribe(self, symbols: List[str]) -> bool:
        """Subscribe to options trades for given underlying symbols."""
        if not self.connected:
            print("[Polygon WS] Not connected, cannot subscribe")
            return False

        try:
            # Subscribe to all options for these underlyings
            # Format: O.* for all options, or O:SPY* for specific underlying
            params = ",".join([f"O:{sym}*" for sym in symbols])
            sub_msg = {"action": "subscribe", "params": params}
            await self._ws.send(json.dumps(sub_msg))

            self._subscribed_symbols.update(symbols)
            print(f"[Polygon WS] Subscribed to: {symbols}")

            # Initialize trade storage
            for sym in symbols:
                if sym not in self.trades:
                    self.trades[sym] = deque(maxlen=self.max_trades_per_symbol)

            return True
        except Exception as e:
            print(f"[Polygon WS] Subscribe error: {e}")
            return False

    async def unsubscribe(self, symbols: List[str]) -> bool:
        """Unsubscribe from options trades."""
        if not self.connected:
            return False

        try:
            params = ",".join([f"O:{sym}*" for sym in symbols])
            unsub_msg = {"action": "unsubscribe", "params": params}
            await self._ws.send(json.dumps(unsub_msg))

            self._subscribed_symbols -= set(symbols)
            print(f"[Polygon WS] Unsubscribed from: {symbols}")
            return True
        except Exception as e:
            print(f"[Polygon WS] Unsubscribe error: {e}")
            return False

    def _parse_trade(self, msg: dict) -> Optional[OptionsTrade]:
        """Parse a trade message from Polygon."""
        try:
            # Message format for options trades:
            # {"ev": "T", "sym": "O:SPY251219C00500000", "p": 1.23, "s": 10, ...}
            if msg.get("ev") != "T":
                return None

            contract = msg.get("sym", "")
            parsed = parse_contract_symbol(contract)
            if not parsed:
                return None

            price = msg.get("p", 0)
            size = msg.get("s", 0)

            # Timestamp is in nanoseconds
            ts_ns = msg.get("t", 0)
            timestamp = datetime.fromtimestamp(ts_ns / 1e9) if ts_ns else datetime.now()

            trade = OptionsTrade(
                symbol=parsed["underlying"],
                contract=contract,
                strike=parsed["strike"],
                expiration=parsed["expiration"],
                contract_type=parsed["contract_type"],
                price=price,
                size=size,
                premium=price * size * 100,
                timestamp=timestamp,
                exchange=msg.get("x", 0),
                conditions=msg.get("c", []),
            )

            return trade
        except Exception as e:
            print(f"[Polygon WS] Parse error: {e}")
            return None

    async def _message_loop(self):
        """Main message processing loop."""
        while self._running and self._ws:
            try:
                msg = await asyncio.wait_for(self._ws.recv(), timeout=60)
                data = json.loads(msg)

                # Handle array of messages
                if isinstance(data, list):
                    for item in data:
                        await self._handle_message(item)
                else:
                    await self._handle_message(data)

            except asyncio.TimeoutError:
                # Send ping to keep connection alive
                continue
            except ConnectionClosed:
                print("[Polygon WS] Connection closed")
                self._connected = False
                if self.on_disconnect:
                    self.on_disconnect()
                break
            except Exception as e:
                print(f"[Polygon WS] Message loop error: {e}")
                await asyncio.sleep(1)

    async def _handle_message(self, msg: dict):
        """Handle a single message."""
        ev = msg.get("ev")

        if ev == "T":  # Trade
            trade = self._parse_trade(msg)
            if trade:
                # Store trade
                if trade.symbol in self.trades:
                    self.trades[trade.symbol].append(trade)

                self.total_trades_received += 1
                self.last_trade_time = trade.timestamp

                # Callback
                if self.on_trade:
                    try:
                        self.on_trade(trade)
                    except Exception as e:
                        print(f"[Polygon WS] Callback error: {e}")

        elif ev == "status":
            status = msg.get("status")
            message = msg.get("message", "")
            print(f"[Polygon WS] Status: {status} - {message}")

    async def start(self):
        """Start the message processing loop."""
        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._message_loop())
        print("[Polygon WS] Message loop started")

    async def stop(self):
        """Stop the client and close connection."""
        self._running = False

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        if self._ws:
            await self._ws.close()
            self._ws = None

        self._connected = False
        self._authenticated = False
        print("[Polygon WS] Stopped")

    def get_recent_trades(self, symbol: str, limit: int = 20) -> List[dict]:
        """Get recent trades for a symbol."""
        if symbol not in self.trades:
            return []

        trades = list(self.trades[symbol])[-limit:]
        return [t.to_dict() for t in reversed(trades)]  # Most recent first

    def get_stats(self) -> dict:
        """Get client statistics."""
        return {
            "connected": self.connected,
            "subscribed_symbols": list(self._subscribed_symbols),
            "total_trades_received": self.total_trades_received,
            "last_trade_time": self.last_trade_time.isoformat() if self.last_trade_time else None,
            "trades_per_symbol": {sym: len(trades) for sym, trades in self.trades.items()},
        }


# Global client instance
_ws_client: Optional[MassiveOptionsWSClient] = None


def get_options_ws_client() -> Optional[MassiveOptionsWSClient]:
    """Get the global WebSocket client instance."""
    global _ws_client
    return _ws_client


async def init_options_ws(symbols: List[str] = None, use_delayed: bool = True) -> MassiveOptionsWSClient:
    """Initialize and start the Massive Options WebSocket client."""
    global _ws_client

    if _ws_client and _ws_client.connected:
        return _ws_client

    _ws_client = MassiveOptionsWSClient(use_delayed=use_delayed)

    if await _ws_client.connect():
        if symbols:
            await _ws_client.subscribe(symbols)
        await _ws_client.start()

    return _ws_client


async def shutdown_options_ws():
    """Shutdown the WebSocket client."""
    global _ws_client
    if _ws_client:
        await _ws_client.stop()
        _ws_client = None
