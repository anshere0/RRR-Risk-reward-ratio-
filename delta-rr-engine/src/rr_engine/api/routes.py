"""FastAPI routes for the Delta Options Risk-Reward Engine."""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import time
from typing import Any

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect

from rr_engine import __version__
from rr_engine.config import get_config_hash
from rr_engine.engine.models import EvaluateRequest, RREvaluateResponse, StatusResponse

router = APIRouter()


@router.post("/api/evaluate", response_model=RREvaluateResponse)
async def evaluate_endpoint(req: EvaluateRequest, request: Request) -> RREvaluateResponse:
    """Evaluate an option setup for the retail trader.

    Returns TRADE / NO_TRADE verdict, reasons, spot & option levels, and net RR.
    Target p95 latency < 200ms.
    """
    engine = request.app.state.engine
    return await engine.evaluate(req)


@router.get("/api/status", response_model=StatusResponse)
async def status_endpoint(request: Request) -> StatusResponse:
    """Returns engine and feed health status."""
    manager = request.app.state.feed_manager
    state = manager.state
    clock = state.clock

    age = clock.max_data_age(("index", "option_ticker"))
    is_conn = state.is_ws_connected

    health_status: Any = "healthy"
    if not is_conn:
        health_status = "disconnected"
    elif age > 3.0:
        health_status = "degraded"

    return StatusResponse(
        status=health_status,
        ws_connected=is_conn,
        region=manager.settings.region,
        active_symbol=manager.active_symbol,
        spot_index_price=state.spot_index_price,
        data_age_seconds=round(age if age != float("inf") else 999.0, 2),
        candles_count=len(state.candles_5m),
        config_hash=get_config_hash(),
        version=__version__,
    )


