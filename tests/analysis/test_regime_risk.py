"""Tests for RegimeRiskManager's trust-gated SHOCK handling.

Adversarial review (2026-08-18, gpt-5.6-terra) on the ORION_FEATURE_ENRICHMENT_
PREFER_HEBER_CONTEXT rollout found that RegimeGate's hard trading block
treated any fresh vix_level as ground truth, including a VIXY-ETF-price
proxy that is not a real spot-VIX reading. These tests pin the fix: a SHOCK
classification only hard-blocks (and only zeroes sizing) when it is backed
by a trusted vix source.
"""

from datetime import UTC, datetime

from orion.analysis.regime import (
    MarketRegimeSnapshot,
    RiskRegime,
    SessionRegime,
    TrendRegime,
    VIXRegime,
    VolRegime,
)
from orion.analysis.regime_risk import CONFIG_PATH, RegimeRiskManager, _load_regime_risk_config


def _shock_snapshot(vix_source: str | None) -> MarketRegimeSnapshot:
    return MarketRegimeSnapshot(
        ts=datetime.now(UTC),
        trend=TrendRegime.FLAT,
        vol=VolRegime.SHOCK,
        risk=RiskRegime.NEUTRAL,
        session=SessionRegime.MIDDAY,
        vix_regime=VIXRegime.EXTREME,
        vix_level=40.0,
        vix_source=vix_source,
    )


class TestConfigPathResolvesToRealFile:
    """CONFIG_PATH has pointed at src/config/regime_risk.yaml (which does not
    exist) since this module's original commit (6a37a10d, 2026-01-02) — three
    `.parent`s from src/orion/analysis/regime_risk.py lands in src/, not the
    repo root where config/regime_risk.yaml actually lives. _load_regime_risk_
    config() has therefore always silently returned {}, so every multiplier
    lookup fell back to its 1.0 default and regime-based position sizing has
    never actually applied — found via the untrusted-proxy-shock sizing test
    below unexpectedly returning 1.0 instead of the config's 0.0 for SHOCK."""

    def test_config_path_points_at_the_real_yaml_file(self):
        assert CONFIG_PATH.exists(), f"{CONFIG_PATH} does not exist"

    def test_config_actually_loads_real_multiplier_values(self):
        config = _load_regime_risk_config()

        assert config.get("vol_multipliers", {}).get("shock") == 0.0
        assert config.get("vol_multipliers", {}).get("high") == 0.6


class TestShouldTrade:
    def test_untrusted_proxy_shock_does_not_block(self):
        manager = RegimeRiskManager()

        assert manager.should_trade(_shock_snapshot(vix_source="proxy:VIXY")) is True

    def test_missing_vix_source_on_shock_does_not_block(self):
        manager = RegimeRiskManager()

        assert manager.should_trade(_shock_snapshot(vix_source=None)) is True

    def test_trusted_spot_vix_shock_blocks(self):
        manager = RegimeRiskManager()

        assert manager.should_trade(_shock_snapshot(vix_source="spot_vix")) is False


class TestSizingMultiplier:
    def test_untrusted_proxy_shock_does_not_zero_sizing(self):
        manager = RegimeRiskManager()

        combined = manager.calculate_combined_multiplier(_shock_snapshot(vix_source="proxy:VIXY"))

        assert combined > 0.0

    def test_trusted_spot_vix_shock_still_zeroes_sizing(self):
        manager = RegimeRiskManager()

        combined = manager.calculate_combined_multiplier(_shock_snapshot(vix_source="spot_vix"))

        assert combined == 0.0
