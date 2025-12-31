/**
 * GEX Dashboard - Heatmap JavaScript
 */

// Auto-detect API URL: use relative path in production, localhost in development
const API_BASE = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1'
    ? 'http://localhost:8000'
    : '';

// State - load from localStorage if available
let currentSymbol = localStorage.getItem('gex_currentSymbol') || 'SPX';
let refreshInterval = parseInt(localStorage.getItem('gex_refreshInterval')) || 5;
let frontendRefreshSec = 10; // Frontend polling (seconds)
let autoRefreshTimer = null;
let symbols = [];
let lastPrices = {}; // Track price changes
let currentView = localStorage.getItem('gex_currentView') || 'gex'; // 'gex', 'vex', or 'dex'
let expirationMode = localStorage.getItem('gex_expirationMode') || 'all'; // 'all' or '0dte'
let currentData = null; // Store current data for view switching
let viewMode = localStorage.getItem('gex_viewMode') || 'single'; // 'single' or 'trinity'
let selectedStrike = null; // Currently selected strike for detail panel
let trinitySymbols = JSON.parse(localStorage.getItem('gex_trinitySymbols')) || ['SPY', 'QQQ', 'IWM'];
let trinityData = {}; // Cache data for trinity columns
let trendFilter = localStorage.getItem('gex_trendFilter') || 'all'; // 'all', 'increasing', 'decreasing'

// Parse date string as LOCAL date (not UTC) to avoid timezone issues
function parseLocalDate(dateStr) {
    const [year, month, day] = dateStr.split('-').map(Number);
    return new Date(year, month - 1, day); // month is 0-indexed
}

// Get today's date in YYYY-MM-DD format (local timezone)
function getTodayString() {
    const now = new Date();
    return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
}

// Save state to localStorage
function saveState() {
    localStorage.setItem('gex_currentSymbol', currentSymbol);
    localStorage.setItem('gex_refreshInterval', refreshInterval.toString());
    localStorage.setItem('gex_currentView', currentView);
    localStorage.setItem('gex_expirationMode', expirationMode);
    localStorage.setItem('gex_viewMode', viewMode);
    localStorage.setItem('gex_trinitySymbols', JSON.stringify(trinitySymbols));
    localStorage.setItem('gex_trendFilter', trendFilter);
}

// Scroll heatmap to center on spot price row
function scrollToSpotPrice() {
    const currentPriceRow = document.querySelector('#heatmapBody tr.current-price');
    if (currentPriceRow) {
        currentPriceRow.scrollIntoView({ behavior: 'smooth', block: 'center' });
        // Flash highlight
        currentPriceRow.style.transition = 'background 0.3s';
        currentPriceRow.style.background = 'rgba(59, 130, 246, 0.3)';
        setTimeout(() => {
            currentPriceRow.style.background = '';
        }, 500);
    }
}

// Scroll heatmap to center on King (highest GEX) row
function scrollToKing() {
    const kingRow = document.querySelector('#heatmapBody tr[data-king="true"]');
    if (kingRow) {
        kingRow.scrollIntoView({ behavior: 'smooth', block: 'center' });
        // Flash highlight
        kingRow.style.transition = 'background 0.3s';
        kingRow.style.background = 'rgba(251, 191, 36, 0.3)';
        setTimeout(() => {
            kingRow.style.background = '';
        }, 500);
    }
}

// Set trend filter and re-render
function setTrendFilter(trend) {
    trendFilter = trend;
    saveState();
    // Update button icon
    const btnTrendFilter = document.getElementById('btnTrendFilter');
    if (btnTrendFilter) {
        const icon = btnTrendFilter.querySelector('.control-icon');
        if (icon) {
            if (trend === 'increasing') icon.textContent = '↗';
            else if (trend === 'decreasing') icon.textContent = '↘';
            else icon.textContent = '↕';
        }
    }
    // Re-render with filter
    if (currentData) {
        renderHeatmap(currentData, currentView);
    }
}

// Navigate to previous/next symbol in favorites
function navigateSymbol(direction) {
    if (symbols.length < 2) return;
    const currentIdx = symbols.indexOf(currentSymbol);
    let newIdx;
    if (direction === 'prev') {
        newIdx = currentIdx <= 0 ? symbols.length - 1 : currentIdx - 1;
    } else {
        newIdx = currentIdx >= symbols.length - 1 ? 0 : currentIdx + 1;
    }
    loadSymbol(symbols[newIdx]);
}

// DOM Elements - will be populated after DOM is ready
let elements = {};

function initElements() {
    elements = {
        connectionStatus: document.getElementById('connectionStatus'),
        lastUpdate: document.getElementById('lastUpdate'),
        currentDate: document.getElementById('currentDate'),
        currentTime: document.getElementById('currentTime'),
        symbolTabs: document.getElementById('symbolTabs'),
        opexWarning: document.getElementById('opexWarning'),
        opexDate: document.getElementById('opexDate'),
        symbolName: document.getElementById('symbolName'),
        spotPrice: document.getElementById('spotPrice'),
        kingDistance: document.getElementById('kingDistance'),
        netGex: document.getElementById('netGex'),
        kingNode: document.getElementById('kingNode'),
        kingGex: document.getElementById('kingGex'),
        zeroGamma: document.getElementById('zeroGamma'),
        heatmapHeader: document.getElementById('heatmapHeader'),
        heatmapBody: document.getElementById('heatmapBody'),
        zonesList: document.getElementById('zonesList'),
        dataSource: document.getElementById('dataSource'),
        symbolCount: document.getElementById('symbolCount'),
        btnRefresh: document.getElementById('btnRefresh'),
        symbolInput: document.getElementById('symbolInput'),
        btnAddSymbol: document.getElementById('btnAddSymbol'),
        searchDropdown: document.getElementById('searchDropdown'),
        // Regime & Alerts
        regimeBar: document.getElementById('regimeBar'),
        regimeVix: document.getElementById('regimeVix'),
        regimeBadge: document.getElementById('regimeBadge'),
        reliabilityBadge: document.getElementById('reliabilityBadge'),
        deltaGex: document.getElementById('deltaGex'),
        deltaVex: document.getElementById('deltaVex'),
        deltaDex: document.getElementById('deltaDex'),
        alertsIndicator: document.getElementById('alertsIndicator'),
        alertCount: document.getElementById('alertCount'),
        eventBadge: document.getElementById('eventBadge'),
        marketStatusBadge: document.getElementById('marketStatusBadge'),
        // View title
        heatmapViewTitle: document.getElementById('heatmapViewTitle'),
    };

    // Debug: check if critical elements are found
    console.log('Elements initialized:', {
        btnRefresh: !!elements.btnRefresh,
        symbolInput: !!elements.symbolInput,
        heatmapBody: !!elements.heatmapBody
    });
}

// Search state
let searchTimeout = null;
let searchResults = [];
let selectedSearchIndex = -1;

// =============================================================================
// API CALLS
// =============================================================================

async function fetchAPI(endpoint) {
    try {
        const response = await fetch(`${API_BASE}${endpoint}`);
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }
        return await response.json();
    } catch (error) {
        console.error(`API Error (${endpoint}):`, error);
        setConnectionStatus('error', 'Connection Error');
        throw error;
    }
}

async function fetchSymbols() {
    try {
        const data = await fetchAPI('/symbols');
        symbols = data.symbols || [];
        renderSymbolTabs();
        elements.symbolCount.textContent = symbols.length;
        return symbols;
    } catch (error) {
        console.error('Failed to fetch symbols:', error);
        return [];
    }
}

async function fetchGEX(symbol, forceRefresh = false) {
    const endpoint = `/gex/${symbol}${forceRefresh ? '?refresh=true' : ''}`;
    return await fetchAPI(endpoint);
}

async function setRefreshInterval(minutes) {
    console.log('Setting refresh interval to:', minutes, 'minutes');
    try {
        const response = await fetch(`${API_BASE}/settings/refresh?minutes=${minutes}`, {
            method: 'POST'
        });
        if (!response.ok) {
            const text = await response.text();
            throw new Error(`HTTP ${response.status}: ${text}`);
        }
        const data = await response.json();
        console.log('Refresh interval response:', data);
        refreshInterval = minutes;
        saveState(); // Persist refresh interval
        setupAutoRefresh();
    } catch (error) {
        console.error('Failed to set refresh interval:', error);
        alert(`Failed to set refresh interval: ${error.message}`);
    }
}

async function checkMarketStatus() {
    try {
        const response = await fetch(`${API_BASE}/status`);
        if (!response.ok) return;
        const data = await response.json();

        const badge = elements.marketStatusBadge;
        if (!badge) return;

        const refreshLoop = data.refresh_loop || {};
        const marketOpen = refreshLoop.market_open;
        const isWeekend = refreshLoop.is_weekend;
        const paused = refreshLoop.paused;

        if (isWeekend) {
            badge.textContent = 'WEEKEND';
            badge.className = 'market-status-badge weekend';
            badge.style.display = 'inline-flex';
            badge.title = 'Market closed on weekends - data from last trading day';
        } else if (!marketOpen || paused) {
            badge.textContent = 'MARKET CLOSED';
            badge.className = 'market-status-badge closed';
            badge.style.display = 'inline-flex';
            badge.title = 'Market hours: 9:00am - 4:30pm ET';
        } else {
            badge.style.display = 'none';
        }
    } catch (error) {
        console.error('Failed to check market status:', error);
    }
}

async function addSymbol(symbol) {
    const response = await fetch(`${API_BASE}/symbols/${symbol}`, {
        method: 'POST'
    });

    const data = await response.json();

    if (!response.ok) {
        // Extract error message from response
        const errorMsg = data.detail || `HTTP ${response.status}`;
        throw new Error(errorMsg);
    }

    return data;
}

async function removeSymbol(symbol) {
    try {
        const response = await fetch(`${API_BASE}/symbols/${symbol}`, {
            method: 'DELETE'
        });
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }
        return await response.json();
    } catch (error) {
        console.error(`Failed to remove symbol ${symbol}:`, error);
        throw error;
    }
}

async function searchSymbols(query) {
    try {
        const response = await fetch(`${API_BASE}/search?q=${encodeURIComponent(query)}`);
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }
        return await response.json();
    } catch (error) {
        console.error('Search failed:', error);
        return { results: [] };
    }
}

// =============================================================================
// RENDERING
// =============================================================================

function setConnectionStatus(status, text) {
    elements.connectionStatus.className = `status-badge ${status}`;
    elements.connectionStatus.textContent = text;
}

function renderSymbolTabs() {
    elements.symbolTabs.innerHTML = symbols.map(sym => {
        const isActive = sym.symbol === currentSymbol;
        const gexType = sym.net_gex >= 0 ? 'positive' : 'negative';
        return `
            <button class="symbol-tab ${isActive ? 'active' : ''}"
                    data-symbol="${sym.symbol}">
                ${sym.symbol}
                <span class="gex-indicator ${gexType}"></span>
                <span class="btn-remove" data-symbol="${sym.symbol}" title="Remove ${sym.symbol}">x</span>
            </button>
        `;
    }).join('');

    // Add click handlers for tabs
    document.querySelectorAll('.symbol-tab').forEach(tab => {
        tab.addEventListener('click', (e) => {
            // Ignore if clicking the remove button
            if (e.target.classList.contains('btn-remove')) return;
            currentSymbol = tab.dataset.symbol;
            loadSymbol(currentSymbol);
        });
    });

    // Add click handlers for remove buttons
    document.querySelectorAll('.btn-remove').forEach(btn => {
        btn.addEventListener('click', async (e) => {
            e.stopPropagation();
            const symbol = btn.dataset.symbol;
            await handleRemoveSymbol(symbol);
        });
    });
}

function formatGEX(value) {
    const absVal = Math.abs(value);
    const sign = value >= 0 ? '+' : '-';

    if (absVal >= 1e9) {
        return `${sign}$${(absVal / 1e9).toFixed(1)}B`;
    } else if (absVal >= 1e6) {
        return `${sign}$${(absVal / 1e6).toFixed(1)}M`;
    } else if (absVal >= 1e3) {
        return `${sign}$${(absVal / 1e3).toFixed(1)}K`;
    }
    return `${sign}$${absVal.toFixed(0)}`;
}

function formatPrice(price) {
    return price >= 100 ? price.toFixed(2) : price.toFixed(2);
}

function getGEXColor(value, maxValue) {
    // Return opacity based on magnitude relative to max
    const ratio = Math.abs(value) / Math.abs(maxValue);
    return Math.min(1, ratio * 0.8 + 0.2); // Min 20%, max 100%
}

function renderHeader(data) {
    elements.symbolName.textContent = data.symbol;

    // Animate price change
    const oldPrice = lastPrices[data.symbol] || data.spot_price;
    const newPrice = data.spot_price;
    lastPrices[data.symbol] = newPrice;

    // Flash effect on price change
    if (oldPrice !== newPrice) {
        elements.spotPrice.classList.add('price-flash');
        setTimeout(() => elements.spotPrice.classList.remove('price-flash'), 500);
    }

    elements.spotPrice.textContent = `$${formatPrice(newPrice)}`;

    // King distance display (like Skylit)
    // Shows how far price is from King and in which direction
    if (data.king_node) {
        const kingStrike = data.king_node.strike;
        const distance = newPrice - kingStrike;  // positive = price above King
        const distancePct = ((distance / newPrice) * 100).toFixed(2);  // % of current price
        const arrow = distance > 0 ? '↓' : '↑';  // ↑ means price needs to go up to reach King
        const direction = distance > 0 ? 'above' : 'below';
        elements.kingDistance.textContent = `${arrow} $${Math.abs(distance).toFixed(2)} (${Math.abs(distancePct)}%) ${direction} King`;
        elements.kingDistance.className = `king-distance ${distance > 0 ? 'above' : 'below'}`;
    } else {
        elements.kingDistance.textContent = '--';
    }

    // GEX Summary
    elements.netGex.textContent = formatGEX(data.meta.net_gex);
    elements.kingNode.textContent = data.king_node ? formatPrice(data.king_node.strike) : '--';
    elements.kingGex.textContent = data.king_node ? data.king_node.gex_formatted : '--';
    elements.zeroGamma.textContent = data.meta.zero_gamma_level
        ? formatPrice(data.meta.zero_gamma_level)
        : '--';

    // OPEX Warning
    if (data.opex_warning) {
        elements.opexWarning.style.display = 'flex';
        elements.opexDate.textContent = data.opex_date ? `OPEX: ${data.opex_date}` : '';
    } else {
        elements.opexWarning.style.display = 'none';
    }

    // Update timestamp
    const updateTime = new Date(data.timestamp);
    elements.lastUpdate.textContent = updateTime.toLocaleTimeString();

    // Connection status
    if (data.stale_error) {
        setConnectionStatus('error', 'Data Stale');
    } else if (data.stale_warning) {
        setConnectionStatus('warning', 'Data Aging');
    } else {
        setConnectionStatus('connected', 'Live');
    }

    // Render regime and changes
    renderRegime(data);
}

