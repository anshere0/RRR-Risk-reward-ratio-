/**
 * Delta BTC Options Risk-Reward Engine - HUD Client Script
 * Conforms to docs/01, docs/02, docs/04, docs/06.
 */

// State
let ws = null;
let reconnectTimer = null;
let lastComputedAtEpoch = null;
let lastDataAgeBase = 0.0;
let lastDataAgeReceivedAt = Date.now();
let availableOptions = [];

// DOM Elements
const elements = {
  // Status & Age
  statusDot: document.getElementById('statusDot'),
  statusText: document.getElementById('statusText'),
  dataAge: document.getElementById('dataAge'),
  spotIndexPrice: document.getElementById('spotIndexPrice'),
  computedAgo: document.getElementById('computedAgo'),

  // Inputs
  symbolInput: document.getElementById('symbolInput'),
  lotsInput: document.getElementById('lotsInput'),
  maxRiskInput: document.getElementById('maxRiskInput'),
  holdMinutesInput: document.getElementById('holdMinutesInput'),
  evaluateBtn: document.getElementById('evaluateBtn'),
  autoRefreshToggle: document.getElementById('autoRefreshToggle'),
  atmCallBtn: document.getElementById('atmCallBtn'),
  atmPutBtn: document.getElementById('atmPutBtn'),
  optionsList: document.getElementById('optionsList'),

  // Verdict Hero
  verdictHero: document.getElementById('verdictHero'),
  verdictText: document.getElementById('verdictText'),
  directionBadge: document.getElementById('directionBadge'),

  // Option Chain
  hudExpiryChips: document.getElementById('hudExpiryChips'),
  hudChainTbody: document.getElementById('hudChainTbody'),

  // Metrics
  netRrVal: document.getElementById('netRrVal'),
  beWinVal: document.getElementById('beWinVal'),
  planRiskVal: document.getElementById('planRiskVal'),
  planRewardVal: document.getElementById('planRewardVal'),

  // Price Table
  entryOptionVal: document.getElementById('entryOptionVal'),
  entrySpotVal: document.getElementById('entrySpotVal'),
  slOptionVal: document.getElementById('slOptionVal'),
  slOptionSub: document.getElementById('slOptionSub'),
  slSpotVal: document.getElementById('slSpotVal'),
  slSpotSub: document.getElementById('slSpotSub'),
  tpOptionVal: document.getElementById('tpOptionVal'),
  tpOptionSub: document.getElementById('tpOptionSub'),
  tpSpotVal: document.getElementById('tpSpotVal'),
  tpSpotSub: document.getElementById('tpSpotSub'),

  // Sizing & Provenance
  lotsSizingVal: document.getElementById('lotsSizingVal'),
  worstLossVal: document.getElementById('worstLossVal'),
  feesVal: document.getElementById('feesVal'),
  maxLotsVal: document.getElementById('maxLotsVal'),
  provSymbol: document.getElementById('provSymbol'),
  provIndex: document.getElementById('provIndex'),
  provConfigHash: document.getElementById('provConfigHash'),
  provRegion: document.getElementById('provRegion'),

  // Reasons
  reasonsList: document.getElementById('reasonsList'),

  // Modal
  infoBtn: document.getElementById('infoBtn'),
  infoModal: document.getElementById('infoModal'),
  modalClose: document.getElementById('modalClose'),
};

// Formatting helpers
function formatUSD(val, decimals = 2) {
  if (val === null || val === undefined || isNaN(val)) return '--';
  return '$' + Number(val).toLocaleString('en-US', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals
  });
}

function formatNumber(val, decimals = 1) {
  if (val === null || val === undefined || isNaN(val)) return '--';
  return Number(val).toLocaleString('en-US', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals
  });
}

function getRequestPayload() {
  const symbol = elements.symbolInput.value.trim() || null;
  const lots = Math.max(1.0, parseFloat(elements.lotsInput.value) || 10.0);
  const maxRiskRaw = elements.maxRiskInput.value.trim();
  const maxRisk = maxRiskRaw ? parseFloat(maxRiskRaw) : null;
  const holdMin = parseInt(elements.holdMinutesInput.value) || 30;

  return {
    symbol: symbol,
    lots: lots,
    max_risk_usd: maxRisk,
    hold_minutes: holdMin,
  };
}

