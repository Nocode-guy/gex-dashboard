/**
 * GEX Dashboard - Heatmap JavaScript
 */

// Auto-detect API URL: use current origin in development, relative path in production
const API_BASE = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1'
    ? `${window.location.protocol}//${window.location.host}`
    : '';

// =============================================================================
// AUTH STATE
// =============================================================================
let accessToken = sessionStorage.getItem('gex_access_token') || null;
let tokenRefreshTimer = null;
let isAuthenticated = false;
let currentUser = null;

// Check authentication and refresh token if needed
async function checkAuth() {
    // Try to get token from session storage
    accessToken = sessionStorage.getItem('gex_access_token');

    if (accessToken) {
        isAuthenticated = true;
        // Schedule token refresh (55 min before 60 min expiry)
        scheduleTokenRefresh();
        // Fetch current user info
        await fetchCurrentUser();
        return true;
    }

    // No access token - try to get one using refresh token cookie
    try {
        const refreshRes = await fetch(`${API_BASE}/auth/refresh`, {
            method: 'POST',
            credentials: 'include'
        });

        if (refreshRes.ok) {
            const data = await refreshRes.json();
            accessToken = data.access_token;
            sessionStorage.setItem('gex_access_token', accessToken);
            isAuthenticated = true;
            scheduleTokenRefresh();
            await fetchCurrentUser();
            console.log('Session restored from refresh token');
            return true;
        }
    } catch (err) {
        console.log('Token refresh failed:', err);
    }

    // Check if auth is required
    try {
        const res = await fetch(`${API_BASE}/auth/check`, {
            credentials: 'include'
        });
        const data = await res.json();

        if (data.auth_required) {
            // Auth is required but user is not authenticated
            window.location.href = '/login';
            return false;
        }
    } catch (err) {
        console.log('Auth check failed, continuing without auth');
    }

    return false;
}

// Fetch current user info from /api/me
async function fetchCurrentUser() {
    try {
        const res = await fetch(`${API_BASE}/api/me`, {
            headers: getAuthHeaders(),
            credentials: 'include'
        });
        if (res.ok) {
            currentUser = await res.json();
            updateUserMenu();
        }
    } catch (err) {
        console.log('Failed to fetch user info:', err);
    }
}

// Update user menu with current user info
function updateUserMenu() {
    const userEmailEl = document.getElementById('userEmail');
    const adminLink = document.getElementById('adminLink');

    if (currentUser) {
        if (userEmailEl) userEmailEl.textContent = currentUser.email || 'Signed in';
        if (adminLink) adminLink.style.display = currentUser.is_admin ? 'block' : 'none';
    }
}

// Schedule token refresh before expiry
function scheduleTokenRefresh() {
    if (tokenRefreshTimer) {
        clearTimeout(tokenRefreshTimer);
    }
    // Refresh 10 minutes before expiry (230 minutes, token expires at 240 / 4 hours)
    tokenRefreshTimer = setTimeout(refreshAccessToken, 230 * 60 * 1000);
}

// Refresh the access token
async function refreshAccessToken() {
    try {
        const res = await fetch(`${API_BASE}/auth/refresh`, {
            method: 'POST',
            credentials: 'include'
        });

        if (res.ok) {
            const data = await res.json();
            accessToken = data.access_token;
            sessionStorage.setItem('gex_access_token', accessToken);
            scheduleTokenRefresh();
        } else {
            // Refresh failed, redirect to login
            sessionStorage.removeItem('gex_access_token');
            window.location.href = '/login';
        }
    } catch (err) {
        console.error('Token refresh failed:', err);
    }
}

// Get authorization headers
function getAuthHeaders() {
    const headers = {
        'Content-Type': 'application/json'
    };
    if (accessToken) {
        headers['Authorization'] = `Bearer ${accessToken}`;
    }
    return headers;
}

// State - load from localStorage if available
let currentSymbol = localStorage.getItem('gex_currentSymbol') || 'SPX';
let refreshInterval = parseInt(localStorage.getItem('gex_refreshInterval')) || 1;
let frontendRefreshSec = 10; // Frontend polling (seconds) - balance between freshness and performance
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

// Intraday baseline tracking (reset at market open)
let intradayBaseline = {
    gex: null,
    vex: null,
    dex: null,
    timestamp: null
};

// Recent DEX values for slope calculation
let dexHistory = [];
const DEX_HISTORY_LENGTH = 5; // Track last 5 values

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

// Debounce timer for server sync
let prefSyncTimer = null;

// Save state to localStorage (and optionally sync to server)
function saveState() {
    localStorage.setItem('gex_currentSymbol', currentSymbol);
    localStorage.setItem('gex_refreshInterval', refreshInterval.toString());
    localStorage.setItem('gex_currentView', currentView);
    localStorage.setItem('gex_expirationMode', expirationMode);
    localStorage.setItem('gex_viewMode', viewMode);
    localStorage.setItem('gex_trinitySymbols', JSON.stringify(trinitySymbols));
    localStorage.setItem('gex_trendFilter', trendFilter);

    // Sync to server if authenticated (debounced)
    if (isAuthenticated && accessToken) {
        syncPreferencesToServer();
    }
}

// Sync preferences to server (debounced to avoid too many requests)
function syncPreferencesToServer() {
    if (prefSyncTimer) {
        clearTimeout(prefSyncTimer);
    }
    prefSyncTimer = setTimeout(async () => {
        try {
            const theme = document.body.classList.contains('light-theme') ? 'light' : 'dark';
            await fetch(`${API_BASE}/api/me/preferences`, {
                method: 'PUT',
                headers: getAuthHeaders(),
                credentials: 'include',
                body: JSON.stringify({
                    current_symbol: currentSymbol,
                    refresh_interval: refreshInterval,
                    current_view: currentView,
                    expiration_mode: expirationMode,
                    view_mode: viewMode,
                    trinity_symbols: trinitySymbols,
                    trend_filter: trendFilter,
                    theme: theme
                })
            });
            console.log('[Prefs] Synced to server');
        } catch (err) {
            console.log('[Prefs] Server sync failed:', err);
        }
    }, 2000); // 2 second debounce
}