function updatePressureGauge(data) {
    const marker = document.getElementById('pressureMarker');
    const text = document.getElementById('pressureText');

    if (!marker || !text) return;

    // =========================================================================
    // PRESSURE CALCULATION v2 - Trading Grade
    // =========================================================================
    //
    // SIGN CONVENTIONS (Dealer Perspective):
    // ----------------------------------------
    // DEX positive = Dealers NET LONG delta = They SELL rallies = BEARISH for market
    // DEX negative = Dealers NET SHORT delta = They BUY dips = BULLISH for market
    // GEX positive = Call gamma dominates = Dampening moves = Stable/mean-reverting
    // GEX negative = Put gamma dominates = Amplifying moves = Volatile/trending
    //
    // OUTPUT DEFINITIONS:
    // ----------------------------------------
    // Pressure (0-100) = Expected directional drift (0=bearish, 100=bullish)
    // Regime (POS/NEG γ) = Volatility behavior (dampen vs amplify) - SEPARATE from direction
    //
    // IMPORTANT: High pressure + negative gamma ≠ same as high pressure + positive gamma
    // The regime tells you HOW price will move, pressure tells you WHERE
    //
    // =========================================================================

    const spotPrice = data.spot_price;
    const zeroGamma = data.meta?.zero_gamma_level;
    const netGex = data.meta?.net_gex || 0;
    const netDex = data.meta?.net_dex || 0;
    const kingStrike = data.king_node?.strike;
    const kingGex = data.king_node?.gex || 0;
    const reliability = data.reliability?.level || 'medium';

    // Track top drivers for transparency
    const drivers = [];

    // -------------------------------------------------------------------------
    // Factor 1: DEX (40%) - Primary directional indicator
    // Normalize by instrument type (SPX ~5B range, ETFs ~500M range)
    // -------------------------------------------------------------------------
    let dexScore = 50;
    const dexNormBase = spotPrice > 1000 ? 5e9 : 500e6;
    if (netDex !== 0) {
        const normalizedDex = Math.max(-1, Math.min(1, netDex / dexNormBase));
        // Invert: positive dealer delta = bearish market pressure
        dexScore = 50 - (normalizedDex * 40);

        if (Math.abs(normalizedDex) > 0.25) {
            const dexPct = Math.round(Math.abs(normalizedDex) * 100);
            drivers.push(`DEX ${normalizedDex > 0 ? 'long' : 'short'} ${dexPct}%`);
        }
    }

    // -------------------------------------------------------------------------
    // Factor 2: GEX Magnitude (25%) - Support/resistance strength
    // -------------------------------------------------------------------------
    let gexScore = 50;
    const gexNormBase = spotPrice > 1000 ? 10e9 : 1e9;
    if (netGex !== 0) {
        const normalizedGex = Math.max(-1, Math.min(1, netGex / gexNormBase));
        gexScore = 50 + (normalizedGex * 25);

        if (Math.abs(normalizedGex) > 0.25) {
            drivers.push(`GEX ${normalizedGex > 0 ? '+' : ''}${Math.round(normalizedGex * 100)}%`);
        }
    }

    // -------------------------------------------------------------------------
    // Factor 3: Gamma Environment (20%) - Position vs Zero Gamma
    // Cap at +/- 2% for stability (prevents one factor from dominating)
    // -------------------------------------------------------------------------
    let gammaEnvScore = 50;
    let gammaRegime = 'FLAT';
    let pctFromZG = 0;

    if (zeroGamma && spotPrice) {
        pctFromZG = ((spotPrice - zeroGamma) / spotPrice) * 100;
        const cappedPct = Math.max(-2, Math.min(2, pctFromZG));
        gammaEnvScore = 50 + (cappedPct * 15);

        // Determine regime label (separate from pressure direction)
        if (pctFromZG > 0.3) {
            gammaRegime = 'POS γ'; // Stabilizing
            drivers.push(`+${pctFromZG.toFixed(1)}% vs ZG`);
        } else if (pctFromZG < -0.3) {
            gammaRegime = 'NEG γ'; // Accelerating
            drivers.push(`${pctFromZG.toFixed(1)}% vs ZG`);
        }
    }

    // -------------------------------------------------------------------------
    // Factor 4: King Proximity (15%) - Magnet effect
    // Cap at +/- 2% distance for stability
    // -------------------------------------------------------------------------
    let kingScore = 50;
    if (kingStrike && spotPrice && kingGex > 0) {
        const pctFromKing = ((spotPrice - kingStrike) / spotPrice) * 100;
        const cappedPct = Math.max(-2, Math.min(2, pctFromKing));

        if (cappedPct < 0) {
            // Below King = magnet pull UP
            kingScore = 50 + (Math.abs(cappedPct) * 15);
        } else {
            // Above King = resistance
            kingScore = 50 - (cappedPct * 10);
        }
    }

    // -------------------------------------------------------------------------
    // Weighted combination
    // -------------------------------------------------------------------------
    const weights = { dex: 0.40, gex: 0.25, gammaEnv: 0.20, king: 0.15 };

    let pressure = (dexScore * weights.dex) +
                   (gexScore * weights.gex) +
                   (gammaEnvScore * weights.gammaEnv) +
                   (kingScore * weights.king);

    pressure = Math.round(Math.max(0, Math.min(100, pressure)));

    // Update marker
    marker.style.left = `${pressure}%`;

    // -------------------------------------------------------------------------
    // Labels - Wider NEUTRAL band (41-60) to reduce flip-flopping
    // -------------------------------------------------------------------------
    let label, className;
    if (pressure >= 81) {
        label = 'STR BULL';
        className = 'bullish';
    } else if (pressure >= 61) {
        label = 'BULLISH';
        className = 'bullish';
    } else if (pressure <= 20) {
        label = 'STR BEAR';
        className = 'bearish';
    } else if (pressure <= 40) {
        label = 'BEARISH';
        className = 'bearish';
    } else {
        label = 'NEUTRAL';
        className = 'neutral';
    }

    // Display: Pressure + Regime together
    text.textContent = `${label} ${pressure} | ${gammaRegime}`;
    text.className = `pressure-text ${className}`;

    // Console output for debugging & transparency
    console.log('Pressure v2:', {
        score: pressure,
        label: label,
        regime: gammaRegime,
        reliability: reliability,
        drivers: drivers.slice(0, 2).join(' | ') || 'balanced',
        raw: { dex: dexScore.toFixed(0), gex: gexScore.toFixed(0), env: gammaEnvScore.toFixed(0), king: kingScore.toFixed(0) }
    });
}

function renderRegime(data) {
    // Update pressure gauge
    updatePressureGauge(data);

    // Regime data
    if (data.regime) {
        const regime = data.regime;
        elements.regimeVix.textContent = regime.vix.toFixed(1);

        // Regime badge
        const regimeColors = {
            'low': 'regime-low',
            'normal': 'regime-normal',
            'elevated': 'regime-elevated',
            'high': 'regime-high',
            'extreme': 'regime-extreme'
        };
        elements.regimeBadge.textContent = regime.regime.toUpperCase();
        elements.regimeBadge.className = `regime-badge ${regimeColors[regime.regime] || ''}`;

        // Reliability badge
        const reliabilityColors = {
            'HIGH': 'reliability-high',
            'MEDIUM': 'reliability-medium',
            'LOW': 'reliability-low'
        };
        elements.reliabilityBadge.textContent = regime.reliability;
        elements.reliabilityBadge.className = `reliability-badge ${reliabilityColors[regime.reliability] || ''}`;

        // Event badge (economic calendar events)
        if (elements.eventBadge) {
            if (regime.event_day && regime.event_name) {
                elements.eventBadge.textContent = regime.event_name.toUpperCase();
                elements.eventBadge.style.display = 'inline-flex';
                const impactClass = regime.event_impact ? `${regime.event_impact}-impact` : '';
                elements.eventBadge.className = `event-badge ${impactClass}`;
            } else {
                elements.eventBadge.style.display = 'none';
            }
        }
    }

    // Intraday delta changes (since market open) - prefer these over refresh-to-refresh
    if (data.intraday) {
        const intraday = data.intraday;

        // Show intraday changes with "Today" label
        elements.deltaGex.textContent = `Today: ${formatGEX(intraday.delta_net_gex)}`;
        elements.deltaGex.className = `delta-item ${intraday.delta_net_gex >= 0 ? 'positive' : 'negative'}`;
        elements.deltaGex.title = `GEX change since ${intraday.baseline_time} (market open)`;

        elements.deltaVex.textContent = `VEX: ${formatGEX(intraday.delta_net_vex)}`;
        elements.deltaVex.className = `delta-item ${intraday.delta_net_vex >= 0 ? 'positive' : 'negative'}`;
        elements.deltaVex.title = `VEX change since ${intraday.baseline_time}`;

        elements.deltaDex.textContent = `DEX: ${formatGEX(intraday.delta_net_dex)}`;
        elements.deltaDex.className = `delta-item ${intraday.delta_net_dex >= 0 ? 'positive' : 'negative'}`;
        elements.deltaDex.title = `DEX change since ${intraday.baseline_time}`;

        // Log intraday data for debugging
        console.log('Intraday changes:', {
            baseline: intraday.baseline_time,
            deltaGex: formatGEX(intraday.delta_net_gex),
            deltaSpot: `$${intraday.delta_spot?.toFixed(2) || 0}`,
            kingChanged: intraday.king_changed
        });
    } else if (data.changes) {
        // Fallback to refresh-to-refresh changes if no intraday baseline yet
        const changes = data.changes;
        elements.deltaGex.textContent = `ΔGEX: ${formatGEX(changes.delta_gex)}`;
        elements.deltaGex.className = `delta-item ${changes.delta_gex >= 0 ? 'positive' : 'negative'}`;

        elements.deltaVex.textContent = `ΔVEX: ${formatGEX(changes.delta_vex)}`;
        elements.deltaVex.className = `delta-item ${changes.delta_vex >= 0 ? 'positive' : 'negative'}`;

        elements.deltaDex.textContent = `ΔDEX: ${formatGEX(changes.delta_dex)}`;
        elements.deltaDex.className = `delta-item ${changes.delta_dex >= 0 ? 'positive' : 'negative'}`;
    }

    // Alerts
    if (data.alerts && data.alerts.length > 0) {
        elements.alertsIndicator.style.display = 'flex';
        elements.alertCount.textContent = data.alerts.length;
        // Store alerts for popup
        elements.alertsIndicator.dataset.alerts = JSON.stringify(data.alerts);
    } else {
        elements.alertsIndicator.style.display = 'none';
        elements.alertsIndicator.dataset.alerts = '[]';
    }
}

// Alerts popup
function showAlertsPopup(alerts) {
    // Remove existing popup
    const existing = document.querySelector('.alerts-popup');
    if (existing) existing.remove();

    if (!alerts || alerts.length === 0) return;

    const popup = document.createElement('div');
    popup.className = 'alerts-popup';
    popup.innerHTML = `
        <div class="alerts-popup-header">
            <span>Recent Alerts</span>
            <button class="alerts-popup-close">&times;</button>
        </div>
        <div class="alerts-popup-body">
            ${alerts.map(alert => `
                <div class="alert-item ${alert.severity || 'info'}">
                    <span class="alert-type">${alert.type || 'Alert'}</span>
                    <span class="alert-message">${alert.message || alert.description || JSON.stringify(alert)}</span>
                    <span class="alert-time">${alert.timestamp ? new Date(alert.timestamp).toLocaleTimeString() : ''}</span>
                </div>
            `).join('')}
        </div>
    `;

    document.body.appendChild(popup);

    // Position near the bell icon
    const indicator = document.getElementById('alertsIndicator');
    const rect = indicator.getBoundingClientRect();
    popup.style.top = `${rect.bottom + 8}px`;
    popup.style.right = `${window.innerWidth - rect.right}px`;

    // Close handlers
    popup.querySelector('.alerts-popup-close').addEventListener('click', () => popup.remove());
    document.addEventListener('click', function closePopup(e) {
        if (!popup.contains(e.target) && !indicator.contains(e.target)) {
            popup.remove();
            document.removeEventListener('click', closePopup);
        }
    });
}

