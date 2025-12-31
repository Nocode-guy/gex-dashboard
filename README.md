# GEX Dashboard

A Gamma Exposure (GEX) analysis tool inspired by Skylit's Heatseeker. Calculates and visualizes dealer positioning from options data to identify key support/resistance levels.

## Features

- **GEX Calculation Engine** - Computes gamma exposure per strike from options chains
- **Web Dashboard** - Heatmap visualization of GEX across strikes and expirations
- **NinjaTrader Indicator** - Draws GEX zones directly on your futures charts
- **Real-time Updates** - Configurable refresh intervals (1, 5, 15, 30, 60 min)
- **OPEX Detection** - Warns when data reliability may be reduced
- **Stale Data Alerts** - Visual indicators when data is outdated

## Architecture

```
┌─────────────────────┐         ┌─────────────────────┐
│   Tradier API       │         │   Web Dashboard     │
│   (Options Data)    │         │   (localhost:5000)  │
└─────────┬───────────┘         └──────────▲──────────┘
          │                                │
          ▼                                │
┌─────────────────────┐                    │
│   FastAPI Backend   │────────────────────┘
│   - GEX Calculator  │
│   - REST API        │─────────────────────┐
└─────────────────────┘                     │
                                            ▼
                               ┌─────────────────────┐
                               │   NinjaTrader       │
                               │   GEX Indicator     │
                               └─────────────────────┘
```

## Quick Start

### 1. Install Dependencies

```bash
cd backend
pip install -r requirements.txt
```

### 2. Start the Backend (Mock Data)

```bash
cd backend
python app.py
```

The API will start at `http://localhost:5000` with mock data.

### 3. View the Dashboard

Open `frontend/index.html` in your browser, or serve it:

```bash
cd frontend
python -m http.server 8080
```

Then visit `http://localhost:8080`

### 4. Connect Tradier (Optional)

Once your Tradier account is verified:

```bash
# Set your API key
set TRADIER_API_KEY=your_api_key_here

# Or for paper trading
set TRADIER_API_KEY=your_sandbox_key_here

# Start the backend
cd backend
python app.py
```

### 5. NinjaTrader Indicator

1. Copy `ninjatrader/GEXZonesIndicator.cs` to:
   `Documents\NinjaTrader 8\bin\Custom\Indicators\`

2. In NinjaTrader, compile (F5)

3. Add the indicator to your chart:
   - Right-click chart → Indicators → GEXZonesIndicator
   - Set `OptionsSymbol` to match your chart:
     - ES → SPX
     - NQ → NDX or QQQ
     - Individual stocks → same symbol

## Configuration

### Backend Settings (`backend/config.py`)

| Setting | Default | Description |
|---------|---------|-------------|
| `MIN_OPEN_INTEREST` | 500 | Ignore strikes with OI below this |
| `MIN_GEX_VALUE` | $10M | Ignore zones with GEX below this |
| `MAX_ZONES` | 20 | Maximum zones to return |
| `DEFAULT_REFRESH_INTERVAL` | 5 min | How often to refresh data |

### DTE Decay Rules

Near-term expirations are automatically weighted down:

| Days to Expiry | Weight |
|----------------|--------|
| < 2 days | 20% |
| < 7 days | 50% |
| < 14 days | 80% |
| >= 14 days | 100% |

### NinjaTrader Indicator Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `ApiUrl` | localhost:5000 | GEX Dashboard API URL |
| `OptionsSymbol` | SPX | Which options to fetch |
| `RefreshMinutes` | 5 | How often to refresh |
| `ZoneHeightTicks` | 20 | Height of drawn zones |
| `MaxZonesToShow` | 8 | Max zones on chart |

## API Endpoints

### GET /gex/{symbol}
Full GEX data with heatmap for dashboard.

### GET /gex/{symbol}/levels
Compact format for NinjaTrader indicator.

### GET /symbols
List of active symbols with summary data.

### POST /settings/refresh?minutes=5
Set refresh interval.

## Understanding GEX

### Positive GEX (Green/Yellow)
- Dealers are **long gamma**
- They will **sell rallies** and **buy dips**
- Acts as **support/resistance** - price gets absorbed
- Higher values = stronger "magnet" effect

### Negative GEX (Purple)
- Dealers are **short gamma**
- They will **buy rallies** and **sell dips**
- Acts as **accelerator** - price moves amplified
- Higher values = more volatility potential

### Key Node Types

| Node | Meaning |
|------|---------|
| **King** | Largest GEX - primary price target |
| **Gatekeeper** | Guards the King - deflection zone |
| **Support** | Positive GEX below current price |
| **Resistance** | Positive GEX above current price |
| **Accelerator** | Negative GEX - volatility zone |

## Limitations

1. **Not a signal generator** - Use for context, not entries
2. **Low liquidity = noise** - Stick to liquid tickers
3. **OPEX week** - Near-term nodes less reliable
4. **Map reshuffles** - Levels can change intraday

## Tradier MCP Setup (Claude Code)

```bash
claude mcp add --transport http tradier https://mcp.tradier.com/mcp \
  --header "API_KEY: your_api_key_here" \
  --header "PAPER_TRADING: true"
```

## Credits

- GEX concepts inspired by [Skylit Heatseeker](https://docs.skylit.ai/)
- Options data from [Tradier API](https://tradier.com/)
