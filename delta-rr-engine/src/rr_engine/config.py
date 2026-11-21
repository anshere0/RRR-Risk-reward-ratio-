"""Configuration loader with pydantic v2 validation and stable SHA-256 hash.

The config hash is stored with every evaluation in the journal so we can
reproduce results exactly and detect when settings changed.

Units are documented in comments — read config/settings.example.yaml for details.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator


class HostsConfig(BaseModel):
    rest: str
    ws: str


class ServerConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8000


class PricingConfig(BaseModel):
    risk_free_rate: float = 0.0
    seconds_per_year: int = 31_536_000  # 365 * 24 * 3600
    iv_shock_sl: float = -0.02  # vol-point shock at stop (decimal, e.g. -0.02 = -2 vol points)
    iv_shock_tp: float = 0.0  # vol-point credit at target (0 = conservative, no credit)
    iv_shock_tp_put: float = 0.008  # skew expansion at Put target
    sl_spread_stress: float = 1.5  # multiply half-spread by this at the stop (spreads widen in fast moves)
    hold_minutes_default: int = 30  # default holding window in minutes


class FeesConfig(BaseModel):
    gst: float = 0.18  # GST on fees for Delta India; 0.0 for global
    use_product_rates: bool = True  # read taker/premium commission rates from the product


class StructureConfig(BaseModel):
    atr_period: int = 14
    pivot_k: int = 2
    stop_lookback_candles: int = 36
    target_lookback_candles: int = 144
    stop_buffer_atr: float = 0.25  # ATR fraction added/subtracted as buffer beyond the swing pivot
    stop_min_atr: float = 0.5  # noise floor: stop must be at least this many ATRs away
    stop_max_atr: float = 2.0  # stop wider than this → STOP_TOO_WIDE
    stop_fallback_atr: float = 1.0  # ATR multiplier if no pivot found
    max_premium_risk_pct: float = 0.20  # Max 20% option premium drawdown threshold
    target_min_atr: float = 0.75  # opposing level closer than this → TARGET_BLOCKED
    min_structural_rrr: float = 1.50  # Target distance must be >= 1.5x stop distance
    target_fallback_atr: float = 1.5  # ATR multiplier for open-air target
    target_reach_factor: float = 1.5  # target must be reachable within this × sigma_5m × sqrt(N)
    round_number_steps: list[int] = Field(default_factory=lambda: [500, 1000])


class GatesConfig(BaseModel):
    data_stale_seconds: float = 3.0
    index_perp_divergence_pct: float = 0.25
    clock_skew_seconds: float = 2.0
    greeks_mismatch_warn: float = 0.07
    greeks_mismatch_hard: float = 0.15
    expiry_min_minutes: int = 60
    window_end_before_expiry_minutes: int = 10
    spread_max_pct: float = 1.5
    spread_share_of_reward_max: float = 0.20
    fees_share_of_reward_max: float = 0.30
    depth_entry_multiple: float = 1.5
    depth_exit_multiple: float = 1.0
    premium_min: float = 20.0
    adx_period: int = 14
    adx_chop: float = 18.0
    adx_trend: float = 25.0
    spike_atr_multiple: float = 2.5
    spike_wait_candles: int = 2
    vol_regime_multiple: float = 3.0
    net_rr_min: float = 1.5
    breakeven_max: float = 0.45
    atm_delta_range: list[float] = Field(default_factory=lambda: [0.35, 0.65])
    event_windows: list[dict[str, Any]] = Field(default_factory=list)
    bb_quartile_lookback: int = 200
    bb_quartile_threshold: float = 0.25


class EdgeConfig(BaseModel):
    mode: str = "none"  # none | empirical (empirical only after docs/05 go-live criteria)
    min_samples: int = 150
    wilson_confidence: float = 0.80
    margin_points: int = 3

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, v: str) -> str:
        if v not in ("none", "empirical"):
            raise ValueError(f"edge.mode must be 'none' or 'empirical', got '{v}'")
        return v


class MonteCarloConfig(BaseModel):
    paths: int = 20_000
    pool_days: int = 7
    enabled: bool = True


class SessionConfig(BaseModel):
    daily_loss_limit_usd: float | None = None
    cooldown_after_losses: int = 3
    cooldown_minutes: int = 30


class Settings(BaseModel):
    """Top-level settings. Every gate threshold lives here — no magic numbers in code."""

    region: str = "india"
    hosts: dict[str, HostsConfig] = Field(
        default_factory=lambda: {
            "india": HostsConfig(
                rest="https://api.india.delta.exchange", ws="wss://public-socket.india.delta.exchange"
            ),
            "global": HostsConfig(rest="https://api.delta.exchange", ws="wss://socket.delta.exchange"),
        }
    )
    server: ServerConfig = Field(default_factory=ServerConfig)
    timeframe: str = "5m"
    underlying: str = "BTC"
    display_timezone: str = "Asia/Kolkata"

    pricing: PricingConfig = Field(default_factory=PricingConfig)
    fees: FeesConfig = Field(default_factory=FeesConfig)
    structure: StructureConfig = Field(default_factory=StructureConfig)
    gates: GatesConfig = Field(default_factory=GatesConfig)
    edge: EdgeConfig = Field(default_factory=EdgeConfig)
    montecarlo: MonteCarloConfig = Field(default_factory=MonteCarloConfig)
    session: SessionConfig = Field(default_factory=SessionConfig)

    @field_validator("region")
    @classmethod
    def validate_region(cls, v: str) -> str:
        if v not in ("india", "global"):
            raise ValueError(f"region must be 'india' or 'global', got '{v}'")
        return v

    @model_validator(mode="after")
    def validate_hosts_has_region(self) -> Settings:
        if self.region not in self.hosts:
            raise ValueError(f"hosts config missing for region '{self.region}'")
        return self

    @property
    def active_hosts(self) -> HostsConfig:
        return self.hosts[self.region]


def _canonical_json(settings: Settings) -> str:
    """Deterministic JSON for hashing — sorted keys, no extra whitespace."""
    return json.dumps(settings.model_dump(), sort_keys=True, separators=(",", ":"), default=str)


def config_hash(settings: Settings) -> str:
    """SHA-256 of the canonical JSON representation. Stored with every evaluation."""
    return hashlib.sha256(_canonical_json(settings).encode("utf-8")).hexdigest()


def load_settings(path: str | Path | None = None) -> Settings:
    """Load settings from a YAML file. Falls back to defaults if no file provided."""
    if path is None:
        # Look in standard locations
        candidates = [
            Path("config/settings.yaml"),
            Path(__file__).parent.parent.parent / "config" / "settings.yaml",
        ]
        for c in candidates:
            if c.exists():
                path = c
                break
    if path is not None:
        path = Path(path)
        if path.exists():
            with open(path) as f:
                raw = yaml.safe_load(f) or {}
            return Settings(**raw)
    return Settings()


# Module-level singleton — loaded once, immutable during the session.
# Config changes require a restart (documented in docs/06).
_settings: Settings | None = None
_hash: str | None = None


def get_settings(path: str | Path | None = None) -> Settings:
    """Get or load the settings singleton."""
    global _settings, _hash
    if _settings is None:
        _settings = load_settings(path)
        _hash = config_hash(_settings)
    return _settings


def get_config_hash() -> str:
    """Get the hash of the current settings. Call get_settings() first."""
    if _hash is None:
        get_settings()
    assert _hash is not None
    return _hash


def reset_settings() -> None:
    """For testing only — reset the singleton so a new config can be loaded."""
    global _settings, _hash
    _settings = None
    _hash = None