function renderHeatmap(data, view = 'gex') {
    // Select GEX, VEX, or DEX data based on view
    let heatmap;
    if (view === 'vex') {
        heatmap = data.vex_heatmap;
    } else if (view === 'dex') {
        heatmap = data.dex_heatmap;
    } else {
        heatmap = data.heatmap;
    }

    if (!heatmap) {
        console.warn('No heatmap data for view:', view);
        return;
    }

    // Get intraday zone deltas if available
    const zoneDeltas = data.intraday?.zone_deltas || {};
    const hasIntraday = Object.keys(zoneDeltas).length > 0;

    // Debug: log zone deltas
    if (hasIntraday) {
        console.log('Zone deltas available:', Object.keys(zoneDeltas).length, 'strikes');
        // Show a sample
        const sampleKey = Object.keys(zoneDeltas)[0];
        console.log('Sample delta:', sampleKey, zoneDeltas[sampleKey]);
    }

    const strikes = heatmap.strikes;
    let expirations = heatmap.expirations;
    let heatmapData = heatmap.data;

    // Filter to near-term (0DTE or nearest) if mode is set
    let nearTermLabel = null;  // Will show "0DTE" or "Next: Jan 2" etc.
    if (expirationMode === '0dte' && expirations.length > 0) {
        // Find today's expiration(s) - compare date portion only
        const today = getTodayString();
        const todayIndices = [];

        expirations.forEach((exp, idx) => {
            // exp is already in YYYY-MM-DD format from backend
            if (exp === today) {
                todayIndices.push(idx);
            }
        });

        // If we found today's expiration, filter to just that
        if (todayIndices.length > 0) {
            expirations = todayIndices.map(i => expirations[i]);
            heatmapData = strikes.map((_, rowIdx) =>
                todayIndices.map(colIdx => heatmap.data[rowIdx][colIdx])
            );
            nearTermLabel = '0DTE';
        } else {
            // No 0DTE available - show first (nearest) expiration
            const nearestExp = expirations[0];
            expirations = [nearestExp];
            heatmapData = strikes.map((_, rowIdx) => [heatmap.data[rowIdx][0]]);
            // Calculate days until expiration
            const expDate = parseLocalDate(nearestExp);
            const todayDate = new Date();
            const daysUntil = Math.ceil((expDate - todayDate) / (1000 * 60 * 60 * 24));
            nearTermLabel = `${daysUntil}DTE`;
        }
    }

    // King and Gatekeeper strikes (global)
    const kingStrike = data.king_node?.strike;
    const gatekeeperStrike = data.gatekeeper_node?.strike;

    // Show ALL strikes - no slicing, let the table scroll naturally
    // The backend already filters to ±30% of spot and centers around King
    const visibleStrikes = strikes;
    const visibleData = heatmapData;

    // Find max value for color scaling (from visible data only)
    const allValues = visibleData.flat().filter(v => v !== 0);
    const maxValue = Math.max(...allValues.map(Math.abs)) || 1;

    // Find current price row in visible strikes
    const currentPriceIndex = visibleStrikes.findIndex(s => s <= data.spot_price);

    // Find Magnet (highest positive GEX) for EACH expiration column
    const columnMagnets = expirations.map((_, colIndex) => {
        let maxVal = 0;
        let maxRowIdx = -1;
        visibleData.forEach((row, rowIdx) => {
            const val = row[colIndex] || 0;
            if (val > maxVal) {
                maxVal = val;
                maxRowIdx = rowIdx;
            }
        });
        return { rowIdx: maxRowIdx, value: maxVal };
    });

    // Find Accelerator (highest negative GEX) for EACH expiration column
    const columnAccelerators = expirations.map((_, colIndex) => {
        let minVal = 0;
        let minRowIdx = -1;
        visibleData.forEach((row, rowIdx) => {
            const val = row[colIndex] || 0;
            if (val < minVal) {
                minVal = val;
                minRowIdx = rowIdx;
            }
        });
        return { rowIdx: minRowIdx, value: minVal };
    });

    // Legacy alias for backwards compatibility
    const columnKings = columnMagnets.map(m => m.rowIdx);

    // Render header - use parseLocalDate to avoid timezone issues
    elements.heatmapHeader.innerHTML = `
        <th class="strike-col">Strike</th>
        ${expirations.map((exp, idx) => {
            const expDate = parseLocalDate(exp);
            const dateStr = expDate.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
            // Show DTE label in near-term mode
            const dteLabel = (nearTermLabel && idx === 0) ? `<span class="dte-label">${nearTermLabel}</span>` : '';
            return `<th>${dateStr}${dteLabel}</th>`;
        }).join('')}
    `;

    // Render body - centered around King
    elements.heatmapBody.innerHTML = visibleStrikes.map((strike, rowIndex) => {
        const isCurrentPrice = rowIndex === currentPriceIndex;
        const isGlobalKing = strike === kingStrike;
        const isGatekeeper = strike === gatekeeperStrike;

        // Apply trend filter
        const zoneData = zoneDeltas[String(strike)] || zoneDeltas[strike.toFixed(1)] || {};
        const zoneDelta = zoneData.delta_gex || 0;

        // Skip rows based on trend filter (but always show current price and king)
        if (trendFilter !== 'all' && !isCurrentPrice && !isGlobalKing) {
            if (trendFilter === 'increasing' && zoneDelta <= 0) {
                return ''; // Skip decreasing/unchanged rows
            }
            if (trendFilter === 'decreasing' && zoneDelta >= 0) {
                return ''; // Skip increasing/unchanged rows
            }
        }

        const rowClasses = [
            isCurrentPrice ? 'current-price' : '',
            isGlobalKing ? 'global-king-row' : '',
        ].filter(Boolean).join(' ');

        const cells = visibleData[rowIndex].map((value, colIndex) => {
            if (value === 0) {
                return `<td>--</td>`;
            }

            const isPositive = value >= 0;
            const isColumnKing = columnKings[colIndex] === rowIndex && value > 0;
            const isColumnAccelerator = columnAccelerators[colIndex].rowIdx === rowIndex && value < 0;

            // Get intraday delta for this strike (GEX only for zone-level)
            // Keys in zoneDeltas are strings like "700.0", so convert strike to string
            const zoneData = zoneDeltas[String(strike)] || zoneDeltas[strike.toFixed(1)] || {};
            const zoneDelta = zoneData.delta_gex || 0;
            const zonePct = zoneData.pct_gex || 0;
            const hasDelta = hasIntraday && zonePct !== 0; // Show any non-zero change

            const cellClasses = [
                isPositive ? 'positive' : 'negative',
                isColumnKing ? 'column-king' : '',
                isColumnAccelerator ? 'column-accelerator' : '',
                hasDelta ? (zoneDelta > 0 ? 'delta-up' : 'delta-down') : '',
            ].filter(Boolean).join(' ');

            const opacity = getGEXColor(value, maxValue);
            const isLightMode = document.body.classList.contains('light-mode');

            // Solid background color based on view type, value, and theme
            // Light mode uses softer, more muted colors like Skylit
            let bgColor;
            if (isLightMode) {
                // Light mode - softer warm tones
                if (view === 'vex') {
                    bgColor = isPositive
                        ? `rgba(14, 165, 180, ${opacity * 0.25})`  // Soft teal for positive VEX
                        : `rgba(168, 85, 247, ${opacity * 0.2})`;  // Soft purple for negative
                } else if (view === 'dex') {
                    bgColor = isPositive
                        ? `rgba(59, 130, 246, ${opacity * 0.25})`  // Soft blue for positive DEX
                        : `rgba(168, 85, 247, ${opacity * 0.2})`;  // Soft purple for negative
                } else {
                    bgColor = isPositive
                        ? `rgba(34, 160, 94, ${opacity * 0.25})`   // Soft green for positive GEX
                        : `rgba(168, 85, 247, ${opacity * 0.2})`;  // Soft purple for negative
                }
            } else {
                // Dark mode - vibrant colors
                if (view === 'vex') {
                    bgColor = isPositive
                        ? `rgba(34, 211, 238, ${opacity * 0.35})`  // Aqua/Cyan for positive VEX
                        : `rgba(168, 85, 247, ${opacity * 0.3})`;  // Purple for negative
                } else if (view === 'dex') {
                    bgColor = isPositive
                        ? `rgba(59, 130, 246, ${opacity * 0.35})`  // Blue for positive DEX
                        : `rgba(168, 85, 247, ${opacity * 0.3})`;  // Purple for negative
                } else {
                    bgColor = isPositive
                        ? `rgba(74, 222, 128, ${opacity * 0.3})`   // Green for positive GEX
                        : `rgba(168, 85, 247, ${opacity * 0.3})`;  // Purple for negative
                }
            }

            const bgStyle = `background: ${bgColor}`;

            // Add star for column king (magnet)
            const kingStar = isColumnKing ? '<span class="king-star">★</span>' : '';
            // Add bolt for column accelerator
            const accelBolt = isColumnAccelerator ? '<span class="accel-bolt">⚡</span>' : '';

            // Add percentage change indicator like Skylit (show on left side of cell)
            let pctIndicator = '';
            if (hasDelta) {
                const pctSign = zonePct > 0 ? '+' : '';
                const pctClass = zonePct > 0 ? 'pct-up' : 'pct-down';
                pctIndicator = `<span class="gex-pct ${pctClass}">${pctSign}${Math.round(zonePct)}%</span>`;
            }

            return `<td class="${cellClasses}" style="${bgStyle}">${pctIndicator}${formatGEX(value)}${kingStar}${accelBolt}</td>`;
        }).join('');

        // Add King/Gatekeeper indicator in strike column
        let strikeLabel = formatPrice(strike);
        if (isGlobalKing) {
            strikeLabel = `<span class="strike-king">${strikeLabel}</span>`;
        } else if (isGatekeeper) {
            strikeLabel = `<span class="strike-gatekeeper">${strikeLabel}</span>`;
        }

        // Add data attribute for King row to scroll to it
        const dataAttrs = isGlobalKing ? 'data-king="true"' : '';

        return `
            <tr class="${rowClasses}" ${dataAttrs}>
                <td class="strike-col">${strikeLabel}</td>
                ${cells}
            </tr>
        `;
    }).join('');
}

// Strike Detail Panel - click to show details in 0DTE mode
// Use event delegation to avoid stacking listeners
let strikeClickHandlerAttached = false;

function setupStrikeClickHandlers() {
    if (strikeClickHandlerAttached) return;

    const heatmapBody = document.getElementById('heatmapBody');
    if (!heatmapBody) return;

    heatmapBody.addEventListener('click', (e) => {
        if (expirationMode !== '0dte') return;

        // Find the clicked row
        const row = e.target.closest('tr');
        if (!row) return;

        // Remove previous selection
        document.querySelectorAll('#heatmapBody tr.selected').forEach(r => r.classList.remove('selected'));
        row.classList.add('selected');

        // Add has-selection class to show the detail panel
        const container = document.querySelector('.heatmap-container');
        if (container) container.classList.add('has-selection');

        // Get strike from row
        const strikeCell = row.querySelector('.strike-col');
        if (!strikeCell) return;

        const strikeText = strikeCell.textContent.replace('★', '').replace('◆', '').trim();
        const strike = parseFloat(strikeText);

        // Get GEX value from the row
        const gexCell = row.querySelector('td:not(.strike-col)');
        const gexText = gexCell ? gexCell.textContent.replace('--', '0').trim() : '0';

        updateStrikeDetailPanel(strike, gexText);

        // Scroll to show the strike detail panel
        requestAnimationFrame(() => {
            const panel = document.getElementById('strikeDetailPanel');
            if (panel && panel.offsetParent !== null) {
                const panelRect = panel.getBoundingClientRect();
                const viewportHeight = window.innerHeight;

                // If panel is below the viewport or partially hidden, scroll to it
                if (panelRect.top > viewportHeight - 100 || panelRect.bottom > viewportHeight) {
                    panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
                }
            }
        });
    });

    strikeClickHandlerAttached = true;
}

function updateStrikeDetailPanel(strike, gexValue) {
    selectedStrike = strike;
    console.log('Updating detail panel for strike:', strike, 'GEX:', gexValue);

    // Get the heatmap data
    const heatmap = currentData?.heatmap;
    if (!heatmap) {
        console.warn('No heatmap data available');
        return;
    }

    // Find the strike index and get the expiration
    const strikeIndex = heatmap.strikes.indexOf(strike);
    const expiration = heatmap.expirations[0]; // 0DTE is first expiration

    // Parse GEX value
    const gexNum = parseGEXValue(gexValue);
    const isPositive = gexNum >= 0;

    // Update panel elements - check each exists
    const strikeEl = document.getElementById('detailStrike');
    const expiryEl = document.getElementById('detailExpiry');

    if (strikeEl) strikeEl.textContent = `Strike ${strike.toFixed(0)}`;
    if (expiryEl) expiryEl.textContent = parseLocalDate(expiration).toLocaleDateString('en-US', {
        year: 'numeric', month: '2-digit', day: '2-digit'
    });

    // Badge based on GEX magnitude
    const badge = document.getElementById('detailBadge');
    const absGex = Math.abs(gexNum);
    if (badge) {
        if (absGex > 1000000) {
            badge.textContent = 'HOT';
            badge.className = 'detail-badge hot';
        } else if (absGex > 100000) {
            badge.textContent = 'WARM';
            badge.className = 'detail-badge warm';
        } else {
            badge.textContent = 'COOL';
            badge.className = 'detail-badge cool';
        }
    }

    // Current value
    const valueEl = document.getElementById('detailCurrentValue');
    if (valueEl) {
        valueEl.textContent = gexValue;
        valueEl.className = `detail-value ${isPositive ? 'positive' : 'negative'}`;
    }

    // Trend (placeholder - needs historical data)
    const trendEl = document.getElementById('detailTrend');
    if (trendEl) {
        trendEl.textContent = isPositive ? '↗ Exposure Increasing' : '↘ Exposure Decreasing';
        trendEl.className = `detail-trend ${isPositive ? 'increasing' : 'decreasing'}`;
    }

    // Generate dynamic sparkline based on strike's GEX history
    const sparklineEl = document.getElementById('detailSparkline');
    if (sparklineEl) {
        const sparklinePath = generateStrikeSparkline(strike, gexNum, isPositive);
        const color = isPositive ? '#4ade80' : '#a855f7';
        sparklineEl.innerHTML = `
            <svg class="sparkline-svg" viewBox="0 0 200 60" preserveAspectRatio="none">
                <path d="${sparklinePath.path}" fill="none" stroke="${color}" stroke-width="2"/>
                <circle cx="${sparklinePath.endX}" cy="${sparklinePath.endY}" r="4" fill="${color}"/>
            </svg>
        `;
    }

    // Rate of change (placeholder - needs historical data from backend)
    updateRateOfChange(gexNum);

    // Velocity
    const velocityEl = document.getElementById('detailVelocity');
    const velocityRateEl = document.getElementById('detailVelocityRate');
    if (velocityEl) {
        velocityEl.textContent = isPositive ? '+60.2%' : '-45.3%';
        velocityEl.className = `velocity-value ${isPositive ? 'positive' : 'negative'}`;
    }
    if (velocityRateEl) {
        velocityRateEl.textContent = isPositive ? '(+150.3K/min)' : '(-98.2K/min)';
    }
}