// Update UI with response from evaluate
function updateUI(data) {
  if (!data) return;

  const verdict = data.verdict || 'NO_TRADE';
  const isTrade = (verdict === 'TRADE');
  const isCaution = (verdict === 'CAUTION');
  elements.verdictText.textContent = isTrade ? 'TRADE' : isCaution ? 'CAUTION' : 'NO TRADE';
  elements.verdictHero.className = 'verdict-hero ' + (isTrade ? 'trade' : isCaution ? 'caution' : 'no-trade');

  // Direction badge
  if (data.setup && data.setup.direction) {
    const dir = data.setup.direction.toLowerCase();
    elements.directionBadge.textContent = dir === 'call' ? 'CALL (BULLISH)' : 'PUT (BEARISH)';
    elements.directionBadge.className = 'direction-badge ' + dir;
    elements.directionBadge.style.display = 'inline-block';
  } else {
    elements.directionBadge.style.display = 'none';
  }

  // Provenance & Age base
  if (data.provenance) {
    const prov = data.provenance;
    elements.provSymbol.textContent = prov.symbol || '--';
    elements.provIndex.textContent = prov.index_symbol || '.DEXBTUSD';
    elements.provConfigHash.textContent = (prov.config_hash || '').slice(0, 8);
    elements.provRegion.textContent = prov.region || 'india';

    if (prov.index_price) {
      elements.spotIndexPrice.textContent = 'BTC: ' + formatUSD(prov.index_price, 1);
    }

    if (prov.symbol && !elements.symbolInput.value) {
      elements.symbolInput.value = prov.symbol;
    }

    lastDataAgeBase = prov.data_age_seconds || 0.0;
    lastDataAgeReceivedAt = Date.now();

    if (prov.computed_at_utc) {
      lastComputedAtEpoch = new Date(prov.computed_at_utc).getTime();
    }
  }

  // Setup Details
  if (data.setup) {
    const setup = data.setup;
    const spot = setup.spot || {};
    const opt = setup.option || {};
    const money = setup.money || {};

    // Core Metrics
    const effectiveRr = money.clean_rr || money.net_rr || 0;
    elements.netRrVal.textContent = (effectiveRr > 0) ? `${formatNumber(effectiveRr, 2)} : 1` : '--';
    elements.netRrVal.className = 'metric-value ' + (effectiveRr >= 1.5 ? 'highlight-green' : 'highlight-red');

    elements.beWinVal.textContent = (money.breakeven_win_probability > 0)
      ? `${formatNumber(money.breakeven_win_probability * 100, 1)}%`
      : '--';

    elements.planRiskVal.textContent = (money.risk_usd > 0) ? `-${formatUSD(money.risk_usd)}` : '--';
    elements.planRewardVal.textContent = (money.reward_usd > 0) ? `+${formatUSD(money.reward_usd)}` : '--';

    // Price Table
    elements.entryOptionVal.textContent = formatUSD(opt.entry_limit, 1);
    elements.entrySpotVal.textContent = formatUSD(spot.price, 1);

    elements.slOptionVal.textContent = formatUSD(opt.sl_premium_model, 1);
    elements.slOptionSub.textContent = `Net: ${formatUSD(opt.sl_exit_net, 1)}`;
    elements.slSpotVal.textContent = formatUSD(spot.sl, 1);
    elements.slSpotSub.textContent = spot.sl_source || 'Swing Pivot';

    elements.tpOptionVal.textContent = formatUSD(opt.tp_premium_model, 1);
    elements.tpOptionSub.textContent = `Net: ${formatUSD(opt.tp_exit_net, 1)}`;
    elements.tpSpotVal.textContent = formatUSD(spot.tp, 1);
    elements.tpSpotSub.textContent = spot.tp_source || 'Target Pivot';

    // Sizing
    elements.lotsSizingVal.textContent = formatNumber(money.lots, 0);
    elements.worstLossVal.textContent = formatUSD(money.worst_case_loss_usd);
    elements.feesVal.textContent = formatUSD(money.fees_usd_round_trip);
    elements.maxLotsVal.textContent = money.max_lots_for_risk_budget !== null && money.max_lots_for_risk_budget !== undefined
      ? formatNumber(money.max_lots_for_risk_budget, 0)
      : 'Unlimited';
  } else {
    // Clear metrics if setup is absent
    elements.netRrVal.textContent = '--';
    elements.netRrVal.className = 'metric-value';
    elements.beWinVal.textContent = '--';
    elements.planRiskVal.textContent = '--';
    elements.planRewardVal.textContent = '--';

    elements.entryOptionVal.textContent = '--';
    elements.entrySpotVal.textContent = '--';
    elements.slOptionVal.textContent = '--';
    elements.slOptionSub.textContent = '';
    elements.slSpotVal.textContent = '--';
    elements.slSpotSub.textContent = '';
    elements.tpOptionVal.textContent = '--';
    elements.tpOptionSub.textContent = '';
    elements.tpSpotVal.textContent = '--';
    elements.tpSpotSub.textContent = '';

    elements.lotsSizingVal.textContent = '--';
    elements.worstLossVal.textContent = '--';
    elements.feesVal.textContent = '--';
    elements.maxLotsVal.textContent = '--';
  }

  // Reasons list
  elements.reasonsList.innerHTML = '';
  const reasons = data.reasons || [];
  if (reasons.length === 0) {
    if (isTrade) {
      const li = document.createElement('li');
      li.className = 'reason-item pass';
      li.innerHTML = '<span class="reason-code">[PASS]</span><span>All safety & RR gates satisfied. Optimal setup.</span>';
      elements.reasonsList.appendChild(li);
    }
  } else {
    reasons.forEach(r => {
      const li = document.createElement('li');
      li.className = `reason-item ${r.severity || 'hard'}`;
      const badge = r.severity === 'warning' ? '[WARN]' : '[HARD]';
      li.innerHTML = `<span class="reason-code">${badge} ${r.code}:</span> <span>${r.message}</span>`;
      elements.reasonsList.appendChild(li);
    });
  }
}

