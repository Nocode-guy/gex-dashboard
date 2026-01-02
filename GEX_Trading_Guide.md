# GEX Dashboard Trading Guide
## Complete Strategy Manual for Gamma Exposure Trading

---

## Table of Contents
1. [Understanding the Basics](#1-understanding-the-basics)
2. [Reading the Dashboard](#2-reading-the-dashboard)
3. [Key Levels Explained](#3-key-levels-explained)
4. [Trading Setups](#4-trading-setups)
5. [Entry Rules](#5-entry-rules)
6. [Exit Rules](#6-exit-rules)
7. [Risk Management](#7-risk-management)
8. [Cheat Sheet](#8-cheat-sheet)

---

## 1. Understanding the Basics

### What is GEX (Gamma Exposure)?

GEX measures how market makers (dealers) must hedge their options positions when price moves. This hedging activity creates predictable price behavior.

**The Core Concept:**
- When you buy options, dealers sell them to you
- Dealers must hedge by buying/selling the underlying stock
- Their hedging creates **support, resistance, and momentum**

### The Two Regimes

| Regime | GEX Type | Color | Price Behavior |
|--------|----------|-------|----------------|
| **MAGNET** | Positive GEX | Green/Yellow | Price gets pulled to level, low volatility, mean reversion |
| **ACCELERATOR** | Negative GEX | Purple | Price moves amplified, high volatility, trending |

### Why This Works

**Positive GEX (Magnet) Mechanics:**
- Dealers are LONG gamma
- Price goes UP → Dealers SELL (pushes price back down)
- Price goes DOWN → Dealers BUY (pushes price back up)
- **Result:** Price gets "pinned" to magnet levels

**Negative GEX (Accelerator) Mechanics:**
- Dealers are SHORT gamma
- Price goes UP → Dealers BUY (pushes price higher)
- Price goes DOWN → Dealers SELL (pushes price lower)
- **Result:** Moves get amplified in either direction

---

## 2. Reading the Dashboard

### Main Components

#### Heatmap (Center)
- Shows GEX at each strike price by expiration
- **Green/Yellow** = Positive GEX (Magnet)
- **Purple** = Negative GEX (Accelerator)
- **Brighter** = Stronger effect

#### Key Zones Panel (Right Side)
- Lists significant strikes with their GEX values
- Shows zone type: MAGNET, ACCELERATOR, VOL ZONE
- Displays percentage changes

#### Stats Bar (Top)
| Stat | Meaning |
|------|---------|
| Net GEX | Total gamma exposure (positive = magnet regime, negative = accelerator regime) |
| VIX | Volatility index (higher = less reliable GEX levels) |
| King | Highest GEX strike (strongest magnet) |
| Zero Gamma | Level where GEX flips from positive to negative |

#### Put/Call Walls
- **Call Wall** = Strike with highest call open interest (resistance)
- **Put Wall** = Strike with highest put open interest (support)

---

## 3. Key Levels Explained

### King Strike (Most Important)
- Strike with the **highest absolute GEX**
- Strongest magnet on the board
- Price gravitates here, especially into expiration

**How to Trade the King:**
- If price is BELOW King with positive GEX → Expect drift UP toward King
- If price is ABOVE King with positive GEX → Expect drift DOWN toward King
- King with NEGATIVE GEX = Strong resistance/support (price repels from it)

### Zero Gamma Level
- Where total GEX equals zero
- **Critical flip point** between regimes

**How to Trade Zero Gamma:**
- **Price ABOVE Zero Gamma** = Positive GEX territory (magnets work, fade moves)
- **Price BELOW Zero Gamma** = Negative GEX territory (accelerators work, ride momentum)

### Put Wall (Support)
- Strike with highest put open interest
- Dealers sold these puts → They BUY stock as price approaches
- Acts as **support** in normal conditions

### Call Wall (Resistance)
- Strike with highest call open interest
- Dealers sold these calls → They SELL stock as price approaches
- Acts as **resistance** in normal conditions

---

## 4. Trading Setups

### Setup 1: Magnet Fade (Mean Reversion)

**Conditions:**
- [ ] Price is in POSITIVE GEX territory (above Zero Gamma)
- [ ] VIX is low/normal (below 20)
- [ ] Price has moved away from King strike
- [ ] No major news/events

**Trade:**
- Fade moves away from King
- Buy dips, sell rips
- Target: King strike

**Example:**
```
King at 690, price drops to 687
→ BUY expecting drift back to 690
```

### Setup 2: Accelerator Momentum (Trend Following)

**Conditions:**
- [ ] Price breaks into NEGATIVE GEX territory (below Zero Gamma)
- [ ] Multiple accelerator zones stacked in direction of move
- [ ] Break of key magnet level

**Trade:**
- Go WITH the momentum
- Don't fade - ride the move
- Target: Next magnet level or VOL ZONE

**Example:**
```
Price breaks below 687 (magnet) into accelerator zone
→ SHORT and ride down through 686, 685, 684...
```

### Setup 3: Wall Bounce

**Conditions:**
- [ ] Price approaching Put Wall (for longs) or Call Wall (for shorts)
- [ ] VIX is low/normal
- [ ] Wall has strong open interest concentration

**Trade:**
- Buy at Put Wall (support)
- Short at Call Wall (resistance)
- Tight stops in case wall breaks

**Example:**
```
Put Wall at 680, price drops to 680
→ BUY expecting bounce
Stop loss: Below 679
```

### Setup 4: Wall Break (Breakout)

**Conditions:**
- [ ] Price breaks through Put Wall (bearish) or Call Wall (bullish)
- [ ] High volume on break
- [ ] Accelerator zones beyond the wall

**Trade:**
- Trade the breakout direction
- Walls become magnets once broken (support → resistance)
- Target: Next significant level

**Example:**
```
Put Wall at 680 breaks with volume
→ SHORT targeting next support
Previous support (680) becomes resistance
```

### Setup 5: King Flip

**Conditions:**
- [ ] King strike changes location (new highest GEX)
- [ ] Indicates major positioning shift

**Trade:**
- New King becomes the target
- Old King may lose magnetic power
- Expect price to gravitate to new King

---

## 5. Entry Rules

### Long Entry Checklist
- [ ] Price above Zero Gamma OR at strong Put Wall
- [ ] King strike is ABOVE current price (magnet pulling up)
- [ ] VIX below 25 (GEX levels reliable)
- [ ] Not in accelerator zone pointing down
- [ ] Positive GEX in immediate strikes above

### Short Entry Checklist
- [ ] Price below Zero Gamma OR at strong Call Wall
- [ ] King strike is BELOW current price (magnet pulling down)
- [ ] In accelerator territory OR breaking below Put Wall
- [ ] Negative GEX in immediate strikes below
- [ ] No major magnet support directly below

### Confirmation Signals
1. **Price action confirms** (candle pattern, break of level)
2. **Volume supports** (high volume on breakout)
3. **Multiple GEX levels align** (King, Wall, Zero Gamma pointing same direction)

---

## 6. Exit Rules

### Profit Targets

**Magnet Trades (Mean Reversion):**
- Target 1: 50% position at nearest magnet
- Target 2: Remaining at King strike
- Trail stop if momentum continues

**Accelerator Trades (Momentum):**
- Target 1: 50% position at first VOL ZONE
- Target 2: Remaining at next magnet level
- Trail stop through accelerator zones

### Stop Loss Rules

| Setup Type | Stop Loss Location |
|------------|-------------------|
| Magnet Fade | Beyond the next accelerator zone |
| Accelerator Momentum | Back above/below the broken magnet |
| Wall Bounce | Beyond the wall (1-2 strikes) |
| Wall Break | Back inside the broken wall |

### Exit Signals
- Price reaches King (take profit)
- GEX regime flips (positioning changed)
- Price enters strong opposing zone
- VIX spikes above 30 (levels less reliable)

---

## 7. Risk Management

### Position Sizing
- Risk 1-2% of account per trade
- Smaller size in accelerator zones (more volatile)
- Larger size in magnet zones (mean reversion)

### When NOT to Trade

**Avoid trading when:**
- [ ] VIX above 30 (extreme fear, GEX unreliable)
- [ ] Major news event (FOMC, CPI, earnings)
- [ ] Conflicting signals (GEX vs technicals disagree)
- [ ] Low liquidity (pre-market, after-hours)
- [ ] King strike is changing rapidly

### VIX Regime Adjustments

| VIX Level | GEX Reliability | Adjustment |
|-----------|-----------------|------------|
| Below 15 | HIGH | Full position, tight stops |
| 15-20 | MEDIUM-HIGH | Normal position |
| 20-25 | MEDIUM | Smaller position, wider stops |
| 25-30 | LOW | Very small or no position |
| Above 30 | UNRELIABLE | Don't rely on GEX levels |

---

## 8. Cheat Sheet

### Quick Reference

```
POSITIVE GEX (Green/Yellow) = MAGNET
→ Price sticks, low vol, fade moves, mean reversion

NEGATIVE GEX (Purple) = ACCELERATOR
→ Price slides, high vol, ride momentum, don't fade
```

### Decision Tree

```
1. Where is price vs Zero Gamma?
   ├── ABOVE → Magnet territory → Look for fades
   └── BELOW → Accelerator territory → Look for momentum

2. Where is King strike?
   ├── ABOVE price → Expect upward drift
   └── BELOW price → Expect downward drift

3. What's the VIX?
   ├── LOW (<20) → Trust GEX levels
   └── HIGH (>25) → Be cautious, reduce size

4. Is price at a Wall?
   ├── PUT WALL → Potential support/bounce
   └── CALL WALL → Potential resistance/rejection
```

### Color Guide

| Color | Zone Type | What It Means | How to Trade |
|-------|-----------|---------------|--------------|
| Bright Yellow | Strong Magnet | Very sticky level | High probability pin |
| Green | Magnet | Support/Resistance | Fade toward it |
| Purple | Accelerator | Slippery zone | Ride through it |
| Bright Purple | Strong Accelerator | Fast move zone | Don't fight it |

### Daily Routine

**Pre-Market (Before 9:30am):**
1. Check overnight GEX changes
2. Identify King, Zero Gamma, Walls
3. Note magnet vs accelerator zones
4. Check VIX level

**Market Open (9:30-10:00am):**
1. Watch for initial direction
2. Note if price respects GEX levels
3. Wait for setup confirmation

**During Session:**
1. Monitor for King flips
2. Watch wall breaks
3. Adjust as positioning changes

**Market Close (3:30-4:00pm):**
1. Expect increased pinning to King
2. Gamma effects strongest into expiration
3. Close positions or adjust for overnight

---

## Key Takeaways

1. **GEX tells you WHERE price will stick vs slide**
2. **Magnets attract price, Accelerators amplify moves**
3. **Trade WITH GEX, not against it**
4. **VIX determines how reliable GEX levels are**
5. **King strike is your primary target in most setups**
6. **Zero Gamma is your regime flip indicator**
7. **Walls provide support/resistance until broken**
8. **When technicals AND GEX agree = highest probability**

---

*Generated for GEX Dashboard - Your Gamma Edge*