// Generate a unique sparkline path for each strike based on its GEX value
function generateStrikeSparkline(strike, gexNum, isPositive) {
    // Use strike as seed for consistent but unique patterns per strike
    const seed = strike * 1000;
    const points = [];
    const numPoints = 11;

    // Generate points based on strike value for uniqueness
    for (let i = 0; i < numPoints; i++) {
        const x = (i / (numPoints - 1)) * 200;
        // Create variation based on strike value
        const variation = Math.sin(seed + i * 0.7) * 15 + Math.cos(seed * 0.3 + i) * 10;

        // Trend direction based on positive/negative
        const trend = isPositive
            ? 50 - (i / numPoints) * 35  // Upward trend (lower y = higher on chart)
            : 15 + (i / numPoints) * 35; // Downward trend

        const y = Math.max(5, Math.min(55, trend + variation));
        points.push({ x, y });
    }

    // Build SVG path
    const pathParts = points.map((p, i) =>
        i === 0 ? `M${p.x.toFixed(1)},${p.y.toFixed(1)}` : `L${p.x.toFixed(1)},${p.y.toFixed(1)}`
    );

    const lastPoint = points[points.length - 1];

    return {
        path: pathParts.join(' '),
        endX: lastPoint.x.toFixed(1),
        endY: lastPoint.y.toFixed(1)
    };
}

function parseGEXValue(gexStr) {
    // Parse formatted GEX string like "+$5.7M" or "-$123.4K"
    const cleaned = gexStr.replace(/[$,+]/g, '').trim();
    let multiplier = 1;

    if (cleaned.endsWith('B')) {
        multiplier = 1e9;
    } else if (cleaned.endsWith('M')) {
        multiplier = 1e6;
    } else if (cleaned.endsWith('K')) {
        multiplier = 1e3;
    }

    const numPart = parseFloat(cleaned.replace(/[BMK]/g, ''));
    return numPart * multiplier;
}

function updateRateOfChange(currentGex) {
    // Placeholder values - in production this would come from historical snapshots
    // TODO: Add backend endpoint to fetch strike-level history

    const changes = [
        { period: '1m', el: 'change1m', pctEl: 'changePct1m', value: currentGex * 0.05, pct: 5.2 },
        { period: '5m', el: 'change5m', pctEl: 'changePct5m', value: currentGex * 0.15, pct: 15.8 },
        { period: '10m', el: 'change10m', pctEl: 'changePct10m', value: currentGex * 0.25, pct: 28.4 },
    ];

    changes.forEach(c => {
        const isPos = c.value >= 0;
        const valueEl = document.getElementById(c.el);
        const pctEl = document.getElementById(c.pctEl);

        valueEl.textContent = formatGEX(c.value);
        valueEl.className = `change-value ${isPos ? 'positive' : 'negative'}`;

        pctEl.textContent = `${isPos ? '+' : ''}${c.pct.toFixed(1)}%`;
        pctEl.className = `change-pct ${isPos ? 'positive' : 'negative'}`;
    });
}

function renderZones(data) {
    const zones = data.zones.slice(0, 10); // Top 10 zones

    // Get intraday zone deltas for percentage changes
    const zoneDeltas = data.intraday?.zone_deltas || {};
    const hasIntraday = Object.keys(zoneDeltas).length > 0;

    // Find highest POSITIVE GEX zone (Magnet - like Skylit's yellow node)
    // This is the key price target / absorption level
    const positiveZones = zones.filter(z => z.type === 'positive');
    const highestPositiveZone = positiveZones.length > 0
        ? positiveZones.reduce((max, z) => {
            const maxGex = parseFloat(max.gex_formatted.replace(/[$,M]/g, '')) || 0;
            const zGex = parseFloat(z.gex_formatted.replace(/[$,M]/g, '')) || 0;
            return zGex > maxGex ? z : max;
          })
        : null;

    // Find highest NEGATIVE GEX zone (Accelerator - vol expansion zone)
    const negativeZones = zones.filter(z => z.type === 'negative');
    const highestNegativeZone = negativeZones.length > 0
        ? negativeZones.reduce((max, z) => {
            const maxGex = Math.abs(parseFloat(max.gex_formatted.replace(/[$,M-]/g, '')) || 0);
            const zGex = Math.abs(parseFloat(z.gex_formatted.replace(/[$,M-]/g, '')) || 0);
            return zGex > maxGex ? z : max;
          })
        : null;

    // Trading context labels and colors
    const contextLabels = {
        'absorption': { label: 'ABSORPTION', class: 'ctx-absorption', hint: 'Expect bounce/fade' },
        'acceleration': { label: 'ACCELERATION', class: 'ctx-acceleration', hint: 'Vol expansion' },
        'magnet': { label: 'MAGNET', class: 'ctx-magnet', hint: 'Price target' },
        'support': { label: 'SUPPORT', class: 'ctx-support', hint: 'Bounce zone' },
        'resistance': { label: 'RESISTANCE', class: 'ctx-resistance', hint: 'Rejection zone' },
        'neutral': { label: '', class: '', hint: '' }
    };

    elements.zonesList.innerHTML = zones.map(zone => {
        const isPositive = zone.type === 'positive';
        const isKing = zone.role === 'king';
        const isGatekeeper = zone.role === 'gatekeeper';

        // Check if this is the highest positive GEX (Magnet/Yellow Node)
        const isMagnet = highestPositiveZone && zone.strike === highestPositiveZone.strike && isPositive;
        // Check if this is the highest negative GEX (Accelerator)
        const isAccelerator = highestNegativeZone && zone.strike === highestNegativeZone.strike && !isPositive;

        const ctx = contextLabels[zone.trading_context] || contextLabels['neutral'];

        const itemClasses = [
            isPositive ? 'positive' : 'negative',
            isKing ? 'king' : '',
            isGatekeeper ? 'gatekeeper' : '',
            isMagnet ? 'magnet-highlight' : '',
            isAccelerator ? 'accelerator-highlight' : '',
        ].filter(Boolean).join(' ');

        const roleClasses = [
            isKing ? 'king' : '',
            isGatekeeper ? 'gatekeeper' : '',
            isMagnet ? 'magnet' : '',
            isAccelerator ? 'accelerator' : '',
        ].filter(Boolean).join(' ');

        // Determine the role label - prioritize Magnet if highest positive
        let roleLabel = zone.role;
        if (isMagnet && !isKing) {
            roleLabel = 'MAGNET';
        } else if (isAccelerator && !isKing && !isGatekeeper) {
            roleLabel = 'ACCEL';
        }

        // Trading context badge - add MAGNET badge if this is the highest positive
        let contextBadge = '';
        if (isMagnet) {
            contextBadge = `<span class="trading-context ctx-magnet-primary" title="Highest positive GEX - Price magnet">PRICE TARGET</span>`;
        } else if (isAccelerator) {
            contextBadge = `<span class="trading-context ctx-acceleration" title="Highest negative GEX - Vol expansion">VOL ZONE</span>`;
        } else if (ctx.label) {
            contextBadge = `<span class="trading-context ${ctx.class}" title="${ctx.hint}">${ctx.label}</span>`;
        }

        // Get percentage change for this zone
        const zoneData = zoneDeltas[String(zone.strike)] || zoneDeltas[zone.strike.toFixed(1)] || {};
        const zonePct = zoneData.pct_gex || 0;
        const hasPctChange = hasIntraday && zonePct !== 0;

        let pctBadge = '';
        if (hasPctChange) {
            const pctSign = zonePct > 0 ? '+' : '';
            const pctClass = zonePct > 0 ? 'zone-pct-up' : 'zone-pct-down';
            pctBadge = `<span class="zone-pct ${pctClass}">${pctSign}${Math.round(zonePct)}%</span>`;
        }

        return `
            <div class="zone-item ${itemClasses}">
                <div class="zone-header">
                    <span class="zone-strike">${formatPrice(zone.strike)}</span>
                    <span class="zone-role ${roleClasses}">${roleLabel}</span>
                    ${contextBadge}
                </div>
                <div class="zone-gex-row">
                    ${pctBadge}
                    <span class="zone-gex ${isPositive ? 'positive' : 'negative'}">
                        ${zone.gex_formatted}
                    </span>
                </div>
                <div class="zone-strength">
                    <div class="zone-strength-bar ${isPositive ? 'positive' : 'negative'} ${isMagnet ? 'magnet-bar' : ''}"
                         style="width: ${zone.strength * 100}%"></div>
                </div>
            </div>
        `;
    }).join('');
}

// =============================================================================
// NEW FEATURES - Expected Move, GEX Flip, Put/Call Walls, Price Chart
// =============================================================================

let priceChart = null;
let candleSeries = null;
let chartLevelLines = [];

function renderExpectedMove(data) {
    const em = data.expected_move;
    if (!em || !em.iv) {
        return;
    }

    const spotPrice = data.spot_price;

    // Update IV display
    const featureIV = document.getElementById('featureIV');
    if (featureIV) featureIV.textContent = `IV: ${em.iv}%`;

    // Daily range
    const emDailyLow = document.getElementById('emDailyLow');
    const emDailyHigh = document.getElementById('emDailyHigh');
    const emDailySpot = document.getElementById('emDailySpot');

    if (emDailyLow) emDailyLow.textContent = formatPrice(em.daily.low);
    if (emDailyHigh) emDailyHigh.textContent = formatPrice(em.daily.high);

    // Position spot marker within daily range
    if (emDailySpot && em.daily.low && em.daily.high) {
        const range = em.daily.high - em.daily.low;
        const position = range > 0 ? ((spotPrice - em.daily.low) / range) * 80 + 10 : 50;
        emDailySpot.style.left = `${Math.max(5, Math.min(95, position))}%`;
    }

    // Weekly range
    const emWeeklyLow = document.getElementById('emWeeklyLow');
    const emWeeklyHigh = document.getElementById('emWeeklyHigh');
    const emWeeklySpot = document.getElementById('emWeeklySpot');

    if (emWeeklyLow) emWeeklyLow.textContent = formatPrice(em.weekly.low);
    if (emWeeklyHigh) emWeeklyHigh.textContent = formatPrice(em.weekly.high);

    // Position spot marker within weekly range
    if (emWeeklySpot && em.weekly.low && em.weekly.high) {
        const range = em.weekly.high - em.weekly.low;
        const position = range > 0 ? ((spotPrice - em.weekly.low) / range) * 80 + 10 : 50;
        emWeeklySpot.style.left = `${Math.max(5, Math.min(95, position))}%`;
    }
}

function renderGexFlip(data) {
    const flipLevel = data.gex_flip_level;
    const spotPrice = data.spot_price;

    const gexFlipValue = document.getElementById('gexFlipValue');
    const gexFlipDistance = document.getElementById('gexFlipDistance');
    const gexFlipRegime = document.getElementById('gexFlipRegime');

    if (!flipLevel) {
        if (gexFlipValue) gexFlipValue.textContent = '--';
        if (gexFlipDistance) gexFlipDistance.textContent = '';
        if (gexFlipRegime) gexFlipRegime.textContent = '';
        return;
    }

    if (gexFlipValue) gexFlipValue.textContent = formatPrice(flipLevel);

    const distance = spotPrice - flipLevel;
    const distancePct = ((distance / spotPrice) * 100).toFixed(2);
    const isAbove = distance > 0;

    if (gexFlipDistance) {
        const arrow = isAbove ? '↑' : '↓';
        gexFlipDistance.textContent = `${arrow} $${Math.abs(distance).toFixed(2)} (${Math.abs(distancePct)}%)`;
    }

    if (gexFlipRegime) {
        gexFlipRegime.textContent = isAbove ? 'POSITIVE GAMMA' : 'NEGATIVE GAMMA';
        gexFlipRegime.className = `gex-flip-regime ${isAbove ? 'above-flip' : 'below-flip'}`;
    }
}

function renderPutCallWalls(data) {
    const walls = data.put_call_walls;
    if (!walls || !walls.walls || walls.walls.length === 0) {
        return;
    }

    const container = document.getElementById('wallsChart');
    if (!container) return;

    const spotPrice = walls.spot_price || data.spot_price;

    // Find max absolute GEX for scaling
    let maxGex = 0;
    walls.walls.forEach(w => {
        maxGex = Math.max(maxGex, Math.abs(w.call_gex), Math.abs(w.put_gex));
    });

    if (maxGex === 0) maxGex = 1;

    // Render bars with percentages
    container.innerHTML = walls.walls.map(wall => {
        const callWidth = (Math.abs(wall.call_gex) / maxGex) * 45;
        const putWidth = (Math.abs(wall.put_gex) / maxGex) * 45;
        const isCurrentPrice = Math.abs(wall.strike - spotPrice) < (spotPrice * 0.002);

        // Calculate put/call percentages for this strike
        const totalGex = Math.abs(wall.put_gex) + Math.abs(wall.call_gex);
        const putPct = totalGex > 0 ? ((Math.abs(wall.put_gex) / totalGex) * 100).toFixed(0) : 0;
        const callPct = totalGex > 0 ? ((Math.abs(wall.call_gex) / totalGex) * 100).toFixed(0) : 0;

        // Only show % if bar is wide enough (> 8%)
        const showPutPct = putWidth > 8;
        const showCallPct = callWidth > 8;

        return `
            <div class="wall-row ${isCurrentPrice ? 'current-price' : ''}">
                <span class="wall-strike">${formatPrice(wall.strike)}</span>
                <div class="wall-bars">
                    <div class="wall-put-bar" style="width: ${putWidth}%; margin-left: auto;" title="Put GEX: ${formatGEX(wall.put_gex)}">
                        ${showPutPct ? `<span class="wall-pct">${putPct}%</span>` : ''}
                    </div>
                    <div class="wall-center"></div>
                    <div class="wall-call-bar" style="width: ${callWidth}%" title="Call GEX: ${formatGEX(wall.call_gex)}">
                        ${showCallPct ? `<span class="wall-pct">${callPct}%</span>` : ''}
                    </div>
                </div>
            </div>
        `;
    }).join('');
}

