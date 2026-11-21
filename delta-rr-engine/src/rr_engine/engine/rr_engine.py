"""Risk-Reward Engine orchestrator.

Coordinates market state, chart structure, Black-Scholes pricing,
fee computation, gate checks, and verdict generation.
"""

from __future__ import annotations

import datetime
import logging
import math
import time

from rr_engine import __version__
from rr_engine.config import Settings, get_config_hash, get_settings
from rr_engine.costs.spread import calculate_spread
from rr_engine.engine import gates
from rr_engine.engine.models import EvaluateRequest, Provenance, ReasonItem, RREvaluateResponse
from rr_engine.engine.rr_core import RRParams, compute_rr, scenario_ladder
from rr_engine.engine.verdict import aggregate_verdict
from rr_engine.feed.manager import FeedManager
from rr_engine.indicators import (
    adx_wilder,
    atr_wilder,
    bollinger_bands,
    check_vol_regime_extreme,
    ema,
    ewma_realized_vol,
    is_bandwidth_in_lowest_quartile,
    session_vwap,
)
from rr_engine.journal.db import JournalDB
from rr_engine.montecarlo.simulate import extract_standardized_residuals, run_monte_carlo_async
from rr_engine.pricing.black_scholes import years_from_seconds
from rr_engine.pricing.greeks import bs_greeks
from rr_engine.pricing.implied_vol import ImpliedVolError, implied_vol
from rr_engine.state.market_state import MarketState
from rr_engine.structure import build_extra_levels, check_target_reachability, select_stop, select_target

logger = logging.getLogger("delta_engine")