// Live timer tick (updates age colors and 'computed X s ago' every 100ms)
function startLocalClockTick() {
  setInterval(() => {
    // 1. Data age
    const elapsedSinceReceive = (Date.now() - lastDataAgeReceivedAt) / 1000.0;
    const currentAge = lastDataAgeBase + elapsedSinceReceive;
    elements.dataAge.textContent = `Age: ${currentAge.toFixed(1)}s`;

    if (currentAge < 1.0) {
      elements.dataAge.className = 'data-age age-green';
    } else if (currentAge < 3.0) {
      elements.dataAge.className = 'data-age age-yellow';
    } else {
      elements.dataAge.className = 'data-age age-red';
    }

    // 2. Computed X s ago
    if (lastComputedAtEpoch) {
      const secondsAgo = Math.max(0, (Date.now() - lastComputedAtEpoch) / 1000.0);
      elements.computedAgo.textContent = `Computed ${secondsAgo.toFixed(1)}s ago`;
    }
  }, 100);
}

// Evaluate via REST fallback
async function evaluateViaRest() {
  try {
    const payload = getRequestPayload();
    const resp = await fetch('/api/evaluate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (resp.ok) {
      const data = await resp.json();
      updateUI(data);
    }
  } catch (err) {
    console.error('REST evaluate failed:', err);
  }
}

// Send evaluate request over WebSocket
function triggerEvaluate() {
  const payload = getRequestPayload();
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(payload));
  } else {
    evaluateViaRest();
  }
}