async function loadAndRenderChart(symbol, resolution = '5') {
    const chartContainer = document.getElementById('chartContainer');
    const chartWrapper = document.getElementById('priceChart');

    if (!chartContainer || !chartWrapper) return;

    // Show chart container
    chartContainer.style.display = 'block';

    // Fetch candle data
    try {
        const response = await fetch(`${API_BASE}/candles/${symbol}?resolution=${resolution}&count=100`);
        if (!response.ok) {
            console.error('Failed to fetch candles');
            return;
        }

        const data = await response.json();

        // Initialize chart if not exists
        if (!priceChart) {
            priceChart = LightweightCharts.createChart(chartWrapper, {
                width: chartWrapper.clientWidth,
                height: 280,
                layout: {
                    background: { color: '#141414' },
                    textColor: '#a0a0a0',
                },
                grid: {
                    vertLines: { color: '#2a2a2a' },
                    horzLines: { color: '#2a2a2a' },
                },
                crosshair: {
                    mode: LightweightCharts.CrosshairMode.Normal,
                },
                rightPriceScale: {
                    borderColor: '#2a2a2a',
                },
                timeScale: {
                    borderColor: '#2a2a2a',
                    timeVisible: true,
                },
            });

            candleSeries = priceChart.addCandlestickSeries({
                upColor: '#22c55e',
                downColor: '#ef4444',
                borderDownColor: '#ef4444',
                borderUpColor: '#22c55e',
                wickDownColor: '#ef4444',
                wickUpColor: '#22c55e',
            });

            // Handle resize
            window.addEventListener('resize', () => {
                if (priceChart && chartWrapper) {
                    priceChart.applyOptions({ width: chartWrapper.clientWidth });
                }
            });
        }

        // Set candle data
        const candles = data.candles.map(c => ({
            time: c.time,
            open: c.open,
            high: c.high,
            low: c.low,
            close: c.close,
        }));

        candleSeries.setData(candles);

        // Remove old level lines
        chartLevelLines.forEach(line => {
            try { candleSeries.removePriceLine(line); } catch (e) {}
        });
        chartLevelLines = [];

        // Add GEX level lines
        const levels = data.levels;
        if (levels) {
            if (levels.king) {
                chartLevelLines.push(candleSeries.createPriceLine({
                    price: levels.king,
                    color: '#fbbf24',
                    lineWidth: 2,
                    lineStyle: LightweightCharts.LineStyle.Solid,
                    axisLabelVisible: true,
                    title: 'King',
                }));
            }

            if (levels.gatekeeper) {
                chartLevelLines.push(candleSeries.createPriceLine({
                    price: levels.gatekeeper,
                    color: '#a855f7',
                    lineWidth: 2,
                    lineStyle: LightweightCharts.LineStyle.Dashed,
                    axisLabelVisible: true,
                    title: 'GK',
                }));
            }

            if (levels.zero_gamma) {
                chartLevelLines.push(candleSeries.createPriceLine({
                    price: levels.zero_gamma,
                    color: '#3b82f6',
                    lineWidth: 1,
                    lineStyle: LightweightCharts.LineStyle.Dotted,
                    axisLabelVisible: true,
                    title: '0γ',
                }));
            }

            if (levels.gex_flip) {
                chartLevelLines.push(candleSeries.createPriceLine({
                    price: levels.gex_flip,
                    color: '#f97316',
                    lineWidth: 1,
                    lineStyle: LightweightCharts.LineStyle.Dashed,
                    axisLabelVisible: true,
                    title: 'Flip',
                }));
            }

            // Expected move range as area
            if (levels.expected_move && levels.expected_move.daily) {
                chartLevelLines.push(candleSeries.createPriceLine({
                    price: levels.expected_move.daily.high,
                    color: '#666666',
                    lineWidth: 1,
                    lineStyle: LightweightCharts.LineStyle.Dotted,
                    axisLabelVisible: false,
                    title: 'EM+',
                }));
                chartLevelLines.push(candleSeries.createPriceLine({
                    price: levels.expected_move.daily.low,
                    color: '#666666',
                    lineWidth: 1,
                    lineStyle: LightweightCharts.LineStyle.Dotted,
                    axisLabelVisible: false,
                    title: 'EM-',
                }));
            }
        }

        priceChart.timeScale().fitContent();

    } catch (error) {
        console.error('Error loading chart:', error);
    }
}

function closeWallsPopup() {
    const overlay = document.getElementById('wallsOverlay');
    if (overlay) {
        overlay.style.display = 'none';
    }
}

function closeStrikeDetailPanel() {
    const container = document.querySelector('.heatmap-container');
    if (container) {
        container.classList.remove('has-selection');
    }
    // Remove row selection
    document.querySelectorAll('#heatmapBody tr.selected').forEach(r => r.classList.remove('selected'));
    selectedStrike = null;
}

function togglePriceChart() {
    const container = document.getElementById('chartContainer');

    if (container.style.display === 'none') {
        loadAndRenderChart(currentSymbol);
    } else {
        container.style.display = 'none';
    }
}

// =============================================================================
// MAIN FUNCTIONS
// =============================================================================

async function loadSymbol(symbol, forceRefresh = false) {
    try {
        currentSymbol = symbol;
        saveState(); // Persist current symbol

        // Update active tab
        document.querySelectorAll('.symbol-tab').forEach(tab => {
            tab.classList.toggle('active', tab.dataset.symbol === symbol);
        });

        const data = await fetchGEX(symbol, forceRefresh);
        currentData = data; // Store for view switching

        renderHeader(data);
        renderHeatmap(data, currentView);
        renderZones(data);

        // Render new features
        renderExpectedMove(data);
        renderGexFlip(data);

        // If walls chart is visible, update it
        const wallsContainer = document.getElementById('wallsContainer');
        if (wallsContainer && wallsContainer.style.display !== 'none') {
            renderPutCallWalls(data);
        }

        // If price chart is visible, update it
        const chartContainer = document.getElementById('chartContainer');
        if (chartContainer && chartContainer.style.display !== 'none') {
            loadAndRenderChart(symbol);
        }

    } catch (error) {
        console.error('Failed to load symbol:', error);
    }
}

function switchView(view) {
    currentView = view;
    saveState(); // Persist view

    // Update active button
    document.querySelectorAll('.btn-view').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.view === view);
    });

    // Update view title
    if (elements.heatmapViewTitle) {
        const titles = { gex: 'GEX Heatmap', vex: 'VEX Heatmap', dex: 'DEX Heatmap' };
        elements.heatmapViewTitle.textContent = titles[view] || 'GEX Heatmap';
        elements.heatmapViewTitle.className = 'heatmap-view-title';
        if (view === 'vex') elements.heatmapViewTitle.classList.add('vex-view');
        if (view === 'dex') elements.heatmapViewTitle.classList.add('dex-view');
    }

    // Re-render heatmap with new view
    if (currentData) {
        renderHeatmap(currentData, view);
    }
}

function switchExpirationMode(mode) {
    expirationMode = mode;
    saveState(); // Persist expiration mode

    // Update active button
    document.querySelectorAll('.btn-exp').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.exp === mode);
    });

    // Toggle 0DTE mode class on container for styling
    const container = document.querySelector('.heatmap-container');
    if (container) {
        container.classList.toggle('zero-dte-mode', mode === '0dte');
        // Remove selection when switching modes
        container.classList.remove('has-selection');
    }

    // Re-render heatmap with new expiration filter
    if (currentData) {
        renderHeatmap(currentData, currentView);
        // Setup click handlers for 0DTE mode
        setupStrikeClickHandlers();
    }

    // Reset detail panel when switching modes
    selectedStrike = null;
    document.querySelectorAll('#heatmapBody tr.selected').forEach(r => r.classList.remove('selected'));
}

async function refreshAll() {
    await fetchSymbols();
    await loadSymbol(currentSymbol, true);
}

async function handleAddSymbol(symbol) {
    symbol = symbol.trim().toUpperCase();
    if (!symbol) return;

    const searchEl = document.querySelector('.symbol-search');

    // Check if already exists
    if (symbols.find(s => s.symbol === symbol)) {
        showError('Symbol already in list');
        searchEl.classList.add('error');
        setTimeout(() => searchEl.classList.remove('error'), 500);
        return;
    }

    // Show loading state
    searchEl.classList.add('loading');
    elements.symbolInput.disabled = true;
    elements.btnAddSymbol.disabled = true;
    elements.symbolInput.placeholder = `Loading ${symbol}...`;

    try {
        const result = await addSymbol(symbol);
        await fetchSymbols();

        // Switch to the new symbol
        currentSymbol = symbol;
        await loadSymbol(symbol);

        // Clear input
        elements.symbolInput.value = '';
        console.log(result.message);

    } catch (error) {
        searchEl.classList.add('error');
        setTimeout(() => searchEl.classList.remove('error'), 500);
        showError(error.message);
    } finally {
        searchEl.classList.remove('loading');
        elements.symbolInput.disabled = false;
        elements.btnAddSymbol.disabled = false;
        elements.symbolInput.placeholder = 'Add symbol (e.g., AMD)';
        elements.symbolInput.focus();
    }
}

function showError(message) {
    // Show error as a temporary tooltip/toast
    const existing = document.querySelector('.error-toast');
    if (existing) existing.remove();

    const toast = document.createElement('div');
    toast.className = 'error-toast';
    toast.textContent = message;
    document.body.appendChild(toast);

    setTimeout(() => toast.remove(), 3000);
}

// =============================================================================
// SEARCH AUTOCOMPLETE
// =============================================================================

function handleSearchInput(query) {
    // Clear previous timeout
    if (searchTimeout) clearTimeout(searchTimeout);

    // Hide dropdown if empty
    if (!query.trim()) {
        hideSearchDropdown();
        return;
    }

    // Debounce - wait 300ms after typing stops
    searchTimeout = setTimeout(async () => {
        const data = await searchSymbols(query);
        searchResults = data.results || [];
        selectedSearchIndex = -1;
        renderSearchDropdown();
    }, 300);
}

function renderSearchDropdown() {
    if (searchResults.length === 0) {
        hideSearchDropdown();
        return;
    }

    elements.searchDropdown.innerHTML = searchResults.map((item, index) => `
        <div class="search-item ${index === selectedSearchIndex ? 'selected' : ''}"
             data-symbol="${item.symbol}" data-index="${index}">
            <div class="search-item-symbol">${item.symbol}</div>
            <div class="search-item-name">${item.name}</div>
            <div class="search-item-type">${item.type} ${item.exchange ? '· ' + item.exchange : ''}</div>
        </div>
    `).join('');

    elements.searchDropdown.classList.add('show');

    // Add click handlers
    document.querySelectorAll('.search-item').forEach(item => {
        item.addEventListener('click', () => {
            selectSearchResult(item.dataset.symbol);
        });
    });
}

function hideSearchDropdown() {
    elements.searchDropdown.classList.remove('show');
    searchResults = [];
    selectedSearchIndex = -1;
}

function selectSearchResult(symbol) {
    elements.symbolInput.value = symbol;
    hideSearchDropdown();
    handleAddSymbol(symbol);
}

function navigateSearchResults(direction) {
    if (searchResults.length === 0) return;

    if (direction === 'down') {
        selectedSearchIndex = Math.min(selectedSearchIndex + 1, searchResults.length - 1);
    } else {
        selectedSearchIndex = Math.max(selectedSearchIndex - 1, -1);
    }

    renderSearchDropdown();

    // Scroll selected item into view
    const selected = elements.searchDropdown.querySelector('.selected');
    if (selected) selected.scrollIntoView({ block: 'nearest' });
}

async function handleRemoveSymbol(symbol) {
    if (symbols.length <= 1) {
        console.warn('Cannot remove last symbol');
        return;
    }

    try {
        await removeSymbol(symbol);
        await fetchSymbols();

        // If we removed the current symbol, switch to first available
        if (currentSymbol === symbol && symbols.length > 0) {
            currentSymbol = symbols[0].symbol;
            await loadSymbol(currentSymbol);
        }
    } catch (error) {
        console.error('Failed to remove symbol:', error);
    }
}

function setupAutoRefresh() {
    if (autoRefreshTimer) {
        clearInterval(autoRefreshTimer);
    }

    // Fast frontend polling (every 10 seconds by default)
    autoRefreshTimer = setInterval(() => {
        loadSymbol(currentSymbol, false); // Don't force backend refresh
        updateRefreshCountdown();
    }, frontendRefreshSec * 1000);

    console.log(`Frontend auto-refresh set to ${frontendRefreshSec} seconds`);
}

function updateRefreshCountdown() {
    // Update the last update time display
    const now = new Date();
    elements.lastUpdate.textContent = `Last: ${now.toLocaleTimeString()}`;
}

function updateClock() {
    const now = new Date();

    // Format date: "Dec 28, 2025"
    const dateStr = now.toLocaleDateString('en-US', {
        month: 'short',
        day: 'numeric',
        year: 'numeric'
    });

    // Format time: "9:30:45 AM"
    const timeStr = now.toLocaleTimeString('en-US', {
        hour: 'numeric',
        minute: '2-digit',
        second: '2-digit'
    });

    if (elements.currentDate) elements.currentDate.textContent = dateStr;
    if (elements.currentTime) elements.currentTime.textContent = timeStr;
}

// Start clock - updates every second
function startClock() {
    updateClock();
    setInterval(updateClock, 1000);
}

// =============================================================================
// TRINITY MODE
// =============================================================================

