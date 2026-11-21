/**
 * Delta Options Risk-Reward Engine - Sidelay Chrome Extension Content Script
 * 
 * Features:
 * - Floating, resizable sidelay overlay directly on Delta Exchange (24/7)
 * - TradingView Keyboard Isolation: blocks keyboard events from bubbling to TradingView's "Change interval" dialog
 * - Discrete Two-Way Synchronization: Lots <---> Max Risk USD (never overwrites while trader is typing)
 * - Dual Entry Mode: [⚡ Live Market Price] vs [✏️ Manual Limit Entry]
 * - Prominent ⚡ EVALUATE & LOCK RRR button with instant lock/unlock capability
 * - Classical Standard Pivot Points (24h P, R1, R2, R3, S1, S2, S3)
 * - 5m Intraday Stop Loss (~15-18% risk, clamped to 1.15 ATR) & Target expansion
 * - 0ms DOM Scrape directly from TradingView Buy/Sell buttons + fallback to REST API
 * - Reopenable Edge Launcher Dock & Draggable Minimized Pill (Alt+D shortcut)
 */

(function () {
  function mount() {
    if (document.getElementById('delta-rr-host')) return;
    if (!document.body) {
      if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', mount);
      } else {
        setTimeout(mount, 50);
      }
      return;
    }

    const API_BASE = 'http://127.0.0.1:8000';
    let activeTab = 'chain'; // 'chain' | 'analytics'
    let selectedExpiry = null;
    let selectedSymbol = null;
    let entryMode = 'live'; // 'live' | 'manual'
    let customEntryPrice = null;
    let currentLots = 10;
    let capitalBudget = null;
    let isSetupLocked = false;
    let isMinimized = false;
    let isClosed = false;
    let pillPos = { x: null, y: null };
    let isPillDragging = false;
    let pillMoved = false;
    let pillDragStart = { x: 0, y: 0 };
    let pillOffset = { x: 0, y: 0 };
    let lastChainData = null;
    let lastEvalData = null;
    let isDragging = false;
    let isResizing = false;
    let dragOffset = { x: 0, y: 0 };
    let resizeStart = { x: 0, y: 0, w: 0, h: 0 };
    let pollInterval = null;
    let riskPerLot = null;
    let rewardPerLot = null;
    let lastPriceUpdateTime = null;

    // Restore persisted state
    try {
      const saved = JSON.parse(localStorage.getItem('delta_rr_overlay_state') || '{}');
      if (saved.lots) currentLots = saved.lots;
      if (saved.selectedSymbol) selectedSymbol = saved.selectedSymbol;
      if (saved.customEntryPrice) customEntryPrice = saved.customEntryPrice;
      if (saved.capitalBudget) capitalBudget = saved.capitalBudget;
      if (saved.entryMode) entryMode = saved.entryMode;
      if (saved.isSetupLocked !== undefined) isSetupLocked = saved.isSetupLocked;
      if (saved.isMinimized !== undefined) isMinimized = saved.isMinimized;
      // Do not default to closed on new tab load so user always sees the overlay
      isClosed = false;
      if (saved.pillX !== undefined && saved.pillY !== undefined) {
        pillPos.x = saved.pillX;
        pillPos.y = saved.pillY;
      }
    } catch (e) {
      // Ignore
    }

    // Create Shadow Root container with bulletproof styling on host
    const host = document.createElement('div');
    host.id = 'delta-rr-host';
    host.style.cssText = 'position: fixed !important; top: 0 !important; left: 0 !important; width: 0 !important; height: 0 !important; z-index: 2147483647 !important; display: block !important; overflow: visible !important; pointer-events: none !important;';
    document.body.appendChild(host);
    const shadow = host.attachShadow({ mode: 'open' });

    // --------------------------------------------------------------------------
    // KEYBOARD ISOLATION: Stop TradingView from intercepting number keys!
    // In Chromium, keys typed inside Shadow DOM bubble to window unless stopped
    // during the CAPTURE phase. This prevents the "Change interval: 5" popup.
    // --------------------------------------------------------------------------
    ['keydown', 'keyup', 'keypress'].forEach((eventType) => {
      host.addEventListener(eventType, (e) => {
        e.stopPropagation();
        if (typeof e.stopImmediatePropagation === 'function') {
          e.stopImmediatePropagation();
        }
      }, true); // Capture phase!
    });

    // Load stylesheet via link
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = (typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.getURL)
      ? chrome.runtime.getURL('overlay.css')
      : `${API_BASE}/hud/styles.css`;
    shadow.appendChild(link);

    // Direct inline stylesheet injection to guarantee 100% visibility even under strict CSP
    const styleFallback = document.createElement('style');
    styleFallback.textContent = `
      :host {
        all: initial;
        display: block !important;
        pointer-events: none !important;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
        font-size: 13px;
        line-height: 1.4;
        color: #e6edf3;
        box-sizing: border-box;
      }
      *, *::before, *::after {
        box-sizing: border-box;
        margin: 0;
        padding: 0;
      }
      .sidelay-window, .sidelay-pill, .sidelay-launcher {
        pointer-events: auto !important;
      }
      .btn-icon {
        cursor: pointer !important;
        pointer-events: auto !important;
      }
      .sidelay-window {
        position: fixed !important;
        z-index: 2147483647 !important;
        top: 70px;
        right: 24px;
        width: 480px;
        height: 680px;
        min-width: 300px;
        min-height: 220px;
        max-width: 98vw;
        max-height: 98vh;
        background: #0d1117;
        border: 1px solid #30363d;
        border-radius: 12px;
        box-shadow: 0 16px 40px rgba(0, 0, 0, 0.7), 0 0 0 1px rgba(255, 255, 255, 0.05);
        display: flex;
        flex-direction: column;
        overflow: hidden;
        resize: both;
      }
      .sidelay-footer {
        position: relative !important;
        background: #161b22;
        border-top: 1px solid #21262d;
        padding: 6px 12px;
        display: flex;
        align-items: center;
        justify-content: space-between;
        font-size: 10px;
        color: #8b949e;
        user-select: none;
      }
      .resize-handle {
        position: absolute !important;
        right: 0 !important;
        bottom: 0 !important;
        width: 24px !important;
        height: 24px !important;
        cursor: nwse-resize !important;
        color: #58a6ff !important;
        user-select: none !important;
        display: flex !important;
        align-items: center !important;
        justify-content: center !important;
        font-size: 14px !important;
        z-index: 100 !important;
        pointer-events: auto !important;
      }
      .resize-handle:hover {
        color: #79c0ff !important;
        transform: scale(1.2) !important;
      }
      .sidelay-pill {
        position: fixed !important;
        z-index: 2147483647 !important;
        top: 80px !important;
        right: 20px !important;
        background: #161b22 !important;
        border: 1px solid #388bfd !important;
        border-radius: 24px !important;
        padding: 6px 14px !important;
        display: flex;
        align-items: center !important;
        gap: 10px !important;
        box-shadow: 0 8px 24px rgba(0, 0, 0, 0.6) !important;
        cursor: grab !important;
      }
      .sidelay-launcher {
        position: fixed !important;
        z-index: 2147483647 !important;
        top: 140px !important;
        right: 0 !important;
        background: #1f6feb !important;
        border: 1px solid #388bfd !important;
        border-right: none !important;
        border-radius: 8px 0 0 8px !important;
        padding: 8px 12px !important;
        color: #ffffff !important;
        font-size: 12px !important;
        font-weight: 800 !important;
        cursor: pointer !important;
        display: flex;
        align-items: center !important;
        gap: 6px !important;
        box-shadow: -2px 0 12px rgba(0,0,0,0.4) !important;
      }
      @keyframes flashSyncGlow {
        0% { background-color: rgba(63, 185, 80, 0.45); box-shadow: 0 0 12px rgba(63, 185, 80, 0.7); }
        50% { background-color: rgba(63, 185, 80, 0.2); }
        100% { background-color: transparent; box-shadow: none; }
      }
      .flash-sync {
        animation: flashSyncGlow 0.9s ease-out !important;
      }
    `;
    shadow.appendChild(styleFallback);

    // Also fetch full overlay.css and inject as text in case link is blocked by CSP
    try {
      if (typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.getURL) {
        fetch(chrome.runtime.getURL('overlay.css'))
          .then((r) => r.text())
          .then((css) => {
            const el = document.createElement('style');
            el.textContent = css;
            shadow.appendChild(el);
          })
          .catch(() => {});
      }
    } catch (e) {}

    // Template DOM
    const root = document.createElement('div');
    root.innerHTML = `
      <!-- Edge Launcher Dock (Shown when closed/hidden) -->
      <div id="sidelayLauncher" class="sidelay-launcher" style="display: none;" title="Open Delta RR Engine (Alt+D)">
        <div class="launcher-dot"></div>
        <span>⚡ DELTA RR</span>
      </div>

    <!-- Minimized Pill (Draggable) -->
    <div id="sidelayPill" class="sidelay-pill" style="display: none;" title="Drag to move • Click to expand">
      <div class="pill-dot"></div>
      <span class="pill-spot" id="pillSpot">BTC $--</span>
      <span class="pill-rr" id="pillRr">RR --</span>
      <span class="pill-expand">⤢ Expand</span>
    </div>

    <!-- Main Window -->
    <div id="sidelayWindow" class="sidelay-window">
      <!-- Header -->
      <div id="sidelayHeader" class="sidelay-header">
        <div class="header-left">
          <span class="brand-badge">⚡ DELTA RR</span>
          <span class="spot-pill" id="headerSpot">BTC $--</span>
          <span id="badgeValidation" style="font-size:9px;font-weight:700;padding:2px 5px;border-radius:3px;background:#6e7681;color:#fff;">UNVALIDATED</span>
          <span id="headerDataAge" style="font-size:10px;color:#8b949e;margin-left:4px;" title="Market quotes age">--s</span>
        </div>
        <div class="header-actions">
          <button class="btn-icon" id="btnDockLeft" title="Snap to Left">⇤</button>
          <button class="btn-icon" id="btnDockRight" title="Snap to Right">⇥</button>
          <button class="btn-icon" id="btnRefresh" title="Live Refresh Prices">⚡</button>
          <button class="btn-icon" id="btnMinimize" title="Minimize to Pill">_</button>
          <button class="btn-icon" id="btnClose" title="Close to Edge Launcher">✕</button>
        </div>
      </div>

      <!-- Navigation Tabs -->
      <div class="sidelay-tabs">
        <button class="tab-btn active" id="tabBtnChain">Option Chain (24/7)</button>
        <button class="tab-btn" id="tabBtnAnalytics">Risk:Reward Analytics</button>
      </div>

      <!-- Tab 1: Option Chain -->
      <div id="tabContentChain" class="tab-content">
        <div class="chain-controls">
          <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:4px;">
            <span style="font-size:11px;font-weight:700;color:#8b949e;text-transform:uppercase;">Select Expiry</span>
            <button id="btnFastRefreshChain" style="background:#21262d;border:1px solid #388bfd;color:#58a6ff;border-radius:4px;padding:2px 8px;font-size:10px;font-weight:700;cursor:pointer;">⚡ Live Sync</button>
          </div>
          <div class="expiry-chips" id="expiryChips">
            <span style="font-size:11px;color:#8b949e;padding:4px;">Loading expiries...</span>
          </div>
        </div>
        <div class="chain-table-container">
          <table class="chain-table">
            <thead>
              <tr>
                <th colspan="3" class="th-call">CALLS</th>
                <th class="th-strike">STRIKE</th>
                <th colspan="3" class="th-put">PUTS</th>
              </tr>
              <tr>
                <th>Bid</th>
                <th>Ask</th>
                <th>Mark</th>
                <th class="th-strike">USD</th>
                <th>Mark</th>
                <th>Bid</th>
                <th>Ask</th>
              </tr>
            </thead>
            <tbody id="chainTbody">
              <tr><td colspan="7" style="text-align:center;padding:20px;color:#8b949e;">Connecting to live Delta feed...</td></tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- Tab 2: Risk:Reward Analytics -->
      <div id="tabContentAnalytics" class="tab-content" style="display: none;">
        <div class="analytics-body">
          
          <!-- Symbol Header -->
          <div style="background:#161b22;border:1px solid #30363d;border-radius:6px;padding:8px 10px;margin-bottom:6px;">
            <div style="display:flex;align-items:center;justify-content:space-between;gap:6px;margin-bottom:8px;">
              <div style="display:flex;align-items:center;gap:6px;flex:1;">
                <label style="font-size:11px;color:#8b949e;font-weight:700;white-space:nowrap;">OPTION:</label>
                <input type="text" id="inputSymbol" style="flex:1;background:#0d1117;border:1px solid #30363d;color:#c9d1d9;border-radius:4px;padding:4px 6px;font-size:11px;font-family:monospace;font-weight:700;" placeholder="e.g. P-BTC-63000-201126">
                <span class="badge-tag tag-call" id="heroDirectionBadge" style="font-size:10px;">--</span>
              </div>
            </div>

            <!-- Dedicated Entry Mode Toggle: Live Market vs Manual Limit -->
            <div style="display:flex;gap:6px;margin-bottom:8px;">
              <button type="button" id="btnModeLive" class="entry-mode-btn active" style="flex:1;padding:5px 8px;border-radius:4px;font-size:11px;font-weight:700;cursor:pointer;display:flex;align-items:center;justify-content:center;gap:5px;background:#1f6feb;border:1px solid #388bfd;color:#fff;">
                <span style="display:inline-block;width:6px;height:6px;border-radius:50%;background:#3fb950;"></span>
                <span>⚡ Live Market Price</span>
              </button>
              <button type="button" id="btnModeManual" class="entry-mode-btn" style="flex:1;padding:5px 8px;border-radius:4px;font-size:11px;font-weight:700;cursor:pointer;display:flex;align-items:center;justify-content:center;gap:5px;background:#21262d;border:1px solid #30363d;color:#8b949e;">
                <span>✏️ Manual Limit Entry</span>
              </button>
            </div>

            <!-- Entry Price Input & Status Banner -->
            <div style="display:flex;align-items:center;gap:6px;background:#0d1117;border:1px solid #21262d;border-radius:4px;padding:6px 8px;margin-bottom:8px;">
              <div style="flex:1;display:flex;align-items:center;gap:6px;">
                <label style="font-size:11px;color:#8b949e;font-weight:700;white-space:nowrap;">ENTRY $:</label>
                <input type="number" step="0.1" id="inputOptionEntry" style="width:100px;background:#161b22;border:1px solid #30363d;color:#58a6ff;border-radius:4px;padding:4px 6px;font-size:13px;font-weight:800;" placeholder="0.0">
                <div id="entryStatusTag" style="font-size:10px;font-weight:700;color:#3fb950;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">
                  ● LIVE MOVING ASK
                </div>
              </div>
              <button type="button" id="btnGrabLivePrice" style="background:#21262d;border:1px solid #30363d;color:#58a6ff;border-radius:4px;padding:3px 8px;font-size:10px;font-weight:700;cursor:pointer;white-space:nowrap;" title="Sync current orderbook ask price">
                ↻ SYNC ASK
              </button>
            </div>

            <!-- PROMINENT EVALUATE / LOCK BUTTON -->
            <div style="display:flex;gap:6px;">
              <button type="button" id="btnEvaluate" style="flex:3;background:linear-gradient(180deg, #238636 0%, #196c2e 100%);border:1px solid #2ea043;color:#ffffff;border-radius:6px;padding:8px 12px;font-size:12px;font-weight:800;letter-spacing:0.3px;cursor:pointer;display:flex;align-items:center;justify-content:center;gap:6px;box-shadow:0 2px 6px rgba(35,134,54,0.3);">
                <span id="btnEvaluateIcon">⚡</span>
                <span id="btnEvaluateText">EVALUATE & LOCK RRR</span>
              </button>
              <button type="button" id="btnUnlock" style="display:none;flex:1;background:#21262d;border:1px solid #30363d;color:#c9d1d9;border-radius:6px;padding:8px 8px;font-size:11px;font-weight:700;cursor:pointer;white-space:nowrap;">
                🔓 UNLOCK
              </button>
            </div>
          </div>

          <!-- Lots <---> Max Risk Two-Way Synchronization -->
          <div style="display:flex;gap:6px;margin-bottom:6px;">
            <div style="flex:1;background:#161b22;border:1px solid #21262d;border-radius:6px;padding:6px 8px;">
              <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:2px;">
                <label style="font-size:10px;color:#8b949e;font-weight:700;">LOTS (Integer)</label>
                <span style="font-size:9px;color:#58a6ff;">1 lot = 0.001 BTC</span>
              </div>
              <input type="number" id="inputLots" style="width:100%;background:#0d1117;border:1px solid #30363d;color:#58a6ff;border-radius:4px;padding:4px 6px;font-size:13px;font-weight:700;" value="10" min="1" step="1">
            </div>
            <div style="flex:1;background:#161b22;border:1px solid #21262d;border-radius:6px;padding:6px 8px;">
              <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:2px;">
                <label style="font-size:10px;color:#8b949e;font-weight:700;">MAX RISK USD ($)</label>
                <span style="font-size:9px;color:#8b949e;">Auto-linked</span>
              </div>
              <input type="number" id="inputCapitalRisk" style="width:100%;background:#0d1117;border:1px solid #30363d;color:#f85149;border-radius:4px;padding:4px 6px;font-size:13px;font-weight:700;" placeholder="$0.00" min="0" step="1">
            </div>
          </div>

          <!-- Symbol Mismatch Cross-check Banner (F7) -->
          <div id="symbolMismatchBanner" style="display:none;color:#d29922;font-size:11px;font-weight:600;margin-bottom:6px;padding:4px 6px;background:#2b2210;border:1px solid #9e6a03;border-radius:4px;"></div>

          <!-- Over Budget Banner (F8) -->
          <div id="budgetWarningBanner" style="display:none;color:#f85149;font-size:11px;font-weight:600;margin-bottom:6px;padding:4px 6px;background:#2d1517;border:1px solid #da3633;border-radius:4px;"></div>

          <!-- Hero RR Card (F3: Hero = verdict + net_rr; clean_rr small labelled "before costs and time decay") -->
          <div class="rr-hero-card">
            <div class="rr-hero-left">
              <div style="display:flex;align-items:center;gap:6px;margin-bottom:2px;">
                <span class="rr-hero-label">VERDICT:</span>
                <span id="heroVerdictText" style="font-size:13px;font-weight:800;letter-spacing:0.5px;">--</span>
              </div>
              <div class="rr-hero-ratio" id="heroNetRr" style="color:#484f58;">-- : --</div>
              <span style="font-size:11px;color:#8b949e;" id="heroSubRr">before costs and time decay</span>
            </div>
            <div class="rr-hero-right">
              <span class="tag-sub" id="heroSymbol" style="font-size:11px;">Select option from chain or type above</span>
              <div id="provenanceMeta" style="font-size:9px;color:#8b949e;text-align:right;margin-top:4px;"></div>
            </div>
          </div>

          <!-- Hidden Numbers Toggle if NO_TRADE (F3) -->
          <div id="levelsHiddenToggle" style="display:none;background:#161b22;border:1px dashed #30363d;border-radius:6px;padding:8px;text-align:center;font-size:11px;color:#8b949e;margin-bottom:6px;">
            Numbers hidden because verdict is NO TRADE. <a id="btnToggleShowAnyway" href="javascript:void(0)" style="color:#58a6ff;text-decoration:underline;">Show anyway</a>
          </div>

          <div id="levelsSection">
            <!-- Option TP/SL Results -->
            <div class="levels-grid">
              <div class="level-card">
                <span class="level-title">Option Take Profit (Target)</span>
                <span class="level-val val-green" id="optTp">$--</span>
                <span class="level-sub val-green" id="optTpGain">--</span>
              </div>
              <div class="level-card">
                <span class="level-title">Option Stop Loss (SL)</span>
                <span class="level-val val-red" id="optSl">$--</span>
                <span class="level-sub val-red" id="optSlLoss">--</span>
              </div>
            </div>

            <!-- Planned Money Summary -->
            <div style="background:#161b22;border:1px solid #21262d;border-radius:6px;padding:8px 12px;display:flex;justify-content:space-between;font-size:11px;margin-bottom:6px;">
              <div><span>Max Risk: </span><strong class="val-red" id="sumRisk">-$--</strong></div>
              <div><span>Potential Reward: </span><strong class="val-green" id="sumReward">+$--</strong></div>
              <div><span style="color:#8b949e;">Est Fees: </span><strong id="sumFees" style="color:#8b949e;">$--</strong></div>
            </div>
          </div>

          <!-- Paper Trading Controls (F10) -->
          <div style="display:flex;gap:6px;margin-bottom:6px;">
            <button type="button" id="btnTakeTrade" style="flex:1;background:#238636;border:1px solid #2ea043;color:#fff;border-radius:4px;padding:5px 8px;font-size:11px;font-weight:700;cursor:pointer;">📝 I Took It</button>
            <button type="button" id="btnCloseTrade" style="flex:1;background:#21262d;border:1px solid #30363d;color:#c9d1d9;border-radius:4px;padding:5px 8px;font-size:11px;font-weight:700;cursor:pointer;">🏁 Closed at Price</button>
          </div>

          <!-- Holding Time -->
          <div class="holding-time-badge" id="holdingTimeBadge" style="margin-bottom:6px;">
            <span>⏱️ Estimated Duration:</span>
            <strong id="holdingTimeVal">--</strong>
          </div>

          <!-- Classical Standard Pivot Points (Analysis Breakdown) -->
          <div style="background:#161b22;border:1px solid #21262d;border-radius:6px;padding:8px 10px;margin-bottom:6px;">
            <div style="font-size:10px;text-transform:uppercase;color:#8b949e;font-weight:700;margin-bottom:4px;">Classical Standard Pivot Analysis (24h)</div>
            <div class="pivot-ref-text" id="pivotRefText" style="color:#79c0ff;font-weight:600;margin-bottom:6px;">--</div>
            <div id="pivotGridLevels" style="font-size:10px;font-family:monospace;color:#8b949e;line-height:1.5;background:#0d1117;padding:4px 6px;border-radius:4px;">
              Pivots calculating...
            </div>
          </div>

          <!-- Advisory -->
          <div class="advisory-box">
            <div class="advisory-title">Market Notes & Safety</div>
            <ul class="advisory-list" id="advisoryList">
              <li class="advisory-item pass"><span>✓</span><span>Ready</span></li>
            </ul>
          </div>

        </div>
      </div>

      <!-- Footer Bar with Custom Resize Handle -->
      <div class="sidelay-footer">
        <span id="footerStatus">● Engine Ready</span>
        <span>Drag corner ⤡ to resize</span>
        <div class="resize-handle" id="resizeHandle" title="Drag to resize">⤡</div>
      </div>
    </div>
  `;
  shadow.appendChild(root);

  // DOM Elements
  const elWindow = shadow.getElementById('sidelayWindow');
  const elPill = shadow.getElementById('sidelayPill');
  const elLauncher = shadow.getElementById('sidelayLauncher');
  const elHeader = shadow.getElementById('sidelayHeader');
  const resizeHandle = shadow.getElementById('resizeHandle');
  const btnRefresh = shadow.getElementById('btnRefresh');
  const btnFastRefreshChain = shadow.getElementById('btnFastRefreshChain');
  const btnDockLeft = shadow.getElementById('btnDockLeft');
  const btnDockRight = shadow.getElementById('btnDockRight');
  const btnMinimize = shadow.getElementById('btnMinimize');
  const btnClose = shadow.getElementById('btnClose');
  const tabBtnChain = shadow.getElementById('tabBtnChain');
  const tabBtnAnalytics = shadow.getElementById('tabBtnAnalytics');
  const tabContentChain = shadow.getElementById('tabContentChain');
  const tabContentAnalytics = shadow.getElementById('tabContentAnalytics');
  const expiryChips = shadow.getElementById('expiryChips');
  const chainTbody = shadow.getElementById('chainTbody');
  const headerSpot = shadow.getElementById('headerSpot');
  const pillSpot = shadow.getElementById('pillSpot');
  const pillRr = shadow.getElementById('pillRr');
  const inputLots = shadow.getElementById('inputLots');
  const inputCapitalRisk = shadow.getElementById('inputCapitalRisk');
  const inputOptionEntry = shadow.getElementById('inputOptionEntry');
  const inputSymbol = shadow.getElementById('inputSymbol');
  const btnModeLive = shadow.getElementById('btnModeLive');
  const btnModeManual = shadow.getElementById('btnModeManual');
  const entryStatusTag = shadow.getElementById('entryStatusTag');
  const btnGrabLivePrice = shadow.getElementById('btnGrabLivePrice');
  const btnEvaluate = shadow.getElementById('btnEvaluate');
  const btnEvaluateText = shadow.getElementById('btnEvaluateText');
  const btnEvaluateIcon = shadow.getElementById('btnEvaluateIcon');
  const btnUnlock = shadow.getElementById('btnUnlock');
  const pivotGridLevels = shadow.getElementById('pivotGridLevels');
  const heroNetRr = shadow.getElementById('heroNetRr');
  const heroSubRr = shadow.getElementById('heroSubRr');
  const heroDirectionBadge = shadow.getElementById('heroDirectionBadge');
  const heroSymbol = shadow.getElementById('heroSymbol');
  const holdingTimeVal = shadow.getElementById('holdingTimeVal');
  const sumRisk = shadow.getElementById('sumRisk');
  const sumReward = shadow.getElementById('sumReward');
  const sumFees = shadow.getElementById('sumFees');
  const optTp = shadow.getElementById('optTp');
  const optTpGain = shadow.getElementById('optTpGain');
  const optSl = shadow.getElementById('optSl');
  const optSlLoss = shadow.getElementById('optSlLoss');
  const pivotRefText = shadow.getElementById('pivotRefText');
  const advisoryList = shadow.getElementById('advisoryList');
  const footerStatus = shadow.getElementById('footerStatus');
  const badgeValidation = shadow.getElementById('badgeValidation');
  const headerDataAge = shadow.getElementById('headerDataAge');
  const heroVerdictText = shadow.getElementById('heroVerdictText');
  const provenanceMeta = shadow.getElementById('provenanceMeta');
  const symbolMismatchBanner = shadow.getElementById('symbolMismatchBanner');
  const budgetWarningBanner = shadow.getElementById('budgetWarningBanner');
  const levelsHiddenToggle = shadow.getElementById('levelsHiddenToggle');
  const btnToggleShowAnyway = shadow.getElementById('btnToggleShowAnyway');
  const levelsSection = shadow.getElementById('levelsSection');
  const btnTakeTrade = shadow.getElementById('btnTakeTrade');
  const btnCloseTrade = shadow.getElementById('btnCloseTrade');

  let forceShowNumbers = false;
  let activePaperTradeId = null;

  if (btnToggleShowAnyway) {
    btnToggleShowAnyway.addEventListener('click', (e) => {
      e.preventDefault();
      forceShowNumbers = !forceShowNumbers;
      levelsSection.style.display = forceShowNumbers ? 'block' : 'none';
      btnToggleShowAnyway.textContent = forceShowNumbers ? 'Hide' : 'Show anyway';
    });
  }

  if (btnTakeTrade) {
    btnTakeTrade.addEventListener('click', async () => {
      if (!lastEvalData || !lastEvalData.provenance) {
        alert('No active evaluation to record.');
        return;
      }
      try {
        const evalId = lastEvalData.provenance.evaluation_id;
        const res = await callApi('/api/journal/paper-trade', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            evaluation_id: evalId,
            actual_fill_price: customEntryPrice || (lastEvalData.setup && lastEvalData.setup.option ? lastEvalData.setup.option.entry_limit : 0),
          }),
        });
        if (res && res.success) {
          activePaperTradeId = res.trade_id;
          btnTakeTrade.textContent = `✓ Trade #${res.trade_id} Taken`;
          btnTakeTrade.style.background = '#1f6feb';
        } else {
          alert('Paper trade logging: ' + (res.error || 'Failed'));
        }
      } catch (err) {
        alert('Error: ' + err.message);
      }
    });
  }

  if (btnCloseTrade) {
    btnCloseTrade.addEventListener('click', async () => {
      const exitP = prompt('Enter actual exit price:', customEntryPrice || '');
      if (exitP && !isNaN(parseFloat(exitP)) && activePaperTradeId) {
        try {
          const res = await callApi('/api/journal/close-trade', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              trade_id: activePaperTradeId,
              actual_exit_price: parseFloat(exitP),
            }),
          });
          if (res && res.success) {
            alert(`Trade #${activePaperTradeId} closed. P&L: $${res.pnl_usd || 0}`);
            activePaperTradeId = null;
            btnTakeTrade.textContent = '📝 I Took It';
            btnTakeTrade.style.background = '#238636';
          }
        } catch (err) {
          alert('Error: ' + err.message);
        }
      }
    });
  }

  // Stop key propagation inside individual input elements
  [inputLots, inputCapitalRisk, inputOptionEntry, inputSymbol].forEach((inputEl) => {
    if (!inputEl) return;
    ['keydown', 'keyup', 'keypress'].forEach((evt) => {
      inputEl.addEventListener(evt, (e) => {
        e.stopPropagation();
        if (typeof e.stopImmediatePropagation === 'function') {
          e.stopImmediatePropagation();
        }
      }, true);
    });
  });

  inputLots.value = currentLots;
  if (capitalBudget) inputCapitalRisk.value = capitalBudget;
  if (customEntryPrice) inputOptionEntry.value = customEntryPrice;
  if (selectedSymbol) inputSymbol.value = selectedSymbol;

  // Restore persisted position & dimensions if valid, otherwise anchor safely in upper-right
  try {
    const saved = JSON.parse(localStorage.getItem('delta_rr_overlay_state') || '{}');
    if (saved.width && saved.height) {
      elWindow.style.width = `${Math.max(300, saved.width)}px`;
      elWindow.style.height = `${Math.max(220, saved.height)}px`;
    }
    if (saved.x !== undefined && saved.y !== undefined && saved.x >= 0 && saved.y >= 0) {
      elWindow.style.left = `${Math.min(window.innerWidth - 100, Math.max(10, saved.x))}px`;
      elWindow.style.top = `${Math.min(window.innerHeight - 100, Math.max(10, saved.y))}px`;
      elWindow.style.right = 'auto';
    } else {
      elWindow.style.top = '70px';
      elWindow.style.right = '24px';
      elWindow.style.left = 'auto';
    }
  } catch (e) {
    elWindow.style.top = '70px';
    elWindow.style.right = '24px';
    elWindow.style.left = 'auto';
  }
  elWindow.style.setProperty('display', 'flex', 'important');
  elLauncher.style.setProperty('display', 'none', 'important');
  elPill.style.setProperty('display', 'none', 'important');
  isClosed = false;
  isMinimized = false;

  // SPA safety: ensure host remains attached if Delta React re-renders body
  setInterval(() => {
    if (!document.getElementById('delta-rr-host') && document.body) {
      document.body.appendChild(host);
    }
  }, 1000);

  function persistState() {
    try {
      const rect = elWindow.getBoundingClientRect();
      const pRect = elPill.getBoundingClientRect();
      const state = {
        x: rect.left,
        y: rect.top,
        width: rect.width,
        height: rect.height,
        pillX: pRect.left,
        pillY: pRect.top,
        lots: currentLots,
        capitalBudget: capitalBudget,
        selectedSymbol: selectedSymbol,
        customEntryPrice: customEntryPrice,
        entryMode: entryMode,
        isSetupLocked: isSetupLocked,
        isMinimized: isMinimized,
        isClosed: isClosed,
      };
      localStorage.setItem('delta_rr_overlay_state', JSON.stringify(state));
    } catch (e) {
      // Ignore
    }
  }

  function openWindow() {
    isClosed = false;
    isMinimized = false;
    elLauncher.style.setProperty('display', 'none', 'important');
    elPill.style.setProperty('display', 'none', 'important');
    elWindow.style.setProperty('display', 'flex', 'important');
    persistState();
  }

  function minimizeWindow() {
    isClosed = false;
    isMinimized = true;
    elWindow.style.setProperty('display', 'none', 'important');
    elLauncher.style.setProperty('display', 'none', 'important');
    elPill.style.setProperty('display', 'flex', 'important');
    persistState();
  }

  function closeToLauncher() {
    isClosed = true;
    isMinimized = false;
    elWindow.style.setProperty('display', 'none', 'important');
    elPill.style.setProperty('display', 'none', 'important');
    elLauncher.style.setProperty('display', 'flex', 'important');
    persistState();
  }

  // Window Dragging Logic
  elHeader.addEventListener('mousedown', (e) => {
    if (e.target.closest('button') || e.target.closest('input')) return;
    isDragging = true;
    const rect = elWindow.getBoundingClientRect();
    dragOffset.x = e.clientX - rect.left;
    dragOffset.y = e.clientY - rect.top;
    elWindow.style.left = `${rect.left}px`;
    elWindow.style.top = `${rect.top}px`;
    elWindow.style.right = 'auto';
    e.preventDefault();
  });

  // Dedicated Corner Resizing Handle Logic
  resizeHandle.addEventListener('mousedown', (e) => {
    isResizing = true;
    const rect = elWindow.getBoundingClientRect();
    elWindow.style.left = `${rect.left}px`;
    elWindow.style.top = `${rect.top}px`;
    elWindow.style.right = 'auto';
    resizeStart.x = e.clientX;
    resizeStart.y = e.clientY;
    resizeStart.w = rect.width;
    resizeStart.h = rect.height;
    e.preventDefault();
    e.stopPropagation();
  });

  // Draggable Minimized Pill Logic
  elPill.addEventListener('mousedown', (e) => {
    isPillDragging = true;
    pillMoved = false;
    pillDragStart.x = e.clientX;
    pillDragStart.y = e.clientY;
    const rect = elPill.getBoundingClientRect();
    pillOffset.x = e.clientX - rect.left;
    pillOffset.y = e.clientY - rect.top;
    e.preventDefault();
  });

  window.addEventListener('mousemove', (e) => {
    if (isDragging) {
      const newX = Math.max(10, Math.min(window.innerWidth - elWindow.offsetWidth - 10, e.clientX - dragOffset.x));
      const newY = Math.max(10, Math.min(window.innerHeight - elWindow.offsetHeight - 10, e.clientY - dragOffset.y));
      elWindow.style.left = `${newX}px`;
      elWindow.style.top = `${newY}px`;
      elWindow.style.right = 'auto';
    } else if (isResizing) {
      const newW = Math.max(300, Math.min(window.innerWidth - 20, resizeStart.w + (e.clientX - resizeStart.x)));
      const newH = Math.max(220, Math.min(window.innerHeight - 20, resizeStart.h + (e.clientY - resizeStart.y)));
      elWindow.style.width = `${newW}px`;
      elWindow.style.height = `${newH}px`;
    } else if (isPillDragging) {
      if (Math.hypot(e.clientX - pillDragStart.x, e.clientY - pillDragStart.y) > 4) {
        pillMoved = true;
      }
      const newX = Math.max(10, Math.min(window.innerWidth - elPill.offsetWidth - 10, e.clientX - pillOffset.x));
      const newY = Math.max(10, Math.min(window.innerHeight - elPill.offsetHeight - 10, e.clientY - pillOffset.y));
      elPill.style.left = `${newX}px`;
      elPill.style.top = `${newY}px`;
      elPill.style.right = 'auto';
    }
  });

  window.addEventListener('mouseup', () => {
    if (isDragging || isResizing) {
      isDragging = false;
      isResizing = false;
      persistState();
    }
    if (isPillDragging) {
      isPillDragging = false;
      if (pillMoved) {
        const pRect = elPill.getBoundingClientRect();
        pillPos.x = pRect.left;
        pillPos.y = pRect.top;
        persistState();
      } else {
        openWindow();
      }
    }
  });

  // Snap to Left / Right Dock Presets
  btnDockLeft.addEventListener('click', () => {
    elWindow.style.left = '20px';
    elWindow.style.top = '70px';
    elWindow.style.right = 'auto';
    persistState();
  });

  btnDockRight.addEventListener('click', () => {
    elWindow.style.left = `${Math.max(20, window.innerWidth - elWindow.offsetWidth - 20)}px`;
    elWindow.style.top = '70px';
    elWindow.style.right = 'auto';
    persistState();
  });

  // Minimize / Expand logic
  btnMinimize.addEventListener('click', (e) => {
    e.preventDefault();
    e.stopPropagation();
    minimizeWindow();
  });

  // Close to Launcher logic
  btnClose.addEventListener('click', (e) => {
    e.preventDefault();
    e.stopPropagation();
    closeToLauncher();
  });

  // Edge Launcher button click -> Reopen window
  elLauncher.addEventListener('click', (e) => {
    e.preventDefault();
    e.stopPropagation();
    openWindow();
  });

  // Global Keyboard Shortcut: Alt+D to toggle open/closed anytime
  window.addEventListener('keydown', (e) => {
    if ((e.altKey && (e.key === 'd' || e.key === 'D')) || (e.ctrlKey && e.shiftKey && (e.key === 'd' || e.key === 'D'))) {
      e.preventDefault();
      if (isClosed || isMinimized) {
        openWindow();
      } else {
        closeToLauncher();
      }
    }
  });

  // Tab switching
  tabBtnChain.addEventListener('click', () => {
    activeTab = 'chain';
    tabBtnChain.classList.add('active');
    tabBtnAnalytics.classList.remove('active');
    tabContentChain.style.display = 'flex';
    tabContentAnalytics.style.display = 'none';
  });

  tabBtnAnalytics.addEventListener('click', () => {
    activeTab = 'analytics';
    tabBtnAnalytics.classList.add('active');
    tabBtnChain.classList.remove('active');
    tabContentAnalytics.style.display = 'flex';
    tabContentChain.style.display = 'none';
  });

  // --------------------------------------------------------------------------
  // Entry Mode Selection: Live Market Track vs Manual Limit Entry
  // --------------------------------------------------------------------------
  function setEntryMode(mode) {
    entryMode = mode;
    if (mode === 'live') {
      btnModeLive.style.background = '#1f6feb';
      btnModeLive.style.borderColor = '#388bfd';
      btnModeLive.style.color = '#ffffff';
      btnModeManual.style.background = '#21262d';
      btnModeManual.style.borderColor = '#30363d';
      btnModeManual.style.color = '#8b949e';
      if (!isSetupLocked) {
        entryStatusTag.textContent = '● TRACKING LIVE MARKET';
        entryStatusTag.style.color = '#3fb950';
      }
    } else {
      btnModeManual.style.background = '#1f6feb';
      btnModeManual.style.borderColor = '#388bfd';
      btnModeManual.style.color = '#ffffff';
      btnModeLive.style.background = '#21262d';
      btnModeLive.style.borderColor = '#30363d';
      btnModeLive.style.color = '#8b949e';
      if (!isSetupLocked) {
        entryStatusTag.textContent = '✏️ MANUAL LIMIT ENTRY';
        entryStatusTag.style.color = '#58a6ff';
      }
    }
    persistState();
  }

  btnModeLive.addEventListener('click', () => {
    setEntryMode('live');
    if (selectedSymbol) {
      syncLivePriceAndEvaluate(selectedSymbol);
    }
  });

  btnModeManual.addEventListener('click', () => {
    setEntryMode('manual');
    inputOptionEntry.focus();
  });

  // Update Lock UI
  function updateLockStatusUI() {
    if (isSetupLocked) {
      btnEvaluate.style.background = '#21262d';
      btnEvaluate.style.borderColor = '#388bfd';
      btnEvaluateIcon.textContent = '🔒';
      btnEvaluateText.textContent = `LOCKED @ $${customEntryPrice ? Number(customEntryPrice).toFixed(1) : '--'} (CLICK TO RE-CALC)`;
      btnUnlock.style.display = 'block';
      entryStatusTag.textContent = `🔒 LOCKED @ $${customEntryPrice ? Number(customEntryPrice).toFixed(1) : '--'}`;
      entryStatusTag.style.color = '#79c0ff';
    } else {
      btnEvaluate.style.background = 'linear-gradient(180deg, #238636 0%, #196c2e 100%)';
      btnEvaluate.style.borderColor = '#2ea043';
      btnEvaluateIcon.textContent = '⚡';
      btnEvaluateText.textContent = 'EVALUATE & LOCK RRR';
      btnUnlock.style.display = 'none';
      if (entryMode === 'live') {
        entryStatusTag.textContent = '● LIVE MOVING ASK';
        entryStatusTag.style.color = '#3fb950';
      } else {
        entryStatusTag.textContent = '✏️ MANUAL LIMIT ENTRY';
        entryStatusTag.style.color = '#58a6ff';
      }
    }
  }

  // EVALUATE BUTTON CLICK: Locks setup and evaluates RRR
  btnEvaluate.addEventListener('click', async () => {
    const sym = inputSymbol.value.trim().toUpperCase();
    if (sym && sym.match(/^[CP]-BTC-[0-9]+-[0-9]+$/)) {
      selectedSymbol = sym;
    }
    if (!selectedSymbol) {
      footerStatus.textContent = '⚠️ Select an option contract first';
      footerStatus.style.color = '#f85149';
      return;
    }

    // Lock price to current input value or fetch live from engine
    let targetPrice = parseFloat(inputOptionEntry.value);
    if (entryMode === 'live' && (!targetPrice || targetPrice <= 0)) {
      const tk = await fetchSingleTicker(selectedSymbol);
      if (tk && tk.ask > 0) targetPrice = tk.ask;
    }

    if (targetPrice && targetPrice > 0) {
      customEntryPrice = targetPrice;
      inputOptionEntry.value = targetPrice;
    }

    isSetupLocked = true;
    updateLockStatusUI();
    persistState();
    await evaluateSymbol(selectedSymbol);
  });

  // UNLOCK BUTTON CLICK: Returns to live tracking
  btnUnlock.addEventListener('click', async () => {
    isSetupLocked = false;
    if (entryMode === 'live') {
      customEntryPrice = null;
    }
    updateLockStatusUI();
    persistState();
    if (selectedSymbol) {
      await syncLivePriceAndEvaluate(selectedSymbol);
    }
  });

  // User manually edits Entry Price
  inputOptionEntry.addEventListener('input', () => {
    const val = parseFloat(inputOptionEntry.value);
    if (!isNaN(val) && val > 0) {
      customEntryPrice = val;
      setEntryMode('manual');
      persistState();
    }
  });

  // User manually changes Symbol
  inputSymbol.addEventListener('input', () => {
    const val = inputSymbol.value.trim().toUpperCase();
    if (val.match(/^[CP]-BTC-[0-9]+-[0-9]+$/)) {
      selectedSymbol = val;
      isSetupLocked = false;
      updateLockStatusUI();
      persistState();
      syncLivePriceAndEvaluate(selectedSymbol);
    }
  });

  // Quick "Sync Ask" button (F7: Entry comes from engine ask)
  btnGrabLivePrice.addEventListener('click', async () => {
    if (selectedSymbol) {
      btnGrabLivePrice.textContent = '↻ SYNCING...';
      btnGrabLivePrice.style.color = '#e3b341';
      try {
        const tk = await fetchSingleTicker(selectedSymbol);
        let liveAsk = (tk && tk.ask > 0) ? tk.ask : null;
        if (liveAsk && liveAsk > 0) {
          customEntryPrice = liveAsk;
          inputOptionEntry.value = liveAsk;
          inputOptionEntry.classList.add('flash-sync');
          setTimeout(() => inputOptionEntry.classList.remove('flash-sync'), 900);
          btnGrabLivePrice.textContent = `✓ $${Number(liveAsk).toFixed(1)}`;
          btnGrabLivePrice.style.color = '#3fb950';
          persistState();
          if (isSetupLocked) {
            await evaluateSymbol(selectedSymbol);
          }
          setTimeout(() => {
            btnGrabLivePrice.textContent = '↻ SYNC ASK';
            btnGrabLivePrice.style.color = '#58a6ff';
          }, 1500);
        } else {
          btnGrabLivePrice.textContent = '⚠️ NO ASK';
          btnGrabLivePrice.style.color = '#f85149';
          setTimeout(() => {
            btnGrabLivePrice.textContent = '↻ SYNC ASK';
            btnGrabLivePrice.style.color = '#58a6ff';
          }, 1500);
        }
      } catch (err) {
        btnGrabLivePrice.textContent = '⚠️ ERROR';
        btnGrabLivePrice.style.color = '#f85149';
        setTimeout(() => {
          btnGrabLivePrice.textContent = '↻ SYNC ASK';
          btnGrabLivePrice.style.color = '#58a6ff';
        }, 1500);
      }
    }
  });

  // --------------------------------------------------------------------------
  // Discrete Lots <---> Max Risk USD Synchronization
  // (F8: max_lots = floor(budget / risk_per_lot), which may be 0; no Math.max(1, ...))
  // --------------------------------------------------------------------------
  inputLots.addEventListener('input', () => {
    currentLots = parseInt(inputLots.value) || 0;
    persistState();
    if (riskPerLot && riskPerLot > 0) {
      const computedRisk = (currentLots * riskPerLot).toFixed(2);
      if (shadow.activeElement !== inputCapitalRisk) {
        inputCapitalRisk.value = computedRisk;
      }
      sumRisk.textContent = `-$${formatUSD(computedRisk, 2)}`;
      if (rewardPerLot && rewardPerLot > 0) {
        sumReward.textContent = `+$${formatUSD((currentLots * rewardPerLot).toFixed(2), 2)}`;
      }
    }
  });

  inputCapitalRisk.addEventListener('input', () => {
    const maxR = parseFloat(inputCapitalRisk.value);
    if (!isNaN(maxR) && maxR >= 0) {
      capitalBudget = maxR;
      persistState();
      if (riskPerLot && riskPerLot > 0) {
        const discreteLots = Math.floor(maxR / riskPerLot);
        currentLots = discreteLots;
        if (shadow.activeElement !== inputLots) {
          inputLots.value = discreteLots;
        }
        sumRisk.textContent = `-$${formatUSD((discreteLots * riskPerLot).toFixed(2), 2)}`;
        if (rewardPerLot && rewardPerLot > 0) {
          sumReward.textContent = `+$${formatUSD((discreteLots * rewardPerLot).toFixed(2), 2)}`;
        }
        if (discreteLots === 0) {
          budgetWarningBanner.style.display = 'block';
          budgetWarningBanner.textContent = `1 lot risks $${riskPerLot.toFixed(2)}, above your $${maxR.toFixed(2)} budget.`;
        } else {
          budgetWarningBanner.style.display = 'none';
        }
      }
    }
  });

  // Live Refresh Buttons (Force 0ms cache bypass with visual pulse)
  async function handleFastRefresh() {
    btnFastRefreshChain.textContent = '↻ SYNCING...';
    btnFastRefreshChain.style.color = '#e3b341';
    btnFastRefreshChain.style.borderColor = '#d29922';

    try {
      await fetchOptionChain(true);
      btnFastRefreshChain.textContent = '✓ SYNCED!';
      btnFastRefreshChain.style.color = '#3fb950';
      btnFastRefreshChain.style.borderColor = '#2ea043';

      if (headerSpot) {
        headerSpot.classList.add('flash-sync');
        setTimeout(() => headerSpot.classList.remove('flash-sync'), 900);
      }
      const cells = chainTbody.querySelectorAll('td');
      cells.forEach((c) => {
        c.classList.add('flash-sync');
        setTimeout(() => c.classList.remove('flash-sync'), 900);
      });

      setTimeout(() => {
        btnFastRefreshChain.textContent = '⚡ Live Sync';
        btnFastRefreshChain.style.color = '#58a6ff';
        btnFastRefreshChain.style.borderColor = '#388bfd';
      }, 1500);
    } catch (e) {
      btnFastRefreshChain.textContent = '⚠️ OFFLINE';
      btnFastRefreshChain.style.color = '#f85149';
      btnFastRefreshChain.style.borderColor = '#da3633';
      setTimeout(() => {
        btnFastRefreshChain.textContent = '⚡ Live Sync';
        btnFastRefreshChain.style.color = '#58a6ff';
        btnFastRefreshChain.style.borderColor = '#388bfd';
      }, 2000);
    }
  }

  btnRefresh.addEventListener('click', handleFastRefresh);
  btnFastRefreshChain.addEventListener('click', handleFastRefresh);

  // Unified API call helper: delegates to background.js service worker
  async function callApi(endpoint, options = {}) {
    const targetUrl = endpoint.startsWith('http') ? endpoint : `${API_BASE}${endpoint}`;

    if (typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.sendMessage) {
      try {
        const response = await new Promise((resolve, reject) => {
          chrome.runtime.sendMessage({ type: 'FETCH_API', url: targetUrl, options }, (resp) => {
            if (chrome.runtime.lastError) {
              return reject(new Error(chrome.runtime.lastError.message));
            }
            if (resp && resp.success) {
              return resolve(resp.data);
            }
            reject(new Error(resp?.error || 'Engine connection error'));
          });
        });
        return response;
      } catch (bgErr) {
        // Fallback
      }
    }

    const res = await fetch(targetUrl, options);
    if (!res.ok) {
      const text = await res.text();
      throw new Error(`HTTP ${res.status}: ${text}`);
    }
    return await res.json();
  }

  // Format Helper
  function formatUSD(num, decimals = 1) {
    if (num === null || num === undefined || isNaN(num)) return '--';
    return '$' + Number(num).toLocaleString('en-US', {
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals,
    });
  }

  // --------------------------------------------------------------------------
  // Direct DOM reader from Delta Exchange active screen
  // Scrapes TradingView chart order buttons (757.0 SELL / 762.0 BUY) with 0ms delay
  // --------------------------------------------------------------------------
  function readDeltaPagePrices() {
    try {
      // 1. Direct search in document for TradingView Chart BUY / SELL buttons
      const buttons = document.querySelectorAll('button, div[role="button"], span, div');
      let buyPrice = null;
      let sellPrice = null;

      for (const el of buttons) {
        if (el.children.length > 3) continue;
        const txt = (el.innerText || el.textContent || '').trim();
        if (!txt) continue;

        if (txt.includes('BUY') || txt.includes('Buy')) {
          const m = txt.match(/([0-9,.]+)\s*(?:BUY|Buy)/i) || txt.match(/(?:BUY|Buy)\s*([0-9,.]+)/i);
          if (m) {
            const val = parseFloat(m[1].replace(/,/g, ''));
            if (!isNaN(val) && val > 0 && val < 500000) buyPrice = val;
          }
        }

        if (txt.includes('SELL') || txt.includes('Sell')) {
          const m = txt.match(/([0-9,.]+)\s*(?:SELL|Sell)/i) || txt.match(/(?:SELL|Sell)\s*([0-9,.]+)/i);
          if (m) {
            const val = parseFloat(m[1].replace(/,/g, ''));
            if (!isNaN(val) && val > 0 && val < 500000) sellPrice = val;
          }
        }
      }

      if (buyPrice && sellPrice) {
        return { bid: sellPrice, ask: buyPrice };
      }

      // Check inside accessible iframes
      const iframes = document.querySelectorAll('iframe');
      for (const iframe of iframes) {
        try {
          const idoc = iframe.contentDocument || iframe.contentWindow?.document;
          if (!idoc) continue;
          const ibuttons = idoc.querySelectorAll('button, div[role="button"], span, div');
          for (const el of ibuttons) {
            if (el.children.length > 3) continue;
            const txt = (el.innerText || el.textContent || '').trim();
            if (txt.includes('BUY') || txt.includes('Buy')) {
              const m = txt.match(/([0-9,.]+)\s*(?:BUY|Buy)/i) || txt.match(/(?:BUY|Buy)\s*([0-9,.]+)/i);
              if (m) {
                const val = parseFloat(m[1].replace(/,/g, ''));
                if (!isNaN(val) && val > 0 && val < 500000) buyPrice = val;
              }
            }
            if (txt.includes('SELL') || txt.includes('Sell')) {
              const m = txt.match(/([0-9,.]+)\s*(?:SELL|Sell)/i) || txt.match(/(?:SELL|Sell)\s*([0-9,.]+)/i);
              if (m) {
                const val = parseFloat(m[1].replace(/,/g, ''));
                if (!isNaN(val) && val > 0 && val < 500000) sellPrice = val;
              }
            }
          }
          if (buyPrice && sellPrice) {
            return { bid: sellPrice, ask: buyPrice };
          }
        } catch (frameErr) {
          // Cross-origin protection
        }
      }

      // 2. Orderbook best bid / ask fallback
      const bestBidEl = document.querySelector('.best-bid, [data-testid="best-bid"], .orderbook-bid');
      const bestAskEl = document.querySelector('.best-ask, [data-testid="best-ask"], .orderbook-ask');
      if (bestBidEl && bestAskEl) {
        const bid = parseFloat(bestBidEl.textContent.replace(/[^0-9.]/g, ''));
        const ask = parseFloat(bestAskEl.textContent.replace(/[^0-9.]/g, ''));
        if (bid > 0 && ask > 0) return { bid, ask };
      }
    } catch (e) {
      // Ignore DOM scrape errors
    }
    return null;
  }

  // Detect active contract from URL, page title, or Delta UI headers
  function detectActiveContractFromDOM() {
    try {
      const urlMatch = window.location.href.match(/([CP]-BTC-[0-9]+-[0-9]+)/i);
      if (urlMatch) return urlMatch[1].toUpperCase();

      const titleMatch = document.title.match(/([CP]-BTC-[0-9]+-[0-9]+)/i);
      if (titleMatch) return titleMatch[1].toUpperCase();

      const elements = document.querySelectorAll('button, span, div, h1, h2, h3');
      for (const el of elements) {
        if (el.children.length === 0 && el.textContent) {
          const txt = el.textContent.trim();
          const m = txt.match(/^([CP]-BTC-[0-9]+-[0-9]+)$/i);
          if (m) return m[1].toUpperCase();
        }
      }
    } catch (e) {
      // Ignore
    }
    return null;
  }

  // Fetch 0-latency live ticker for active symbol (F7: never fall back to scraped DOM price)
  async function fetchSingleTicker(symbol) {
    try {
      return await callApi(`/api/ticker?symbol=${encodeURIComponent(symbol)}`);
    } catch (e) {
      // Fail closed: never use scraped DOM price in money calculations
    }
    return null;
  }

  // Fetch Option Chain from API
  async function fetchOptionChain(forceRefresh = false) {
    try {
      let endpoint = '/api/option-chain';
      const params = [];
      if (selectedExpiry) params.push(`expiry=${encodeURIComponent(selectedExpiry)}`);
      if (forceRefresh) params.push('refresh=true');
      if (params.length > 0) endpoint += `?${params.join('&')}`;

      const data = await callApi(endpoint);
      lastChainData = data;
      lastPriceUpdateTime = new Date();
      renderOptionChain(data);

      const timeStr = lastPriceUpdateTime.toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
      footerStatus.textContent = `● Live (${timeStr})`;
      footerStatus.style.color = '#3fb950';
    } catch (err) {
      footerStatus.textContent = '○ Engine offline — run START_ENGINE.bat';
      footerStatus.style.color = '#f85149';
    }
  }

  // Render Option Chain Table & Expiries
  function renderOptionChain(data) {
    const spot = data.spot_price || 0;
    const spotStr = formatUSD(spot, 1);
    headerSpot.textContent = `BTC ${spotStr}`;
    pillSpot.textContent = `BTC ${spotStr}`;

    if (data.expiries && data.expiries.length > 0) {
      if (!selectedExpiry && data.selected_expiry) {
        selectedExpiry = data.selected_expiry;
      }
      expiryChips.innerHTML = '';
      data.expiries.forEach((exp) => {
        const btn = document.createElement('button');
        btn.className = `chip-btn ${exp.code === selectedExpiry ? 'active' : ''}`;
        btn.textContent = exp.display;
        btn.addEventListener('click', () => {
          selectedExpiry = exp.code;
          fetchOptionChain(true);
        });
        expiryChips.appendChild(btn);
      });
    }

    const strikes = data.strikes || [];
    if (strikes.length === 0) {
      chainTbody.innerHTML = '<tr><td colspan="7" style="text-align:center;padding:20px;color:#8b949e;">No active strikes found</td></tr>';
      return;
    }

    let closestDiff = Infinity;
    let closestStrike = null;
    strikes.forEach((s) => {
      const diff = Math.abs(s.strike - spot);
      if (diff < closestDiff) {
        closestDiff = diff;
        closestStrike = s.strike;
      }
    });

    chainTbody.innerHTML = '';
    strikes.forEach((s) => {
      const tr = document.createElement('tr');
      if (s.strike === closestStrike) {
        tr.className = 'atm-row';
      }

      const c = s.call || {};
      const p = s.put || {};

      const isCallSelected = c.symbol && c.symbol === selectedSymbol;
      const isPutSelected = p.symbol && p.symbol === selectedSymbol;

      tr.innerHTML = `
        <td class="cell-call ${isCallSelected ? 'cell-selected' : ''}" data-symbol="${c.symbol || ''}">${c.bid ? formatUSD(c.bid, 0) : '--'}</td>
        <td class="cell-call ${isCallSelected ? 'cell-selected' : ''}" data-symbol="${c.symbol || ''}">${c.ask ? formatUSD(c.ask, 0) : '--'}</td>
        <td class="cell-call ${isCallSelected ? 'cell-selected' : ''}" data-symbol="${c.symbol || ''}">${c.mark ? formatUSD(c.mark, 0) : '--'}</td>
        <td class="cell-strike">${s.strike.toLocaleString()}</td>
        <td class="cell-put ${isPutSelected ? 'cell-selected' : ''}" data-symbol="${p.symbol || ''}">${p.mark ? formatUSD(p.mark, 0) : '--'}</td>
        <td class="cell-put ${isPutSelected ? 'cell-selected' : ''}" data-symbol="${p.symbol || ''}">${p.bid ? formatUSD(p.bid, 0) : '--'}</td>
        <td class="cell-put ${isPutSelected ? 'cell-selected' : ''}" data-symbol="${p.symbol || ''}">${p.ask ? formatUSD(p.ask, 0) : '--'}</td>
      `;

      tr.querySelectorAll('.cell-call').forEach((cell) => {
        cell.addEventListener('click', () => {
          if (c.symbol) selectAndEvaluate(c.symbol, c.ask || c.mark);
        });
      });

      tr.querySelectorAll('.cell-put').forEach((cell) => {
        cell.addEventListener('click', () => {
          if (p.symbol) selectAndEvaluate(p.symbol, p.ask || p.mark);
        });
      });

      chainTbody.appendChild(tr);
    });
  }

  // Fetch live market moving price from ticker API (engine ask), then evaluate.
  // F7: DOM scraping removed from money path. Entry comes from engine ask.
  async function syncLivePriceAndEvaluate(symbol) {
    if (!isSetupLocked && entryMode === 'live') {
      const tk = await fetchSingleTicker(symbol);
      let liveAsk = (tk && tk.ask > 0) ? tk.ask : null;
      if (liveAsk && liveAsk > 0) {
        customEntryPrice = liveAsk;
        if (shadow.activeElement !== inputOptionEntry) {
          inputOptionEntry.value = liveAsk;
        }
      }
    }
    evaluateSymbol(symbol);
  }

  // User selects an option contract from chain table
  function selectAndEvaluate(symbol, initialPrice = null) {
    const isNewSymbol = symbol !== selectedSymbol;
    selectedSymbol = symbol;
    if (shadow.activeElement !== inputSymbol) {
      inputSymbol.value = symbol;
    }

    if (isNewSymbol) {
      isSetupLocked = false;
      customEntryPrice = (initialPrice && initialPrice > 0) ? initialPrice : null;
      if (customEntryPrice && shadow.activeElement !== inputOptionEntry) {
        inputOptionEntry.value = customEntryPrice;
      }
    }
    updateLockStatusUI();
    persistState();

    activeTab = 'analytics';
    tabBtnAnalytics.classList.add('active');
    tabBtnChain.classList.remove('active');
    tabContentAnalytics.style.display = 'flex';
    tabContentChain.style.display = 'none';

    if (symbol.startsWith('C-')) {
      heroDirectionBadge.textContent = 'CALL';
      heroDirectionBadge.className = 'badge-tag tag-call';
    } else {
      heroDirectionBadge.textContent = 'PUT';
      heroDirectionBadge.className = 'badge-tag tag-put';
    }
    heroSymbol.textContent = symbol;

    syncLivePriceAndEvaluate(symbol);
  }

  // Call /api/evaluate with user custom entry price, risk capital, and standard pivots
  async function evaluateSymbol(symbol) {
    heroSymbol.textContent = symbol;
    footerStatus.textContent = 'Calculating chart Risk:Reward...';

    try {
      const payload = {
        symbol: symbol,
        lots: currentLots,
        max_risk_usd: capitalBudget,
        hold_minutes: 30,
        entry_override_option: customEntryPrice,
      };

      const data = await callApi('/api/evaluate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });

      lastEvalData = data;
      renderAnalytics(data);

      if (isSetupLocked) {
        footerStatus.textContent = `🔒 Locked Setup @ $${customEntryPrice ? Number(customEntryPrice).toFixed(1) : '--'}`;
        footerStatus.style.color = '#79c0ff';
      } else {
        const timeStr = new Date().toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
        footerStatus.textContent = `● Live Moving Entry (${timeStr})`;
        footerStatus.style.color = '#3fb950';
      }
    } catch (err) {
      footerStatus.textContent = '⚠️ Engine offline - run START_ENGINE.bat: ' + err.message;
      footerStatus.style.color = '#f85149';
    }
  }

  // Render Analytics Tab — shows TP, SL, clean RR, and max risk for chosen lots
  // F3: Hero = verdict (colour-coded) + net_rr. clean_rr shown small labelled "before costs and time decay".
  function renderAnalytics(data) {
    const verdict = data.verdict || 'NO_TRADE';
    const isTrade = verdict === 'TRADE';
    const isCaution = verdict === 'CAUTION';
    const verdictColor = isTrade ? '#3fb950' : isCaution ? '#d29922' : '#f85149';

    if (heroVerdictText) {
      heroVerdictText.textContent = verdict;
      heroVerdictText.style.color = verdictColor;
    }

    // Always show: validation_status badge (UNVALIDATED) and data_age_seconds
    if (badgeValidation) {
      badgeValidation.textContent = 'UNVALIDATED';
    }
    const dataAge = (data.provenance && data.provenance.data_age_seconds !== undefined) ? data.provenance.data_age_seconds : 0;
    if (headerDataAge) {
      headerDataAge.textContent = `${Number(dataAge).toFixed(1)}s`;
      headerDataAge.style.color = dataAge > 3.0 ? '#f85149' : '#8b949e';
    }

    if (provenanceMeta && data.provenance) {
      const idxSym = data.provenance.index_symbol || '.DEXBTUSD';
      const idxP = data.provenance.spot_index_price ? `$${Number(data.provenance.spot_index_price).toFixed(1)}` : '--';
      const compTime = data.provenance.computed_at ? new Date(data.provenance.computed_at).toLocaleTimeString() : '--';
      provenanceMeta.innerHTML = `Analyzed: ${data.provenance.symbol || ''} (${idxSym} ${idxP})<br>Computed: ${compTime}`;
    }

    // If verdict == NO_TRADE, hide entry/stop/target numbers behind a "show anyway" link
    if (verdict === 'NO_TRADE') {
      levelsHiddenToggle.style.display = 'block';
      levelsSection.style.display = forceShowNumbers ? 'block' : 'none';
    } else {
      levelsHiddenToggle.style.display = 'none';
      levelsSection.style.display = 'block';
    }

    const setup = data.setup;
    if (!setup) {
      heroNetRr.textContent = '-- : --';
      heroNetRr.style.color = verdictColor;
      heroSubRr.textContent = 'Engine returned no setup (check advisory below)';
      advisoryList.innerHTML = '';
      (data.reasons || []).forEach((r) => {
        const li = document.createElement('li');
        const isHard = r.severity === 'hard';
        li.className = isHard ? 'advisory-item hard-warn' : 'advisory-item warn';
        li.style.color = isHard ? '#f85149' : '#d29922';
        li.style.borderColor = isHard ? '#da3633' : '#9e6a03';
        li.innerHTML = `<span>${isHard ? '🛑' : '⚠️'}</span><span>${r.message}</span>`;
        advisoryList.appendChild(li);
      });
      return;
    }

    const opt = setup.option || {};
    const money = setup.money || {};
    const isCall = setup.direction === 'call';

    // Update Entry Price field ONLY if user hasn't manually set one and not typing
    if ((!customEntryPrice || customEntryPrice <= 0) && shadow.activeElement !== inputOptionEntry) {
      customEntryPrice = opt.entry_limit || opt.ask;
      inputOptionEntry.value = customEntryPrice;
    }

    // Direction badge
    heroDirectionBadge.textContent = isCall ? 'CALL' : 'PUT';
    heroDirectionBadge.className = `badge-tag ${isCall ? 'tag-call' : 'tag-put'}`;

    // F3: Hero = verdict + net_rr. clean_rr shown small labelled "before costs and time decay"
    const cleanRR = money.clean_rr || 0;
    const netRR = money.net_rr || 0;

    if (netRR > 0) {
      heroNetRr.textContent = `1 : ${Number(netRR).toFixed(2)}`;
      heroNetRr.style.color = verdictColor;
    } else {
      heroNetRr.textContent = '-- : --';
      heroNetRr.style.color = verdictColor;
    }
    heroSubRr.textContent = cleanRR > 0
      ? `Gross RR: 1 : ${Number(cleanRR).toFixed(2)} (before costs and time decay)`
      : 'before costs and time decay';
    pillRr.textContent = netRR > 0 ? `Net 1:${Number(netRR).toFixed(1)}` : 'RR --';

    // Risk and reward per lot
    if (currentLots > 0 && money.risk_usd !== undefined) {
      riskPerLot = money.risk_usd / currentLots;
      rewardPerLot = (money.reward_usd || 0) / currentLots;
      // Do NOT overwrite user while they are actively typing!
      if (shadow.activeElement !== inputCapitalRisk) {
        inputCapitalRisk.value = (currentLots * riskPerLot).toFixed(2);
      }
    }

    // Money Summary
    sumRisk.textContent = `-${formatUSD(money.risk_usd || 0, 2)}`;
    sumReward.textContent = `+${formatUSD(money.reward_usd || 0, 2)}`;
    sumFees.textContent = formatUSD(money.fees_usd_round_trip || 0, 2);

    if (money.max_lots_for_risk_budget !== undefined && capitalBudget) {
      heroSubRr.textContent += ` | Max lots for $${capitalBudget} risk: ${money.max_lots_for_risk_budget}`;
    }

    // Option TP and SL prices
    const entryP = customEntryPrice || opt.entry_limit;
    const spotTp = setup.spot ? setup.spot.tp : null;
    const spotSl = setup.spot ? setup.spot.sl : null;

    // TP
    const tpPrice = opt.tp_premium_model || 0;
    optTp.textContent = formatUSD(tpPrice, 1);
    if (tpPrice > 0 && entryP > 0) {
      const tpGain = tpPrice - entryP;
      const tpPct = ((tpGain / entryP) * 100).toFixed(1);
      let tpText = `+${formatUSD(tpGain, 1)} (+${tpPct}%)`;
      if (spotTp) tpText += ` • Spot: $${Number(spotTp).toLocaleString(undefined, {minimumFractionDigits: 1, maximumFractionDigits: 1})}`;
      optTpGain.textContent = tpText;
    } else {
      optTpGain.textContent = '--';
    }

    // SL
    const slPrice = opt.sl_premium_model || 0;
    optSl.textContent = formatUSD(slPrice, 1);
    if (slPrice >= 0 && entryP > 0) {
      const slLoss = entryP - slPrice;
      const slPct = ((slLoss / entryP) * 100).toFixed(1);
      let slText = `-${formatUSD(slLoss, 1)} (-${slPct}%)`;
      if (spotSl) slText += ` • Spot: $${Number(spotSl).toLocaleString(undefined, {minimumFractionDigits: 1, maximumFractionDigits: 1})}`;
      optSlLoss.textContent = slText;
    } else {
      optSlLoss.textContent = '--';
    }

    // Holding Time
    holdingTimeVal.textContent = setup.estimated_holding_time || '--';

    // Classical Standard Pivot Points (24h)
    pivotRefText.textContent = setup.pivot_reference || 'Target & Stop derived from Classical Standard Pivots';
    if (setup.pivots) {
      const p = setup.pivots;
      pivotGridLevels.innerHTML = `
        <div style="display:flex;justify-content:space-between;margin-bottom:3px;font-size:10px;">
          <span style="color:#f85149;" title="Resistance 2">R2: $${Number(p.R2 || 0).toLocaleString()}</span>
          <span style="color:#d29922;" title="Resistance 1">R1: $${Number(p.R1 || 0).toLocaleString()}</span>
          <span style="color:#58a6ff;font-weight:700;" title="Central Pivot">P: $${Number(p.P || 0).toLocaleString()}</span>
          <span style="color:#3fb950;" title="Support 1">S1: $${Number(p.S1 || 0).toLocaleString()}</span>
          <span style="color:#2ea043;" title="Support 2">S2: $${Number(p.S2 || 0).toLocaleString()}</span>
        </div>
        <div style="font-size:9px;color:#8b949e;display:flex;justify-content:space-between;border-top:1px solid #21262d;padding-top:2px;margin-top:2px;">
          <span>Index Spot: $${Number(setup.spot.price || 0).toLocaleString()}</span>
          <span>5m ATR: $${Number(setup.spot.atr || 0).toFixed(1)}</span>
        </div>
      `;
    }

    // Advisory Checklist (F3: render severity == "hard" in red, soft in amber)
    advisoryList.innerHTML = '';
    const reasons = data.reasons || [];
    if (reasons.length === 0) {
      advisoryList.innerHTML = '<li class="advisory-item pass"><span>✓</span><span>All structure and volatility checks passed.</span></li>';
    } else {
      reasons.forEach((r) => {
        const li = document.createElement('li');
        const isHard = r.severity === 'hard';
        li.className = isHard ? 'advisory-item hard-warn' : 'advisory-item warn';
        li.style.color = isHard ? '#f85149' : '#d29922';
        li.style.borderColor = isHard ? '#da3633' : '#9e6a03';
        li.innerHTML = `<span>${isHard ? '🛑' : '⚠️'}</span><span>${r.message}</span>`;
        advisoryList.appendChild(li);
      });
    }

    // F7: Optional cross-check with DOM price
    const engineMark = opt.mark || opt.ask;
    const domP = readDeltaPagePrices();
    if (domP && domP.ask && engineMark && engineMark > 0) {
      const diffPct = Math.abs(domP.ask - engineMark) / engineMark;
      if (diffPct > 0.03) {
        symbolMismatchBanner.style.display = 'block';
        symbolMismatchBanner.textContent = `⚠️ Possible symbol mismatch: Scraped orderbook ($${domP.ask}) differs from engine ask ($${engineMark}) by ${(diffPct * 100).toFixed(1)}%`;
      } else {
        symbolMismatchBanner.style.display = 'none';
      }
    } else {
      symbolMismatchBanner.style.display = 'none';
    }

    updateLockStatusUI();
  }

  // Delta Exchange Active Contract Sync (Auto-detect from DOM or URL)
  function checkDeltaUrl() {
    try {
      const detected = detectActiveContractFromDOM();
      if (detected && detected !== selectedSymbol) {
        selectAndEvaluate(detected);
      }
    } catch (e) {
      // Ignore
    }
  }

  // Initial Option Chain fetch
  fetchOptionChain();

  // Periodic polling (1.2s cadence)
  pollInterval = setInterval(() => {
    if (activeTab === 'chain') {
      fetchOptionChain(false);
    } else if (activeTab === 'analytics' && selectedSymbol && !isSetupLocked && entryMode === 'live') {
      syncLivePriceAndEvaluate(selectedSymbol);
    }
    checkDeltaUrl();
  }, 1200);

  console.log('[DELTA-RR] Overlay mounted successfully on', window.location.href);
  } // end mount()

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', mount);
  } else {
    mount();
  }
})();
