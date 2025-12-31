"""
Black-Scholes Greeks Calculator

Proper calculation of Greeks including Vanna (second-order Greek).
No proxies or approximations - real Black-Scholes formulas.

Reference: https://en.wikipedia.org/wiki/Greeks_(finance)
"""
import math
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional
from scipy.stats import norm
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo  # Python < 3.9

# Eastern timezone for market hours
ET = ZoneInfo("America/New_York")

# Current risk-free rate (10-year Treasury) - update periodically
# As of Dec 2024, approximately 4.5%
RISK_FREE_RATE = 0.045


@dataclass
class GreeksResult:
    """Complete Greeks calculation result."""
    delta: float
    gamma: float
    theta: float
    vega: float
    vanna: float      # dDelta/dIV - the one we need for VEX
    charm: float      # dDelta/dT (delta decay)
    vomma: float      # dVega/dIV (vega convexity)
    iv: float         # Input IV used

    def to_dict(self) -> dict:
        return {
            "delta": round(self.delta, 6),
            "gamma": round(self.gamma, 6),
            "theta": round(self.theta, 6),
            "vega": round(self.vega, 6),
            "vanna": round(self.vanna, 6),
            "charm": round(self.charm, 6),
            "vomma": round(self.vomma, 6),
            "iv": round(self.iv, 4),
        }


def calculate_d1_d2(
    spot: float,
    strike: float,
    time_to_expiry: float,
    iv: float,
    rate: float = RISK_FREE_RATE
) -> tuple[float, float]:
    """
    Calculate d1 and d2 for Black-Scholes.

    d1 = (ln(S/K) + (r + σ²/2)T) / (σ√T)
    d2 = d1 - σ√T
    """
    if time_to_expiry <= 0 or iv <= 0 or spot <= 0 or strike <= 0:
        return 0.0, 0.0

    sqrt_t = math.sqrt(time_to_expiry)

    d1 = (math.log(spot / strike) + (rate + (iv ** 2) / 2) * time_to_expiry) / (iv * sqrt_t)
    d2 = d1 - iv * sqrt_t

    return d1, d2


def calculate_greeks(
    spot: float,
    strike: float,
    expiration: date,
    iv: float,
    option_type: str,  # 'call' or 'put'
    rate: float = RISK_FREE_RATE,
    calculation_date: Optional[date] = None
) -> GreeksResult:
    """
    Calculate all Greeks using Black-Scholes formulas.

    Args:
        spot: Current underlying price
        strike: Option strike price
        expiration: Expiration date
        iv: Implied volatility (as decimal, e.g., 0.20 for 20%)
        option_type: 'call' or 'put'
        rate: Risk-free interest rate
        calculation_date: Date to calculate from (defaults to today)

    Returns:
        GreeksResult with all Greeks including proper Vanna
    """
    if calculation_date is None:
        calculation_date = date.today()

    # Time to expiry in years
    days_to_expiry = (expiration - calculation_date).days

    # For 0DTE, calculate hours remaining until 4 PM ET market close
    if days_to_expiry == 0:
        now_et = datetime.now(ET)
        market_close_hour = 16  # 4 PM ET
        hours_remaining = max(0.5, market_close_hour - now_et.hour - now_et.minute / 60)
        # Convert hours to years (trading hours in a year ~ 252 days * 6.5 hours)
        time_to_expiry = hours_remaining / (252 * 6.5)
    else:
        time_to_expiry = max(days_to_expiry / 365.0, 0.0001)  # Avoid division by zero

    # Handle edge cases
    if iv <= 0 or spot <= 0 or strike <= 0:
        return GreeksResult(
            delta=0, gamma=0, theta=0, vega=0,
            vanna=0, charm=0, vomma=0, iv=iv
        )

    # Calculate d1 and d2
    d1, d2 = calculate_d1_d2(spot, strike, time_to_expiry, iv, rate)

    sqrt_t = math.sqrt(time_to_expiry)

    # Standard normal PDF and CDF
    n_d1 = norm.pdf(d1)
    N_d1 = norm.cdf(d1)
    N_d2 = norm.cdf(d2)
    N_neg_d1 = norm.cdf(-d1)
    N_neg_d2 = norm.cdf(-d2)

    # ===================
    # DELTA
    # ===================
    # Call: N(d1)
    # Put: N(d1) - 1
    if option_type == 'call':
        delta = N_d1
    else:
        delta = N_d1 - 1

    # ===================
    # GAMMA (same for call and put)
    # ===================
    # Gamma = n(d1) / (S * σ * √T)
    gamma = n_d1 / (spot * iv * sqrt_t)

    # ===================
    # VEGA (same for call and put)
    # ===================
    # Vega = S * n(d1) * √T
    # Note: Conventionally expressed per 1% move in IV
    vega = spot * n_d1 * sqrt_t / 100  # Divide by 100 for per 1% IV move

    # ===================
    # THETA
    # ===================
    # Theta is different for calls and puts
    discount = math.exp(-rate * time_to_expiry)

    term1 = -(spot * n_d1 * iv) / (2 * sqrt_t)

    if option_type == 'call':
        term2 = rate * strike * discount * N_d2
        theta = (term1 - term2) / 365  # Per day
    else:
        term2 = rate * strike * discount * N_neg_d2
        theta = (term1 + term2) / 365  # Per day

    # ===================
    # VANNA (the key one for VEX)
    # ===================
    # Vanna = dDelta/dIV = Vega/S * (1 - d1/(σ*√T))
    # Alternative formula: Vanna = -n(d1) * d2 / (σ)
    # Or: Vanna = Vega * (1 - d1/(σ*√T)) / S
    if iv * sqrt_t > 0:
        vanna = (vega * 100) * (1 - d1 / (iv * sqrt_t)) / spot  # Undo the /100 from vega
        # Alternative cleaner formula:
        # vanna = -n_d1 * d2 / iv
    else:
        vanna = 0.0

    # ===================
    # CHARM (delta decay)
    # ===================
    # Charm = -dDelta/dT
    # Charm = n(d1) * (2*r*T - d2*σ*√T) / (2*T*σ*√T)
    if time_to_expiry > 0:
        charm = n_d1 * (2 * rate * time_to_expiry - d2 * iv * sqrt_t) / (2 * time_to_expiry * iv * sqrt_t)
        if option_type == 'put':
            charm = -charm
    else:
        charm = 0.0

    # ===================
    # VOMMA (vega convexity)
    # ===================
    # Vomma = dVega/dIV = Vega * d1 * d2 / σ
    if iv > 0:
        vomma = (vega * 100) * d1 * d2 / iv / 100  # Keep in same units as vega
    else:
        vomma = 0.0

    return GreeksResult(
        delta=delta,
        gamma=gamma,
        theta=theta,
        vega=vega,
        vanna=vanna,
        charm=charm,
        vomma=vomma,
        iv=iv
    )