function switchViewMode(mode) {
    viewMode = mode;
    saveState(); // Persist view mode

    // Update button states
    document.querySelectorAll('.btn-view-mode').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.mode === mode);
    });

    // Toggle body class
    document.body.classList.toggle('trinity-mode', mode === 'trinity');

    // Show/hide containers
    const mainContent = document.querySelector('.main-content');
    const trinityContainer = document.getElementById('trinityContainer');

    if (mode === 'trinity') {
        mainContent.style.display = 'none';
        trinityContainer.style.display = 'grid';
        initTrinityMode();
    } else {
        mainContent.style.display = '';
        trinityContainer.style.display = 'none';
    }
}

function initTrinityMode() {
    // Setup searchable inputs for each trinity column
    const searchContainers = document.querySelectorAll('.trinity-search');

    searchContainers.forEach((container) => {
        const colIndex = parseInt(container.dataset.col);
        const input = container.querySelector('.trinity-search-input');
        const dropdown = container.querySelector('.trinity-dropdown');

        // Set initial value
        input.value = trinitySymbols[colIndex] || '';

        let searchTimeout = null;

        // Search as you type
        input.addEventListener('input', (e) => {
            const query = e.target.value.trim();

            if (searchTimeout) clearTimeout(searchTimeout);

            if (!query) {
                dropdown.classList.remove('show');
                return;
            }

            // Debounce search
            searchTimeout = setTimeout(async () => {
                const data = await searchSymbols(query);
                const results = data.results || [];

                if (results.length === 0) {
                    dropdown.classList.remove('show');
                    return;
                }

                dropdown.innerHTML = results.map(item => `
                    <div class="trinity-dropdown-item" data-symbol="${item.symbol}">
                        <span class="symbol">${item.symbol}</span>
                        <span class="name">${item.name}</span>
                    </div>
                `).join('');

                dropdown.classList.add('show');

                // Add click handlers
                dropdown.querySelectorAll('.trinity-dropdown-item').forEach(item => {
                    item.addEventListener('click', () => {
                        selectTrinitySymbol(colIndex, item.dataset.symbol, input, dropdown);
                    });
                });
            }, 300);
        });

        // Handle Enter key to select first result or typed symbol
        input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                const firstItem = dropdown.querySelector('.trinity-dropdown-item');
                if (firstItem) {
                    selectTrinitySymbol(colIndex, firstItem.dataset.symbol, input, dropdown);
                } else if (input.value.trim()) {
                    selectTrinitySymbol(colIndex, input.value.trim().toUpperCase(), input, dropdown);
                }
            } else if (e.key === 'Escape') {
                dropdown.classList.remove('show');
            }
        });

        // Hide dropdown when clicking outside
        document.addEventListener('click', (e) => {
            if (!container.contains(e.target)) {
                dropdown.classList.remove('show');
            }
        });
    });

    // Load all three columns
    loadAllTrinityColumns();
}

async function selectTrinitySymbol(colIndex, symbol, input, dropdown) {
    symbol = symbol.toUpperCase();
    input.value = symbol;
    dropdown.classList.remove('show');

    // Check if symbol is already in our list
    const exists = symbols.find(s => s.symbol === symbol);

    if (!exists) {
        // Add the symbol to the dashboard
        try {
            input.disabled = true;
            input.placeholder = `Loading ${symbol}...`;

            await addSymbol(symbol);
            await fetchSymbols(); // Refresh symbol list

        } catch (error) {
            console.error(`Failed to add ${symbol}:`, error);
            // Still try to load it
        } finally {
            input.disabled = false;
            input.placeholder = 'Search symbol...';
            input.value = symbol;
        }
    }

    // Update trinity symbols array and load
    trinitySymbols[colIndex] = symbol;
    saveState(); // Persist trinity symbols
    await loadTrinityColumn(colIndex);
}

async function loadAllTrinityColumns() {
    for (let i = 0; i < 3; i++) {
        await loadTrinityColumn(i);
    }
}

async function loadTrinityColumn(colIndex) {
    const symbol = trinitySymbols[colIndex];
    const column = document.getElementById(`trinityCol${colIndex + 1}`);

    if (!symbol || !column) return;

    try {
        const data = await fetchGEX(symbol);
        trinityData[symbol] = data;
        renderTrinityColumn(column, data);
    } catch (error) {
        console.error(`Failed to load ${symbol} for trinity:`, error);
    }
}

function renderTrinityColumn(column, data) {
    // Update info section
    const priceEl = column.querySelector('.trinity-price');
    const distanceEl = column.querySelector('.trinity-king-distance');
    const kingGexEl = column.querySelector('.trinity-king-gex');

    priceEl.textContent = `$${formatPrice(data.spot_price)}`;

    // King distance - shows how far price is from King
    if (data.king_node) {
        const kingStrike = data.king_node.strike;
        const distance = data.spot_price - kingStrike;  // positive = price above King
        const distancePct = ((distance / data.spot_price) * 100).toFixed(2);  // % of current price
        const arrow = distance > 0 ? '↓' : '↑';  // ↑ means price needs to go up to reach King
        const direction = distance > 0 ? 'above' : 'below';
        distanceEl.textContent = `${arrow} $${Math.abs(distance).toFixed(2)} (${Math.abs(distancePct)}%) ${direction}`;
        distanceEl.className = `trinity-king-distance ${distance > 0 ? 'above' : 'below'}`;

        kingGexEl.innerHTML = `King: <span class="value">${data.king_node.strike} ${data.king_node.gex_formatted}</span>`;
    } else {
        distanceEl.textContent = '--';
        kingGexEl.textContent = 'King: --';
    }

    // Render mini heatmap
    renderTrinityHeatmap(column.querySelector('.trinity-heatmap'), data);
}

function renderTrinityHeatmap(container, data) {
    const heatmap = data.heatmap;
    if (!heatmap) return;

    const strikes = heatmap.strikes;
    const expirations = heatmap.expirations.slice(0, 4); // Only show 4 expirations
    const heatmapData = heatmap.data;

    // Find max for color scaling
    const allValues = heatmapData.flat().filter(v => v !== 0);
    const maxValue = Math.max(...allValues.map(Math.abs)) || 1;

    // Find current price row
    const currentPriceIndex = strikes.findIndex(s => s <= data.spot_price);

    // Global King and Gatekeeper strikes
    const kingStrike = data.king_node?.strike;
    const gatekeeperStrike = data.gatekeeper_node?.strike;

    // Find King (highest positive GEX) for EACH expiration column
    const columnKings = expirations.map((_, colIndex) => {
        let maxVal = 0;
        let maxRowIdx = -1;
        strikes.forEach((_, rowIdx) => {
            const val = heatmapData[rowIdx]?.[colIndex] || 0;
            if (val > maxVal) {
                maxVal = val;
                maxRowIdx = rowIdx;
            }
        });
        return maxRowIdx;
    });

    // Build table
    let html = `
        <table>
            <thead>
                <tr>
                    <th class="strike-col">Strike</th>
                    ${expirations.map(exp => {
                        const d = parseLocalDate(exp);
                        return `<th>${d.toLocaleDateString('en-US', {month: 'short', day: 'numeric'})}</th>`;
                    }).join('')}
                </tr>
            </thead>
            <tbody>
    `;

    strikes.forEach((strike, rowIndex) => {
        const isCurrentPrice = rowIndex === currentPriceIndex;
        const isGlobalKing = strike === kingStrike;
        const isGatekeeper = strike === gatekeeperStrike;

        const rowClasses = [
            isCurrentPrice ? 'current-price' : '',
            isGlobalKing ? 'global-king-row' : ''
        ].filter(Boolean).join(' ');

        const cells = expirations.map((_, colIndex) => {
            const value = heatmapData[rowIndex]?.[colIndex] || 0;
            if (value === 0) return '<td>--</td>';

            const isPositive = value >= 0;
            const isColumnKing = columnKings[colIndex] === rowIndex && value > 0;

            const cellClasses = [
                isPositive ? 'positive' : 'negative',
                isColumnKing ? 'column-king' : ''
            ].filter(Boolean).join(' ');

            const opacity = Math.min(1, Math.abs(value) / maxValue * 0.8 + 0.2);
            const bgColor = isPositive
                ? `rgba(74, 222, 128, ${opacity * 0.3})`
                : `rgba(168, 85, 247, ${opacity * 0.3})`;

            // Add star for column king
            const kingStar = isColumnKing ? '<span class="king-star">★</span>' : '';

            return `<td class="${cellClasses}" style="background: ${bgColor}">
                ${formatGEX(value)}${kingStar}
            </td>`;
        }).join('');

        // Add King/Gatekeeper indicator in strike column
        let strikeLabel = formatPrice(strike);
        if (isGlobalKing) {
            strikeLabel = `<span class="strike-king">${strikeLabel}</span>`;
        } else if (isGatekeeper) {
            strikeLabel = `<span class="strike-gatekeeper">${strikeLabel}</span>`;
        }

        html += `<tr class="${rowClasses}">
            <td class="strike-col">${strikeLabel}</td>
            ${cells}
        </tr>`;
    });

    html += '</tbody></table>';
    container.innerHTML = html;
}

// =============================================================================
// QUIVER QUANT - Alternative Data Functions
// =============================================================================

// Cache for Quiver data
let quiverCache = {
    congress: null,
    darkpool: null,
    insider: null,
    wsb: null,
    lastFetch: {}
};

// Sort state for Quiver tables
let quiverSort = {
    congress: { column: 'date', direction: 'desc' },
    darkpool: { column: 'short_percent', direction: 'desc' },
    insider: { column: 'date', direction: 'desc' },
    wsb: { column: 'mentions', direction: 'desc' }
};

// Sort Quiver data
function sortQuiverData(data, type) {
    if (!data || data.length === 0) return data;

    const { column, direction } = quiverSort[type];
    const sorted = [...data].sort((a, b) => {
        let valA = a[column];
        let valB = b[column];

        // Handle amount strings like "50001.0" or "$1,001 - $15,000"
        if (column === 'amount') {
            // Extract first number from amount string
            const numA = parseFloat(String(valA).replace(/[^0-9.]/g, '')) || 0;
            const numB = parseFloat(String(valB).replace(/[^0-9.]/g, '')) || 0;
            return direction === 'asc' ? numA - numB : numB - numA;
        }

        // Handle numeric values
        if (typeof valA === 'number' && typeof valB === 'number') {
            return direction === 'asc' ? valA - valB : valB - valA;
        }

        // Handle date strings
        if (column === 'date' || column === 'report_date') {
            const dateA = new Date(valA || '1970-01-01');
            const dateB = new Date(valB || '1970-01-01');
            return direction === 'asc' ? dateA - dateB : dateB - dateA;
        }

        // Handle string values
        valA = String(valA || '').toLowerCase();
        valB = String(valB || '').toLowerCase();
        if (valA < valB) return direction === 'asc' ? -1 : 1;
        if (valA > valB) return direction === 'asc' ? 1 : -1;
        return 0;
    });

    return sorted;
}

// Handle column header click for sorting
function handleQuiverSort(type, column) {
    const current = quiverSort[type];

    // Toggle direction if same column, otherwise reset to desc
    if (current.column === column) {
        current.direction = current.direction === 'desc' ? 'asc' : 'desc';
    } else {
        current.column = column;
        current.direction = 'desc';
    }

    // Re-render with sorted data
    const cached = quiverCache[type];
    if (cached) {
        switch (type) {
            case 'congress': renderCongressTrades(cached); break;
            case 'darkpool': renderDarkPool(cached); break;
            case 'insider': renderInsiderTrades(cached); break;
            case 'wsb': renderWSBMentions(cached); break;
        }
    }
}

// Get sort indicator arrow
function getSortIndicator(type, column) {
    const { column: sortCol, direction } = quiverSort[type];
    if (sortCol !== column) return '';
    return direction === 'desc' ? ' ▼' : ' ▲';
}

// Fetch Congress trades
async function fetchCongressTrades() {
    try {
        const response = await fetch(`${API_BASE}/quiver/congress?limit=50`);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();
        quiverCache.congress = data.trades;
        quiverCache.lastFetch.congress = new Date();
        return data.trades;
    } catch (error) {
        console.error('Failed to fetch Congress trades:', error);
        return [];
    }
}

// Fetch Dark Pool data
async function fetchDarkPool() {
    try {
        const response = await fetch(`${API_BASE}/quiver/darkpool?limit=50`);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();
        quiverCache.darkpool = data.data;
        quiverCache.lastFetch.darkpool = new Date();
        return data.data;
    } catch (error) {
        console.error('Failed to fetch Dark Pool data:', error);
        return [];
    }
}

// Fetch Insider trades
async function fetchInsiderTrades() {
    try {
        const response = await fetch(`${API_BASE}/quiver/insider?limit=50`);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();
        quiverCache.insider = data.trades;
        quiverCache.lastFetch.insider = new Date();
        return data.trades;
    } catch (error) {
        console.error('Failed to fetch Insider trades:', error);
        return [];
    }
}

// Fetch WSB mentions
async function fetchWSBMentions() {
    try {
        const response = await fetch(`${API_BASE}/quiver/wsb?limit=30`);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();
        quiverCache.wsb = data.mentions;
        quiverCache.lastFetch.wsb = new Date();
        return data.mentions;
    } catch (error) {
        console.error('Failed to fetch WSB mentions:', error);
        return [];
    }
}

// Fetch Quiver alerts for watchlist
async function fetchQuiverAlerts() {
    try {
        // Get symbols from current watchlist
        const watchlistSymbols = symbols.map(s => s.symbol).join(',');
        const response = await fetch(`${API_BASE}/quiver/alerts?symbols=${watchlistSymbols}`);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return await response.json();
    } catch (error) {
        console.error('Failed to fetch Quiver alerts:', error);
        return { congress: [], insider: [], dark_pool: [], wsb: [] };
    }
}

