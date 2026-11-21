"""Tests for config loading, validation, and hashing."""

from pathlib import Path

import pytest
import yaml

from rr_engine.config import (
    Settings,
    config_hash,
    get_config_hash,
    get_settings,
    load_settings,
    reset_settings,
)


@pytest.fixture(autouse=True)
def _reset():
    """Reset the singleton before each test."""
    reset_settings()
    yield
    reset_settings()


def test_default_settings_are_valid():
    s = Settings()
    assert s.region == "india"
    assert s.active_hosts.rest == "https://api.india.delta.exchange"
    assert s.pricing.iv_shock_sl == -0.02
    assert s.gates.net_rr_min == 1.5


def test_config_hash_is_deterministic():
    s1 = Settings()
    s2 = Settings()
    assert config_hash(s1) == config_hash(s2)
    assert len(config_hash(s1)) == 64  # SHA-256 hex


def test_config_hash_changes_with_settings():
    s1 = Settings()
    s2 = Settings(region="global")
    assert config_hash(s1) != config_hash(s2)


def test_load_from_yaml(tmp_path):
    cfg = {
        "region": "india",
        "pricing": {"iv_shock_sl": -0.03, "hold_minutes_default": 45},
        "gates": {"net_rr_min": 2.0},
    }
    p = tmp_path / "settings.yaml"
    p.write_text(yaml.dump(cfg))
    s = load_settings(p)
    assert s.pricing.iv_shock_sl == -0.03
    assert s.pricing.hold_minutes_default == 45
    assert s.gates.net_rr_min == 2.0
    # Unchanged defaults preserved
    assert s.fees.gst == 0.18


def test_invalid_region_rejected():
    with pytest.raises(ValueError, match="region"):
        Settings(region="mars")


def test_missing_region_in_hosts_rejected():
    with pytest.raises(ValueError, match="hosts config missing"):
        Settings(region="india", hosts={"global": {"rest": "https://x", "ws": "wss://x"}})


def test_invalid_edge_mode_rejected():
    with pytest.raises(ValueError, match="edge.mode"):
        Settings(edge={"mode": "yolo"})


def test_singleton_pattern():
    s1 = get_settings()
    s2 = get_settings()
    assert s1 is s2
    h = get_config_hash()
    assert len(h) == 64


def test_load_nonexistent_file_uses_defaults():
    s = load_settings(Path("/nonexistent/path/settings.yaml"))
    assert s.region == "india"


def test_every_gate_threshold_in_config():
    """Every gate threshold documented in docs/04 must be in settings."""
    s = Settings()
    g = s.gates
    # Data integrity
    assert g.data_stale_seconds > 0
    assert g.index_perp_divergence_pct > 0
    assert g.clock_skew_seconds > 0
    assert g.greeks_mismatch_warn > 0
    assert g.greeks_mismatch_hard > g.greeks_mismatch_warn
    # Option quality
    assert g.expiry_min_minutes > 0
    assert g.spread_max_pct > 0
    assert g.spread_share_of_reward_max > 0
    assert g.fees_share_of_reward_max > 0
    assert g.depth_entry_multiple > 0
    assert g.premium_min > 0
    # Market context
    assert g.adx_chop > 0
    assert g.adx_trend > g.adx_chop
    assert g.spike_atr_multiple > 0
    assert g.vol_regime_multiple > 0
    # Trade geometry
    assert g.net_rr_min > 0
    assert 0 < g.breakeven_max < 1
    assert len(g.atm_delta_range) == 2