class RREngine:
    """Orchestrates option risk-reward evaluation with fail-closed safety."""

    def __init__(
        self,
        feed_manager: FeedManager | None = None,
        settings: Settings | None = None,
        state: MarketState | None = None,
        journal: JournalDB | None = None,
        enable_journal: bool = True,
    ) -> None:
        self.settings = settings or get_settings()
        self.feed = feed_manager
        self.state = state or (self.feed.state if self.feed else MarketState())
        self.enable_journal = enable_journal
        self.journal = journal if journal is not None else (JournalDB() if enable_journal else None)

    async def evaluate(self, req: EvaluateRequest) -> RREvaluateResponse:
        """Evaluate an option setup for the retail trader."""
        now_dt = datetime.datetime.now(datetime.UTC)
        now_iso = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        cfg_hash = get_config_hash()

        # 1. Determine active symbol
        symbol = req.symbol or (self.feed.active_symbol if self.feed else None) or self.state.active_symbol
        if not symbol:
            if self.feed:
                symbol = await self.feed.discover_and_set_symbol()
            if not symbol:
                symbol = "UNKNOWN"

        region = self.settings.region  # 'india' or 'global'
        index_sym = self.settings.underlying if not self.feed else self.feed.index_symbol
        spot_price = self.state.spot_index_price or 0.0
        data_age = self.state.clock.max_data_age(("index", "option_ticker"))

        prov = Provenance(
            symbol=symbol,
            region=region,  # type: ignore[arg-type]
            index_symbol=index_sym,
            index_price=spot_price,
            computed_at_utc=now_iso,
            data_age_seconds=round(data_age if data_age != float("inf") else 999.0, 2),
            seconds_to_expiry=None,
            config_hash=cfg_hash,
            engine_version=__version__,
        )

        try:
            resp = await self._evaluate_internal(req, symbol, prov)
        except Exception as e:
            logger.exception("Engine failure during evaluation: %s", e)
            # Fail closed to NO_TRADE with ENGINE_ERROR
            resp = RREvaluateResponse(
                verdict="NO_TRADE",
                validation_status="UNVALIDATED",
                provenance=prov,
                reasons=[
                    ReasonItem(
                        code="ENGINE_ERROR",
                        severity="hard",
                        message=f"Internal engine error during calculation: {e}. Fail closed.",
                        value=str(e),
                        threshold=None,
                    )
                ],
                setup=None,
            )

        if self.enable_journal and self.journal:
            try:
                self._record_to_journal(req, symbol, prov, resp, now_iso, cfg_hash)
            except Exception as j_err:
                logger.warning("Could not record evaluation to journal: %s", j_err)

        return resp

    async def _evaluate_internal(self, req: EvaluateRequest, symbol: str, prov: Provenance) -> RREvaluateResponse:
        state = self.state
        clock = state.clock
        g_cfg = self.settings.gates
        p_cfg = self.settings.pricing
        s_cfg = self.settings.structure

        # ====================================================================
        # Section A: Data Integrity Gates (FAIL CLOSED IMMEDIATELY IF TRIGGERED)
        # ====================================================================
        gate_results: list[gates.GateResult] = []

        # A1: Feed connected
        gate_results.append(gates.gate_feed_disconnected(state.is_ws_connected))

        # A2: Data stale
        age = clock.max_data_age(("index", "option_ticker"))
        gate_results.append(gates.gate_data_stale(age, limit_seconds=g_cfg.data_stale_seconds))

        # A3: Candle gap in 5m closed candles
        has_gap, gap_msg = state.candles_5m.has_gap(lookback_candles=60)
        gate_results.append(gates.gate_candle_gap(has_gap, gap_msg))

        # A4 & A5: Product tradability and symbol check
        spec = state.products.get(symbol)
        if spec is None and self.feed:
            try:
                spec = await self.feed.set_active_symbol(symbol)
            except Exception:
                spec = None

        is_btc = ("BTC" in symbol) and (symbol.startswith(("C-BTC-", "P-BTC-")))
        gate_results.append(gates.gate_symbol_invalid(symbol, is_btc))

        if spec:
            gate_results.append(gates.gate_product_not_tradable(spec.state, spec.trading_status))
            prov.seconds_to_expiry = spec.seconds_to_expiry()
        else:
            gate_results.append(
                gates.GateResult(
                    code="PRODUCT_NOT_TRADABLE",
                    severity="hard",
                    fired=True,
                    message=f"Product specifications for '{symbol}' could not be retrieved.",
                )
            )

        # A6: Quotes valid - refresh if missing or stale
        tk = state.get_ticker(symbol)
        if (
            (tk is None or tk.best_bid <= 0 or age > g_cfg.data_stale_seconds)
            and self.feed
            and hasattr(self.feed, "rest")
        ):
            try:
                tk = await self.feed.rest.get_ticker(symbol)
                state.update_option_ticker(tk)
                now_epoch = time.time()
                clock.record("option_ticker", int(now_epoch * 1_000_000), now_epoch)
            except Exception:
                pass
        bid = tk.best_bid if tk else 0.0
        ask = tk.best_ask if tk else 0.0
        gate_results.append(gates.gate_quote_invalid(bid, ask))

        # A7: Index vs perp divergence
        is_div, div_pct = state.check_index_perp_divergence(limit_pct=g_cfg.index_perp_divergence_pct)
        gate_results.append(gates.gate_index_perp_divergence(div_pct, limit_pct=g_cfg.index_perp_divergence_pct))

        # A8: Clock skew
        is_skewed, skew_sec = clock.check_clock_skew("index", limit_seconds=g_cfg.clock_skew_seconds)
        gate_results.append(gates.gate_clock_skew(skew_sec, limit_seconds=g_cfg.clock_skew_seconds))

        # If any Section A gate fired, stop immediately and return NO_TRADE without computing numbers
        if any(g.fired and g.severity == "hard" for g in gate_results):
            return aggregate_verdict(
                provenance=prov,
                gate_results=gate_results,
                raw_setup=None,
                validation_status="UNVALIDATED",
            )

        assert spec is not None
        assert tk is not None

        # ====================================================================
        # Indicators & Structure Computation
        # ====================================================================
        closed_dicts = state.candles_5m.closed_dicts
        if len(closed_dicts) < 60:
            # Not enough historical candles
            gate_results.append(
                gates.GateResult(
                    code="CANDLE_GAP",
                    severity="hard",
                    fired=True,
                    message=f"Only {len(closed_dicts)} closed candles available (need at least 60 to compute indicators).",
                )
            )
            return aggregate_verdict(provenance=prov, gate_results=gate_results, raw_setup=None)

        # F5: Zero-close check on historical candles
        has_zero_close = any(c.get("c", 0) <= 0 for c in closed_dicts)
        if has_zero_close:
            gate_results.append(gates.gate_data_sanity_failed(None, None, state.spot_index_price, has_zero_close=True))
            return aggregate_verdict(provenance=prov, gate_results=gate_results, raw_setup=None)

        # 1. Indicators (F5: fail closed with INDICATOR_UNAVAILABLE instead of silent defaults)
        atrs = atr_wilder(closed_dicts, s_cfg.atr_period)
        atr = atrs[-1] if atrs and atrs[-1] is not None else None
        if atr is None or math.isnan(atr):
            gate_results.append(gates.gate_indicator_unavailable("ATR"))
            return aggregate_verdict(provenance=prov, gate_results=gate_results, raw_setup=None)

        closes = [c["c"] for c in closed_dicts]
        ema20_series = ema(closes, 20)
        ema50_series = ema(closes, 50)
        ema20 = ema20_series[-1] if ema20_series and ema20_series[-1] is not None else None
        ema50 = ema50_series[-1] if ema50_series and ema50_series[-1] is not None else None
        if ema20 is None or ema50 is None or math.isnan(ema20) or math.isnan(ema50):
            gate_results.append(gates.gate_indicator_unavailable("EMA"))
            return aggregate_verdict(provenance=prov, gate_results=gate_results, raw_setup=None)

        adx_dict = adx_wilder(closed_dicts, g_cfg.adx_period)
        adx = adx_dict["adx"][-1] if adx_dict and adx_dict.get("adx") and adx_dict["adx"][-1] is not None else None
        if adx is None or math.isnan(adx):
            gate_results.append(gates.gate_indicator_unavailable("ADX"))
            return aggregate_verdict(provenance=prov, gate_results=gate_results, raw_setup=None)

        vol_series = ewma_realized_vol(closed_dicts, decay=0.94, min_periods=30)
        sigma_5m = vol_series[-1] if vol_series and vol_series[-1] is not None else None
        if sigma_5m is None or math.isnan(sigma_5m):
            gate_results.append(gates.gate_indicator_unavailable("sigma_5m"))
            return aggregate_verdict(provenance=prov, gate_results=gate_results, raw_setup=None)

        bb = bollinger_bands(closes, period=20, num_std=2.0)
        is_low_bw, cur_bw, _ = is_bandwidth_in_lowest_quartile(
            bb["bandwidth"],
            lookback=g_cfg.bb_quartile_lookback,
            percentile=g_cfg.bb_quartile_threshold,
        )

        # Session VWAP from perp candles
        perp_dicts = state.perp_candles_5m.closed_dicts
        vwap_series = session_vwap(perp_dicts) if perp_dicts else []
        current_vwap = vwap_series[-1] if vwap_series else None

        # Direction (+1 for call, -1 for put)
        direction = 1 if spec.is_call else -1
        spot = state.spot_index_price or closes[-1]
        prov.index_symbol = getattr(spec, "spot_index_symbol", None) or ".DEXBTUSD"
        prov.index_price = spot

        # F5 Data-sanity gate check: fail closed without raw_setup if volatility or ATR is insane
        data_sanity_gate = gates.gate_data_sanity_failed(sigma_5m, atr, spot, has_zero_close=False)
        gate_results.append(data_sanity_gate)
        if data_sanity_gate.fired:
            return aggregate_verdict(provenance=prov, gate_results=gate_results, raw_setup=None)

        # F7: Validate manual entry override if present (must lie within [bid - 2 ticks, ask + 2 ticks])
        if req.entry_override_option is not None:
            override_gate = gates.gate_entry_override_invalid(req.entry_override_option, bid, ask, spec.tick_size)
            gate_results.append(override_gate)
            if override_gate.fired:
                return aggregate_verdict(provenance=prov, gate_results=gate_results, raw_setup=None)

        # Pre-compute Black-Scholes calibration & Greeks for tactical stop/target bounds
        seconds_left = spec.seconds_to_expiry()
        try:
            T0 = years_from_seconds(seconds_left)
            sigma_anchor = implied_vol(tk.mark_price, spot, spec.strike_price, T0, spec.is_call)
            gate_results.append(gates.gate_model_calibration_failed(None))
        except ImpliedVolError as e:
            gate_results.append(gates.gate_model_calibration_failed(str(e)))
            return aggregate_verdict(provenance=prov, gate_results=gate_results, raw_setup=None)

        # Greeks consistency check (F5: missing delta fails closed with GREEKS_UNAVAILABLE)
        model_greeks = bs_greeks(spot, spec.strike_price, T0, sigma_anchor, spec.is_call)
        if tk.delta is None or math.isnan(tk.delta):
            gate_results.append(gates.gate_greeks_unavailable())
        else:
            delta_diff = abs(model_greeks["delta"] - tk.delta)
            gate_results.append(gates.gate_greeks_mismatch_severe(delta_diff, g_cfg.greeks_mismatch_hard))
            gate_results.append(gates.gate_greeks_mismatch_warn(delta_diff, g_cfg.greeks_mismatch_warn))

        # 2. Stop selection with tactical option premium drawdown bounding
        entry_cand = req.entry_override_option if req.entry_override_option is not None else ask
        if req.sl_override_spot is not None:
            # Validate user override
            sl_spot = req.sl_override_spot
            if (direction > 0 and sl_spot >= spot) or (direction < 0 and sl_spot <= spot):
                gate_results.append(
                    gates.GateResult(
                        code="STOP_TOO_WIDE",
                        severity="hard",
                        fired=True,
                        message=f"Manual stop override ({sl_spot}) is on the wrong side of spot ({spot}).",
                    )
                )
            sl_dict = {
                "sl_spot": sl_spot,
                "dist": abs(spot - sl_spot),
                "dist_atr": abs(spot - sl_spot) / atr,
                "source": "user_override",
                "widened_to_noise_floor": False,
                "gate": None,
            }
        else:
            sl_dict = select_stop(
                direction=direction,
                spot=spot,
                candles=closed_dicts,
                atr=atr,
                k=s_cfg.pivot_k,
                lookback=s_cfg.stop_lookback_candles,
                buffer_atr=s_cfg.stop_buffer_atr,
                min_atr=s_cfg.stop_min_atr,
                max_atr=s_cfg.stop_max_atr,
                fallback_atr=s_cfg.stop_fallback_atr,
                entry_premium=entry_cand,
                delta=model_greeks.get("delta"),
                max_premium_risk_pct=getattr(s_cfg, "max_premium_risk_pct", 0.20),
            )

        # 3. Target selection with minimum RRR filtering
        extra_lvls = build_extra_levels(closed_dicts, vwap=current_vwap, spot=spot, steps=s_cfg.round_number_steps)
        if req.tp_override_spot is not None:
            tp_spot = req.tp_override_spot
            if (direction > 0 and tp_spot <= spot) or (direction < 0 and tp_spot >= spot):
                gate_results.append(
                    gates.GateResult(
                        code="TARGET_BLOCKED",
                        severity="hard",
                        fired=True,
                        message=f"Manual target override ({tp_spot}) is on the wrong side of spot ({spot}).",
                    )
                )
            tp_dict = {
                "tp_spot": tp_spot,
                "dist": abs(tp_spot - spot),
                "dist_atr": abs(tp_spot - spot) / atr,
                "source": "user_override",
                "label": "user_override",
                "gate": None,
            }
        else:
            tp_dict = select_target(
                direction=direction,
                spot=spot,
                candles=closed_dicts,
                atr=atr,
                k=s_cfg.pivot_k,
                lookback=s_cfg.target_lookback_candles,
                min_tp_atr=s_cfg.target_min_atr,
                extra_levels=extra_lvls,
                fallback_atr=s_cfg.target_fallback_atr,
                stop_dist=sl_dict["dist"],
                min_rrr=getattr(s_cfg, "min_structural_rrr", 1.50),
            )

        # Check geometry gates from structure
        if sl_dict.get("gate") == "STOP_TOO_WIDE":
            gate_results.append(gates.gate_stop_too_wide(sl_dict["dist_atr"], s_cfg.stop_max_atr))
        else:
            gate_results.append(gates.gate_stop_too_wide(sl_dict["dist_atr"], s_cfg.stop_max_atr))

        if tp_dict.get("gate") == "TARGET_BLOCKED":
            gate_results.append(
                gates.gate_target_blocked(tp_dict["dist_atr"], s_cfg.target_min_atr, tp_dict.get("label", ""))
            )
        else:
            gate_results.append(gates.gate_target_blocked(tp_dict["dist_atr"], s_cfg.target_min_atr))

        # Check target reachability
        holding_candles = max(1, req.hold_minutes // 5)
        is_reach, t_dist, max_r = check_target_reachability(
            target_dist=tp_dict["dist"],
            spot=spot,
            sigma_5m=sigma_5m,
            holding_candles=holding_candles,
            reach_factor=s_cfg.target_reach_factor,
        )
        gate_results.append(gates.gate_target_unreachable_in_window(is_reach, t_dist, max_r))

        # ====================================================================
        # Black-Scholes Pricing & Risk-Reward Computation
        # ====================================================================
        # First-Passage Expected Resolution Time
        sigma_pts = (sigma_5m * spot) if spot > 0 else 1.0
        expected_candles = (sl_dict["dist"] * tp_dict["dist"]) / (sigma_pts**2) if sigma_pts > 0 else 0.0
        expected_seconds = max(600.0, min(expected_candles * 300.0, 1800.0))
        hold_seconds = expected_seconds if req.hold_minutes == 30 else float(req.hold_minutes * 60)

        # RR computation
        region = self.settings.region
        gst_rate = self.settings.fees.gst if region == "india" else 0.0
        iv_tp_adj = getattr(p_cfg, "iv_shock_tp_put", 0.008) if (not spec.is_call and p_cfg.iv_shock_tp == 0.0) else p_cfg.iv_shock_tp

        rr_params = RRParams(
            spot=spot,
            strike=spec.strike_price,
            is_call=spec.is_call,
            seconds_to_expiry=seconds_left,
            mark=tk.mark_price,
            bid=bid,
            ask=ask,
            sl_spot=sl_dict["sl_spot"],
            tp_spot=tp_dict["tp_spot"],
            lots=req.lots,
            contract_value=spec.contract_value,
            hold_seconds=hold_seconds,
            r=p_cfg.risk_free_rate,
            taker_commission_rate=spec.taker_commission_rate,
            premium_commission_rate=spec.premium_commission_rate,
            gst=gst_rate,
            sl_spread_stress=p_cfg.sl_spread_stress,
            iv_shock_sl=p_cfg.iv_shock_sl,
            iv_shock_tp=iv_tp_adj,
            extra={"entry_override": req.entry_override_option},
        )

        rr_result = compute_rr(rr_params)
        ladder = scenario_ladder(rr_params, [-300.0, -150.0, 0.0, 150.0, 300.0])

        # Compute max lots for budget (F8: floor(budget / risk_per_lot), which may be 0)
        max_lots_calc = None
        risk_per_lot = 0.0
        if req.max_risk_usd and req.max_risk_usd > 0:
            rr_1lot_params = RRParams(
                spot=spot,
                strike=spec.strike_price,
                is_call=spec.is_call,
                seconds_to_expiry=seconds_left,
                mark=tk.mark_price,
                bid=bid,
                ask=ask,
                sl_spot=sl_dict["sl_spot"],
                tp_spot=tp_dict["tp_spot"],
                lots=1.0,
                contract_value=spec.contract_value,
                hold_seconds=hold_seconds,
                r=p_cfg.risk_free_rate,
                taker_commission_rate=spec.taker_commission_rate,
                premium_commission_rate=spec.premium_commission_rate,
                gst=gst_rate,
                sl_spread_stress=p_cfg.sl_spread_stress,
                iv_shock_sl=p_cfg.iv_shock_sl,
                iv_shock_tp=p_cfg.iv_shock_tp,
                extra={"entry_override": req.entry_override_option},
            )
            rr_1lot = compute_rr(rr_1lot_params)
            risk_per_lot = rr_1lot["risk_usd"]
            max_lots_calc = math.floor(req.max_risk_usd / risk_per_lot) if risk_per_lot > 0 else 0

        # ====================================================================
        # Section B & C & D Remaining Gates
        # ====================================================================
        # B: Option Quality
        gate_results.append(
            gates.gate_expiry_too_close(
                seconds_left,
                min_minutes=g_cfg.expiry_min_minutes,
                hold_minutes=req.hold_minutes,
                buffer_minutes=g_cfg.window_end_before_expiry_minutes,
            )
        )
        _, _, spread_pct = calculate_spread(bid, ask)
        gate_results.append(gates.gate_spread_too_wide(spread_pct, g_cfg.spread_max_pct))
        gate_results.append(
            gates.gate_spread_eats_reward(
                rr_result["spread_round_trip_usd"],
                rr_result["reward_usd"],
                g_cfg.spread_share_of_reward_max,
            )
        )
        gate_results.append(
            gates.gate_depth_too_thin(
                tk.ask_size,
                tk.bid_size,
                req.lots,
                g_cfg.depth_entry_multiple,
                g_cfg.depth_exit_multiple,
            )
        )
        gate_results.append(gates.gate_premium_too_low(ask, g_cfg.premium_min))

        tot_fees = sum(rr_result["fees_usd"].values())
        gate_results.append(
            gates.gate_fees_eat_reward(tot_fees, rr_result["reward_usd"], g_cfg.fees_share_of_reward_max)
        )

        # C: Market Context
        gate_results.append(gates.gate_choppy_market(adx, is_low_bw, g_cfg.adx_chop))
        gate_results.append(gates.gate_against_trend(direction, ema20, ema50, adx, g_cfg.adx_trend))

        last_c = closed_dicts[-1]
        last_range = last_c["h"] - last_c["l"]
        gate_results.append(
            gates.gate_spike_just_happened(
                candles_since_spike=99,  # checked via last_range
                wait_candles=g_cfg.spike_wait_candles,
                last_candle_range=last_range,
                atr=atr,
                spike_mult=g_cfg.spike_atr_multiple,
            )
        )

        is_ext_vol, cur_vol, thresh_vol = check_vol_regime_extreme(vol_series, g_cfg.vol_regime_multiple)
        gate_results.append(gates.gate_vol_regime_extreme(is_ext_vol, cur_vol, thresh_vol))
        gate_results.append(gates.gate_event_window(time.time(), g_cfg.event_windows))

        # D: Trade Geometry
        gate_results.append(
            gates.gate_net_rr_too_low(
                rr_result["net_rr"],
                g_cfg.net_rr_min,
                rr_result["risk_usd"],
                rr_result["reward_usd"],
            )
        )
        gate_results.append(gates.gate_breakeven_too_high(rr_result["breakeven_win_probability"], g_cfg.breakeven_max))
        gate_results.append(
            gates.gate_risk_over_budget(
                rr_result["risk_usd"],
                req.max_risk_usd,
                max_lots=max_lots_calc if max_lots_calc is not None else 1,
                risk_per_lot=risk_per_lot,
            )
        )
        gate_results.append(gates.gate_strike_not_atm(model_greeks["delta"], tuple(g_cfg.atm_delta_range)))

        # Spot RR
        spot_risk = abs(spot - sl_dict["sl_spot"])
        spot_reward = abs(tp_dict["tp_spot"] - spot)
        spot_rr = (spot_reward / spot_risk) if spot_risk > 0 else float("inf")

        # Estimated holding duration (F11: (stop_dist * target_dist) / (sigma_pts^2) candles)
        stop_dist = sl_dict["dist"]
        target_dist = tp_dict["dist"]
        sigma_pts = (sigma_5m * spot) if spot > 0 else 1.0
        expected_candles = (stop_dist * target_dist) / (sigma_pts**2) if sigma_pts > 0 else 0.0
        expected_minutes = expected_candles * 5.0
        holding_str = f"~{expected_minutes:.0f} mins ({expected_candles:.1f} candles)"
        if expected_minutes > req.hold_minutes:
            gate_results.append(
                gates.GateResult(
                    code="HOLD_TIME_EXCEEDS_WINDOW",
                    severity="warning",
                    fired=True,
                    message=f"Expected resolution time (~{expected_minutes:.0f}m) exceeds planned hold window ({req.hold_minutes}m).",
                    value=round(expected_minutes, 1),
                    threshold=req.hold_minutes,
                )
            )

        from rr_engine.structure.levels import standard_pivot_points

        std_pivots = standard_pivot_points(closed_dicts)
        pivot_label = tp_dict.get("label") or "Chart Resistance"
        if "pivot_standard" in str(pivot_label):
            p_clean = str(pivot_label).replace("pivot_standard_", "Pivot ")
            pivot_ref_str = f"Target based on {p_clean} (${tp_dict['tp_spot']:,.1f})"
        else:
            closest_p = None
            closest_diff = float("inf")
            for p_name, p_val in std_pivots.items():
                diff = abs(p_val - tp_dict["tp_spot"])
                if diff < closest_diff:
                    closest_diff = diff
                    closest_p = (p_name, p_val)
            if closest_p and closest_diff < 400:
                pivot_ref_str = f"Target: {pivot_label} (${tp_dict['tp_spot']:,.1f}) [Confluence with {closest_p[0]} ${closest_p[1]:,.1f}]"
            else:
                pivot_ref_str = f"Target based on {pivot_label} (${tp_dict['tp_spot']:,.1f})"

        raw_setup = {
            "direction": direction,
            "spot": {
                "price": spot,
                "sl": sl_dict["sl_spot"],
                "tp": tp_dict["tp_spot"],
                "sl_source": sl_dict["source"],
                "tp_source": tp_dict["source"],
                "atr": round(atr, 2),
                "spot_rr": round(spot_rr, 2),
            },
            "option": {
                "entry_price": rr_result["entry_price"],
                "sl_option_model": rr_result["sl_option_model"],
                "tp_option_model": rr_result["tp_option_model"],
                "sl_exit_net": rr_result["sl_exit_net_price"],
                "tp_exit_net": rr_result["tp_exit_net_price"],
                "sigma_anchor": sigma_anchor,
                "bid": bid,
                "ask": ask,
                "mark": tk.mark_price,
                "spread_pct": spread_pct,
                "clean_rr": rr_result.get("clean_rr"),
            },
            "money": {
                "lots": req.lots,
                "risk_usd": rr_result["risk_usd"],
                "reward_usd": rr_result["reward_usd"],
                "net_rr": rr_result["net_rr"],
                "clean_rr": rr_result.get("clean_rr"),
                "breakeven_win_probability": rr_result["breakeven_win_probability"],
                "worst_case_loss_usd": rr_result["worst_case_loss_usd"],
                "fees_usd_round_trip": tot_fees,
                "max_lots_for_risk_budget": max_lots_calc,
            },
            "ladder": ladder,
            "probability": None,  # Computed async in Phase 7
            "estimated_holding_time": holding_str,
            "pivot_reference": pivot_ref_str,
            "pivots": std_pivots,
        }

        # Section F: Session discipline (uses journal)
        if self.journal:
            now_dt = datetime.datetime.now(datetime.UTC)
            today_prefix = now_dt.strftime("%Y-%m-%d")
            daily_loss = self.journal.get_daily_loss(today_prefix)
            gate_results.append(gates.gate_daily_loss_limit(daily_loss, self.settings.session.daily_loss_limit_usd))

            cons_losses, last_loss_time = self.journal.get_consecutive_losses()
            last_loss_sec = None
            if last_loss_time:
                try:
                    last_dt = datetime.datetime.fromisoformat(last_loss_time.replace("Z", "+00:00"))
                    last_loss_sec = max(0.0, (now_dt - last_dt).total_seconds())
                except Exception:
                    pass
            cooldown_sec = self.settings.session.cooldown_minutes * 60.0
            gate_results.append(
                gates.gate_cooldown(
                    consecutive_losses=cons_losses,
                    last_loss_seconds_ago=last_loss_sec,
                    max_losses=self.settings.session.cooldown_after_losses,
                    cooldown_seconds=cooldown_sec,
                )
            )

        # Validate edge mode: UNVALIDATED banner until Phase 9
        val_status = "VALIDATED" if self.settings.edge.mode == "empirical" else "UNVALIDATED"

        resp = aggregate_verdict(
            provenance=prov,
            gate_results=gate_results,
            raw_setup=raw_setup,
            validation_status=val_status,
            option_tick_size=spec.tick_size,
            index_tick_size=0.5,
            strict_mode=req.strict_mode,
        )

        # Run Monte Carlo probability simulation asynchronously if requested (never blocks primary verdict)
        if req.include_mc and resp.setup is not None and self.settings.montecarlo.enabled:
            try:
                residuals = extract_standardized_residuals(closed_dicts)
                annual_vol = sigma_5m * math.sqrt(105_120.0)
                mc_prob = await run_monte_carlo_async(
                    spot=resp.setup.spot.price,
                    sl_spot=resp.setup.spot.sl,
                    tp_spot=resp.setup.spot.tp,
                    direction=resp.setup.direction,
                    annual_vol=annual_vol,
                    hold_minutes=req.hold_minutes,
                    n_paths=self.settings.montecarlo.paths,
                    residuals=residuals,
                )
                resp.setup.probability = mc_prob
            except Exception as mc_err:
                logger.warning("Monte Carlo simulation failed: %s", mc_err)

        return resp

    def _record_to_journal(
        self,
        req: EvaluateRequest,
        symbol: str,
        prov: Provenance,
        resp: RREvaluateResponse,
        now_iso: str,
        cfg_hash: str,
    ) -> None:
        if not self.journal:
            return

        spec = self.state.products.get(symbol)
        tk = self.state.get_ticker(symbol)

        # F10: Store hash + last 100 candles instead of 2x2,000 candles
        import hashlib
        import json

        c100 = self.state.candles_5m.closed_dicts[-100:]
        p100 = self.state.perp_candles_5m.closed_dicts[-100:]
        c_hash = hashlib.sha256(json.dumps(c100).encode()).hexdigest()

        snapshot = {
            "spot_index_price": self.state.spot_index_price,
            "perp_mark_price": self.state.perp_mark_price,
            "candles_hash": c_hash,
            "candles_5m": c100,
            "perp_candles_5m": p100,
            "option_ticker": {
                "symbol": tk.symbol,
                "mark_price": tk.mark_price,
                "best_bid": tk.best_bid,
                "best_ask": tk.best_ask,
                "bid_size": tk.bid_size,
                "ask_size": tk.ask_size,
                "delta": tk.delta,
                "mark_iv": getattr(tk, "mark_iv", None),
                "timestamp": tk.timestamp,
                "spot_price_in_ticker": tk.spot_price_in_ticker,
            }
            if tk
            else None,
            "product_spec": {
                "id": spec.id,
                "symbol": spec.symbol,
                "contract_type": spec.contract_type,
                "strike_price": spec.strike_price,
                "contract_value": spec.contract_value,
                "tick_size": spec.tick_size,
                "settlement_time_iso": spec.settlement_time_iso,
                "settlement_time_epoch": spec.settlement_time_epoch,
                "state": spec.state,
                "trading_status": spec.trading_status,
                "taker_commission_rate": spec.taker_commission_rate,
                "maker_commission_rate": spec.maker_commission_rate,
                "premium_commission_rate": spec.premium_commission_rate,
                "spot_index_symbol": spec.spot_index_symbol,
                "underlying_asset_symbol": spec.underlying_asset_symbol,
                "fetched_at": spec.fetched_at,
            }
            if spec
            else None,
            "clock_records": {
                name: {
                    "last_exchange_time": s.last_exchange_time,
                    "last_local_receive_time": s.last_local_receive_time,
                }
                for name, s in self.state.clock.streams.items()
            },
            "eval_time_epoch": time.time(),
        }

        net_rr = resp.setup.money.net_rr if resp.setup else None
        be_win = resp.setup.money.breakeven_win_probability if resp.setup else None

        eval_id = self.journal.record_evaluation(
            timestamp_utc=now_iso,
            symbol=symbol,
            spot_index_price=prov.index_price,
            verdict=resp.verdict,
            validation_status=resp.validation_status,
            config_hash=cfg_hash,
            request_data=req.model_dump(),
            market_snapshot=snapshot,
            response_data=resp.model_dump(),
            net_rr=net_rr,
            breakeven_win_probability=be_win,
        )
        resp.provenance.evaluation_id = eval_id