// Render Congress trades
function renderCongressTrades(trades) {
    const container = document.getElementById('congressContent');
    if (!container) return;

    if (!trades || trades.length === 0) {
        container.innerHTML = '<div class="quiver-loading">No recent Congress trades found</div>';
        return;
    }

    // Sort the data
    const sortedTrades = sortQuiverData(trades, 'congress');

    let html = `
        <div class="quiver-table-header congress">
            <span class="sortable" onclick="handleQuiverSort('congress', 'representative')">Representative${getSortIndicator('congress', 'representative')}</span>
            <span class="sortable" onclick="handleQuiverSort('congress', 'ticker')">Ticker${getSortIndicator('congress', 'ticker')}</span>
            <span class="sortable" onclick="handleQuiverSort('congress', 'transaction')">Transaction${getSortIndicator('congress', 'transaction')}</span>
            <span class="sortable" onclick="handleQuiverSort('congress', 'amount')">Amount${getSortIndicator('congress', 'amount')}</span>
            <span class="sortable" onclick="handleQuiverSort('congress', 'house')">House${getSortIndicator('congress', 'house')}</span>
            <span class="sortable" onclick="handleQuiverSort('congress', 'date')">Date${getSortIndicator('congress', 'date')}</span>
        </div>
    `;

    sortedTrades.forEach(trade => {
        const isPurchase = trade.transaction?.toLowerCase().includes('purchase');
        const partyClass = trade.party === 'R' ? 'R' : trade.party === 'D' ? 'D' : '';
        const ticker = trade.ticker || '--';

        html += `
            <div class="congress-trade-item">
                <div class="congress-name">
                    ${trade.representative || 'Unknown'}
                    <span class="party ${partyClass}">${trade.party || ''} - ${trade.district || ''}</span>
                </div>
                <span class="congress-ticker clickable-ticker" onclick="switchToSymbolFromQuiver('${ticker}')" title="View ${ticker} GEX">${ticker}</span>
                <span class="congress-transaction ${isPurchase ? 'purchase' : 'sale'}">
                    ${trade.transaction || '--'}
                </span>
                <span class="congress-amount">${trade.amount || '--'}</span>
                <span>${trade.house || '--'}</span>
                <span class="congress-date">${trade.date || '--'}</span>
            </div>
        `;
    });

    container.innerHTML = html;

    // Update timestamp
    const updateEl = document.getElementById('congressUpdate');
    if (updateEl && quiverCache.lastFetch.congress) {
        updateEl.textContent = `Updated: ${quiverCache.lastFetch.congress.toLocaleTimeString()}`;
    }
}

// Render Dark Pool data
function renderDarkPool(data) {
    const container = document.getElementById('darkpoolContent');
    if (!container) return;

    if (!data || data.length === 0) {
        container.innerHTML = '<div class="quiver-loading">No dark pool data available</div>';
        return;
    }

    // Sort the data
    const sortedData = sortQuiverData(data, 'darkpool');

    let html = `
        <div class="quiver-table-header darkpool">
            <span class="sortable" onclick="handleQuiverSort('darkpool', 'ticker')">Ticker${getSortIndicator('darkpool', 'ticker')}</span>
            <span>Short Volume Bar</span>
            <span class="sortable" onclick="handleQuiverSort('darkpool', 'short_percent')">Short %${getSortIndicator('darkpool', 'short_percent')}</span>
            <span class="sortable" onclick="handleQuiverSort('darkpool', 'short_volume')">Short Vol${getSortIndicator('darkpool', 'short_volume')}</span>
            <span class="sortable" onclick="handleQuiverSort('darkpool', 'date')">Date${getSortIndicator('darkpool', 'date')}</span>
        </div>
    `;

    sortedData.forEach(item => {
        const shortPct = item.short_percent || 0;
        const pctClass = shortPct > 50 ? 'high' : shortPct > 40 ? 'medium' : 'low';

        html += `
            <div class="darkpool-item">
                <span class="darkpool-ticker clickable-ticker" onclick="switchToSymbolFromQuiver('${item.ticker}')" title="View ${item.ticker} GEX">${item.ticker || '--'}</span>
                <div class="darkpool-bar">
                    <div class="darkpool-bar-fill" style="width: ${shortPct}%"></div>
                </div>
                <span class="darkpool-short-pct ${pctClass}">${shortPct.toFixed(1)}%</span>
                <span class="darkpool-volume">${formatVolume(item.short_volume)}</span>
                <span class="darkpool-volume">${item.date || '--'}</span>
            </div>
        `;
    });

    container.innerHTML = html;

    // Update timestamp
    const updateEl = document.getElementById('darkpoolUpdate');
    if (updateEl && quiverCache.lastFetch.darkpool) {
        updateEl.textContent = `Updated: ${quiverCache.lastFetch.darkpool.toLocaleTimeString()}`;
    }
}

// Render Insider trades
function renderInsiderTrades(trades) {
    const container = document.getElementById('insiderContent');
    if (!container) return;

    if (!trades || trades.length === 0) {
        container.innerHTML = '<div class="quiver-loading">No recent insider trades found</div>';
        return;
    }

    // Sort the data
    const sortedTrades = sortQuiverData(trades, 'insider');

    let html = `
        <div class="quiver-table-header insider">
            <span class="sortable" onclick="handleQuiverSort('insider', 'name')">Insider${getSortIndicator('insider', 'name')}</span>
            <span class="sortable" onclick="handleQuiverSort('insider', 'ticker')">Ticker${getSortIndicator('insider', 'ticker')}</span>
            <span class="sortable" onclick="handleQuiverSort('insider', 'transaction_type')">Type${getSortIndicator('insider', 'transaction_type')}</span>
            <span class="sortable" onclick="handleQuiverSort('insider', 'shares')">Shares${getSortIndicator('insider', 'shares')}</span>
            <span class="sortable" onclick="handleQuiverSort('insider', 'value')">Value${getSortIndicator('insider', 'value')}</span>
            <span class="sortable" onclick="handleQuiverSort('insider', 'date')">Date${getSortIndicator('insider', 'date')}</span>
        </div>
    `;

    sortedTrades.forEach(trade => {
        const isPurchase = trade.transaction_type === 'P';
        const value = trade.value || 0;
        const valueClass = value > 1000000 ? 'large' : '';

        html += `
            <div class="insider-trade-item">
                <div class="insider-name">
                    ${trade.name || 'Unknown'}
                    <span class="title">${trade.title || ''}</span>
                </div>
                <span class="insider-ticker">${trade.ticker || '--'}</span>
                <span class="insider-type ${isPurchase ? 'purchase' : 'sale'}">
                    ${isPurchase ? 'BUY' : 'SELL'}
                </span>
                <span class="insider-shares">${formatNumber(trade.shares)}</span>
                <span class="insider-value ${valueClass}">${formatCurrency(value)}</span>
                <span class="insider-date">${trade.date || '--'}</span>
            </div>
        `;
    });

    container.innerHTML = html;

    // Update timestamp
    const updateEl = document.getElementById('insiderUpdate');
    if (updateEl && quiverCache.lastFetch.insider) {
        updateEl.textContent = `Updated: ${quiverCache.lastFetch.insider.toLocaleTimeString()}`;
    }
}

// Render WSB mentions
function renderWSBMentions(mentions) {
    const container = document.getElementById('wsbContent');
    if (!container) return;

    if (!mentions || mentions.length === 0) {
        container.innerHTML = '<div class="quiver-loading">No WSB mentions found</div>';
        return;
    }

    // Sort the data
    const sortedMentions = sortQuiverData(mentions, 'wsb');

    let html = `
        <div class="quiver-table-header wsb">
            <span class="sortable" onclick="handleQuiverSort('wsb', 'ticker')">Ticker${getSortIndicator('wsb', 'ticker')}</span>
            <span class="sortable" onclick="handleQuiverSort('wsb', 'mentions')">Mentions${getSortIndicator('wsb', 'mentions')}</span>
            <span class="sortable" onclick="handleQuiverSort('wsb', 'sentiment')">Sentiment${getSortIndicator('wsb', 'sentiment')}</span>
            <span>Sample Comments</span>
        </div>
    `;

    sortedMentions.forEach(item => {
        const mentionCount = item.mentions || 0;
        const mentionClass = mentionCount > 50 ? 'hot' : mentionCount > 20 ? 'warm' : 'cool';
        const sentimentLabel = item.sentiment_label || 'Neutral';
        const sentimentClass = sentimentLabel.toLowerCase();
        const comments = (item.sample_comments || []).join(' | ').substring(0, 100);

        html += `
            <div class="wsb-item">
                <span class="wsb-ticker">${item.ticker || '--'}</span>
                <span class="wsb-mentions ${mentionClass}">${mentionCount}</span>
                <span class="wsb-sentiment ${sentimentClass}">${sentimentLabel}</span>
                <span class="wsb-comments">${comments || '--'}</span>
            </div>
        `;
    });

    container.innerHTML = html;

    // Update timestamp
    const updateEl = document.getElementById('wsbUpdate');
    if (updateEl && quiverCache.lastFetch.wsb) {
        updateEl.textContent = `Updated: ${quiverCache.lastFetch.wsb.toLocaleTimeString()}`;
    }
}

// Helper: Format volume
function formatVolume(vol) {
    if (!vol) return '--';
    if (vol >= 1e9) return `${(vol / 1e9).toFixed(1)}B`;
    if (vol >= 1e6) return `${(vol / 1e6).toFixed(1)}M`;
    if (vol >= 1e3) return `${(vol / 1e3).toFixed(1)}K`;
    return vol.toString();
}

// Helper: Format number with commas
function formatNumber(num) {
    if (!num) return '--';
    return num.toLocaleString();
}

// Helper: Format currency
function formatCurrency(val) {
    if (!val) return '--';
    if (val >= 1e6) return `$${(val / 1e6).toFixed(1)}M`;
    if (val >= 1e3) return `$${(val / 1e3).toFixed(0)}K`;
    return `$${val.toFixed(0)}`;
}

// Switch to a symbol from Quiver popup (click on ticker)
function switchToSymbolFromQuiver(ticker) {
    if (!ticker) return;

    // Close all Quiver popups
    document.querySelectorAll('.quiver-overlay').forEach(o => o.style.display = 'none');

    // Check if symbol already exists in our list
    const existingSymbol = symbols.find(s => s.symbol.toUpperCase() === ticker.toUpperCase());

    if (existingSymbol) {
        // Switch to existing symbol
        switchSymbol(ticker.toUpperCase());
    } else {
        // Add and switch to new symbol
        addSymbol(ticker.toUpperCase());
    }

}

// Close Quiver popup
function closeQuiverPopup(popupId) {
    const overlay = document.getElementById(popupId);
    if (overlay) overlay.style.display = 'none';
}

// Open and load Quiver popup
async function openQuiverPopup(type) {
    const overlayId = `${type}Overlay`;
    const overlay = document.getElementById(overlayId);
    if (!overlay) return;

    overlay.style.display = 'flex';

    // Fetch and render data
    switch (type) {
        case 'congress':
            const congressTrades = await fetchCongressTrades();
            renderCongressTrades(congressTrades);
            break;
        case 'darkpool':
            const darkpoolData = await fetchDarkPool();
            renderDarkPool(darkpoolData);
            break;
        case 'insider':
            const insiderTrades = await fetchInsiderTrades();
            renderInsiderTrades(insiderTrades);
            break;
        case 'wsb':
            const wsbMentions = await fetchWSBMentions();
            renderWSBMentions(wsbMentions);
            break;
    }
}

// Update alerts from Quiver for watchlist symbols
async function updateQuiverAlerts() {
    if (symbols.length === 0) return;

    try {
        const alerts = await fetchQuiverAlerts();

        // Combine all alerts
        const allAlerts = [
            ...alerts.congress.map(a => ({ ...a, severity: 'info' })),
            ...alerts.insider.map(a => ({ ...a, severity: 'warning' })),
            ...alerts.dark_pool.map(a => ({ ...a, severity: 'info' })),
            ...alerts.wsb.map(a => ({ ...a, severity: 'info' }))
        ];

        // Update the alerts indicator
        if (allAlerts.length > 0) {
            elements.alertsIndicator.style.display = 'flex';
            elements.alertCount.textContent = allAlerts.length;
            elements.alertsIndicator.dataset.alerts = JSON.stringify(allAlerts);

            // Update button badges
            updateQuiverButtonBadges(alerts);
        }
    } catch (error) {
        console.error('Failed to update Quiver alerts:', error);
    }
}

// Update badges on Quiver buttons showing alert counts
function updateQuiverButtonBadges(alerts) {
    const updateBadge = (btnId, count) => {
        const btn = document.getElementById(btnId);
        if (!btn) return;

        // Remove existing badge
        const existing = btn.querySelector('.alert-badge');
        if (existing) existing.remove();

        // Add new badge if count > 0
        if (count > 0) {
            const badge = document.createElement('span');
            badge.className = 'alert-badge';
            badge.textContent = count;
            btn.appendChild(badge);
        }
    };

    updateBadge('btnCongress', alerts.congress?.length || 0);
    updateBadge('btnInsider', alerts.insider?.length || 0);
    updateBadge('btnDarkPool', alerts.dark_pool?.length || 0);
    updateBadge('btnWSB', alerts.wsb?.length || 0);
}

// =============================================================================
// EVENT HANDLERS
// =============================================================================

