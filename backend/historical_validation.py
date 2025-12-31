"""
Historical Validation Module

Validates GEX levels against historical price action.
Answers: "Do King/Gatekeeper levels actually predict price behavior?"

Uses MarketData.app historical options data to:
1. Calculate historical GEX levels
2. Compare against actual price movements
3. Generate statistical confidence metrics
"""
import asyncio
import sqlite3
from datetime import datetime, date, timedelta
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field
import statistics


@dataclass
class HistoricalGEXPoint:
    """Single historical GEX data point."""
    date: date
    symbol: str
    spot_price: float
    king_strike: float
    king_gex: float
    gatekeeper_strike: Optional[float]
    gatekeeper_gex: Optional[float]
    zero_gamma_level: Optional[float]
    net_gex: float


@dataclass
class PriceReaction:
    """Price reaction to a GEX level."""
    date: date
    level_strike: float
    level_type: str  # 'king', 'gatekeeper', 'zero_gamma'
    approach_direction: str  # 'from_above', 'from_below'

    # Price behavior
    touched: bool          # Did price reach the level?
    bounced: bool          # Did price reverse at/near level?
    broke_through: bool    # Did price break through?
    max_penetration: float  # How far past level (in $)
    reaction_size: float   # Size of bounce/continuation (in $)

    # Time metrics
    time_at_level: int     # Minutes spent within 0.1% of level
    time_to_reaction: int  # Minutes until decisive move


@dataclass
class LevelStats:
    """Statistical performance of a level type."""
    level_type: str
    total_approaches: int = 0
    touches: int = 0
    bounces: int = 0
    breakouts: int = 0

    # Rates
    touch_rate: float = 0.0       # How often price reaches level
    bounce_rate: float = 0.0      # When touched, how often bounces
    breakout_rate: float = 0.0    # When touched, how often breaks

    # Magnitude
    avg_bounce_size: float = 0.0
    avg_penetration: float = 0.0

    # Reliability
    confidence_score: float = 0.0  # 0-100

    def calculate_rates(self):
        if self.total_approaches > 0:
            self.touch_rate = self.touches / self.total_approaches
        if self.touches > 0:
            self.bounce_rate = self.bounces / self.touches
            self.breakout_rate = self.breakouts / self.touches

    def to_dict(self) -> dict:
        return {
            "level_type": self.level_type,
            "total_approaches": self.total_approaches,
            "touches": self.touches,
            "bounces": self.bounces,
            "breakouts": self.breakouts,
            "touch_rate_pct": round(self.touch_rate * 100, 1),
            "bounce_rate_pct": round(self.bounce_rate * 100, 1),
            "breakout_rate_pct": round(self.breakout_rate * 100, 1),
            "avg_bounce_size": round(self.avg_bounce_size, 2),
            "avg_penetration": round(self.avg_penetration, 2),
            "confidence_score": round(self.confidence_score, 1),
        }


@dataclass
class ValidationResult:
    """Complete validation result for a symbol."""
    symbol: str
    period_start: date
    period_end: date
    trading_days: int

    king_stats: LevelStats
    gatekeeper_stats: LevelStats
    zero_gamma_stats: LevelStats

    # Overall metrics
    overall_accuracy: float = 0.0
    king_predictive_value: float = 0.0
    gatekeeper_predictive_value: float = 0.0

    # Edge calculation
    expected_bounce_value: float = 0.0  # Expected $ gain from fading at King
    expected_breakout_value: float = 0.0  # Expected $ gain from breakout trades

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "period": {
                "start": self.period_start.isoformat(),
                "end": self.period_end.isoformat(),
                "trading_days": self.trading_days,
            },
            "king": self.king_stats.to_dict(),
            "gatekeeper": self.gatekeeper_stats.to_dict(),
            "zero_gamma": self.zero_gamma_stats.to_dict(),
            "overall": {
                "accuracy": round(self.overall_accuracy * 100, 1),
                "king_predictive_value": round(self.king_predictive_value * 100, 1),
                "gatekeeper_predictive_value": round(self.gatekeeper_predictive_value * 100, 1),
            },
            "edge": {
                "expected_bounce_value": round(self.expected_bounce_value, 2),
                "expected_breakout_value": round(self.expected_breakout_value, 2),
            }
        }