// Load preferences from server
async function loadPreferencesFromServer() {
    if (!isAuthenticated || !accessToken) return;

    try {
        const res = await fetch(`${API_BASE}/api/me/preferences`, {
            headers: getAuthHeaders(),
            credentials: 'include'
        });

        if (res.ok) {
            const prefs = await res.json();
            console.log('[Prefs] Loaded from server:', prefs);

            // Apply server preferences (override localStorage)
            if (prefs.current_symbol) currentSymbol = prefs.current_symbol;
            if (prefs.refresh_interval) refreshInterval = prefs.refresh_interval;
            if (prefs.current_view) currentView = prefs.current_view;
            if (prefs.expiration_mode) expirationMode = prefs.expiration_mode;
            if (prefs.view_mode) viewMode = prefs.view_mode;
            if (prefs.trinity_symbols) trinitySymbols = prefs.trinity_symbols;
            if (prefs.trend_filter) trendFilter = prefs.trend_filter;
            if (prefs.theme === 'light') {
                document.body.classList.add('light-theme');
            }

            // Save to localStorage for offline use
            localStorage.setItem('gex_currentSymbol', currentSymbol);
            localStorage.setItem('gex_refreshInterval', refreshInterval.toString());
            localStorage.setItem('gex_currentView', currentView);
            localStorage.setItem('gex_expirationMode', expirationMode);
            localStorage.setItem('gex_viewMode', viewMode);
            localStorage.setItem('gex_trinitySymbols', JSON.stringify(trinitySymbols));
            localStorage.setItem('gex_trendFilter', trendFilter);
        }
    } catch (err) {
        console.log('[Prefs] Failed to load from server:', err);
    }
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
    // Find current index - symbols can be strings or objects
    const currentIdx = symbols.findIndex(s => {
        const sym = typeof s === 'string' ? s : s.symbol;
        return sym === currentSymbol;
    });
    if (currentIdx === -1) return;

    let newIdx;
    if (direction === 'prev') {
        newIdx = currentIdx <= 0 ? symbols.length - 1 : currentIdx - 1;
    } else {
        newIdx = currentIdx >= symbols.length - 1 ? 0 : currentIdx + 1;
    }

    const newSymbol = typeof symbols[newIdx] === 'string' ? symbols[newIdx] : symbols[newIdx].symbol;
    currentSymbol = newSymbol;
    loadSymbol(newSymbol);
    renderSymbolTabs(); // Update pill highlighting
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
        // Flow elements
        flowContainer: document.getElementById('flowContainer'),
        flowSentimentValue: document.getElementById('flowSentimentValue'),
        flowPressureValue: document.getElementById('flowPressureValue'),
        flowCallPremium: document.getElementById('flowCallPremium'),
        flowPutPremium: document.getElementById('flowPutPremium'),
        flowNetPremium: document.getElementById('flowNetPremium'),
        sweepsBullish: document.getElementById('sweepsBullish'),
        sweepsBearish: document.getElementById('sweepsBearish'),
        blocksBullish: document.getElementById('blocksBullish'),
        blocksBearish: document.getElementById('blocksBearish'),
        flowPressureBars: document.getElementById('flowPressureBars'),
        flowUpdate: document.getElementById('flowUpdate'),
        heatmapContainer: document.querySelector('.heatmap-container'),
        // New flow metric elements
        flowSweeps: document.getElementById('flowSweeps'),
        flowBlocks: document.getElementById('flowBlocks'),
        flowVelocity: document.getElementById('flowVelocity'),
        roc1m: document.getElementById('roc1m'),
        roc5m: document.getElementById('roc5m'),
        roc10m: document.getElementById('roc10m'),
        // Dealer behavior elements
        dealerStatus: document.getElementById('dealerStatus'),
        dealerAction: document.getElementById('dealerAction'),
        dealerWarning: document.getElementById('dealerWarning'),
        dealerWarningText: document.getElementById('dealerWarningText'),
        deltaGexOpen: document.getElementById('deltaGexOpen'),
        zeroGammaDistance: document.getElementById('zeroGammaDistance'),
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

async function fetchAPI(endpoint, options = {}, retried = false) {
    try {
        const fetchOptions = {
            ...options,
            credentials: 'include'
        };

        // Add auth headers if we have a token
        if (accessToken) {
            fetchOptions.headers = {
                ...fetchOptions.headers,
                'Authorization': `Bearer ${accessToken}`
            };
        }

        const response = await fetch(`${API_BASE}${endpoint}`, fetchOptions);

        // Handle 401 - try to refresh token and retry once
        if (response.status === 401 && !retried) {
            console.log('Token expired, attempting refresh...');
            try {
                const refreshRes = await fetch(`${API_BASE}/auth/refresh`, {
                    method: 'POST',
                    credentials: 'include'
                });

                if (refreshRes.ok) {
                    const data = await refreshRes.json();
                    accessToken = data.access_token;
                    sessionStorage.setItem('gex_access_token', accessToken);
                    scheduleTokenRefresh();
                    console.log('Token refreshed, retrying request...');
                    // Retry the original request with new token
                    return fetchAPI(endpoint, options, true);
                }
            } catch (refreshErr) {
                console.error('Token refresh failed:', refreshErr);
            }
            // Refresh failed, redirect to login
            window.location.href = '/login';
            return null;
        }

        // Handle 403 - check if auth required
        if (response.status === 403) {
            console.log('Forbidden, checking auth status...');
            const authCheck = await fetch(`${API_BASE}/auth/check`, { credentials: 'include' });
            const authData = await authCheck.json();
            if (authData.auth_required && !authData.authenticated) {
                window.location.href = '/login';
                return null;
            }
        }

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
        let userSymbols = [];

        // If authenticated, fetch user's personal symbols first
        if (isAuthenticated && accessToken) {
            try {
                const userData = await fetchAPI('/api/me/symbols');
                userSymbols = userData.symbols || [];
            } catch (err) {
                console.log('Could not fetch user symbols, using server list');
            }
        }

        // Fetch server's full symbol data (for spot prices, net GEX, etc.)
        const data = await fetchAPI(`/symbols?_=${Date.now()}`);
        const serverSymbols = data.symbols || [];

        // If user has saved symbols, filter server data to only their symbols
        if (userSymbols.length > 0) {
            // Map server data by symbol for quick lookup
            const serverMap = {};
            serverSymbols.forEach(s => { serverMap[s.symbol] = s; });

            // Build symbols array with user's symbols, using server data where available
            symbols = userSymbols.map(sym => {
                return serverMap[sym] || { symbol: sym, spot_price: null, net_gex: null };
            });
        } else {
            // Not authenticated or no saved symbols - use server's default list
            symbols = serverSymbols;
        }

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
        const refreshLoop = data.refresh_loop || {};
        const marketOpen = refreshLoop.market_open;
        const isWeekend = refreshLoop.is_weekend;
        const paused = refreshLoop.paused;

        // Update control bar Live indicator
        const liveIndicator = document.getElementById('liveIndicator');
        if (liveIndicator) {
            const dot = liveIndicator.querySelector('.live-dot');
            const text = liveIndicator.querySelector('.live-text');

            if (marketOpen && !paused && !isWeekend) {
                // Market is open - show LIVE
                liveIndicator.classList.remove('stale');
                liveIndicator.classList.add('live');
                if (dot) {
                    dot.classList.remove('stale');
                    dot.classList.add('live');
                }
                if (text) text.textContent = 'Live';
            } else {
                // Market closed - show STALE
                liveIndicator.classList.remove('live');
                liveIndicator.classList.add('stale');
                if (dot) {
                    dot.classList.remove('live');
                    dot.classList.add('stale');
                }
                if (text) text.textContent = isWeekend ? 'Weekend' : 'Stale';
            }
        }

        // Update old badge if it exists
        if (badge) {
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
        }
    } catch (error) {
        console.error('Failed to check market status:', error);
    }
}

async function addSymbol(symbol) {
    // If authenticated, add to user's personal list
    if (isAuthenticated && accessToken) {
        const response = await fetch(`${API_BASE}/api/me/symbols/${symbol}`, {
            method: 'POST',
            headers: getAuthHeaders(),
            credentials: 'include'
        });

        const data = await response.json();

        if (!response.ok) {
            const errorMsg = data.detail || `HTTP ${response.status}`;
            throw new Error(errorMsg);
        }

        return data;
    }

    // Not authenticated - add to server's global list
    const response = await fetch(`${API_BASE}/symbols/${symbol}`, {
        method: 'POST'
    });

    const data = await response.json();

    if (!response.ok) {
        const errorMsg = data.detail || `HTTP ${response.status}`;
        throw new Error(errorMsg);
    }

    return data;
}

async function removeSymbol(symbol) {
    try {
        // If authenticated, remove from user's personal list
        const endpoint = (isAuthenticated && accessToken)
            ? `${API_BASE}/api/me/symbols/${symbol}`
            : `${API_BASE}/symbols/${symbol}`;

        const response = await fetch(endpoint, {
            method: 'DELETE',
            headers: (isAuthenticated && accessToken) ? getAuthHeaders() : {},
            credentials: 'include'
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
// FLOW DATA
// =============================================================================

let flowData = null;
let waveChart = null;
let waveCallSeries = null;
let wavePutSeries = null;
let waveNetSeries = null;
let waveTimeframe = 60; // Default 1 hour
let waveRefreshInterval = null; // Auto-refresh timer

// Start WAVE auto-refresh (every 10 seconds)
function startWaveAutoRefresh() {
    stopWaveAutoRefresh(); // Clear any existing interval
    if (currentSymbol && currentView === 'flow') {
        waveRefreshInterval = setInterval(() => {
            fetchWaveData(currentSymbol, waveTimeframe);
            fetchFlowData(currentSymbol); // Also refresh flow stats
            fetchLeaderboard(); // Also refresh leaderboard
        }, 10000); // 10 seconds
        console.log('[WAVE] Auto-refresh started');
    }
}

// Stop WAVE auto-refresh
function stopWaveAutoRefresh() {
    if (waveRefreshInterval) {
        clearInterval(waveRefreshInterval);
        waveRefreshInterval = null;
        console.log('[WAVE] Auto-refresh stopped');
    }
}

// =============================================================================
// PRICE CHART (Candlestick with GEX Levels)
// =============================================================================

let priceChart = null;
let candleSeries = null;
let chartLevelLines = [];  // For GEX level lines
let chartResolution = '5';
let chartRefreshInterval = null;
let chartLoadedSymbol = null;  // Track which symbol chart is showing

// Heatmap mode variables
let chartMode = 'basic';  // 'basic' or 'heatmap'
let heatmapCanvas = null;
let heatmapCtx = null;
let gexZonesData = [];  // Store zones for heatmap rendering
let heatmapRangeSubscribed = false;  // Track if we've subscribed to range changes

// Start chart auto-refresh (every 10 seconds)
function startChartAutoRefresh() {
    stopChartAutoRefresh();
    if (currentSymbol && currentView === 'chart') {
        chartRefreshInterval = setInterval(() => {
            // Use loadAndRenderChart instead of fetchCandleData
            loadAndRenderChart(currentSymbol, chartResolution);
        }, 30000); // 30 second refresh
        console.log('[Chart] Auto-refresh started (30s interval)');
    }
}

// Stop chart auto-refresh
function stopChartAutoRefresh() {
    if (chartRefreshInterval) {
        clearInterval(chartRefreshInterval);
        chartRefreshInterval = null;
        console.log('[Chart] Auto-refresh stopped');
    }
}

// Initialize price chart
function initPriceChart() {
    const container = document.getElementById('priceChart');
    if (!container || priceChart) return;

    container.innerHTML = '';

    if (typeof LightweightCharts === 'undefined') {
        console.error('[Chart] LightweightCharts not loaded');
        container.innerHTML = '<div class="chart-loading">Chart library not loaded</div>';
        return;
    }

    try {
        priceChart = LightweightCharts.createChart(container, {
            width: container.clientWidth,
            height: container.clientHeight || 500,
            layout: {
                background: { type: 'solid', color: 'transparent' },
                textColor: '#a0a0a0'
            },
            grid: {
                vertLines: { color: 'rgba(255, 255, 255, 0.05)' },
                horzLines: { color: 'rgba(255, 255, 255, 0.05)' }
            },
            crosshair: {
                mode: LightweightCharts.CrosshairMode.Normal,
                vertLine: { color: 'rgba(255, 255, 255, 0.2)', width: 1, style: 2 },
                horzLine: { color: 'rgba(255, 255, 255, 0.2)', width: 1, style: 2 }
            },
            rightPriceScale: {
                borderColor: 'rgba(255, 255, 255, 0.1)',
                scaleMargins: { top: 0.1, bottom: 0.1 }
            },
            timeScale: {
                borderColor: 'rgba(255, 255, 255, 0.1)',
                timeVisible: true,
                secondsVisible: false
            }
        });

        // Create candlestick series
        candleSeries = priceChart.addCandlestickSeries({
            upColor: '#22c55e',
            downColor: '#ef4444',
            borderUpColor: '#22c55e',
            borderDownColor: '#ef4444',
            wickUpColor: '#22c55e',
            wickDownColor: '#ef4444'
        });

        // Handle resize
        const resizeObserver = new ResizeObserver(() => {
            if (priceChart && container.clientWidth > 0 && container.clientHeight > 0) {
                priceChart.applyOptions({
                    width: container.clientWidth,
                    height: container.clientHeight
                });
            }
        });
        resizeObserver.observe(container);

        console.log('[Chart] Price chart initialized');
    } catch (error) {
        console.error('[Chart] Init error:', error);
        container.innerHTML = '<div class="chart-loading">Failed to initialize chart</div>';
    }
}

// Initialize heatmap canvas
function initHeatmapCanvas() {
    heatmapCanvas = document.getElementById('gexHeatmapCanvas');
    if (!heatmapCanvas) {
        console.warn('[Heatmap] Canvas element not found');
        return;
    }
    heatmapCtx = heatmapCanvas.getContext('2d');
    console.log('[Heatmap] Canvas initialized');
}

// Get visible price range from chart
function getChartPriceRange() {
    if (!priceChart || !candleSeries) return null;
    try {
        // Get the price scale and visible range
        const priceScale = priceChart.priceScale('right');
        // Use barsInLogicalRange to get visible data range
        const logicalRange = priceChart.timeScale().getVisibleLogicalRange();
        if (!logicalRange) return null;

        // Get the price range from visible bars
        const barsInfo = candleSeries.barsInLogicalRange(logicalRange);
        if (!barsInfo) return null;

        // Add some padding to the range
        const range = barsInfo.barsBefore !== undefined ?
            { minPrice: barsInfo.from, maxPrice: barsInfo.to } : null;

        if (!range) {
            // Fallback: estimate from candle data
            const data = candleSeries.data ? candleSeries.data() : [];
            if (data.length > 0) {
                let minPrice = Infinity, maxPrice = -Infinity;
                data.forEach(candle => {
                    if (candle.low < minPrice) minPrice = candle.low;
                    if (candle.high > maxPrice) maxPrice = candle.high;
                });
                // Add 2% padding
                const padding = (maxPrice - minPrice) * 0.02;
                return { minPrice: minPrice - padding, maxPrice: maxPrice + padding };
            }
        }
        return range;
    } catch (e) {
        console.warn('[Heatmap] Error getting price range:', e);
        return null;
    }
}

// Render GEX heatmap bands on canvas
function renderGexHeatmap(zones, priceRange) {
    if (!heatmapCtx || chartMode !== 'heatmap' || !zones || zones.length === 0) return;

    const canvas = heatmapCanvas;
    const wrapper = canvas.parentElement;
    if (!wrapper) return;

    const rect = wrapper.getBoundingClientRect();

    // Set canvas size to match container (accounting for device pixel ratio)
    const dpr = window.devicePixelRatio || 1;
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    canvas.style.width = rect.width + 'px';
    canvas.style.height = rect.height + 'px';
    heatmapCtx.scale(dpr, dpr);

    // Clear canvas
    heatmapCtx.clearRect(0, 0, rect.width, rect.height);

    // Use price range or estimate from zones
    let minPrice = priceRange?.minPrice;
    let maxPrice = priceRange?.maxPrice;

    if (!minPrice || !maxPrice) {
        // Estimate from zones
        const strikes = zones.map(z => z.strike);
        minPrice = Math.min(...strikes) * 0.98;
        maxPrice = Math.max(...strikes) * 1.02;
    }

    const pricePerPixel = (maxPrice - minPrice) / rect.height;
    if (pricePerPixel <= 0) return;

    // Find max GEX for normalization
    const maxGex = Math.max(...zones.map(z => Math.abs(z.gex)));
    if (maxGex === 0) return;

    // Calculate band height based on strike spacing
    const strikes = zones.map(z => z.strike).sort((a, b) => a - b);
    let avgSpacing = 1;
    if (strikes.length > 1) {
        let totalSpacing = 0;
        for (let i = 1; i < strikes.length; i++) {
            totalSpacing += strikes[i] - strikes[i-1];
        }
        avgSpacing = totalSpacing / (strikes.length - 1);
    }
    const bandHeight = Math.max(avgSpacing / pricePerPixel, 2);  // Min 2px height

    // Draw horizontal bands for each zone
    zones.forEach(zone => {
        // Calculate Y position (inverted because canvas Y goes down)
        const y = rect.height - ((zone.strike - minPrice) / pricePerPixel);

        // Skip if outside visible range
        if (y < -bandHeight || y > rect.height + bandHeight) return;

        // Normalize GEX to 0-1 intensity
        const intensity = Math.abs(zone.gex) / maxGex;
        const alpha = 0.1 + (intensity * 0.5);  // 0.1 to 0.6 opacity

        // Color: green for positive GEX (support), red for negative GEX (acceleration)
        if (zone.gex > 0) {
            heatmapCtx.fillStyle = `rgba(34, 197, 94, ${alpha})`;  // Green
        } else {
            heatmapCtx.fillStyle = `rgba(239, 68, 68, ${alpha})`;  // Red
        }

        heatmapCtx.fillRect(0, y - bandHeight/2, rect.width, bandHeight);
    });

    console.log(`[Heatmap] Rendered ${zones.length} zones`);
}

// Setup chart mode tabs (Basic/Heatmap)
function setupChartModeTabs() {
    document.querySelectorAll('.chart-mode-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            // Update active state
            document.querySelectorAll('.chart-mode-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');

            // Set mode
            chartMode = btn.dataset.mode;
            console.log(`[Chart] Mode changed to: ${chartMode}`);

            // Toggle canvas visibility
            const canvas = document.getElementById('gexHeatmapCanvas');
            if (canvas) {
                canvas.classList.toggle('visible', chartMode === 'heatmap');
            }

            // Toggle chart background
            if (priceChart) {
                priceChart.applyOptions({
                    layout: {
                        background: {
                            type: 'solid',
                            color: chartMode === 'heatmap' ? 'transparent' : '#1a1a1a'
                        }
                    }
                });
            }

            // Re-render heatmap if in heatmap mode
            if (chartMode === 'heatmap' && gexZonesData.length > 0) {
                setTimeout(() => {
                    const range = getChartPriceRange();
                    renderGexHeatmap(gexZonesData, range);
                }, 50);
            }
        });
    });
}

// Fetch candle data from API
async function fetchCandleData(symbol, resolution = '5', count = 200) {
    if (!symbol) return;

    try {
        const data = await fetchAPI(`/candles/${symbol}?resolution=${resolution}&count=${count}`);
        updatePriceChart(data);
        updateGexOverlays(data.levels);
    } catch (error) {
        console.error('[Chart] Fetch error:', error);
    }
}

// Update price chart with candle data
function updatePriceChart(data) {
    if (!priceChart || !candleSeries || !data) return;

    const candles = data.candles || [];
    if (candles.length === 0) return;

    // Convert candles to lightweight-charts format
    const chartData = candles.map(c => ({
        time: typeof c.time === 'string' ? Math.floor(new Date(c.time).getTime() / 1000) : c.time,
        open: c.open,
        high: c.high,
        low: c.low,
        close: c.close
    }));

    candleSeries.setData(chartData);
    priceChart.timeScale().fitContent();
}

// Update GEX level overlays on chart
function updateGexOverlays(levels) {
    if (!priceChart || !candleSeries || !levels) return;

    // Remove existing price lines
    chartLevelLines.forEach(line => {
        try {
            candleSeries.removePriceLine(line);
        } catch (e) {}
    });
    chartLevelLines = [];

    // Magnet (King) - Cyan with glow effect
    if (levels.king) {
        const kingLine = candleSeries.createPriceLine({
            price: levels.king,
            color: '#00ffff',
            lineWidth: 2,
            lineStyle: LightweightCharts.LineStyle.Solid,
            axisLabelVisible: true,
            title: '🧲 MAGNET'
        });
        chartLevelLines.push(kingLine);
    }

    // Resistance - Orange
    if (levels.gatekeeper) {
        const gkLine = candleSeries.createPriceLine({
            price: levels.gatekeeper,
            color: '#f97316',
            lineWidth: 2,
            lineStyle: LightweightCharts.LineStyle.Solid,
            axisLabelVisible: true,
            title: 'RESIST'
        });
        chartLevelLines.push(gkLine);
    }

    // Zero Gamma / Flip - White dashed
    if (levels.zero_gamma) {
        const zgLine = candleSeries.createPriceLine({
            price: levels.zero_gamma,
            color: '#ffffff',
            lineWidth: 1,
            lineStyle: LightweightCharts.LineStyle.Dashed,
            axisLabelVisible: true,
            title: '0γ FLIP'
        });
        chartLevelLines.push(zgLine);
    }

    // Expected Move bounds - subtle gray
    if (levels.expected_move && levels.expected_move.daily) {
        const upperEM = candleSeries.createPriceLine({
            price: levels.expected_move.daily.high,
            color: '#555555',
            lineWidth: 1,
            lineStyle: LightweightCharts.LineStyle.Dotted,
            axisLabelVisible: false,
            title: '+EM'
        });
        chartLevelLines.push(upperEM);

        const lowerEM = candleSeries.createPriceLine({
            price: levels.expected_move.daily.low,
            color: '#555555',
            lineWidth: 1,
            lineStyle: LightweightCharts.LineStyle.Dotted,
            axisLabelVisible: false,
            title: '-EM'
        });
        chartLevelLines.push(lowerEM);
    }

    // Acceleration Zone - Purple (top call wall)
    if (levels.call_walls && levels.call_walls.length > 0) {
        const topCallWall = levels.call_walls[0];
        // Skip if same as King or Gatekeeper
        if (topCallWall.strike !== levels.king && topCallWall.strike !== levels.gatekeeper) {
            const accelLine = candleSeries.createPriceLine({
                price: topCallWall.strike,
                color: '#a855f7',
                lineWidth: 3,
                lineStyle: LightweightCharts.LineStyle.Solid,
                axisLabelVisible: true,
                title: '⚡ ACCEL'
            });
            chartLevelLines.push(accelLine);
        }
    }

    // Support - Red (top put wall)
    if (levels.put_walls && levels.put_walls.length > 0) {
        const topPutWall = levels.put_walls[0];
        // Skip if same as King or Gatekeeper
        if (topPutWall.strike !== levels.king && topPutWall.strike !== levels.gatekeeper) {
            const supportLine = candleSeries.createPriceLine({
                price: topPutWall.strike,
                color: '#ef4444',
                lineWidth: 2,
                lineStyle: LightweightCharts.LineStyle.Solid,
                axisLabelVisible: true,
                title: 'SUPPORT'
            });
            chartLevelLines.push(supportLine);
        }
    }
}

// Setup chart timeframe buttons
function setupChartTimeframeButtons() {
    const buttons = document.querySelectorAll('.chart-tf-btn');
    buttons.forEach(btn => {
        btn.addEventListener('click', () => {
            buttons.forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            chartResolution = btn.dataset.resolution;
            window.chartInitialized = false; // Reset fit on timeframe change
            if (currentSymbol) {
                loadAndRenderChart(currentSymbol, chartResolution);
            }
        });
    });
}

// =============================================================================
// TRADE TAPE
// =============================================================================

let tapeData = [];
let tapePaused = false;
let tapeMinPremium = 100000; // Default $100K filter
let lastTapeTradeIds = new Set(); // Track seen trades to avoid duplicates

// Fetch trades from API (disabled until real trade data available)
async function fetchTradeTape(symbol = null) {
    // Tape disabled - uncomment when trade data is available
    return;
}

// =============================================================================
// LEADERBOARD
// =============================================================================

let leaderboardData = [];
let leaderboardSort = 'total'; // 'total' or 'wave'

// Fetch leaderboard from API
async function fetchLeaderboard() {
    try {
        const data = await fetchAPI('/flow/leaderboard?limit=20');
        if (data && data.leaderboard) {
            leaderboardData = data.leaderboard;
            renderLeaderboard();
        }
    } catch (error) {
        console.error('[Leaderboard] Fetch error:', error);
    }
}

// Render ticker and popup leaderboard
function renderLeaderboard() {
    renderTickerLeaderboard();
    renderPopupLeaderboard();
}

// Render horizontal scrolling ticker
function renderTickerLeaderboard() {
    const container = document.getElementById('flowTickerContent');
    if (!container) return;

    if (leaderboardData.length === 0) {
        container.innerHTML = '<span class="ticker-loading">Loading top flow...</span>';
        return;
    }

    // Sort by total premium for ticker
    const sorted = [...leaderboardData].sort((a, b) => b.total_premium - a.total_premium);

    // Create ticker items (duplicate for seamless loop)
    const tickerHtml = sorted.map(item => {
        const sentiment = item.sentiment || 'neutral';
        const wavePct = item.wave_pct || 0;
        const waveSign = wavePct >= 0 ? '+' : '';
        const totalPremium = formatPremium(item.total_premium || 0);
        const arrow = sentiment === 'bullish' ? '▲' : sentiment === 'bearish' ? '▼' : '●';

        return `
            <span class="ticker-item ${sentiment}" data-symbol="${item.symbol}">
                <span class="ticker-symbol">${item.symbol}</span>
                <span class="ticker-premium">${totalPremium}</span>
                <span class="ticker-wave ${sentiment}">${arrow} ${waveSign}${wavePct.toFixed(1)}%</span>
            </span>
        `;
    }).join('<span class="ticker-divider">|</span>');

    // Duplicate content for seamless scrolling
    container.innerHTML = tickerHtml + '<span class="ticker-divider">|</span>' + tickerHtml;

    // Add click handlers
    container.querySelectorAll('.ticker-item').forEach(item => {
        item.addEventListener('click', () => {
            const symbol = item.dataset.symbol;
            if (symbol && symbol !== currentSymbol) {
                loadSymbol(symbol);
            }
        });
    });
}

// Render popup leaderboard
function renderPopupLeaderboard() {
    const container = document.getElementById('leaderboardPopupList');
    if (!container) return;

    if (leaderboardData.length === 0) {
        container.innerHTML = '<div class="leaderboard-loading">No flow data yet...</div>';
        return;
    }

    // Sort data
    const sorted = [...leaderboardData].sort((a, b) => {
        if (leaderboardSort === 'wave') {
            return Math.abs(b.wave_pct) - Math.abs(a.wave_pct);
        }
        return b.total_premium - a.total_premium;
    });

    container.innerHTML = sorted.map((item, index) => {
        const sentiment = item.sentiment || 'neutral';
        const wavePct = item.wave_pct || 0;
        const waveSign = wavePct >= 0 ? '+' : '';
        const totalPremium = formatPremium(item.total_premium || 0);
        const isActive = item.symbol === currentSymbol;

        return `
            <div class="leaderboard-item ${sentiment} ${isActive ? 'active' : ''}" data-symbol="${item.symbol}">
                <div class="leaderboard-rank">${index + 1}</div>
                <div class="leaderboard-symbol">${item.symbol}</div>
                <div class="leaderboard-stats">
                    <div class="leaderboard-premium">${totalPremium}</div>
                    <div class="leaderboard-wave ${sentiment}">${waveSign}${wavePct.toFixed(1)}%</div>
                </div>
                <div class="leaderboard-sentiment ${sentiment}">
                    ${sentiment === 'bullish' ? '▲' : sentiment === 'bearish' ? '▼' : '●'}
                </div>
            </div>
        `;
    }).join('');

    // Add click handlers
    container.querySelectorAll('.leaderboard-item').forEach(item => {
        item.addEventListener('click', () => {
            const symbol = item.dataset.symbol;
            if (symbol && symbol !== currentSymbol) {
                loadSymbol(symbol);
                closeLeaderboardPopup();
            }
        });
    });
}

// Show/hide leaderboard popup
function openLeaderboardPopup() {
    const overlay = document.getElementById('leaderboardOverlay');
    if (overlay) {
        overlay.style.display = 'flex';
        renderPopupLeaderboard();
    }
}

function closeLeaderboardPopup() {
    const overlay = document.getElementById('leaderboardOverlay');
    if (overlay) {
        overlay.style.display = 'none';
    }
}

// Setup leaderboard controls (ticker expand + popup sort)
function setupLeaderboardControls() {
    // Expand button
    const expandBtn = document.getElementById('btnExpandTicker');
    if (expandBtn) {
        expandBtn.addEventListener('click', openLeaderboardPopup);
    }

    // Close button
    const closeBtn = document.getElementById('btnCloseLeaderboard');
    if (closeBtn) {
        closeBtn.addEventListener('click', closeLeaderboardPopup);
    }

    // Overlay click to close
    const overlay = document.getElementById('leaderboardOverlay');
    if (overlay) {
        overlay.addEventListener('click', (e) => {
            if (e.target === overlay) closeLeaderboardPopup();
        });
    }

    // Sort buttons in popup
    const buttons = document.querySelectorAll('.leaderboard-popup-header .leaderboard-sort-btn');
    buttons.forEach(btn => {
        btn.addEventListener('click', () => {
            buttons.forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            leaderboardSort = btn.dataset.sort || 'total';
            renderPopupLeaderboard();
        });
    });
}

// Update tape with new trades
function updateTradeTape(trades) {
    const container = document.getElementById('tradeTapeList');
    const countEl = document.getElementById('tapeTradeCount');
    if (!container) return;

    // Clear loading message on first load
    if (container.querySelector('.tape-loading')) {
        container.innerHTML = '';
    }

    // Filter out trades we've already seen
    const newTrades = trades.filter(t => {
        const tradeId = `${t.symbol}-${t.strike}-${t.timestamp}-${t.premium}`;
        if (lastTapeTradeIds.has(tradeId)) return false;
        lastTapeTradeIds.add(tradeId);
        return true;
    });

    // Keep track set from growing too large
    if (lastTapeTradeIds.size > 500) {
        const arr = Array.from(lastTapeTradeIds);
        lastTapeTradeIds = new Set(arr.slice(-200));
    }

    // Add new trades to the top with animation
    newTrades.reverse().forEach(trade => {
        const tradeEl = createTradeElement(trade);
        tradeEl.classList.add('tape-trade-new');
        container.insertBefore(tradeEl, container.firstChild);

        // Remove animation class after animation completes
        setTimeout(() => tradeEl.classList.remove('tape-trade-new'), 500);
    });

    // Limit total trades shown
    while (container.children.length > 50) {
        container.removeChild(container.lastChild);
    }

    // Update count
    if (countEl) {
        countEl.textContent = container.children.length;
    }

    tapeData = trades;
}

// Create a single trade element
function createTradeElement(trade) {
    const el = document.createElement('div');
    el.className = `tape-trade ${trade.sentiment || 'neutral'}`;

    const premium = trade.premium || 0;
    const premiumStr = formatPremium(premium);
    const strike = trade.strike || 0;
    const contractType = (trade.contract_type || 'C').toUpperCase().charAt(0);
    const tradeType = trade.trade_type || '';
    const size = trade.size || 0;
    const symbol = trade.symbol || currentSymbol || 'SPY';

    // Format expiration
    let expStr = '';
    if (trade.expiration) {
        const exp = new Date(trade.expiration);
        expStr = `${exp.getMonth()+1}/${exp.getDate()}`;
    }

    // Determine badge
    let badge = '';
    if (tradeType === 'sweep') badge = '<span class="trade-badge sweep">SWEEP</span>';
    else if (tradeType === 'block') badge = '<span class="trade-badge block">BLOCK</span>';

    // Time
    let timeStr = '';
    if (trade.timestamp) {
        const t = new Date(trade.timestamp);
        timeStr = t.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    }

    el.innerHTML = `
        <div class="tape-trade-main">
            <span class="trade-symbol">${symbol}</span>
            <span class="trade-contract ${contractType === 'C' ? 'call' : 'put'}">${strike}${contractType}</span>
            <span class="trade-exp">${expStr}</span>
            ${badge}
        </div>
        <div class="tape-trade-details">
            <span class="trade-size">${size.toLocaleString()} @ </span>
            <span class="trade-premium">${premiumStr}</span>
            <span class="trade-time">${timeStr}</span>
        </div>
    `;

    return el;
}

// Setup tape controls
function setupTapeControls() {
    const filterSelect = document.getElementById('tapePremiumFilter');
    const pauseBtn = document.getElementById('tapePauseBtn');

    if (filterSelect) {
        filterSelect.addEventListener('change', (e) => {
            tapeMinPremium = parseInt(e.target.value) || 100000;
            // Clear current trades and refetch
            const container = document.getElementById('tradeTapeList');
            if (container) container.innerHTML = '<div class="tape-loading">Loading trades...</div>';
            lastTapeTradeIds.clear();
            fetchTradeTape(currentSymbol);
        });
    }

    if (pauseBtn) {
        pauseBtn.addEventListener('click', () => {
            tapePaused = !tapePaused;
            pauseBtn.querySelector('.pause-icon').textContent = tapePaused ? '▶' : '⏸';
            pauseBtn.classList.toggle('paused', tapePaused);
        });
    }
}

// Initialize WAVE chart using lightweight-charts
function initWaveChart() {
    const container = document.getElementById('waveChart');
    if (!container || waveChart) return;

    // Clear loading message
    container.innerHTML = '';

    // Check if LightweightCharts is available
    if (typeof LightweightCharts === 'undefined') {
        console.error('LightweightCharts not loaded');
        container.innerHTML = '<div class="wave-loading">Chart library not available</div>';
        return;
    }

    try {
        waveChart = LightweightCharts.createChart(container, {
            width: container.clientWidth,
            height: 200,
            layout: {
                background: { type: 'solid', color: 'transparent' },
                textColor: '#a0a0a0'
            },
            grid: {
                vertLines: { color: 'rgba(255,255,255,0.05)' },
                horzLines: { color: 'rgba(255,255,255,0.05)' }
            },
            timeScale: {
                timeVisible: true,
                secondsVisible: false,
                borderColor: 'rgba(255,255,255,0.1)'
            },
            rightPriceScale: {
                borderColor: 'rgba(255,255,255,0.1)',
                scaleMargins: { top: 0.1, bottom: 0.1 }
            },
            crosshair: {
                mode: LightweightCharts.CrosshairMode.Normal,
                vertLine: { color: 'rgba(255,255,255,0.2)' },
                horzLine: { color: 'rgba(255,255,255,0.2)' }
            }
        });

        // Call flow (green area)
        waveCallSeries = waveChart.addAreaSeries({
            lineColor: '#22c55e',
            topColor: 'rgba(34, 197, 94, 0.4)',
            bottomColor: 'rgba(34, 197, 94, 0.0)',
            lineWidth: 2,
            priceFormat: { type: 'custom', formatter: (price) => formatPremium(price * 1e6) }
        });

        // Put flow (red area)
        wavePutSeries = waveChart.addAreaSeries({
            lineColor: '#ef4444',
            topColor: 'rgba(239, 68, 68, 0.4)',
            bottomColor: 'rgba(239, 68, 68, 0.0)',
            lineWidth: 2,
            priceFormat: { type: 'custom', formatter: (price) => formatPremium(price * 1e6) }
        });

        // Net WAVE line (blue)
        waveNetSeries = waveChart.addLineSeries({
            color: '#3b82f6',
            lineWidth: 3,
            priceFormat: { type: 'custom', formatter: (price) => formatPremium(price * 1e6) }
        });

        // Handle resize
        const resizeObserver = new ResizeObserver(() => {
            if (waveChart && container.clientWidth > 0) {
                waveChart.applyOptions({ width: container.clientWidth });
            }
        });
        resizeObserver.observe(container);

        console.log('[WAVE] Chart initialized');
    } catch (error) {
        console.error('[WAVE] Chart init error:', error);
        container.innerHTML = '<div class="wave-loading">Failed to initialize chart</div>';
    }
}

// Fetch WAVE data from API
async function fetchWaveData(symbol, minutes = 60) {
    if (!symbol) return;

    try {
        const data = await fetchAPI(`/flow/${symbol}/wave?minutes=${minutes}`);
        updateWaveChart(data);
        updateWaveStats(data);
    } catch (error) {
        console.error('[WAVE] Fetch error:', error);
    }
}

// Update WAVE chart with data
function updateWaveChart(data) {
    if (!waveChart || !data) return;

    const history = data.wave_history || [];

    if (history.length === 0) {
        // No history - show current value as single point if available
        if (data.current_wave) {
            const now = Math.floor(Date.now() / 1000);
            const callVal = (data.current_wave.cumulative_call || 0) / 1e6;
            const putVal = (data.current_wave.cumulative_put || 0) / 1e6;
            const netVal = (data.current_wave.wave_value || 0) / 1e6;

            waveCallSeries.setData([{ time: now, value: callVal }]);
            wavePutSeries.setData([{ time: now, value: putVal }]);
            waveNetSeries.setData([{ time: now, value: netVal }]);
        }
        return;
    }

    // Convert history to chart format (values in millions for readability)
    const callData = history.map(d => ({
        time: Math.floor(new Date(d.timestamp).getTime() / 1000),
        value: (parseFloat(d.cumulative_call) || 0) / 1e6
    }));

    const putData = history.map(d => ({
        time: Math.floor(new Date(d.timestamp).getTime() / 1000),
        value: (parseFloat(d.cumulative_put) || 0) / 1e6
    }));

    const netData = history.map(d => ({
        time: Math.floor(new Date(d.timestamp).getTime() / 1000),
        value: (parseFloat(d.wave_value) || 0) / 1e6
    }));

    waveCallSeries.setData(callData);
    wavePutSeries.setData(putData);
    waveNetSeries.setData(netData);

    // Fit content
    waveChart.timeScale().fitContent();
}

// Update WAVE stats display
function updateWaveStats(data) {
    const current = data.current_wave;
    if (!current) return;

    const waveValue = document.getElementById('waveValue');
    const waveCallValue = document.getElementById('waveCallValue');
    const wavePutValue = document.getElementById('wavePutValue');
    const waveDirection = document.getElementById('waveDirection');

    if (waveValue) {
        const net = current.wave_value || 0;
        waveValue.textContent = formatPremium(net);
        waveValue.className = `wave-value ${net > 0 ? 'positive' : net < 0 ? 'negative' : ''}`;
    }

    if (waveCallValue) {
        waveCallValue.textContent = formatPremium(current.cumulative_call || 0);
    }

    if (wavePutValue) {
        wavePutValue.textContent = formatPremium(current.cumulative_put || 0);
    }

    if (waveDirection) {
        const pct = current.wave_pct || 0;
        if (pct > 10) {
            waveDirection.textContent = 'BULLISH';
            waveDirection.className = 'wave-direction bullish';
        } else if (pct < -10) {
            waveDirection.textContent = 'BEARISH';
            waveDirection.className = 'wave-direction bearish';
        } else {
            waveDirection.textContent = 'NEUTRAL';
            waveDirection.className = 'wave-direction neutral';
        }
    }
}

// Setup WAVE timeframe buttons
function setupWaveTimeframeButtons() {
    const buttons = document.querySelectorAll('.wave-tf-btn');
    buttons.forEach(btn => {
        btn.addEventListener('click', () => {
            buttons.forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            waveTimeframe = parseInt(btn.dataset.minutes) || 60;
            fetchWaveData(currentSymbol, waveTimeframe);
        });
    });
}

async function fetchFlowData(symbol) {
    if (!symbol) return;

    // Only show loading on first load, not on updates (prevents flickering)
    if (elements.flowPressureBars && !flowData) {
        elements.flowPressureBars.innerHTML = '<div class="flow-loading">Loading flow data...</div>';
    }

    try {
        // Calculate strike range to get ~40 strikes for all symbols
        // SPY/QQQ have $1 strikes, SPX has $5 strikes, so scale by price level
        const spotPrice = currentData?.spot_price || 100;
        // Target ~40 strikes: for SPY ($680, $1 strikes) = 40 range
        // For SPX ($6800, $5 strikes) = 200 range (40 * 5)
        const strikeInterval = spotPrice > 1000 ? 5 : 1;
        const strikeRange = 40 * strikeInterval;

        const data = await fetchAPI(`/flow/${symbol}?strike_range=${strikeRange}`);
        flowData = data;
        renderFlowData(data);
    } catch (error) {
        console.error('Failed to fetch flow data:', error);
        if (elements.flowPressureBars) {
            elements.flowPressureBars.innerHTML = '<div class="flow-loading">Failed to load flow data</div>';
        }
    }
}

function renderFlowData(data) {
    if (!data) return;

    // Update summary stats
    if (elements.flowSentimentValue) {
        elements.flowSentimentValue.textContent = data.sentiment?.toUpperCase() || 'NEUTRAL';
        elements.flowSentimentValue.className = `sentiment-value ${data.sentiment || 'neutral'}`;
    }

    if (elements.flowPressureValue) {
        const pct = data.pressure_pct || 0;
        elements.flowPressureValue.textContent = `${pct > 0 ? '+' : ''}${pct.toFixed(1)}%`;
        elements.flowPressureValue.className = `sentiment-pressure ${pct > 0 ? 'positive' : pct < 0 ? 'negative' : ''}`;
    }

    if (elements.flowCallPremium) {
        elements.flowCallPremium.textContent = formatPremium(data.total_call_premium || 0);
    }
    if (elements.flowPutPremium) {
        elements.flowPutPremium.textContent = formatPremium(data.total_put_premium || 0);
    }
    if (elements.flowNetPremium) {
        const net = data.net_premium || 0;
        elements.flowNetPremium.textContent = `${net >= 0 ? '+' : ''}${formatPremium(net)}`;
        elements.flowNetPremium.style.color = net > 0 ? 'var(--accent-green)' : net < 0 ? 'var(--accent-red)' : '';
    }

    // Update sweeps count (call sweeps / put sweeps)
    if (elements.flowSweeps) {
        // Support both flat and nested formats
        const callSweeps = data.call_sweeps ?? data.sweeps?.bullish ?? 0;
        const putSweeps = data.put_sweeps ?? data.sweeps?.bearish ?? 0;
        elements.flowSweeps.textContent = `${callSweeps} / ${putSweeps}`;
    }

    // Update blocks count (call blocks / put blocks)
    if (elements.flowBlocks) {
        const callBlocks = data.call_blocks ?? data.blocks?.bullish ?? 0;
        const putBlocks = data.put_blocks ?? data.blocks?.bearish ?? 0;
        elements.flowBlocks.textContent = `${callBlocks} / ${putBlocks}`;
    }

    // Update 1m velocity (net premium per minute approximation)
    if (elements.flowVelocity) {
        const velocity = data.velocity_1m || Math.abs(data.net_premium / 60) || 0;
        elements.flowVelocity.textContent = formatPremium(velocity);
    }

    // Update rate of change values (placeholder for now)
    if (elements.roc1m) {
        const roc = data.roc_1m || 0;
        elements.roc1m.textContent = `${roc >= 0 ? '+' : ''}${formatPremium(roc)}`;
        elements.roc1m.className = `roc-value ${roc > 0 ? 'positive' : roc < 0 ? 'negative' : ''}`;
    }
    if (elements.roc5m) {
        const roc = data.roc_5m || 0;
        elements.roc5m.textContent = `${roc >= 0 ? '+' : ''}${formatPremium(roc)}`;
        elements.roc5m.className = `roc-value ${roc > 0 ? 'positive' : roc < 0 ? 'negative' : ''}`;
    }
    if (elements.roc10m) {
        const roc = data.roc_10m || 0;
        elements.roc10m.textContent = `${roc >= 0 ? '+' : ''}${formatPremium(roc)}`;
        elements.roc10m.className = `roc-value ${roc > 0 ? 'positive' : roc < 0 ? 'negative' : ''}`;
    }

    // Update spot price indicator in header
    const spotIndicator = document.getElementById('flowSpotIndicator');
    if (spotIndicator && data.spot_price) {
        spotIndicator.textContent = `Spot: $${data.spot_price.toFixed(2)}`;
    }

    // Render pressure bars (strike distribution)
    renderPressureBars(data.strike_pressure || {}, data.spot_price || 0);
}


function formatPremium(value) {
    if (Math.abs(value) >= 1e9) {
        return `$${(value / 1e9).toFixed(2)}B`;
    } else if (Math.abs(value) >= 1e6) {
        return `$${(value / 1e6).toFixed(2)}M`;
    } else if (Math.abs(value) >= 1e3) {
        return `$${(value / 1e3).toFixed(0)}K`;
    }
    return `$${value.toFixed(0)}`;
}

function renderPressureBars(strikePressure, spotPrice) {
    if (!elements.flowPressureBars) return;

    // Convert object to sorted array
    const allStrikes = Object.entries(strikePressure)
        .map(([strike, data]) => ({
            strike: parseFloat(strike),
            ...data
        }))
        .sort((a, b) => b.strike - a.strike);

    if (allStrikes.length === 0) {
        elements.flowPressureBars.innerHTML = '<div class="flow-loading">No flow data available</div>';
        return;
    }

    // Limit to 30 strikes centered on spot price (15 above, 15 below)
    const STRIKES_ABOVE = 15;
    const STRIKES_BELOW = 15;

    // Find the strike closest to spot price
    let closestIdx = 0;
    let closestDiff = Infinity;
    allStrikes.forEach((s, idx) => {
        const diff = Math.abs(s.strike - spotPrice);
        if (diff < closestDiff) {
            closestDiff = diff;
            closestIdx = idx;
        }
    });

    // Calculate start and end indices
    // Since array is sorted descending (high to low), above spot = lower index
    let startIdx = Math.max(0, closestIdx - STRIKES_ABOVE);
    let endIdx = Math.min(allStrikes.length, closestIdx + STRIKES_BELOW + 1);

    // Adjust if we don't have enough strikes on one side
    const totalNeeded = STRIKES_ABOVE + STRIKES_BELOW + 1;
    if (endIdx - startIdx < totalNeeded) {
        if (startIdx === 0) {
            endIdx = Math.min(allStrikes.length, totalNeeded);
        } else if (endIdx === allStrikes.length) {
            startIdx = Math.max(0, allStrikes.length - totalNeeded);
        }
    }

    const strikes = allStrikes.slice(startIdx, endIdx);

    // Find max premium for scaling (only among visible strikes)
    const maxPremium = Math.max(
        ...strikes.map(s => Math.max(s.call_premium || 0, s.put_premium || 0))
    );

    // Calculate strike interval for "at spot" detection
    const strikeInterval = strikes.length > 1 ? Math.abs(strikes[0].strike - strikes[1].strike) : 1;

    // Render bars
    elements.flowPressureBars.innerHTML = strikes.map(s => {
        // Mark as "at spot" if this is the closest strike to spot price
        const isAtSpot = Math.abs(s.strike - spotPrice) <= strikeInterval / 2;
        const isAboveSpot = s.strike > spotPrice;

        // Calculate bar widths (0-100%)
        const callWidth = maxPremium > 0 ? ((s.call_premium || 0) / maxPremium) * 100 : 0;
        const putWidth = maxPremium > 0 ? ((s.put_premium || 0) / maxPremium) * 100 : 0;

        // Pressure percentage styling
        const pct = s.pressure_pct || 0;
        const pctClass = pct > 20 ? 'bullish' : pct < -20 ? 'bearish' : 'neutral';

        return `
            <div class="pressure-bar-row ${isAtSpot ? 'at-spot' : ''}">
                <span class="pressure-strike ${isAboveSpot ? 'above-spot' : 'below-spot'}">
                    ${s.strike.toFixed(0)}
                </span>
                <div class="pressure-bar-container">
                    <div class="pressure-bar-left">
                        <div class="pressure-bar-fill put" style="width: ${putWidth}%"></div>
                    </div>
                    <div class="pressure-bar-right">
                        <div class="pressure-bar-fill call" style="width: ${callWidth}%"></div>
                    </div>
                </div>
                <div class="pressure-stats">
                    <span class="stat-call">${formatCompact(s.call_premium || 0)}</span>
                    <span class="stat-put">${formatCompact(s.put_premium || 0)}</span>
                    <span class="stat-pct ${pctClass}">${pct > 0 ? '+' : ''}${pct.toFixed(0)}%</span>
                </div>
            </div>
        `;
    }).join('');
}

function formatCompact(value) {
    if (Math.abs(value) >= 1e6) {
        return `$${(value / 1e6).toFixed(1)}M`;
    } else if (Math.abs(value) >= 1e3) {
        return `$${(value / 1e3).toFixed(0)}K`;
    }
    return `$${value.toFixed(0)}`;
}

// =============================================================================
// RENDERING
// =============================================================================

function setConnectionStatus(status, text) {
    elements.connectionStatus.className = `status-badge ${status}`;
    elements.connectionStatus.textContent = text;
}

function renderSymbolTabs() {
    // Update symbol count
    const countEl = document.getElementById('symbolCount');
    if (countEl) countEl.textContent = symbols.length;

    // Render ticker pills (Skylit style) with X button for removal
    elements.symbolTabs.innerHTML = symbols.map(sym => {
        const symbolName = typeof sym === 'string' ? sym : sym.symbol;
        const isActive = symbolName === currentSymbol;
        return `
            <button class="ticker-pill ${isActive ? 'active' : ''}"
                    data-symbol="${symbolName}">
                <span class="pill-name">${symbolName}</span>
                <span class="pill-remove" data-symbol="${symbolName}" title="Remove ${symbolName}">×</span>
            </button>
        `;
    }).join('');

    // Add click handlers for pills (select symbol)
    document.querySelectorAll('.ticker-pill').forEach(pill => {
        pill.addEventListener('click', (e) => {
            // Ignore if clicking the remove button
            if (e.target.classList.contains('pill-remove')) return;
            const symbol = pill.dataset.symbol;
            currentSymbol = symbol;
            loadSymbol(currentSymbol);
            // Update active state immediately
            document.querySelectorAll('.ticker-pill').forEach(p => {
                p.classList.toggle('active', p.dataset.symbol === symbol);
            });
        });
    });

    // Add click handlers for remove buttons
    document.querySelectorAll('.pill-remove').forEach(btn => {
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

    // Update dealer behavior and intraday tracking
    updateDealerBehavior(data);

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

// =============================================================================
// DEALER BEHAVIOR & INTRADAY TRACKING
// =============================================================================

function updateDealerBehavior(data) {
    const spotPrice = data.spot_price;
    const zeroGamma = data.meta?.zero_gamma_level;
    const netDex = data.meta?.net_dex || 0;
    const netGex = data.meta?.net_gex || 0;

    // --- 1. Update intraday baseline (reset at market open 9:30 ET) ---
    const now = new Date();
    const marketOpen = new Date(now);
    marketOpen.setHours(9, 30, 0, 0);

    // Reset baseline if it's a new day or after market open with no baseline
    if (!intradayBaseline.timestamp ||
        (now > marketOpen && intradayBaseline.timestamp < marketOpen)) {
        intradayBaseline = {
            gex: netGex,
            vex: data.meta?.net_vex || 0,
            dex: netDex,
            timestamp: now
        };
        dexHistory = [netDex];
    }

    // Track DEX history for slope calculation
    dexHistory.push(netDex);
    if (dexHistory.length > DEX_HISTORY_LENGTH) {
        dexHistory.shift();
    }

    // --- 2. Calculate and display GEX change since open ---
    if (elements.deltaGexOpen && intradayBaseline.gex !== null) {
        const gexDelta = netGex - intradayBaseline.gex;
        const gexDeltaB = gexDelta / 1e9;
        const sign = gexDelta >= 0 ? '+' : '';
        elements.deltaGexOpen.textContent = `${sign}${gexDeltaB.toFixed(1)}B`;
        elements.deltaGexOpen.className = `metric-delta ${gexDelta >= 0 ? 'positive' : 'negative'}`;
    }

    // --- 3. Zero Gamma distance and flip risk ---
    if (elements.zeroGammaDistance && zeroGamma && spotPrice) {
        const zgDistance = spotPrice - zeroGamma;
        const zgPct = ((zgDistance / spotPrice) * 100).toFixed(1);
        const isNearFlip = Math.abs(zgPct) < 0.5; // Within 0.5% of zero gamma

        if (isNearFlip) {
            elements.zeroGammaDistance.textContent = `⚠️ FLIP ZONE`;
            elements.zeroGammaDistance.style.color = 'var(--accent-yellow)';
        } else {
            const direction = zgDistance > 0 ? 'above' : 'below';
            elements.zeroGammaDistance.textContent = `${Math.abs(zgPct)}% ${direction}`;
            elements.zeroGammaDistance.style.color = zgDistance > 0 ? 'var(--accent-green)' : 'var(--accent-red)';
        }
    }

    // --- 4. Dealer action status ---
    // DEX positive = Dealers long delta = Selling rallies = BEARISH
    // DEX negative = Dealers short delta = Buying dips = BULLISH
    if (elements.dealerAction) {
        const dexThreshold = 1e8; // $100M threshold for significant positioning

        if (netDex > dexThreshold) {
            elements.dealerAction.textContent = 'Selling Rallies';
            elements.dealerAction.className = 'dealer-action selling';
        } else if (netDex < -dexThreshold) {
            elements.dealerAction.textContent = 'Buying Dips';
            elements.dealerAction.className = 'dealer-action buying';
        } else {
            elements.dealerAction.textContent = 'Neutral';
            elements.dealerAction.className = 'dealer-action neutral';
        }
    }

    // --- 5. Dealer flip warning banner ---
    if (elements.dealerWarning && elements.dealerWarningText) {
        let showWarning = false;
        let warningText = '';

        // Check for Zero Gamma flip risk (price within 0.5% of ZG)
        if (zeroGamma && spotPrice) {
            const zgPct = Math.abs((spotPrice - zeroGamma) / spotPrice) * 100;
            if (zgPct < 0.5) {
                showWarning = true;
                warningText = `⚠️ Dealer Flip Risk — Price at Zero Gamma (${zeroGamma.toFixed(0)})`;
            }
        }

        // Check for DEX sign flip (rapid change in dealer positioning)
        if (dexHistory.length >= 3) {
            const recentDex = dexHistory.slice(-3);
            const hadPositive = recentDex.some(d => d > 0);
            const hadNegative = recentDex.some(d => d < 0);
            if (hadPositive && hadNegative) {
                showWarning = true;
                warningText = warningText || '⚠️ Dealer Delta Flip — Positioning Reversed';
            }
        }

        // Calculate DEX slope (is pressure building or fading?)
        if (dexHistory.length >= 2) {
            const dexSlope = dexHistory[dexHistory.length - 1] - dexHistory[0];
            const slopeB = Math.abs(dexSlope) / 1e9;

            // Add slope info to warning if significant
            if (slopeB > 0.1 && showWarning) {
                const direction = dexSlope > 0 ? 'Selling pressure building' : 'Buying pressure building';
                warningText += ` — ${direction}`;
            }
        }

        elements.dealerWarning.style.display = showWarning ? 'flex' : 'none';
        if (showWarning) {
            elements.dealerWarningText.textContent = warningText;
        }
    }
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
    const spotPrice = data.spot_price || 0;

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

    // Find RESISTANCE WALL - Highest positive GEX ABOVE spot price
    const zonesAboveSpot = positiveZones.filter(z => z.strike > spotPrice);
    const resistanceWall = zonesAboveSpot.length > 0
        ? zonesAboveSpot.reduce((max, z) => {
            const maxGex = parseFloat(max.gex_formatted.replace(/[$,M]/g, '')) || 0;
            const zGex = parseFloat(z.gex_formatted.replace(/[$,M]/g, '')) || 0;
            return zGex > maxGex ? z : max;
          })
        : null;

    // Find SUPPORT WALL - Highest positive GEX BELOW spot price
    const zonesBelowSpot = positiveZones.filter(z => z.strike < spotPrice);
    const supportWall = zonesBelowSpot.length > 0
        ? zonesBelowSpot.reduce((max, z) => {
            const maxGex = parseFloat(max.gex_formatted.replace(/[$,M]/g, '')) || 0;
            const zGex = parseFloat(z.gex_formatted.replace(/[$,M]/g, '')) || 0;
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
        // Check if this is the Resistance Wall (highest positive GEX above spot)
        const isResistance = resistanceWall && zone.strike === resistanceWall.strike && isPositive;
        // Check if this is the Support Wall (highest positive GEX below spot)
        const isSupport = supportWall && zone.strike === supportWall.strike && isPositive;

        const ctx = contextLabels[zone.trading_context] || contextLabels['neutral'];

        const itemClasses = [
            isPositive ? 'positive' : 'negative',
            isKing ? 'king' : '',
            isGatekeeper ? 'gatekeeper' : '',
            isMagnet ? 'magnet-highlight' : '',
            isAccelerator ? 'accelerator-highlight' : '',
            isResistance ? 'resistance-highlight' : '',
            isSupport ? 'support-highlight' : '',
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
        } else if (isResistance && !isKing && !isMagnet) {
            roleLabel = 'RESISTANCE';
        } else if (isSupport && !isKing && !isMagnet) {
            roleLabel = 'SUPPORT';
        }

        // Trading context badge with trading bias hint
        let contextBadge = '';
        let tradingBias = '';
        let whatHappensIf = '';

        // Find next significant zones for "What Happens If" tooltips
        const currentIdx = zones.indexOf(zone);
        const zonesBelow = zones.slice(currentIdx + 1).filter(z => z.strike < zone.strike);
        const zonesAbove = zones.slice(0, currentIdx).filter(z => z.strike > zone.strike);
        const nextBelow = zonesBelow[0];
        const nextAbove = zonesAbove[zonesAbove.length - 1];

        if (isMagnet) {
            contextBadge = `<span class="trading-context ctx-magnet-primary">MAGNET</span>`;
            tradingBias = zone.strike > spotPrice
                ? 'Above = drift higher | Below = snap back up'
                : 'Below = drift lower | Above = snap back down';
            whatHappensIf = zone.strike > spotPrice
                ? `If price reaches ${zone.strike.toFixed(0)} → expect stall/reversal`
                : `If price holds above ${zone.strike.toFixed(0)} → grind higher likely`;
        } else if (isKing) {
            contextBadge = `<span class="trading-context ctx-king">KING</span>`;
            tradingBias = 'Strongest level — expect price to gravitate here';
            whatHappensIf = `If price holds above King → grind higher likely | Below → magnet pull down`;
        } else if (isGatekeeper) {
            contextBadge = `<span class="trading-context ctx-gatekeeper">GATEKEEPER</span>`;
            tradingBias = 'Break required for trend — rejection = reversal';
            const target = zone.strike > spotPrice ? nextAbove : nextBelow;
            whatHappensIf = zone.strike > spotPrice
                ? `If price breaks ${zone.strike.toFixed(0)} → trend acceleration to ${target ? target.strike.toFixed(0) : 'next level'}`
                : `If price loses ${zone.strike.toFixed(0)} → fast move to ${target ? target.strike.toFixed(0) : 'next support'}`;
        } else if (isAccelerator) {
            contextBadge = `<span class="trading-context ctx-acceleration">ACCELERATOR</span>`;
            tradingBias = 'Fast move if lost — vol expansion zone';
            const target = nextBelow || nextAbove;
            whatHappensIf = `If price loses ${zone.strike.toFixed(0)} → expect fast move to ${target ? target.strike.toFixed(0) : 'next level'}`;
        } else if (isResistance) {
            contextBadge = `<span class="trading-context ctx-resistance">RESISTANCE</span>`;
            tradingBias = 'Above = breakout potential | At = expect fade';
            whatHappensIf = `If price breaks ${zone.strike.toFixed(0)} → breakout, target ${nextAbove ? nextAbove.strike.toFixed(0) : 'higher'} | Rejection → fade back`;
        } else if (isSupport) {
            contextBadge = `<span class="trading-context ctx-support">SUPPORT</span>`;
            tradingBias = 'Below = breakdown risk | At = expect bounce';
            whatHappensIf = `If price holds ${zone.strike.toFixed(0)} → bounce likely | Break → fast to ${nextBelow ? nextBelow.strike.toFixed(0) : 'lower'}`;
        } else if (!isPositive) {
            contextBadge = `<span class="trading-context ctx-vol-zone">VOL ZONE</span>`;
            tradingBias = 'Unstable — momentum risk both ways';
            whatHappensIf = `If price enters ${zone.strike.toFixed(0)} zone → expect volatility expansion, moves amplified`;
        } else if (ctx.label) {
            contextBadge = `<span class="trading-context ${ctx.class}">${ctx.label}</span>`;
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
            <div class="zone-item ${itemClasses}" ${whatHappensIf ? `data-tooltip="${whatHappensIf}"` : ''}>
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
                ${tradingBias ? `<div class="zone-bias">${tradingBias}</div>` : ''}
                ${whatHappensIf ? `<div class="zone-tooltip"><span class="tooltip-label">What if?</span> ${whatHappensIf}</div>` : ''}
                <div class="zone-strength">
                    <div class="zone-strength-bar ${isPositive ? 'positive' : 'negative'} ${isMagnet ? 'magnet-bar' : ''}"
                         style="width: ${zone.strength * 100}%"></div>
                </div>
            </div>
        `;
    }).join('');
}

// =============================================================================
// NEW FEATURES - Expected Move, GEX Flip, Put/Call Walls
// =============================================================================

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
            chartWrapper.innerHTML = ''; // Clear loading message

            // Calculate dynamic height to fill available space
            const chartHeight = Math.max(chartWrapper.clientHeight || 500, 400);

            priceChart = LightweightCharts.createChart(chartWrapper, {
                width: chartWrapper.clientWidth,
                height: chartHeight,
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

            // Handle resize - update both width and height
            window.addEventListener('resize', () => {
                if (priceChart && chartWrapper) {
                    const newHeight = Math.max(chartWrapper.clientHeight || 500, 400);
                    priceChart.applyOptions({
                        width: chartWrapper.clientWidth,
                        height: newHeight
                    });
                }
            });
        }

        // Create candlestick series if not exists
        if (!candleSeries) {
            candleSeries = priceChart.addCandlestickSeries({
                upColor: '#22c55e',
                downColor: '#ef4444',
                borderDownColor: '#ef4444',
                borderUpColor: '#22c55e',
                wickDownColor: '#ef4444',
                wickUpColor: '#22c55e',
            });
        }

        // Verify we have valid series
        if (!candleSeries) {
            console.error('[Chart] Failed to create candle series');
            return;
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
        chartLoadedSymbol = symbol;  // Track which symbol is loaded

        // Remove old level lines
        chartLevelLines.forEach(line => {
            try { candleSeries.removePriceLine(line); } catch (e) {}
        });
        chartLevelLines = [];

        // Add GEX level lines - MATCHING KEY ZONES CLASSIFICATION
        const levels = data.levels;
        if (levels) {
            // Determine if magnet is also resistance (above spot) or support (below spot)
            const magnetIsResistance = levels.magnet && levels.resistance === levels.magnet;
            const magnetIsSupport = levels.magnet && levels.support === levels.magnet;

            // MAGNET - Cyan (highest positive GEX = price target)
            // Add context if it's also resistance or support
            if (levels.magnet) {
                let magnetLabel = '🧲 MAGNET';
                if (magnetIsResistance) magnetLabel = '🧲 MAGNET/RESIST';
                else if (magnetIsSupport) magnetLabel = '🧲 MAGNET/SUPPORT';

                chartLevelLines.push(candleSeries.createPriceLine({
                    price: levels.magnet,
                    color: '#00ffff',
                    lineWidth: 2,
                    lineStyle: LightweightCharts.LineStyle.Solid,
                    axisLabelVisible: true,
                    title: magnetLabel,
                }));
            }

            // RESISTANCE - Orange (highest positive GEX ABOVE spot)
            // Only show separate line if different from Magnet
            if (levels.resistance && levels.resistance !== levels.magnet) {
                chartLevelLines.push(candleSeries.createPriceLine({
                    price: levels.resistance,
                    color: '#f97316',
                    lineWidth: 2,
                    lineStyle: LightweightCharts.LineStyle.Solid,
                    axisLabelVisible: true,
                    title: 'RESISTANCE',
                }));
            }

            // SUPPORT - Green (highest positive GEX BELOW spot)
            // Only show separate line if different from Magnet
            if (levels.support && levels.support !== levels.magnet) {
                chartLevelLines.push(candleSeries.createPriceLine({
                    price: levels.support,
                    color: '#22c55e',
                    lineWidth: 2,
                    lineStyle: LightweightCharts.LineStyle.Solid,
                    axisLabelVisible: true,
                    title: 'SUPPORT',
                }));
            }

            // ACCELERATOR - Purple (highest negative GEX = vol expansion zone)
            if (levels.accelerator) {
                chartLevelLines.push(candleSeries.createPriceLine({
                    price: levels.accelerator,
                    color: '#a855f7',
                    lineWidth: 2,
                    lineStyle: LightweightCharts.LineStyle.Solid,
                    axisLabelVisible: true,
                    title: '⚡ ACCEL',
                }));
            }

            // Zero Gamma / Flip point - White dashed
            if (levels.zero_gamma) {
                chartLevelLines.push(candleSeries.createPriceLine({
                    price: levels.zero_gamma,
                    color: '#ffffff',
                    lineWidth: 1,
                    lineStyle: LightweightCharts.LineStyle.Dashed,
                    axisLabelVisible: true,
                    title: '0γ FLIP',
                }));
            }

            // Expected move range - subtle gray
            if (levels.expected_move && levels.expected_move.daily) {
                chartLevelLines.push(candleSeries.createPriceLine({
                    price: levels.expected_move.daily.high,
                    color: '#555555',
                    lineWidth: 1,
                    lineStyle: LightweightCharts.LineStyle.Dotted,
                    axisLabelVisible: false,
                    title: '+EM',
                }));
                chartLevelLines.push(candleSeries.createPriceLine({
                    price: levels.expected_move.daily.low,
                    color: '#555555',
                    lineWidth: 1,
                    lineStyle: LightweightCharts.LineStyle.Dotted,
                    axisLabelVisible: false,
                    title: '-EM',
                }));
            }
        }

        // Only fit content on first load, not on refresh
        if (!window.chartInitialized) {
            priceChart.timeScale().fitContent();
            window.chartInitialized = true;
        }

        // Store zones for heatmap rendering
        if (data.zones && data.zones.length > 0) {
            gexZonesData = data.zones;
            console.log(`[Heatmap] Stored ${gexZonesData.length} zones for heatmap`);
        }

        // Initialize heatmap canvas if not done
        if (!heatmapCanvas) {
            initHeatmapCanvas();
        }

        // Subscribe to chart range changes for heatmap re-rendering (only once)
        if (!heatmapRangeSubscribed && priceChart) {
            priceChart.timeScale().subscribeVisibleLogicalRangeChange(() => {
                if (chartMode === 'heatmap' && gexZonesData.length > 0) {
                    const range = getChartPriceRange();
                    renderGexHeatmap(gexZonesData, range);
                }
            });
            heatmapRangeSubscribed = true;
        }

        // Render heatmap if in heatmap mode
        if (chartMode === 'heatmap' && gexZonesData.length > 0) {
            setTimeout(() => {
                const range = getChartPriceRange();
                renderGexHeatmap(gexZonesData, range);
            }, 100);
        }

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
        loadAndRenderChart(currentSymbol, chartResolution);
    } else {
        container.style.display = 'none';
    }
}

// =============================================================================
// MAIN FUNCTIONS
// =============================================================================

async function loadSymbol(symbol, forceRefresh = false) {
    try {
        // Reset flow data and chart when switching symbols
        const symbolChanged = currentSymbol !== symbol;
        if (symbolChanged) {
            flowData = null;
            window.chartInitialized = false; // Reset chart fit on symbol change
        }
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

        // Update chart when switching tickers in chart view
        // Use chartLoadedSymbol to detect if chart needs reload (more reliable than symbolChanged)
        console.log(`[loadSymbol] currentView=${currentView}, chartLoadedSymbol=${chartLoadedSymbol}, symbol=${symbol}`);
        if (currentView === 'chart' && chartLoadedSymbol !== symbol) {
            console.log(`[loadSymbol] Reloading chart for ${symbol} (was ${chartLoadedSymbol})`);
            window.chartInitialized = false; // Reset fit for new symbol
            loadAndRenderChart(symbol, chartResolution);
        }

        // If in flow view, also fetch flow data and update WAVE
        if (currentView === 'flow') {
            fetchFlowData(symbol);
            fetchWaveData(symbol, waveTimeframe);
        }

    } catch (error) {
        console.error('Failed to load symbol:', error);
    }
}

function switchView(view) {
    console.log('[switchView] Called with view:', view);
    currentView = view;
    saveState(); // Persist view

    // Update active button
    document.querySelectorAll('.btn-view').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.view === view);
    });

    // Determine view types
    const isFlowView = view === 'flow';
    const isChartView = view === 'chart';
    const isHeatmapView = !isFlowView && !isChartView; // gex, vex, dex
    console.log('[switchView] View types:', { isFlowView, isChartView, isHeatmapView });

    // Get containers
    const heatmapContainer = document.querySelector('.heatmap-container');
    const flowContainer = document.getElementById('flowContainer');
    const chartContainer = document.getElementById('chartContainer');
    const zonesSidebar = document.querySelector('.zones-sidebar');

    // Show/hide containers based on view
    if (heatmapContainer) {
        heatmapContainer.style.display = isHeatmapView ? '' : 'none';
    }
    if (flowContainer) {
        flowContainer.style.display = isFlowView ? 'flex' : 'none';
    }
    if (chartContainer) {
        chartContainer.style.display = isChartView ? 'flex' : 'none';
    }
    if (zonesSidebar) {
        zonesSidebar.style.display = isHeatmapView ? '' : 'none';
    }

    // Update view title
    if (elements.heatmapViewTitle) {
        const titles = { gex: 'GEX Heatmap', vex: 'VEX Heatmap', dex: 'DEX Heatmap', flow: 'Options Flow', chart: 'Price Chart' };
        elements.heatmapViewTitle.textContent = titles[view] || 'GEX Heatmap';
        elements.heatmapViewTitle.className = 'heatmap-view-title';
        if (view === 'vex') elements.heatmapViewTitle.classList.add('vex-view');
        if (view === 'dex') elements.heatmapViewTitle.classList.add('dex-view');
    }

    // Stop all auto-refresh intervals first
    stopWaveAutoRefresh();
    stopChartAutoRefresh();

    // Handle view-specific initialization
    if (isFlowView && currentSymbol) {
        fetchFlowData(currentSymbol);
        setTimeout(() => {
            initWaveChart();
            setupWaveTimeframeButtons();
            fetchWaveData(currentSymbol, waveTimeframe);
            setupLeaderboardControls();
            fetchLeaderboard();
            startWaveAutoRefresh();
        }, 100);
    } else if (isChartView && currentSymbol) {
        // Initialize price chart - use only loadAndRenderChart
        // Always reload if symbol changed since last chart load
        setTimeout(() => {
            setupChartTimeframeButtons();
            if (chartLoadedSymbol !== currentSymbol) {
                console.log(`[Chart] Symbol changed: ${chartLoadedSymbol} -> ${currentSymbol}, reloading`);
                window.chartInitialized = false; // Reset fit for new symbol
            }
            loadAndRenderChart(currentSymbol, chartResolution);
            startChartAutoRefresh();
        }, 100);
    } else if (isHeatmapView && currentData) {
        // Re-render heatmap with new view
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
    if (elements.btnAddSymbol) elements.btnAddSymbol.disabled = true;
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
        if (elements.btnAddSymbol) elements.btnAddSymbol.disabled = false;
        elements.symbolInput.placeholder = '+ Add symbol';
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
async function switchToSymbolFromQuiver(ticker) {
    if (!ticker) return;

    // Close all Quiver popups
    document.querySelectorAll('.quiver-overlay').forEach(o => o.style.display = 'none');

    // Check if symbol already exists in our list
    const existingSymbol = symbols.find(s => s.symbol.toUpperCase() === ticker.toUpperCase());

    if (existingSymbol) {
        // Switch to existing symbol
        await loadSymbol(ticker.toUpperCase());
    } else {
        // Add and switch to new symbol
        try {
            await addSymbol(ticker.toUpperCase());
            await fetchSymbols(); // Refresh symbol list
            await loadSymbol(ticker.toUpperCase());
        } catch (error) {
            console.error(`Failed to add ${ticker}:`, error);
        }
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

        // Combine all alerts (with null safety)
        const allAlerts = [
            ...(alerts.congress || []).map(a => ({ ...a, severity: 'info' })),
            ...(alerts.insider || []).map(a => ({ ...a, severity: 'warning' })),
            ...(alerts.dark_pool || []).map(a => ({ ...a, severity: 'info' })),
            ...(alerts.wsb || []).map(a => ({ ...a, severity: 'info' }))
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
    const viewButtons = document.querySelectorAll('.btn-view');
    console.log('[setupEventHandlers] Found', viewButtons.length, 'view buttons');
    viewButtons.forEach(btn => {
        console.log('[setupEventHandlers] Adding click handler to button:', btn.dataset.view);
        btn.addEventListener('click', () => {
            console.log('[btn-view click] Button clicked:', btn.dataset.view);
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

    // Add symbol button (if it exists)
    if (elements.btnAddSymbol) {
        elements.btnAddSymbol.addEventListener('click', () => {
            hideSearchDropdown();
            handleAddSymbol(elements.symbolInput.value);
        });
    }

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
            chartResolution = resolution; // Update global resolution
            window.chartInitialized = false; // Reset fit on resolution change
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

    // =========================================================================
    // NEW SKYLIT-STYLE UI HANDLERS
    // =========================================================================

    // Interval dropdown options
    document.querySelectorAll('.interval-option').forEach(btn => {
        btn.addEventListener('click', async () => {
            const interval = parseInt(btn.dataset.interval);
            document.querySelectorAll('.interval-option').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            // Update the dropdown button text
            const selectBtn = document.getElementById('btnIntervalSelect');
            if (selectBtn) selectBtn.textContent = `${interval}m ▾`;
            await setRefreshInterval(interval);
        });
    });

    // Metric toggle buttons (GEX/VEX/DEX/Flow)
    document.querySelectorAll('.btn-metric').forEach(btn => {
        btn.addEventListener('click', () => {
            const view = btn.dataset.view;
            document.querySelectorAll('.btn-metric').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            switchView(view);
        });
    });

    // All Tickers dropdown
    const btnAllTickers = document.getElementById('btnAllTickers');
    const allTickersMenu = document.getElementById('allTickersMenu');
    if (btnAllTickers && allTickersMenu) {
        btnAllTickers.addEventListener('click', (e) => {
            e.stopPropagation();
            allTickersMenu.classList.toggle('show');
            renderAllTickersMenu();
        });

        // Close dropdown when clicking outside
        document.addEventListener('click', (e) => {
            if (!e.target.closest('.all-tickers-dropdown')) {
                allTickersMenu.classList.remove('show');
            }
        });
    }

    // Cheat Sheet panel toggle
    const btnCheatSheet = document.getElementById('btnCheatSheet');
    const cheatsheetOverlay = document.getElementById('cheatsheetOverlay');
    const btnCloseCheatsheet = document.getElementById('btnCloseCheatsheet');

    if (btnCheatSheet && cheatsheetOverlay) {
        btnCheatSheet.addEventListener('click', () => {
            cheatsheetOverlay.classList.add('show');
        });

        btnCloseCheatsheet?.addEventListener('click', () => {
            cheatsheetOverlay.classList.remove('show');
        });

        // Close on overlay click (outside panel)
        cheatsheetOverlay.addEventListener('click', (e) => {
            if (e.target === cheatsheetOverlay) {
                cheatsheetOverlay.classList.remove('show');
            }
        });

        // Close on Escape key
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && cheatsheetOverlay.classList.contains('show')) {
                cheatsheetOverlay.classList.remove('show');
            }
        });
    }

    // =========================================================================
    // USER MENU HANDLERS
    // =========================================================================
    const btnUserMenu = document.getElementById('btnUserMenu');
    const userMenu = document.getElementById('userMenu');
    const userEmailEl = document.getElementById('userEmail');
    const adminLink = document.getElementById('adminLink');
    const btnChangePassword = document.getElementById('btnChangePassword');
    const changePasswordModal = document.getElementById('changePasswordModal');
    const btnClosePasswordModal = document.getElementById('closePasswordModal');
    const btnCancelPassword = document.getElementById('cancelPasswordChange');
    const changePasswordForm = document.getElementById('changePasswordForm');

    // User menu dropdown toggle
    if (btnUserMenu && userMenu) {
        btnUserMenu.addEventListener('click', (e) => {
            e.stopPropagation();
            userMenu.classList.toggle('show');
        });

        // Close when clicking outside
        document.addEventListener('click', (e) => {
            if (!e.target.closest('.user-menu-dropdown')) {
                userMenu.classList.remove('show');
            }
        });
    }

    // Update user menu with current user info
    if (isAuthenticated && currentUser) {
        if (userEmailEl) userEmailEl.textContent = currentUser.email || 'Signed in';
        if (adminLink) adminLink.style.display = currentUser.is_admin ? 'block' : 'none';
    }

    // Change password button
    if (btnChangePassword && changePasswordModal) {
        btnChangePassword.addEventListener('click', () => {
            userMenu.classList.remove('show');
            changePasswordModal.style.display = 'flex';
        });
    }

    // Close password modal
    function closePasswordModal() {
        if (changePasswordModal) {
            changePasswordModal.style.display = 'none';
            if (changePasswordForm) changePasswordForm.reset();
            const errorEl = document.getElementById('passwordError');
            if (errorEl) errorEl.textContent = '';
        }
    }

    if (btnClosePasswordModal) btnClosePasswordModal.addEventListener('click', closePasswordModal);
    if (btnCancelPassword) btnCancelPassword.addEventListener('click', closePasswordModal);

    // Close on overlay click
    if (changePasswordModal) {
        changePasswordModal.addEventListener('click', (e) => {
            if (e.target === changePasswordModal) closePasswordModal();
        });
    }

    // Change password form submission
    if (changePasswordForm) {
        changePasswordForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            const errorEl = document.getElementById('passwordError');
            const currentPw = document.getElementById('currentPassword').value;
            const newPw = document.getElementById('newPassword').value;
            const confirmPw = document.getElementById('confirmNewPassword').value;

            if (newPw !== confirmPw) {
                if (errorEl) {
                    errorEl.textContent = 'New passwords do not match';
                    errorEl.style.display = 'block';
                }
                return;
            }

            if (newPw.length < 8) {
                if (errorEl) {
                    errorEl.textContent = 'Password must be at least 8 characters';
                    errorEl.style.display = 'block';
                }
                return;
            }

            // Hide error before attempting
            if (errorEl) errorEl.style.display = 'none';

            try {
                const res = await fetch(`${API_BASE}/api/me/change-password`, {
                    method: 'POST',
                    headers: getAuthHeaders(),
                    body: JSON.stringify({
                        current_password: currentPw,
                        new_password: newPw
                    }),
                    credentials: 'include'
                });

                const data = await res.json();

                if (res.ok) {
                    closePasswordModal();
                    alert('Password changed successfully!');
                } else {
                    if (errorEl) {
                        errorEl.textContent = data.detail || 'Failed to change password';
                        errorEl.style.display = 'block';
                    }
                }
            } catch (err) {
                if (errorEl) {
                    errorEl.textContent = 'Network error. Please try again.';
                    errorEl.style.display = 'block';
                }
            }
        });
    }
}

// Render the All Tickers dropdown menu
function renderAllTickersMenu() {
    const menu = document.getElementById('allTickersMenu');
    if (!menu) return;

    menu.innerHTML = symbols.map(sym => {
        const symbolName = typeof sym === 'string' ? sym : sym.symbol;
        const isActive = symbolName === currentSymbol;
        return `
            <div class="ticker-item ${isActive ? 'active' : ''}" data-symbol="${symbolName}">
                <span class="ticker-name">${symbolName}</span>
                <span class="ticker-remove" data-symbol="${symbolName}" title="Remove">×</span>
            </div>
        `;
    }).join('');

    // Add click handlers
    menu.querySelectorAll('.ticker-item').forEach(item => {
        item.addEventListener('click', (e) => {
            if (e.target.classList.contains('ticker-remove')) return;
            const symbol = item.dataset.symbol;
            currentSymbol = symbol;
            loadSymbol(symbol);
            renderSymbolTabs();
            menu.classList.remove('show');
        });
    });

    menu.querySelectorAll('.ticker-remove').forEach(btn => {
        btn.addEventListener('click', async (e) => {
            e.stopPropagation();
            const symbol = btn.dataset.symbol;
            await handleRemoveSymbol(symbol);
            renderAllTickersMenu();
        });
    });
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

    // Setup event handlers EARLY so buttons work even if API fails
    setupEventHandlers();
    setupStrikeClickHandlers();
    setupPlaybackHandlers();
    setupChartModeTabs();  // Setup Basic/Heatmap chart mode tabs

    setConnectionStatus('', 'Connecting...');

    try {
        // Check authentication status
        await checkAuth();

        // If authenticated, load preferences from server
        if (isAuthenticated) {
            await loadPreferencesFromServer();
            console.log('User authenticated, preferences loaded from server');
        }

        // Check API health (use /health endpoint, not / which redirects)
        const health = await fetchAPI('/health');
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

        // Apply the saved view (GEX/VEX/DEX/Flow) AFTER loading data
        // This ensures flow container is shown if user was on flow view
        if (currentView !== 'gex') {
            switchView(currentView);
        }

        // Setup auto-refresh
        setupAutoRefresh();

        // Check market status (show MARKET CLOSED badge if after hours/weekend)
        checkMarketStatus();
        // Check market status every minute
        setInterval(checkMarketStatus, 60000);

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

// =============================================================================
// PLAYBACK MODE
// =============================================================================

let playbackMode = false;
let playbackData = null;  // Array of snapshots for current date
let playbackIndex = 0;    // Current position in playback
let playbackPlaying = false;
let playbackInterval = null;
let playbackSpeed = 2;    // Seconds between frames

// Playback elements (initialized in setupPlaybackHandlers)
let playbackElements = {};

function setupPlaybackHandlers() {
    playbackElements = {
        bar: document.getElementById('playbackBar'),
        datePicker: document.getElementById('playbackDatePicker'),
        time: document.getElementById('playbackTime'),
        slider: document.getElementById('playbackSlider'),
        startTime: document.getElementById('playbackStart'),
        endTime: document.getElementById('playbackEnd'),
        speed: document.getElementById('playbackSpeed'),
        toggleBtn: document.getElementById('btnPlaybackToggle'),
        enterBtn: document.getElementById('btnEnterPlayback'),
        exitBtn: document.getElementById('btnPlaybackExit'),
        prevBtn: document.getElementById('btnPlaybackPrev'),
        backBtn: document.getElementById('btnPlaybackBack'),
        forwardBtn: document.getElementById('btnPlaybackForward'),
        nextBtn: document.getElementById('btnPlaybackNext')
    };

    // Enter playback button
    if (playbackElements.enterBtn) {
        playbackElements.enterBtn.addEventListener('click', enterPlaybackMode);
    }

    // Exit playback button
    if (playbackElements.exitBtn) {
        playbackElements.exitBtn.addEventListener('click', exitPlaybackMode);
    }

    // Play/Pause toggle
    if (playbackElements.toggleBtn) {
        playbackElements.toggleBtn.addEventListener('click', togglePlayback);
    }

    // Step buttons
    if (playbackElements.prevBtn) {
        playbackElements.prevBtn.addEventListener('click', () => seekPlayback(0));
    }
    if (playbackElements.backBtn) {
        playbackElements.backBtn.addEventListener('click', () => stepPlayback(-1));
    }
    if (playbackElements.forwardBtn) {
        playbackElements.forwardBtn.addEventListener('click', () => stepPlayback(1));
    }
    if (playbackElements.nextBtn) {
        playbackElements.nextBtn.addEventListener('click', () => seekPlayback(playbackData?.length - 1 || 0));
    }

    // Slider
    if (playbackElements.slider) {
        playbackElements.slider.addEventListener('input', (e) => {
            if (playbackData && playbackData.length > 0) {
                const index = Math.round((e.target.value / 100) * (playbackData.length - 1));
                seekPlayback(index);
            }
        });
    }

    // Date picker
    if (playbackElements.datePicker) {
        playbackElements.datePicker.addEventListener('change', async (e) => {
            const date = e.target.value;
            if (date) {
                await loadPlaybackDate(date);
            }
        });
    }

    // Speed selector
    if (playbackElements.speed) {
        playbackElements.speed.addEventListener('change', (e) => {
            playbackSpeed = parseFloat(e.target.value);
            if (playbackPlaying) {
                // Restart interval with new speed
                stopPlaybackInterval();
                startPlaybackInterval();
            }
        });
    }
}

async function enterPlaybackMode() {
    console.log('[Playback] Entering playback mode');
    playbackMode = true;

    // Stop auto-refresh
    if (autoRefreshTimer) {
        clearInterval(autoRefreshTimer);
        autoRefreshTimer = null;
    }

    // Show playback bar
    if (playbackElements.bar) {
        playbackElements.bar.style.display = 'flex';
    }

    // Highlight replay button
    if (playbackElements.enterBtn) {
        playbackElements.enterBtn.classList.add('active');
    }

    // Hide live indicator, show replay state
    const liveIndicator = document.getElementById('liveIndicator');
    if (liveIndicator) liveIndicator.style.display = 'none';

    // Add playback mode class to body
    document.body.classList.add('playback-mode');

    // Load available dates
    try {
        const response = await fetchAPI(`/playback/${currentSymbol}/dates`);
        const dates = response.available_dates || [];

        if (dates.length > 0) {
            // Set date picker to most recent date with data
            playbackElements.datePicker.value = dates[0];
            await loadPlaybackDate(dates[0]);
        } else {
            // Show message but keep playback bar visible
            if (playbackElements.time) {
                playbackElements.time.textContent = 'No data yet';
            }
            // Set date picker to today
            playbackElements.datePicker.value = new Date().toISOString().split('T')[0];
            // Disable controls until data is available
            disablePlaybackControls();
            showPlaybackMessage('Recording starts during market hours. Check back after the market closes today.');
        }
    } catch (error) {
        console.error('[Playback] Error loading dates:', error);
        if (playbackElements.time) {
            playbackElements.time.textContent = 'Error loading';
        }
        showPlaybackMessage('Failed to load playback data. Try again later.');
    }
}

function disablePlaybackControls() {
    const btns = [playbackElements.prevBtn, playbackElements.backBtn,
                  playbackElements.toggleBtn, playbackElements.forwardBtn,
                  playbackElements.nextBtn];
    btns.forEach(btn => {
        if (btn) btn.disabled = true;
    });
    if (playbackElements.slider) {
        playbackElements.slider.disabled = true;
    }
}

function enablePlaybackControls() {
    const btns = [playbackElements.prevBtn, playbackElements.backBtn,
                  playbackElements.toggleBtn, playbackElements.forwardBtn,
                  playbackElements.nextBtn];
    btns.forEach(btn => {
        if (btn) btn.disabled = false;
    });
    if (playbackElements.slider) {
        playbackElements.slider.disabled = false;
    }
}

function showPlaybackMessage(msg) {
    // Show message in a toast or status area
    const statusEl = document.getElementById('connectionStatus');
    if (statusEl) {
        const originalText = statusEl.textContent;
        statusEl.textContent = msg;
        statusEl.style.color = 'var(--accent-yellow)';
        setTimeout(() => {
            statusEl.textContent = originalText;
            statusEl.style.color = '';
        }, 5000);
    }
}

function exitPlaybackMode() {
    console.log('[Playback] Exiting playback mode');
    playbackMode = false;
    playbackPlaying = false;
    stopPlaybackInterval();

    // Hide playback bar
    if (playbackElements.bar) {
        playbackElements.bar.style.display = 'none';
    }

    // Remove highlight from replay button
    if (playbackElements.enterBtn) {
        playbackElements.enterBtn.classList.remove('active');
    }

    // Show live indicator again
    const liveIndicator = document.getElementById('liveIndicator');
    if (liveIndicator) liveIndicator.style.display = 'flex';

    // Remove playback mode class
    document.body.classList.remove('playback-mode');

    // Reset button state
    if (playbackElements.toggleBtn) {
        playbackElements.toggleBtn.textContent = '▶';
        playbackElements.toggleBtn.classList.remove('playing');
    }

    // Restart auto-refresh and load live data
    setupAutoRefresh();
    loadSymbol(currentSymbol, true);
}

async function loadPlaybackDate(date) {
    console.log('[Playback] Loading date:', date);

    try {
        const response = await fetchAPI(`/playback/${currentSymbol}/${date}`);
        playbackData = response.snapshots || [];
        playbackIndex = 0;

        if (playbackData.length === 0) {
            playbackElements.time.textContent = 'No data';
            return;
        }

        // Update slider range
        playbackElements.slider.max = 100;
        playbackElements.slider.value = 0;

        // Update time labels
        const firstTime = new Date(playbackData[0].time);
        const lastTime = new Date(playbackData[playbackData.length - 1].time);
        playbackElements.startTime.textContent = formatPlaybackTime(firstTime);
        playbackElements.endTime.textContent = formatPlaybackTime(lastTime);

        // Display first snapshot
        displayPlaybackSnapshot(0);

        // Enable controls now that we have data
        enablePlaybackControls();

        console.log(`[Playback] Loaded ${playbackData.length} snapshots for ${date}`);

    } catch (error) {
        console.error('[Playback] Error loading date:', error);
        playbackData = [];
    }
}

function displayPlaybackSnapshot(index) {
    if (!playbackData || index < 0 || index >= playbackData.length) return;

    playbackIndex = index;
    const snapshot = playbackData[index];

    // Update time display
    const time = new Date(snapshot.time);
    playbackElements.time.textContent = formatPlaybackTime(time);

    // Update slider
    playbackElements.slider.value = (index / (playbackData.length - 1)) * 100;

    // Update the display with snapshot data
    updateDisplayFromSnapshot(snapshot);
}

function updateDisplayFromSnapshot(snapshot) {
    // Update spot price
    if (elements.spotPrice) {
        elements.spotPrice.textContent = `$${snapshot.spot_price.toFixed(2)}`;
    }

    // Update net GEX
    if (elements.netGex) {
        elements.netGex.textContent = formatGEX(snapshot.net_gex);
    }

    // Update King node
    if (elements.kingNode && snapshot.king_strike) {
        elements.kingNode.textContent = snapshot.king_strike;
    }
    if (elements.kingGex && snapshot.king_gex) {
        elements.kingGex.textContent = formatGEX(snapshot.king_gex);
    }

    // Update Zero Gamma
    if (elements.zeroGamma && snapshot.zero_gamma_level) {
        elements.zeroGamma.textContent = snapshot.zero_gamma_level.toFixed(2);
    }

    // Update zones if available
    if (snapshot.zones && snapshot.zones.length > 0) {
        renderZonesFromPlayback(snapshot.zones);
    }

    // Update heatmap if available
    if (snapshot.heatmap) {
        renderHeatmapFromPlayback(snapshot);
    }
}

function renderZonesFromPlayback(zones) {
    if (!elements.zonesContainer) return;

    const zonesHtml = zones.map(zone => {
        const isPositive = zone.type === 'positive';
        const roleClass = zone.role || '';
        const gexFormatted = formatGEX(zone.gex);

        return `
            <div class="zone-card ${isPositive ? 'positive' : 'negative'} ${roleClass}">
                <div class="zone-strike">${zone.strike}</div>
                <div class="zone-gex">${gexFormatted}</div>
                <div class="zone-role">${zone.role || ''}</div>
            </div>
        `;
    }).join('');

    elements.zonesContainer.innerHTML = zonesHtml;
}

function renderHeatmapFromPlayback(snapshot) {
    // For now, just update the basic info
    // Full heatmap rendering would require more data
    console.log('[Playback] Heatmap data available but simplified rendering');
}

function togglePlayback() {
    if (playbackPlaying) {
        pausePlayback();
    } else {
        startPlayback();
    }
}

function startPlayback() {
    if (!playbackData || playbackData.length === 0) return;

    playbackPlaying = true;
    playbackElements.toggleBtn.textContent = '⏸';
    playbackElements.toggleBtn.classList.add('playing');

    startPlaybackInterval();
}

function pausePlayback() {
    playbackPlaying = false;
    playbackElements.toggleBtn.textContent = '▶';
    playbackElements.toggleBtn.classList.remove('playing');

    stopPlaybackInterval();
}

function startPlaybackInterval() {
    const intervalMs = 1000 / playbackSpeed;

    playbackInterval = setInterval(() => {
        if (playbackIndex < playbackData.length - 1) {
            displayPlaybackSnapshot(playbackIndex + 1);
        } else {
            // Reached end, pause
            pausePlayback();
        }
    }, intervalMs);
}

function stopPlaybackInterval() {
    if (playbackInterval) {
        clearInterval(playbackInterval);
        playbackInterval = null;
    }
}

function stepPlayback(direction) {
    const newIndex = playbackIndex + direction;
    if (newIndex >= 0 && newIndex < playbackData.length) {
        displayPlaybackSnapshot(newIndex);
    }
}

function seekPlayback(index) {
    if (index >= 0 && index < playbackData.length) {
        displayPlaybackSnapshot(index);
    }
}

function formatPlaybackTime(date) {
    return date.toLocaleTimeString('en-US', {
        hour: 'numeric',
        minute: '2-digit',
        hour12: true
    });
}

// Start the app
document.addEventListener('DOMContentLoaded', init);