@router.get("/api/debug/candles")
async def debug_candles_endpoint(request: Request) -> dict[str, Any]:
    """Debug endpoint reporting candle health, volatility, and anomaly metrics (F5)."""
    engine = getattr(request.app.state, "engine", None)
    manager = getattr(request.app.state, "feed_manager", None)
    state = getattr(engine, "state", None) or (manager.state if manager else None)
    if not state:
        return {"error": "Engine/State unavailable"}

    closed = state.candles_5m.closed_candles
    closed_dicts = state.candles_5m.closed_dicts
    count = len(closed)

    sigma_5m = None
    atr = None
    adx = None
    if count >= 30:
        from rr_engine.indicators import adx_wilder, atr_wilder, ewma_realized_vol

        atrs = atr_wilder(closed_dicts, 14)
        atr = round(atrs[-1], 2) if atrs and atrs[-1] is not None else None

        adx_dict = adx_wilder(closed_dicts, 14)
        adx = (
            round(adx_dict["adx"][-1], 2)
            if adx_dict and adx_dict.get("adx") and adx_dict["adx"][-1] is not None
            else None
        )

        vol_series = ewma_realized_vol(closed_dicts, decay=0.94, min_periods=30)
        sigma_5m = float(vol_series[-1]) if vol_series and vol_series[-1] is not None else None

    zero_closes = [c for c in closed_dicts if c.get("c") == 0 or c.get("c") is None]
    repeated_closes = sum(1 for i in range(1, len(closed_dicts)) if closed_dicts[i]["c"] == closed_dicts[i - 1]["c"])

    seen_ts = set()
    dup_ts = 0
    for c in closed_dicts:
        if c["t"] in seen_ts:
            dup_ts += 1
        seen_ts.add(c["t"])

    missing_ts = 0
    for i in range(1, len(closed_dicts)):
        diff = closed_dicts[i]["t"] - closed_dicts[i - 1]["t"]
        if diff > 300:
            missing_ts += (diff // 300) - 1

    return {
        "count": count,
        "last_10": closed_dicts[-10:],
        "sigma_5m": sigma_5m,
        "atr": atr,
        "adx": adx,
        "zero_closes": zero_closes,
        "zero_closes_count": len(zero_closes),
        "repeated_closes_count": repeated_closes,
        "duplicate_timestamps_count": dup_ts,
        "missing_timestamps_count": missing_ts,
    }


_chain_cache: dict[str, Any] = {"ts": 0.0, "raw_tickers": []}


def _parse_expiry_key(code: str) -> tuple[int, int, int]:
    """Parse DDMMYY code for chronological sorting."""
    if len(code) == 6 and code.isdigit():
        day = int(code[:2])
        month = int(code[2:4])
        year = 2000 + int(code[4:6])
        return (year, month, day)
    return (9999, 99, 99)


def _format_expiry_display(code: str) -> str:
    """Format DDMMYY into human-readable e.g. 20 Nov 2026."""
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    if len(code) == 6 and code.isdigit():
        day = code[:2]
        m_idx = int(code[2:4]) - 1
        yr = "20" + code[4:6]
        if 0 <= m_idx < 12:
            return f"{day} {months[m_idx]} {yr}"
    return code


@router.get("/api/option-chain")
async def option_chain_endpoint(request: Request, expiry: str | None = None, refresh: bool = False) -> dict[str, Any]:
    """Return live 24/7 Option Chain across all strikes for Calls and Puts."""
    manager = getattr(request.app.state, "feed_manager", None)
    if not manager or not hasattr(manager, "rest"):
        return {"spot_price": None, "expiries": [], "strikes": [], "selected_expiry": None}

    now = time.time()
    raw_tickers = _chain_cache["raw_tickers"]
    # 0.5s cache TTL, or immediate bypass when refresh=True
    if refresh or now - _chain_cache["ts"] >= 0.5 or not raw_tickers:
        try:
            raw_tickers = await manager.rest.get_all_tickers()
            _chain_cache["raw_tickers"] = raw_tickers
            _chain_cache["ts"] = now
        except Exception:
            raw_tickers = _chain_cache.get("raw_tickers", [])

    btc_opts = [
        t
        for t in raw_tickers
        if t.get("contract_type") in ("call_options", "put_options") and "BTC" in t.get("symbol", "")
    ]

    by_expiry: dict[str, list[dict[str, Any]]] = {}
    detected_spot = None

    for t in btc_opts:
        sym = t.get("symbol", "")
        parts = sym.split("-")
        if len(parts) >= 4:
            exp_code = parts[3]
            by_expiry.setdefault(exp_code, []).append(t)
        if not detected_spot and t.get("spot_price"):
            with contextlib.suppress(ValueError, TypeError):
                detected_spot = float(t.get("spot_price"))

    spot_price = manager.state.spot_index_price or detected_spot or 0.0

    sorted_exp_codes = sorted(by_expiry.keys(), key=_parse_expiry_key)
    expiries_list = [
        {
            "code": exp,
            "display": _format_expiry_display(exp),
            "contracts_count": len(by_expiry[exp]),
        }
        for exp in sorted_exp_codes
    ]

    selected_exp = expiry if (expiry and expiry in by_expiry) else (sorted_exp_codes[0] if sorted_exp_codes else None)

    strikes_list: list[dict[str, Any]] = []
    if selected_exp and selected_exp in by_expiry:
        strike_map: dict[float, dict[str, Any]] = {}
        for t in by_expiry[selected_exp]:
            try:
                strike_val = float(t.get("strike_price") or 0.0)
            except (ValueError, TypeError):
                continue

            if strike_val not in strike_map:
                strike_map[strike_val] = {
                    "strike": strike_val,
                    "call": None,
                    "put": None,
                }

            quotes = t.get("quotes") or {}
            greeks = t.get("greeks") or {}
            opt_data = {
                "symbol": t.get("symbol"),
                "bid": float(quotes.get("best_bid") or 0.0),
                "ask": float(quotes.get("best_ask") or 0.0),
                "mark": float(t.get("mark_price") or 0.0),
                "bid_size": float(quotes.get("bid_size") or 0.0),
                "ask_size": float(quotes.get("ask_size") or 0.0),
                "delta": round(float(greeks.get("delta") or 0.0), 3) if greeks and greeks.get("delta") else None,
                "iv": round(float(quotes.get("mark_iv") or 0.0) * 100.0, 1) if quotes.get("mark_iv") else None,
                "oi": float(t.get("oi") or 0.0),
            }

            if t.get("contract_type") == "call_options":
                strike_map[strike_val]["call"] = opt_data
            else:
                strike_map[strike_val]["put"] = opt_data

        strikes_list = sorted(strike_map.values(), key=lambda s: s["strike"])

    return {
        "spot_price": round(spot_price, 2),
        "index_symbol": manager.index_symbol if hasattr(manager, "index_symbol") else ".DEXBTUSD",
        "selected_expiry": selected_exp,
        "expiries": expiries_list,
        "strikes": strikes_list,
    }


@router.get("/api/ticker")
async def single_ticker_endpoint(symbol: str, request: Request) -> dict[str, Any]:
    """Fetch live 0-latency snapshot ticker for a specific option contract."""
    manager = getattr(request.app.state, "feed_manager", None)
    if not manager or not hasattr(manager, "rest"):
        return {"error": "Feed manager not available"}
    try:
        tk = await manager.rest.get_ticker(symbol)
        manager.state.update_option_ticker(tk)
        return {
            "symbol": symbol,
            "bid": tk.best_bid,
            "ask": tk.best_ask,
            "mark": tk.mark_price,
            "delta": tk.delta,
            "iv": tk.mark_iv,
        }
    except Exception as e:
        return {"error": str(e)}


@router.get("/api/options")
async def list_options_endpoint(request: Request) -> list[dict[str, Any]]:
    """List available active BTC options for HUD dropdown / autocomplete."""
    manager = getattr(request.app.state, "feed_manager", None)
    if not manager or not hasattr(manager, "rest"):
        return []
    try:
        prods = await manager.rest.list_products(underlying="BTC")
        res = []
        for p in prods:
            res.append(
                {
                    "symbol": p.get("symbol"),
                    "contract_type": p.get("contract_type"),
                    "strike_price": float(p.get("strike_price") or 0),
                    "settlement_time": p.get("settlement_time"),
                }
            )
        return res
    except Exception:
        return []


@router.post("/api/journal/paper-trade")
async def record_paper_trade_endpoint(data: dict[str, Any], request: Request) -> dict[str, Any]:
    """Log a paper trade from the HUD for forward testing (docs/05)."""
    engine = request.app.state.engine
    journal = getattr(engine, "journal", None)
    if not journal:
        return {"success": False, "error": "Journal is disabled"}

    eval_id = data.get("evaluation_id")
    if not eval_id:
        return {"success": False, "error": "evaluation_id is required"}

    rec = journal.get_evaluation(int(eval_id))
    if not rec:
        return {"success": False, "error": f"Evaluation #{eval_id} not found"}

    setup = rec.get("response", {}).get("setup")
    if not setup:
        return {"success": False, "error": "Evaluation has no setup details to trade"}

    trade_id = journal.record_paper_trade(
        symbol=rec["symbol"],
        direction=setup.get("direction", "call"),
        lots=float(setup.get("money", {}).get("lots", 1.0)),
        entry_price=float(setup.get("option", {}).get("entry_limit", 0.0)),
        sl_price=float(setup.get("option", {}).get("sl_exit_net", 0.0)),
        tp_price=float(setup.get("option", {}).get("tp_exit_net", 0.0)),
        entered_at=datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        evaluation_id=int(eval_id),
        actual_fill_price=data.get("actual_fill_price"),
    )
    return {"success": True, "trade_id": trade_id, "message": f"Paper trade #{trade_id} logged"}


@router.get("/api/journal/stats")
async def journal_stats_endpoint(request: Request) -> dict[str, Any]:
    """Return journal evaluation and paper-trade statistics for HUD/go-live."""
    engine = request.app.state.engine
    journal = getattr(engine, "journal", None)
    if not journal:
        return {"evaluations_count": 0, "paper_trades_count": 0}
    return {
        "evaluations_count": journal.count_evaluations(),
        "paper_trades_count": journal.count_paper_trades(),
    }


@router.websocket("/ws/hud")
async def hud_websocket_endpoint(websocket: WebSocket) -> None:
    """Live WebSocket feed for the HUD (refreshes card every second)."""
    await websocket.accept()
    engine = websocket.app.state.engine

    # Default request params: lots=10.0, 30 min hold
    current_req = EvaluateRequest(lots=10.0, hold_minutes=30)

    try:
        while True:
            # Check for client messages (e.g. symbol / lots change or evaluate trigger)
            try:
                msg = await asyncio.wait_for(websocket.receive_json(), timeout=1.0)
                if isinstance(msg, dict):
                    current_req = EvaluateRequest(**msg)
            except TimeoutError:
                pass

            # Evaluate setup and push update to HUD
            try:
                resp = await engine.evaluate(current_req)
                await websocket.send_json(resp.model_dump())
            except Exception as e:
                await websocket.send_json({"error": str(e), "verdict": "NO_TRADE"})

    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
