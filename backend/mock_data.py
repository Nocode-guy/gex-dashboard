"""
Mock Data Generator for Testing

Generates realistic options chain data for testing GEX calculations
without needing a live API connection.
"""
import random
from datetime import date, timedelta
from typing import List, Dict
import math

from gex_calculator import OptionContract


def generate_mock_chain(
    symbol: str,
    spot_price: float,
    num_strikes: int = 25,
    num_expirations: int = 4
) -> List[OptionContract]:
    """
    Generate a realistic mock options chain.

    Creates calls and puts across multiple strikes and expirations
    with realistic OI distribution (higher near ATM, lower at wings).
    """
    contracts = []

    # Strike spacing based on price
    if spot_price > 1000:
        strike_spacing = 25  # SPX-like
    elif spot_price > 100:
        strike_spacing = 5   # SPY-like
    else:
        strike_spacing = 2.5  # Lower priced stocks

    # Generate strikes centered around spot
    center_strike = round(spot_price / strike_spacing) * strike_spacing
    strikes = [
        center_strike + (i - num_strikes // 2) * strike_spacing
        for i in range(num_strikes)
    ]

    # Generate expiration dates (weekly for near-term, monthly for far)
    today = date.today()
    expirations = []

    # Next 2 weekly expirations
    days_to_friday = (4 - today.weekday()) % 7
    if days_to_friday == 0:
        days_to_friday = 7  # If today is Friday, go to next
    next_friday = today + timedelta(days=days_to_friday)
    expirations.append(next_friday)
    expirations.append(next_friday + timedelta(days=7))

    # Next 2 monthly expirations (3rd Friday)
    for month_offset in [1, 2]:
        month = today.month + month_offset
        year = today.year
        if month > 12:
            month -= 12
            year += 1

        # Find 3rd Friday
        first_day = date(year, month, 1)
        days_to_friday = (4 - first_day.weekday()) % 7
        third_friday = first_day + timedelta(days=days_to_friday + 14)
        expirations.append(third_friday)

    expirations = expirations[:num_expirations]

    # Generate contracts
    for strike in strikes:
        for exp in expirations:
            dte = (exp - today).days
            moneyness = (strike - spot_price) / spot_price

            # OI distribution - higher near ATM, decay at wings
            atm_factor = math.exp(-5 * moneyness ** 2)  # Gaussian around ATM
            base_oi = int(5000 * atm_factor * random.uniform(0.5, 1.5))

            # Near-term has higher OI
            dte_factor = max(0.3, 1 - dte / 60)
            base_oi = int(base_oi * dte_factor)

            # Add some randomness for "hot" strikes
            if random.random() < 0.1:
                base_oi *= random.randint(3, 10)

            # Gamma calculation (simplified)
            # Gamma peaks at ATM and decays
            base_gamma = 0.01 * math.exp(-3 * moneyness ** 2)
            # Gamma increases as expiration approaches
            gamma_dte_factor = max(0.1, 1 / math.sqrt(max(dte, 1)))
            gamma = base_gamma * gamma_dte_factor * random.uniform(0.8, 1.2)

            # Create call
            call_oi = base_oi + random.randint(-base_oi // 4, base_oi // 4)
            if call_oi > 0:
                contracts.append(OptionContract(
                    strike=strike,
                    expiration=exp,
                    option_type='call',
                    open_interest=max(0, call_oi),
                    gamma=gamma,
                    delta=0.5 + 0.4 * math.tanh(-3 * moneyness),  # Simplified delta
                    volume=random.randint(0, call_oi // 2)
                ))

            # Create put
            put_oi = base_oi + random.randint(-base_oi // 4, base_oi // 4)
            if put_oi > 0:
                contracts.append(OptionContract(
                    strike=strike,
                    expiration=exp,
                    option_type='put',
                    open_interest=max(0, put_oi),
                    gamma=gamma,
                    delta=-0.5 + 0.4 * math.tanh(-3 * moneyness),  # Simplified delta
                    volume=random.randint(0, put_oi // 2)
                ))

    return contracts


# Pre-defined mock data for common symbols
MOCK_SPOT_PRICES = {
    "SPX": 5950.25,
    "SPY": 594.50,
    "QQQ": 515.75,
    "AAPL": 254.30,
    "MSFT": 438.20,
    "NVDA": 137.50,
    "TSLA": 455.80,
    "AMZN": 225.40,
    "META": 612.30,
    "GOOGL": 197.60,
    "APLD": 27.11,  # From your screenshot
}


def get_mock_spot_price(symbol: str) -> float:
    """Get mock spot price for a symbol."""
    if symbol in MOCK_SPOT_PRICES:
        # Add small random variation
        base = MOCK_SPOT_PRICES[symbol]
        return base * random.uniform(0.998, 1.002)
    else:
        # Generate random price for unknown symbols
        return random.uniform(50, 500)


def get_mock_options_chain(symbol: str) -> tuple[float, List[OptionContract]]:
    """
    Get mock options chain for a symbol.

    Returns: (spot_price, list of contracts)
    """
    spot = get_mock_spot_price(symbol)
    contracts = generate_mock_chain(symbol, spot)
    return spot, contracts


# For testing
if __name__ == "__main__":
    from gex_calculator import GEXCalculator

    symbol = "SPX"
    spot, contracts = get_mock_options_chain(symbol)

    print(f"\n{symbol} Mock Data:")
    print(f"Spot Price: ${spot:.2f}")
    print(f"Total Contracts: {len(contracts)}")

    # Count by type
    calls = [c for c in contracts if c.option_type == 'call']
    puts = [c for c in contracts if c.option_type == 'put']
    print(f"Calls: {len(calls)}, Puts: {len(puts)}")

    # Calculate GEX
    calc = GEXCalculator()
    result = calc.calculate(symbol, spot, contracts)

    print(f"\nGEX Results:")
    print(f"Net GEX: {result.net_gex / 1e9:.2f}B")
    print(f"King Node: {result.king_node.strike if result.king_node else 'N/A'}")
    print(f"OPEX Warning: {result.opex_warning}")

    print(f"\nTop Zones:")
    for zone in result.zones[:5]:
        print(f"  {zone.strike}: {zone.gex_formatted} ({zone.role.value})")