class HistoricalValidator:
    """
    Validates GEX predictions against historical data.
    """

    def __init__(self, db_path: str = "gex_history.db"):
        self.db_path = db_path
        self._ensure_validation_tables()

    def _ensure_validation_tables(self):
        """Create validation-specific tables if they don't exist."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Historical GEX levels
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS historical_gex (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                symbol TEXT NOT NULL,
                spot_price REAL NOT NULL,
                king_strike REAL,
                king_gex REAL,
                gatekeeper_strike REAL,
                gatekeeper_gex REAL,
                zero_gamma_level REAL,
                net_gex REAL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(date, symbol)
            )
        """)

        # Price reactions to levels
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS price_reactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                symbol TEXT NOT NULL,
                level_strike REAL NOT NULL,
                level_type TEXT NOT NULL,
                approach_direction TEXT,
                touched INTEGER,
                bounced INTEGER,
                broke_through INTEGER,
                max_penetration REAL,
                reaction_size REAL,
                time_at_level INTEGER,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Validation runs
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS validation_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                period_start TEXT NOT NULL,
                period_end TEXT NOT NULL,
                trading_days INTEGER,
                king_bounce_rate REAL,
                gatekeeper_bounce_rate REAL,
                overall_accuracy REAL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.commit()
        conn.close()

    def save_historical_gex(self, point: HistoricalGEXPoint):
        """Save a historical GEX data point."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            INSERT OR REPLACE INTO historical_gex
            (date, symbol, spot_price, king_strike, king_gex,
             gatekeeper_strike, gatekeeper_gex, zero_gamma_level, net_gex)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            point.date.isoformat(),
            point.symbol,
            point.spot_price,
            point.king_strike,
            point.king_gex,
            point.gatekeeper_strike,
            point.gatekeeper_gex,
            point.zero_gamma_level,
            point.net_gex,
        ))

        conn.commit()
        conn.close()

    def get_historical_gex(
        self,
        symbol: str,
        start_date: date,
        end_date: date
    ) -> List[HistoricalGEXPoint]:
        """Retrieve historical GEX data for a period."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT date, symbol, spot_price, king_strike, king_gex,
                   gatekeeper_strike, gatekeeper_gex, zero_gamma_level, net_gex
            FROM historical_gex
            WHERE symbol = ? AND date BETWEEN ? AND ?
            ORDER BY date
        """, (symbol, start_date.isoformat(), end_date.isoformat()))

        points = []
        for row in cursor.fetchall():
            points.append(HistoricalGEXPoint(
                date=datetime.strptime(row[0], "%Y-%m-%d").date(),
                symbol=row[1],
                spot_price=row[2],
                king_strike=row[3],
                king_gex=row[4],
                gatekeeper_strike=row[5],
                gatekeeper_gex=row[6],
                zero_gamma_level=row[7],
                net_gex=row[8],
            ))

        conn.close()
        return points

    async def fetch_historical_options(
        self,
        symbol: str,
        target_date: date,
        marketdata_client
    ) -> Optional[HistoricalGEXPoint]:
        """
        Fetch historical options data and calculate GEX for a specific date.
        Uses MarketData.app historical endpoint.
        """
        from gex_calculator import GEXCalculator

        try:
            # MarketData.app historical options endpoint
            # /v1/options/chain/{symbol}/?date=YYYY-MM-DD
            params = {
                "date": target_date.strftime("%Y-%m-%d"),
                "minOpenInterest": 100,
            }

            # Get historical quote
            quote_data = await marketdata_client._request(
                f"/stocks/quotes/{symbol}/",
                {"date": target_date.strftime("%Y-%m-%d")}
            )

            if quote_data.get("s") != "ok":
                return None

            spot_price = quote_data.get("last", [0])[0]
            if spot_price == 0:
                return None

            # Get historical options chain
            chain_data = await marketdata_client._request(
                f"/options/chain/{symbol}/",
                params
            )

            if chain_data.get("s") != "ok":
                return None

            # Parse contracts and calculate GEX
            contracts = marketdata_client._parse_chain(chain_data, target_date, spot_price)

            if not contracts:
                return None

            calculator = GEXCalculator()
            result = calculator.calculate(symbol, spot_price, contracts)

            return HistoricalGEXPoint(
                date=target_date,
                symbol=symbol,
                spot_price=result.spot_price,
                king_strike=result.king_node.strike if result.king_node else 0,
                king_gex=result.king_node.gex if result.king_node else 0,
                gatekeeper_strike=result.gatekeeper_node.strike if result.gatekeeper_node else None,
                gatekeeper_gex=result.gatekeeper_node.gex if result.gatekeeper_node else None,
                zero_gamma_level=result.zero_gamma_level,
                net_gex=result.net_gex,
            )

        except Exception as e:
            print(f"Error fetching historical data for {symbol} {target_date}: {e}")
            return None

    async def backfill_historical_gex(
        self,
        symbol: str,
        days: int = 30,
        marketdata_client = None
    ):
        """
        Backfill historical GEX data for a symbol.
        """
        if marketdata_client is None:
            from marketdata_client import get_marketdata_client
            marketdata_client = get_marketdata_client()

        end_date = date.today()
        start_date = end_date - timedelta(days=days)

        print(f"Backfilling {symbol} from {start_date} to {end_date}...")

        current_date = start_date
        success_count = 0

        while current_date <= end_date:
            # Skip weekends
            if current_date.weekday() < 5:  # Monday = 0, Friday = 4
                point = await self.fetch_historical_options(
                    symbol, current_date, marketdata_client
                )

                if point:
                    self.save_historical_gex(point)
                    success_count += 1
                    print(f"  {current_date}: King={point.king_strike}, "
                          f"GEX=${point.net_gex/1e6:.1f}M")

                # Rate limiting
                await asyncio.sleep(0.5)

            current_date += timedelta(days=1)

        print(f"Backfilled {success_count} days for {symbol}")

    def analyze_price_reactions(
        self,
        symbol: str,
        gex_points: List[HistoricalGEXPoint],
        intraday_data: Dict[date, List[Tuple[datetime, float]]]
    ) -> List[PriceReaction]:
        """
        Analyze how price reacted to historical GEX levels.

        Args:
            symbol: Ticker symbol
            gex_points: Historical GEX levels
            intraday_data: Dict mapping date -> [(timestamp, price), ...]
        """
        reactions = []

        for point in gex_points:
            day_prices = intraday_data.get(point.date, [])
            if not day_prices:
                continue

            prices = [p[1] for p in day_prices]
            if not prices:
                continue

            high = max(prices)
            low = min(prices)
            open_price = prices[0]
            close_price = prices[-1]

            # Analyze King level
            if point.king_strike:
                king_reaction = self._analyze_level_reaction(
                    point.king_strike, "king", prices, open_price
                )
                if king_reaction:
                    king_reaction.date = point.date
                    reactions.append(king_reaction)

            # Analyze Gatekeeper level
            if point.gatekeeper_strike:
                gk_reaction = self._analyze_level_reaction(
                    point.gatekeeper_strike, "gatekeeper", prices, open_price
                )
                if gk_reaction:
                    gk_reaction.date = point.date
                    reactions.append(gk_reaction)

            # Analyze Zero Gamma level
            if point.zero_gamma_level:
                zg_reaction = self._analyze_level_reaction(
                    point.zero_gamma_level, "zero_gamma", prices, open_price
                )
                if zg_reaction:
                    zg_reaction.date = point.date
                    reactions.append(zg_reaction)

        return reactions

    def _analyze_level_reaction(
        self,
        level: float,
        level_type: str,
        prices: List[float],
        open_price: float
    ) -> Optional[PriceReaction]:
        """Analyze price reaction to a single level."""
        if not prices or level <= 0:
            return None

        # Determine approach direction
        approach_direction = "from_below" if open_price < level else "from_above"

        # Check if price touched level (within 0.1%)
        touch_threshold = level * 0.001
        touched = any(abs(p - level) <= touch_threshold for p in prices)

        if not touched:
            return PriceReaction(
                date=date.today(),  # Will be set by caller
                level_strike=level,
                level_type=level_type,
                approach_direction=approach_direction,
                touched=False,
                bounced=False,
                broke_through=False,
                max_penetration=0,
                reaction_size=0,
                time_at_level=0,
                time_to_reaction=0,
            )

        # Find touch point
        touch_index = 0
        for i, p in enumerate(prices):
            if abs(p - level) <= touch_threshold:
                touch_index = i
                break

        # Analyze what happened after touch
        post_touch_prices = prices[touch_index:]

        if approach_direction == "from_below":
            # Coming from below - did we break above or bounce down?
            max_above = max(post_touch_prices) - level
            min_after = min(post_touch_prices[1:]) if len(post_touch_prices) > 1 else level

            broke_through = max_above > level * 0.003  # Broke 0.3% above
            bounced = (level - min_after) > level * 0.002  # Dropped 0.2% below level
            max_penetration = max_above
            reaction_size = level - min_after if bounced else max_above

        else:
            # Coming from above - did we break below or bounce up?
            min_below = level - min(post_touch_prices)
            max_after = max(post_touch_prices[1:]) if len(post_touch_prices) > 1 else level

            broke_through = min_below > level * 0.003  # Broke 0.3% below
            bounced = (max_after - level) > level * 0.002  # Rallied 0.2% above level
            max_penetration = min_below
            reaction_size = max_after - level if bounced else min_below

        # Count time at level
        time_at_level = sum(1 for p in prices if abs(p - level) <= touch_threshold)

        return PriceReaction(
            date=date.today(),  # Will be set by caller
            level_strike=level,
            level_type=level_type,
            approach_direction=approach_direction,
            touched=touched,
            bounced=bounced,
            broke_through=broke_through,
            max_penetration=max_penetration,
            reaction_size=reaction_size,
            time_at_level=time_at_level,
            time_to_reaction=0,
        )

    def calculate_level_stats(
        self,
        reactions: List[PriceReaction],
        level_type: str
    ) -> LevelStats:
        """Calculate statistics for a level type."""
        stats = LevelStats(level_type=level_type)

        level_reactions = [r for r in reactions if r.level_type == level_type]

        if not level_reactions:
            return stats

        stats.total_approaches = len(level_reactions)
        stats.touches = sum(1 for r in level_reactions if r.touched)
        stats.bounces = sum(1 for r in level_reactions if r.bounced)
        stats.breakouts = sum(1 for r in level_reactions if r.broke_through)

        stats.calculate_rates()

        # Average sizes
        bounce_sizes = [r.reaction_size for r in level_reactions if r.bounced]
        if bounce_sizes:
            stats.avg_bounce_size = statistics.mean(bounce_sizes)

        penetrations = [r.max_penetration for r in level_reactions if r.touched]
        if penetrations:
            stats.avg_penetration = statistics.mean(penetrations)

        # Confidence score (weighted combination of metrics)
        # Higher sample size + higher bounce rate + lower penetration = more confident
        sample_weight = min(stats.total_approaches / 20, 1.0) * 30  # Max 30 points for sample
        bounce_weight = stats.bounce_rate * 50  # Max 50 points for bounce rate
        consistency_weight = 20 if stats.avg_penetration < 0.5 else 10  # Consistency

        stats.confidence_score = sample_weight + bounce_weight + consistency_weight

        return stats

    def run_validation(
        self,
        symbol: str,
        reactions: List[PriceReaction],
        start_date: date,
        end_date: date
    ) -> ValidationResult:
        """Run complete validation and generate results."""
        king_stats = self.calculate_level_stats(reactions, "king")
        gatekeeper_stats = self.calculate_level_stats(reactions, "gatekeeper")
        zero_gamma_stats = self.calculate_level_stats(reactions, "zero_gamma")

        trading_days = len(set(r.date for r in reactions))

        # Calculate overall accuracy
        total_touches = king_stats.touches + gatekeeper_stats.touches
        total_bounces = king_stats.bounces + gatekeeper_stats.bounces
        overall_accuracy = total_bounces / total_touches if total_touches > 0 else 0

        # King predictive value: weighted by GEX magnitude and bounce success
        king_predictive = king_stats.bounce_rate * (king_stats.confidence_score / 100)

        # Gatekeeper predictive value
        gatekeeper_predictive = gatekeeper_stats.bounce_rate * (gatekeeper_stats.confidence_score / 100)

        # Expected values
        # If King has 70% bounce rate with avg $0.50 bounce, expected value = 0.7 * 0.50 - 0.3 * 0.20 = $0.29
        if king_stats.bounce_rate > 0:
            expected_bounce = (
                king_stats.bounce_rate * king_stats.avg_bounce_size -
                (1 - king_stats.bounce_rate) * king_stats.avg_penetration
            )
        else:
            expected_bounce = 0

        if king_stats.breakout_rate > 0:
            expected_breakout = (
                king_stats.breakout_rate * king_stats.avg_penetration -
                (1 - king_stats.breakout_rate) * king_stats.avg_bounce_size
            )
        else:
            expected_breakout = 0

        result = ValidationResult(
            symbol=symbol,
            period_start=start_date,
            period_end=end_date,
            trading_days=trading_days,
            king_stats=king_stats,
            gatekeeper_stats=gatekeeper_stats,
            zero_gamma_stats=zero_gamma_stats,
            overall_accuracy=overall_accuracy,
            king_predictive_value=king_predictive,
            gatekeeper_predictive_value=gatekeeper_predictive,
            expected_bounce_value=expected_bounce,
            expected_breakout_value=expected_breakout,
        )

        # Save validation run
        self._save_validation_run(result)

        return result

    def _save_validation_run(self, result: ValidationResult):
        """Save validation run to database."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO validation_runs
            (symbol, period_start, period_end, trading_days,
             king_bounce_rate, gatekeeper_bounce_rate, overall_accuracy)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            result.symbol,
            result.period_start.isoformat(),
            result.period_end.isoformat(),
            result.trading_days,
            result.king_stats.bounce_rate,
            result.gatekeeper_stats.bounce_rate,
            result.overall_accuracy,
        ))

        conn.commit()
        conn.close()

    async def validate_symbol(
        self,
        symbol: str,
        days: int = 30
    ) -> Optional[ValidationResult]:
        """
        Run validation for a symbol using stored historical data.

        If no historical data exists, returns a result with zeros.
        Use backfill_historical_gex() first to populate data.
        """
        end_date = date.today()
        start_date = end_date - timedelta(days=days)

        # Get stored historical GEX points
        gex_points = self.get_historical_gex(symbol, start_date, end_date)

        if not gex_points:
            # No historical data - return empty result
            empty_stats = LevelStats(level_type="none")
            return ValidationResult(
                symbol=symbol,
                period_start=start_date,
                period_end=end_date,
                trading_days=0,
                king_stats=LevelStats(level_type="king"),
                gatekeeper_stats=LevelStats(level_type="gatekeeper"),
                zero_gamma_stats=LevelStats(level_type="zero_gamma"),
                overall_accuracy=0,
                king_predictive_value=0,
                gatekeeper_predictive_value=0,
            )

        # For now, use simplified validation based on stored snapshots
        # Full validation requires intraday price data which we'd need to fetch
        # This provides basic statistics from what we have stored

        reactions = []

        # Simulate reactions based on daily data
        for i in range(1, len(gex_points)):
            prev = gex_points[i - 1]
            curr = gex_points[i]

            # Did price approach King level?
            if prev.king_strike and curr.king_strike:
                # Check if price moved toward King
                distance_prev = abs(prev.spot_price - prev.king_strike)
                distance_curr = abs(curr.spot_price - curr.king_strike)

                # Approaching if getting closer
                if distance_curr < distance_prev:
                    approach_direction = "from_below" if curr.spot_price < curr.king_strike else "from_above"

                    # Simple bounce detection: did spot move away from King next day?
                    touched = distance_curr < (curr.king_strike * 0.005)  # Within 0.5%
                    bounced = touched and distance_curr > distance_prev * 0.5
                    broke_through = touched and not bounced

                    reactions.append(PriceReaction(
                        date=curr.date,
                        level_strike=curr.king_strike,
                        level_type="king",
                        approach_direction=approach_direction,
                        touched=touched,
                        bounced=bounced,
                        broke_through=broke_through,
                        max_penetration=abs(curr.spot_price - curr.king_strike) if touched else 0,
                        reaction_size=abs(curr.spot_price - prev.spot_price),
                        time_at_level=0,
                        time_to_reaction=0,
                    ))

            # Check Gatekeeper level
            if prev.gatekeeper_strike and curr.gatekeeper_strike:
                distance_prev = abs(prev.spot_price - prev.gatekeeper_strike)
                distance_curr = abs(curr.spot_price - curr.gatekeeper_strike)

                if distance_curr < distance_prev:
                    approach_direction = "from_below" if curr.spot_price < curr.gatekeeper_strike else "from_above"

                    touched = distance_curr < (curr.gatekeeper_strike * 0.005)
                    bounced = touched and distance_curr > distance_prev * 0.5
                    broke_through = touched and not bounced

                    reactions.append(PriceReaction(
                        date=curr.date,
                        level_strike=curr.gatekeeper_strike,
                        level_type="gatekeeper",
                        approach_direction=approach_direction,
                        touched=touched,
                        bounced=bounced,
                        broke_through=broke_through,
                        max_penetration=abs(curr.spot_price - curr.gatekeeper_strike) if touched else 0,
                        reaction_size=abs(curr.spot_price - prev.spot_price),
                        time_at_level=0,
                        time_to_reaction=0,
                    ))

        # Run validation with collected reactions
        return self.run_validation(symbol, reactions, start_date, end_date)


# Singleton
_validator: Optional[HistoricalValidator] = None


def get_validator() -> HistoricalValidator:
    """Get or create validator singleton."""
    global _validator
    if _validator is None:
        _validator = HistoricalValidator()
    return _validator