def calculate_vanna_exposure(
    spot: float,
    strike: float,
    expiration: date,
    iv: float,
    option_type: str,
    open_interest: int,
    rate: float = RISK_FREE_RATE
) -> float:
    """
    Calculate VEX (Vanna Exposure) for a single contract.

    VEX = Vanna × OI × 100 × Spot

    Positive VEX: When IV rises, dealers need to buy (bullish pressure)
    Negative VEX: When IV rises, dealers need to sell (bearish pressure)
    """
    greeks = calculate_greeks(spot, strike, expiration, iv, option_type, rate)

    # VEX formula (same structure as GEX)
    vex = greeks.vanna * open_interest * 100 * spot

    # Puts have opposite dealer positioning
    if option_type == 'put':
        vex *= -1

    return vex


# ===================
# TREASURY RATE FETCHING
# ===================
def fetch_risk_free_rate() -> float:
    """
    Fetch current 10-year Treasury rate.
    Falls back to default if fetch fails.
    """
    global RISK_FREE_RATE

    try:
        import yfinance as yf
        tnx = yf.Ticker("^TNX")  # 10-year Treasury yield
        hist = tnx.history(period="1d")
        if not hist.empty:
            rate = float(hist['Close'].iloc[-1]) / 100  # Convert from % to decimal
            RISK_FREE_RATE = rate
            return rate
    except Exception as e:
        print(f"Could not fetch Treasury rate: {e}")

    return RISK_FREE_RATE


# ===================
# TEST
# ===================
if __name__ == "__main__":
    # Test with SPY options
    spot = 590.0
    strike = 600.0
    expiration = date(2025, 1, 17)
    iv = 0.15  # 15% IV

    print("Testing Black-Scholes Greeks Calculator")
    print("=" * 50)
    print(f"Spot: ${spot}, Strike: ${strike}")
    print(f"Expiration: {expiration}, IV: {iv*100:.1f}%")
    print()

    # Calculate for call
    call_greeks = calculate_greeks(spot, strike, expiration, iv, 'call')
    print("CALL Greeks:")
    for k, v in call_greeks.to_dict().items():
        print(f"  {k}: {v}")

    print()

    # Calculate for put
    put_greeks = calculate_greeks(spot, strike, expiration, iv, 'put')
    print("PUT Greeks:")
    for k, v in put_greeks.to_dict().items():
        print(f"  {k}: {v}")

    print()

    # Calculate VEX for a position
    oi = 10000
    call_vex = calculate_vanna_exposure(spot, strike, expiration, iv, 'call', oi)
    put_vex = calculate_vanna_exposure(spot, strike, expiration, iv, 'put', oi)

    print(f"VEX with OI={oi}:")
    print(f"  Call VEX: ${call_vex:,.0f}")
    print(f"  Put VEX: ${put_vex:,.0f}")
