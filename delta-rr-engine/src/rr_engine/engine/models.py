"""Pydantic v2 models for API requests, responses, and verdict serialization.

Fully conforms to schemas/rr_response.schema.json.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class EvaluateRequest(BaseModel):
    """User evaluation request."""

    symbol: str | None = Field(
        default=None, description="Option symbol (e.g. C-BTC-61400-211126). Auto-discovers if omitted."
    )
    lots: float = Field(default=1.0, ge=1.0, description="Number of lots (contracts) to trade.")
    max_risk_usd: float | None = Field(default=None, ge=0.0, description="Optional maximum loss budget in USD.")
    hold_minutes: int = Field(default=30, ge=5, le=240, description="Expected holding duration in minutes.")
    sl_override_spot: float | None = Field(default=None, description="Optional manual stop level on spot chart.")
    tp_override_spot: float | None = Field(default=None, description="Optional manual target level on spot chart.")
    include_mc: bool = Field(default=False, description="Whether to run Monte Carlo probability simulation.")
    strict_mode: bool = Field(default=True, description="Strict evaluation mode (debug only).")
    entry_override_option: float | None = Field(
        default=None, ge=0.0, description="Optional manual option entry price selected by trader."
    )


class ReasonItem(BaseModel):
    code: str
    severity: Literal["hard", "warning"]
    message: str
    value: float | str | None = None
    threshold: float | str | None = None


class Provenance(BaseModel):
    symbol: str
    region: Literal["india", "global"]
    index_symbol: str
    index_price: float
    computed_at_utc: str
    data_age_seconds: float
    seconds_to_expiry: float | None = None
    config_hash: str
    engine_version: str
    evaluation_id: int | None = None


class SpotGeometry(BaseModel):
    price: float
    sl: float
    tp: float
    sl_source: str
    tp_source: str
    atr: float
    spot_rr: float


class OptionGeometry(BaseModel):
    entry_limit: float
    sl_premium_model: float
    tp_premium_model: float
    sl_exit_net: float
    tp_exit_net: float
    sigma_anchor: float
    bid: float
    ask: float
    mark: float
    spread_pct: float
    clean_rr: float | None = None


class MoneyGeometry(BaseModel):
    lots: float
    risk_usd: float
    reward_usd: float
    net_rr: float
    breakeven_win_probability: float
    worst_case_loss_usd: float
    fees_usd_round_trip: float
    max_lots_for_risk_budget: float | None = None
    clean_rr: float | None = None


class LadderItem(BaseModel):
    spot: float
    option_model: float


class ProbabilityEstimate(BaseModel):
    p_tp: float
    p_sl: float
    p_timeout: float
    mc_std_error: float


class SetupDetails(BaseModel):
    direction: Literal["call", "put"]
    spot: SpotGeometry
    option: OptionGeometry
    money: MoneyGeometry
    ladder: list[LadderItem] = Field(default_factory=list)
    probability: ProbabilityEstimate | None = None
    estimated_holding_time: str | None = None
    pivot_reference: str | None = None
    pivots: dict[str, float] | None = None


class RREvaluateResponse(BaseModel):
    """Complete response returned by POST /api/evaluate and WebSocket /ws/hud."""

    verdict: Literal["TRADE", "NO_TRADE", "CAUTION"]
    validation_status: Literal["UNVALIDATED", "VALIDATED"] = "UNVALIDATED"
    provenance: Provenance
    reasons: list[ReasonItem] = Field(default_factory=list)
    setup: SetupDetails | None = None


class StatusResponse(BaseModel):
    """Feed and engine health status returned by GET /api/status."""

    status: Literal["healthy", "degraded", "disconnected"]
    ws_connected: bool
    region: str
    active_symbol: str | None
    spot_index_price: float | None
    data_age_seconds: float
    candles_count: int
    config_hash: str
    version: str
