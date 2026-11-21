"""Verdict aggregation and conservative tick rounding.

Enforces:
  1. Verdict is NO_TRADE if ANY hard gate fires.
  2. If TRADE: reasons lists any warnings. If NO_TRADE: list ALL failing hard gates,
     ordered strictly by section A -> F, followed by warnings.
  3. Conservative rounding to tick size:
     - Entry limit: rounded UP to tick size (never round down in user's favor).
     - Target (TP premium): rounded DOWN to tick size.
     - Stop (SL premium): rounded UP to tick size.
     - Spot levels: rounded to index tick size.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from rr_engine.engine.gates import GateResult
from rr_engine.engine.models import (
    LadderItem,
    MoneyGeometry,
    OptionGeometry,
    Provenance,
    ReasonItem,
    RREvaluateResponse,
    SetupDetails,
    SpotGeometry,
)

SECTION_PRIORITY = {
    # Section A: Data integrity & sanity
    "FEED_DISCONNECTED": 1,
    "DATA_STALE": 2,
    "CANDLE_GAP": 3,
    "PRODUCT_NOT_TRADABLE": 4,
    "SYMBOL_INVALID": 5,
    "QUOTE_INVALID": 6,
    "INDEX_PERP_DIVERGENCE": 7,
    "DATA_SANITY_FAILED": 8,
    "INDICATOR_UNAVAILABLE": 9,
    "GREEKS_UNAVAILABLE": 10,
    "MODEL_CALIBRATION_FAILED": 11,
    "GREEKS_MISMATCH_SEVERE": 12,
    "CLOCK_SKEW": 13,
    "ENGINE_ERROR": 14,
    # Section B: Option quality
    "EXPIRY_TOO_CLOSE": 20,
    "SPREAD_TOO_WIDE": 21,
    "SPREAD_EATS_REWARD": 22,
    "DEPTH_TOO_THIN": 23,
    "PREMIUM_TOO_LOW": 24,
    "FEES_EAT_REWARD": 25,
    "ENTRY_OVERRIDE_INVALID": 26,
    # Section C: Market context
    "CHOPPY_MARKET": 30,
    "AGAINST_TREND": 31,
    "SPIKE_JUST_HAPPENED": 32,
    "VOL_REGIME_EXTREME": 33,
    "EVENT_WINDOW": 34,
    # Section D: Trade geometry
    "STOP_TOO_WIDE": 40,
    "TARGET_BLOCKED": 41,
    "TARGET_UNREACHABLE_IN_WINDOW": 42,
    "NET_RR_TOO_LOW": 43,
    "BREAKEVEN_TOO_HIGH": 44,
    "RISK_OVER_BUDGET": 45,
    # Warnings
    "STRIKE_NOT_ATM": 90,
    "GREEKS_MISMATCH": 91,
}


def round_tick(value: float, tick_size: float, mode: str = "nearest") -> float:
    """Conservative tick rounding:

    mode='up': round up (ceiling)
    mode='down': round down (floor)
    mode='nearest': standard round
    """
    if tick_size <= 0:
        return value
    if mode == "up":
        return math.ceil(value / tick_size - 1e-9) * tick_size
    if mode == "down":
        return math.floor(value / tick_size + 1e-9) * tick_size
    return round(value / tick_size) * tick_size


def aggregate_verdict(
    provenance: Provenance,
    gate_results: Sequence[GateResult],
    raw_setup: dict | None = None,
    validation_status: str = "UNVALIDATED",
    option_tick_size: float = 0.1,
    index_tick_size: float = 0.5,
    strict_mode: bool = True,
) -> RREvaluateResponse:
    """Evaluate all gates, order reasons A->F, round ticks conservatively, and build response."""
    fired_hard: list[GateResult] = []
    warnings: list[GateResult] = []

    for g in gate_results:
        if g.fired:
            if g.severity == "hard":
                fired_hard.append(g)
            else:
                warnings.append(g)

    # Sort hard gates by section priority A -> F
    fired_hard.sort(key=lambda g: SECTION_PRIORITY.get(g.code, 99))
    warnings.sort(key=lambda g: SECTION_PRIORITY.get(g.code, 99))

    if len(fired_hard) > 0:
        verdict_str = "NO_TRADE"
    elif len(warnings) > 0:
        verdict_str = "CAUTION"
    else:
        verdict_str = "TRADE"

    # Assemble reasons: all hard failures first, then warnings
    all_reasons = [
        ReasonItem(
            code=g.code,
            severity="hard" if g.severity == "hard" else "warning",
            message=g.message,
            value=g.value,
            threshold=g.threshold,
        )
        for g in (fired_hard + warnings)
    ]

    setup_details: SetupDetails | None = None

    if raw_setup is not None:
        direction = raw_setup["direction"]
        spot_dict = raw_setup["spot"]
        opt_dict = raw_setup["option"]
        money_dict = raw_setup["money"]

        # Conservative rounding
        rounded_entry = round_tick(opt_dict["entry_price"], option_tick_size, mode="up")
        rounded_sl_prem = round_tick(opt_dict["sl_option_model"], option_tick_size, mode="up")
        rounded_tp_prem = round_tick(opt_dict["tp_option_model"], option_tick_size, mode="down")

        rounded_sl_spot = round_tick(spot_dict["sl"], index_tick_size, mode="nearest")
        rounded_tp_spot = round_tick(spot_dict["tp"], index_tick_size, mode="nearest")

        ladder = [
            LadderItem(
                spot=round_tick(row["spot"], index_tick_size, mode="nearest"),
                option_model=round_tick(row["option_model"], option_tick_size, mode="nearest"),
            )
            for row in raw_setup.get("ladder", [])
        ]

        setup_details = SetupDetails(
            direction="call" if direction > 0 else "put",
            spot=SpotGeometry(
                price=round_tick(spot_dict["price"], index_tick_size),
                sl=rounded_sl_spot,
                tp=rounded_tp_spot,
                sl_source=spot_dict["sl_source"],
                tp_source=spot_dict["tp_source"],
                atr=spot_dict["atr"],
                spot_rr=spot_dict["spot_rr"],
            ),
            option=OptionGeometry(
                entry_limit=rounded_entry,
                sl_premium_model=rounded_sl_prem,
                tp_premium_model=rounded_tp_prem,
                sl_exit_net=round(opt_dict["sl_exit_net"], 2),
                tp_exit_net=round(opt_dict["tp_exit_net"], 2),
                sigma_anchor=round(opt_dict["sigma_anchor"], 4),
                bid=opt_dict["bid"],
                ask=opt_dict["ask"],
                mark=opt_dict["mark"],
                spread_pct=round(opt_dict["spread_pct"], 2),
                clean_rr=round(opt_dict["clean_rr"], 2)
                if opt_dict.get("clean_rr") is not None and not math.isinf(opt_dict["clean_rr"])
                else None,
            ),
            money=MoneyGeometry(
                lots=money_dict["lots"],
                risk_usd=round(money_dict["risk_usd"], 2),
                reward_usd=round(money_dict["reward_usd"], 2),
                net_rr=round(money_dict["net_rr"], 2) if not math.isinf(money_dict["net_rr"]) else 0.0,
                breakeven_win_probability=round(money_dict["breakeven_win_probability"], 3),
                worst_case_loss_usd=round(money_dict["worst_case_loss_usd"], 2),
                fees_usd_round_trip=round(money_dict["fees_usd_round_trip"], 2),
                max_lots_for_risk_budget=money_dict.get("max_lots_for_risk_budget"),
                clean_rr=round(money_dict["clean_rr"], 2)
                if money_dict.get("clean_rr") is not None and not math.isinf(money_dict["clean_rr"])
                else None,
            ),
            ladder=ladder,
            probability=raw_setup.get("probability"),
            estimated_holding_time=raw_setup.get("estimated_holding_time"),
            pivot_reference=raw_setup.get("pivot_reference"),
            pivots=raw_setup.get("pivots"),
        )

    return RREvaluateResponse(
        verdict=verdict_str,  # type: ignore[arg-type]
        validation_status=validation_status,  # type: ignore[arg-type]
        provenance=provenance,
        reasons=all_reasons,
        setup=setup_details,
    )