// WebSocket Connection
function connectWebSocket() {
  if (reconnectTimer) clearTimeout(reconnectTimer);

  const loc = window.location;
  const proto = (loc.protocol === 'https:') ? 'wss:' : 'ws:';
  const wsUrl = `${proto}//${loc.host}/ws/hud`;

  try {
    ws = new WebSocket(wsUrl);
  } catch (e) {
    console.warn('Could not instantiate WebSocket, falling back to REST polling', e);
    scheduleReconnect();
    return;
  }

  ws.onopen = () => {
    elements.statusDot.className = 'status-dot connected';
    elements.statusText.textContent = 'CONNECTED';
    // Send initial request with current input values
    ws.send(JSON.stringify(getRequestPayload()));
  };

  ws.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      updateUI(data);
    } catch (e) {
      console.error('Error parsing WS message:', e);
    }
  };

  ws.onclose = () => {
    elements.statusDot.className = 'status-dot';
    elements.statusText.textContent = 'DISCONNECTED';
    scheduleReconnect();
  };

  ws.onerror = () => {
    elements.statusDot.className = 'status-dot';
    elements.statusText.textContent = 'ERROR';
  };
}

function scheduleReconnect() {
  if (reconnectTimer) clearTimeout(reconnectTimer);
  reconnectTimer = setTimeout(() => {
    connectWebSocket();
  }, 3000);
}

// Populate available options for dropdown / quick buttons
let selectedExpiryCode = null;

async function fetchOptionChain() {
  try {
    const url = new URL('/api/option-chain', window.location.origin);
    if (selectedExpiryCode) url.searchParams.set('expiry', selectedExpiryCode);
    const resp = await fetch(url.toString());
    if (!resp.ok) return;
    const data = await resp.json();
    renderOptionChainHUD(data);
  } catch (err) {
    console.warn('Option chain fetch error:', err);
  }
}

function renderOptionChainHUD(data) {
  if (!elements.hudExpiryChips || !elements.hudChainTbody) return;
  const spot = data.spot_price || 0;

  // Expiry chips
  if (data.expiries && data.expiries.length > 0) {
    if (!selectedExpiryCode && data.selected_expiry) {
      selectedExpiryCode = data.selected_expiry;
    }
    elements.hudExpiryChips.innerHTML = '';
    data.expiries.forEach(exp => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = `expiry-chip ${exp.code === selectedExpiryCode ? 'active' : ''}`;
      btn.textContent = exp.display;
      btn.addEventListener('click', () => {
        selectedExpiryCode = exp.code;
        fetchOptionChain();
      });
      elements.hudExpiryChips.appendChild(btn);
    });
  }

  // Strikes table
  const strikes = data.strikes || [];
  if (strikes.length === 0) {
    elements.hudChainTbody.innerHTML = '<tr><td colspan="7" style="text-align:center;padding:12px;color:#94a3b8;">No active strikes found</td></tr>';
    return;
  }

  let closestDiff = Infinity;
  let closestStrike = null;
  strikes.forEach(s => {
    const diff = Math.abs(s.strike - spot);
    if (diff < closestDiff) {
      closestDiff = diff;
      closestStrike = s.strike;
    }
  });

  const curSymbol = elements.symbolInput.value.trim();
  elements.hudChainTbody.innerHTML = '';

  strikes.forEach(s => {
    const tr = document.createElement('tr');
    if (s.strike === closestStrike) tr.className = 'atm-row';

    const c = s.call || {};
    const p = s.put || {};
    const isCallSelected = c.symbol && c.symbol === curSymbol;
    const isPutSelected = p.symbol && p.symbol === curSymbol;

    tr.innerHTML = `
      <td class="cell-call ${isCallSelected ? 'cell-selected' : ''}" data-symbol="${c.symbol || ''}">${c.bid ? formatUSD(c.bid, 0) : '--'}</td>
      <td class="cell-call ${isCallSelected ? 'cell-selected' : ''}" data-symbol="${c.symbol || ''}">${c.ask ? formatUSD(c.ask, 0) : '--'}</td>
      <td class="cell-call ${isCallSelected ? 'cell-selected' : ''}" data-symbol="${c.symbol || ''}">${c.mark ? formatUSD(c.mark, 0) : '--'}</td>
      <td class="cell-strike">${s.strike.toLocaleString()}</td>
      <td class="cell-put ${isPutSelected ? 'cell-selected' : ''}" data-symbol="${p.symbol || ''}">${p.mark ? formatUSD(p.mark, 0) : '--'}</td>
      <td class="cell-put ${isPutSelected ? 'cell-selected' : ''}" data-symbol="${p.symbol || ''}">${p.bid ? formatUSD(p.bid, 0) : '--'}</td>
      <td class="cell-put ${isPutSelected ? 'cell-selected' : ''}" data-symbol="${p.symbol || ''}">${p.ask ? formatUSD(p.ask, 0) : '--'}</td>
    `;

    tr.querySelectorAll('.cell-call').forEach(cell => {
      cell.addEventListener('click', () => {
        if (c.symbol) {
          elements.symbolInput.value = c.symbol;
          triggerEvaluate();
          fetchOptionChain();
        }
      });
    });

    tr.querySelectorAll('.cell-put').forEach(cell => {
      cell.addEventListener('click', () => {
        if (p.symbol) {
          elements.symbolInput.value = p.symbol;
          triggerEvaluate();
          fetchOptionChain();
        }
      });
    });

    elements.hudChainTbody.appendChild(tr);
  });
}