function setupEventHandlers() {
    // Refresh interval buttons
    document.querySelectorAll('.btn-interval').forEach(btn => {
        btn.addEventListener('click', async () => {
            const interval = parseInt(btn.dataset.interval);
            console.log('Interval button clicked:', interval, 'minutes');

            document.querySelectorAll('.btn-interval').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');

            await setRefreshInterval(interval);
            console.log('Refresh interval set to:', interval, 'minutes');
        });
    });

    // GEX/VEX view toggle buttons
    document.querySelectorAll('.btn-view').forEach(btn => {
        btn.addEventListener('click', () => {
            const view = btn.dataset.view;
            switchView(view);
        });
    });

    // Heatmap control buttons
    const btnCenterSpot = document.getElementById('btnCenterSpot');
    const btnCenterKing = document.getElementById('btnCenterKing');
    const btnTrendFilter = document.getElementById('btnTrendFilter');
    const trendDropdown = document.getElementById('trendDropdown');
    const btnThemeToggle = document.getElementById('btnThemeToggle');
    const themeIcon = document.getElementById('themeIcon');

    // Center on Spot Price
    if (btnCenterSpot) {
        btnCenterSpot.addEventListener('click', () => {
            scrollToSpotPrice();
            // Brief highlight effect
            btnCenterSpot.classList.add('active');
            setTimeout(() => btnCenterSpot.classList.remove('active'), 300);
        });
    }

    // Center on King (highest value)
    if (btnCenterKing) {
        btnCenterKing.addEventListener('click', () => {
            scrollToKing();
            // Brief highlight effect
            btnCenterKing.classList.add('active');
            setTimeout(() => btnCenterKing.classList.remove('active'), 300);
        });
    }

    // Trend Filter dropdown
    if (btnTrendFilter && trendDropdown) {
        btnTrendFilter.addEventListener('click', (e) => {
            e.stopPropagation();
            trendDropdown.classList.toggle('show');
        });

        // Close dropdown when clicking outside
        document.addEventListener('click', () => {
            trendDropdown.classList.remove('show');
        });

        // Trend filter options
        document.querySelectorAll('.trend-option').forEach(opt => {
            opt.addEventListener('click', (e) => {
                e.stopPropagation();
                const trend = opt.dataset.trend;
                setTrendFilter(trend);
                document.querySelectorAll('.trend-option').forEach(o => o.classList.remove('active'));
                opt.classList.add('active');
                trendDropdown.classList.remove('show');
            });
        });
    }

    // Theme Toggle (Light/Dark)
    if (btnThemeToggle) {
        // Restore saved theme
        const savedTheme = localStorage.getItem('gex_theme') || 'dark';
        if (savedTheme === 'light') {
            document.body.classList.add('light-mode');
            if (themeIcon) themeIcon.textContent = '🌙';
        }

        btnThemeToggle.addEventListener('click', () => {
            document.body.classList.toggle('light-mode');
            const isLight = document.body.classList.contains('light-mode');
            localStorage.setItem('gex_theme', isLight ? 'light' : 'dark');
            if (themeIcon) themeIcon.textContent = isLight ? '🌙' : '☀';
            // Re-render heatmap to update cell colors for new theme
            if (currentData) {
                renderHeatmap(currentData, currentView);
            }
        });
    }

    // Symbol navigation arrows
    const btnPrevSymbol = document.getElementById('btnPrevSymbol');
    const btnNextSymbol = document.getElementById('btnNextSymbol');

    if (btnPrevSymbol) {
        btnPrevSymbol.addEventListener('click', () => navigateSymbol('prev'));
    }
    if (btnNextSymbol) {
        btnNextSymbol.addEventListener('click', () => navigateSymbol('next'));
    }

    // Restore trend filter state
    const savedTrendFilter = localStorage.getItem('gex_trendFilter') || 'all';
    if (savedTrendFilter !== 'all') {
        setTrendFilter(savedTrendFilter);
        document.querySelectorAll('.trend-option').forEach(opt => {
            opt.classList.toggle('active', opt.dataset.trend === savedTrendFilter);
        });
    }

    // Expiration filter toggle (All Dates / 0DTE)
    document.querySelectorAll('.btn-exp').forEach(btn => {
        btn.addEventListener('click', () => {
            const mode = btn.dataset.exp;
            switchExpirationMode(mode);
        });
    });

    // View mode toggle (Single/Trinity)
    document.querySelectorAll('.btn-view-mode').forEach(btn => {
        btn.addEventListener('click', () => {
            const mode = btn.dataset.mode;
            switchViewMode(mode);
        });
    });

    // Manual refresh button
    if (elements.btnRefresh) {
        elements.btnRefresh.addEventListener('click', async () => {
            console.log('Refresh button clicked, forcing refresh for:', currentSymbol);
            elements.btnRefresh.disabled = true;
            elements.btnRefresh.textContent = '↻ Refreshing...';
            try {
                await loadSymbol(currentSymbol, true);
            } finally {
                elements.btnRefresh.disabled = false;
                elements.btnRefresh.textContent = '↻ Refresh Now';
            }
        });
    }

    // Alerts indicator click
    if (elements.alertsIndicator) {
        elements.alertsIndicator.addEventListener('click', (e) => {
            e.stopPropagation();
            const alerts = JSON.parse(elements.alertsIndicator.dataset.alerts || '[]');
            showAlertsPopup(alerts);
        });
        elements.alertsIndicator.style.cursor = 'pointer';
    }

    // Add symbol button
    elements.btnAddSymbol.addEventListener('click', () => {
        hideSearchDropdown();
        handleAddSymbol(elements.symbolInput.value);
    });

    // Symbol input - search as you type
    elements.symbolInput.addEventListener('input', (e) => {
        handleSearchInput(e.target.value);
    });

    // Symbol input - keyboard navigation
    elements.symbolInput.addEventListener('keydown', (e) => {
        if (e.key === 'ArrowDown') {
            e.preventDefault();
            navigateSearchResults('down');
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            navigateSearchResults('up');
        } else if (e.key === 'Enter') {
            e.preventDefault();
            if (selectedSearchIndex >= 0 && searchResults[selectedSearchIndex]) {
                selectSearchResult(searchResults[selectedSearchIndex].symbol);
            } else {
                hideSearchDropdown();
                handleAddSymbol(elements.symbolInput.value);
            }
        } else if (e.key === 'Escape') {
            hideSearchDropdown();
        }
    });

    // Hide dropdown when clicking outside
    document.addEventListener('click', (e) => {
        if (!e.target.closest('.symbol-search')) {
            hideSearchDropdown();
        }
    });

    // =========================================================================
    // NEW FEATURE EVENT HANDLERS
    // =========================================================================

    // Features toggle button (hamburger menu)
    const btnFeaturesToggle = document.getElementById('btnFeaturesToggle');
    if (btnFeaturesToggle) {
        btnFeaturesToggle.addEventListener('click', () => {
            const featuresRow = document.getElementById('featuresRow');
            if (featuresRow) {
                featuresRow.classList.toggle('collapsed');
                btnFeaturesToggle.classList.toggle('active');
            }
        });
    }

    // Put/Call Walls toggle button - opens popup
    const btnWallsToggle = document.getElementById('btnWallsToggle');
    if (btnWallsToggle) {
        btnWallsToggle.addEventListener('click', () => {
            const overlay = document.getElementById('wallsOverlay');
            if (overlay) {
                overlay.style.display = 'flex';
                // Render walls data when opening
                if (currentData) {
                    renderPutCallWalls(currentData);
                }
            }
        });
    }

    // Close Walls button (X)
    const btnCloseWalls = document.getElementById('btnCloseWalls');
    if (btnCloseWalls) {
        btnCloseWalls.addEventListener('click', closeWallsPopup);
    }

    // Click outside walls popup to close
    const wallsOverlay = document.getElementById('wallsOverlay');
    if (wallsOverlay) {
        wallsOverlay.addEventListener('click', (e) => {
            if (e.target === wallsOverlay) {
                closeWallsPopup();
            }
        });
    }

    // Close Strike Detail Panel button (X)
    const btnCloseDetail = document.getElementById('btnCloseDetail');
    if (btnCloseDetail) {
        btnCloseDetail.addEventListener('click', closeStrikeDetailPanel);
    }

    // Close Chart button
    const btnCloseChart = document.getElementById('btnCloseChart');
    if (btnCloseChart) {
        btnCloseChart.addEventListener('click', () => {
            const container = document.getElementById('chartContainer');
            if (container) container.style.display = 'none';
        });
    }

    // Chart resolution buttons
    document.querySelectorAll('.btn-resolution').forEach(btn => {
        btn.addEventListener('click', () => {
            const resolution = btn.dataset.res;
            document.querySelectorAll('.btn-resolution').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            loadAndRenderChart(currentSymbol, resolution);
        });
    });

    // Click outside strike detail panel to close (only in 0DTE mode)
    document.addEventListener('click', (e) => {
        const container = document.querySelector('.heatmap-container');
        const detailPanel = document.getElementById('strikeDetailPanel');
        if (!container || !detailPanel) return;

        // Only handle if in 0DTE mode with selection
        if (!container.classList.contains('zero-dte-mode') || !container.classList.contains('has-selection')) return;

        // Check if click is outside both the heatmap and detail panel
        const heatmapTable = document.getElementById('heatmapTable');
        if (!heatmapTable.contains(e.target) && !detailPanel.contains(e.target)) {
            closeStrikeDetailPanel();
        }
    });

    // =========================================================================
    // QUIVER BUTTONS EVENT HANDLERS
    // =========================================================================

    // Congress button
    const btnCongress = document.getElementById('btnCongress');
    if (btnCongress) {
        btnCongress.addEventListener('click', () => openQuiverPopup('congress'));
    }

    // Dark Pool button
    const btnDarkPool = document.getElementById('btnDarkPool');
    if (btnDarkPool) {
        btnDarkPool.addEventListener('click', () => openQuiverPopup('darkpool'));
    }

    // Insider button
    const btnInsider = document.getElementById('btnInsider');
    if (btnInsider) {
        btnInsider.addEventListener('click', () => openQuiverPopup('insider'));
    }

    // WSB button
    const btnWSB = document.getElementById('btnWSB');
    if (btnWSB) {
        btnWSB.addEventListener('click', () => openQuiverPopup('wsb'));
    }

    // Close Quiver popups - X buttons
    document.querySelectorAll('.btn-close-quiver').forEach(btn => {
        btn.addEventListener('click', () => {
            const popupId = btn.dataset.popup;
            closeQuiverPopup(popupId);
        });
    });

    // Click outside Quiver popups to close
    document.querySelectorAll('.quiver-overlay').forEach(overlay => {
        overlay.addEventListener('click', (e) => {
            if (e.target === overlay) {
                overlay.style.display = 'none';
            }
        });
    });

    // Keyboard shortcuts
    document.addEventListener('keydown', (e) => {
        // ESC to close popups
        if (e.key === 'Escape') {
            closeWallsPopup();
            closeStrikeDetailPanel();
            // Close Quiver popups
            document.querySelectorAll('.quiver-overlay').forEach(o => o.style.display = 'none');
            const chartContainer = document.getElementById('chartContainer');
            if (chartContainer) chartContainer.style.display = 'none';
        }

        // Ctrl+R or F5 to refresh
        if ((e.ctrlKey && e.key === 'r') || e.key === 'F5') {
            e.preventDefault();
            loadSymbol(currentSymbol, true);
        }

        // Arrow keys to navigate symbols
        if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
            const currentIndex = symbols.findIndex(s => s.symbol === currentSymbol);
            let newIndex;

            if (e.key === 'ArrowLeft') {
                newIndex = currentIndex > 0 ? currentIndex - 1 : symbols.length - 1;
            } else {
                newIndex = currentIndex < symbols.length - 1 ? currentIndex + 1 : 0;
            }

            loadSymbol(symbols[newIndex].symbol);
        }
    });

    // =========================================================================
    // LOGOUT BUTTON
    // =========================================================================
    const btnLogout = document.getElementById('btnLogout');
    if (btnLogout) {
        btnLogout.addEventListener('click', async () => {
            try {
                await fetch(`${API_BASE}/auth/logout`, {
                    method: 'POST',
                    credentials: 'include'
                });
            } catch (err) {
                console.log('Logout error:', err);
            }
            // Redirect to login page
            window.location.href = '/login';
        });
    }
}

// =============================================================================
// INITIALIZATION
// =============================================================================

async function init() {
    console.log('GEX Dashboard initializing...');

    // Initialize DOM elements first
    initElements();

    // Start the clock immediately
    startClock();

    setConnectionStatus('', 'Connecting...');

    try {
        // Check API health
        const health = await fetchAPI('/');
        console.log('API connected:', health);

        elements.dataSource.textContent = 'MarketData.app (Real Greeks)';

        // Load symbols and initial data
        await fetchSymbols();

        // Restore UI state from localStorage
        restoreUIState();

        // Check if saved symbol exists in our list, otherwise use first
        const savedSymbolExists = symbols.find(s => s.symbol === currentSymbol);
        if (!savedSymbolExists && symbols.length > 0) {
            currentSymbol = symbols[0].symbol;
        }

        if (symbols.length > 0) {
            await loadSymbol(currentSymbol);
        }

        // Setup auto-refresh
        setupAutoRefresh();

        // Check market status (show MARKET CLOSED badge if after hours/weekend)
        checkMarketStatus();
        // Check market status every minute
        setInterval(checkMarketStatus, 60000);

        // Setup event handlers
        setupEventHandlers();

        // Setup strike click handlers (uses event delegation, only needs to be done once)
        setupStrikeClickHandlers();

        // If was in trinity mode, switch to it
        if (viewMode === 'trinity') {
            switchViewMode('trinity');
        }

        // Fetch Quiver alerts for watchlist (non-blocking)
        updateQuiverAlerts().catch(err => console.log('Quiver alerts not available:', err.message));

        setConnectionStatus('connected', 'Live');

    } catch (error) {
        console.error('Initialization failed:', error);
        setConnectionStatus('error', 'Offline');

        // Show mock data message
        elements.dataSource.textContent = 'Mock (API Offline)';
    }
}

// Restore UI button states from localStorage
function restoreUIState() {
    // Restore view toggle (GEX/VEX/DEX)
    document.querySelectorAll('.btn-view').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.view === currentView);
    });

    // Restore expiration toggle (All Dates/0DTE)
    document.querySelectorAll('.btn-exp').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.exp === expirationMode);
    });

    // Apply 0DTE mode class if needed
    const container = document.querySelector('.heatmap-container');
    if (container && expirationMode === '0dte') {
        container.classList.add('zero-dte-mode');
    }

    // Restore view mode toggle (Single/Trinity)
    document.querySelectorAll('.btn-view-mode').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.mode === viewMode);
    });

    // Restore refresh interval button
    document.querySelectorAll('.btn-interval').forEach(btn => {
        btn.classList.toggle('active', parseInt(btn.dataset.interval) === refreshInterval);
    });

    console.log('Restored state:', { currentSymbol, currentView, expirationMode, viewMode, refreshInterval });
}

// Start the app
document.addEventListener('DOMContentLoaded', init);