async function fetchAvailableOptions() {
  try {
    const resp = await fetch('/api/options');
    if (resp.ok) {
      availableOptions = await resp.json();
      elements.optionsList.innerHTML = '';
      availableOptions.forEach(opt => {
        const optionEl = document.createElement('option');
        optionEl.value = opt.symbol;
        optionEl.textContent = `${opt.symbol} (Strike: ${opt.strike_price})`;
        elements.optionsList.appendChild(optionEl);
      });
    }
  } catch (err) {
    console.warn('Could not fetch options list:', err);
  }
}

function selectNearestATM(prefType = 'C') {
  if (!availableOptions || availableOptions.length === 0) {
    triggerEvaluate();
    return;
  }
  const filtered = availableOptions.filter(o => o.contract_type && o.contract_type.toLowerCase().includes(prefType === 'C' ? 'call' : 'put'));
  if (filtered.length > 0) {
    elements.symbolInput.value = filtered[0].symbol;
    triggerEvaluate();
  }
}

// Event Listeners
function initEventListeners() {
  // Evaluate button
  elements.evaluateBtn.addEventListener('click', () => {
    triggerEvaluate();
  });

  // Global hotkey 'E'
  window.addEventListener('keydown', (e) => {
    // Only trigger if not actively typing inside input
    const tag = (e.target && e.target.tagName) ? e.target.tagName.toLowerCase() : '';
    if (tag === 'input' || tag === 'textarea') {
      if (e.key === 'Enter') {
        triggerEvaluate();
      }
      return;
    }
    if (e.key === 'e' || e.key === 'E') {
      e.preventDefault();
      triggerEvaluate();
    }
  });

  // Quick ATM buttons
  elements.atmCallBtn.addEventListener('click', () => selectNearestATM('C'));
  elements.atmPutBtn.addEventListener('click', () => selectNearestATM('P'));

  // Info Modal
  elements.infoBtn.addEventListener('click', (e) => {
    e.preventDefault();
    elements.infoModal.style.display = 'flex';
  });

  elements.modalClose.addEventListener('click', () => {
    elements.infoModal.style.display = 'none';
  });

  elements.infoModal.addEventListener('click', (e) => {
    if (e.target === elements.infoModal) {
      elements.infoModal.style.display = 'none';
    }
  });

  // Auto refresh toggle
  let pollInterval = null;
  elements.autoRefreshToggle.addEventListener('change', () => {
    if (!elements.autoRefreshToggle.checked) {
      if (pollInterval) clearInterval(pollInterval);
      pollInterval = null;
    } else {
      if (ws && ws.readyState === WebSocket.OPEN) {
        // WebSocket pushes every 1s natively
      } else {
        pollInterval = setInterval(evaluateViaRest, 1000);
      }
    }
  });
}

// Initialize
document.addEventListener('DOMContentLoaded', () => {
  initEventListeners();
  startLocalClockTick();
  connectWebSocket();
  fetchAvailableOptions();
  fetchOptionChain();
  setInterval(fetchOptionChain, 2000);
});
