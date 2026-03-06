"""
Unit tests for Dynamic Exit Rules.

Tests each of the 6 exit rules with mock flow data and positions.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from orion.processing.rules.exit_rules import (
    IVContractionExitRule,
    NetPremiumDeclineExitRule,
    OpposingClusterExitRule,
    SentimentReversalExitRule,
    VolumeOIDivergenceExitRule,
    WaningMomentumExitRule,
    get_default_exit_rules,
)


@dataclass
class MockPosition:
    """Mock position for testing."""

    ticker: str = "SPY"
    direction: str = "LONG"
    candidate_id: str = "test_candidate_1"
    decision_id: str = "test_decision_1"
    entry_ts: datetime = None
    entry_price: float = 100.0
    option_chain: str | None = "SPY250117C00500000"
    entry_iv: float | None = 0.25
    entry_premium_window: float = 500000.0
    entry_sweep_count: int = 10
    entry_oi: float | None = 5000.0
    qty: float = 10.0

    def __post_init__(self):
        if self.entry_ts is None:
            self.entry_ts = datetime.now(UTC) - timedelta(minutes=15)


@dataclass
class MockFlow:
    """Mock flow record for testing."""

    ticker: str = "SPY"
    flow_ts_utc: datetime = None
    aggressor: str = "ASK"
    put_call: str = "C"
    premium_usd: float = 50000.0
    is_sweep: str = "true"
    option_chain: str = "SPY250117C00500000"
    open_interest: float = 5000.0
    volume_contract: float = 100

    def __post_init__(self):
        if self.flow_ts_utc is None:
            self.flow_ts_utc = datetime.now(UTC)


class TestSentimentReversalExitRule:
    """Test Rule 1: Flow Sentiment Reversal."""

    def test_no_exit_when_flow_aligned(self):
        """No exit signal when flow is aligned with position."""
        rule = SentimentReversalExitRule(min_opposing_premium=100000.0)
        position = MockPosition(direction="LONG")

        # Bullish flow (ASK side calls) - aligned with LONG
        flow = [MockFlow(aggressor="ASK", put_call="C", premium_usd=150000.0, is_sweep="true")]

        signal = rule.should_exit(position, flow)
        assert signal is None

    def test_exit_on_large_opposing_sweep(self):
        """Exit when large opposing sweep appears."""
        rule = SentimentReversalExitRule(min_opposing_premium=100000.0)
        position = MockPosition(direction="LONG")

        # Bearish flow (ASK side puts) - opposing LONG
        flow = [MockFlow(aggressor="ASK", put_call="P", premium_usd=150000.0, is_sweep="true")]

        signal = rule.should_exit(position, flow)
        assert signal is not None
        assert signal.rule_id == "exit_sentiment_reversal_v1"
        assert signal.urgency == "IMMEDIATE"

    def test_no_exit_when_premium_below_threshold(self):
        """No exit when opposing premium is below threshold."""
        rule = SentimentReversalExitRule(min_opposing_premium=100000.0)
        position = MockPosition(direction="LONG")

        # Small bearish flow
        flow = [MockFlow(aggressor="ASK", put_call="P", premium_usd=50000.0, is_sweep="true")]

        signal = rule.should_exit(position, flow)
        assert signal is None


class TestNetPremiumDeclineExitRule:
    """Test Rule 2: Net Premium Decline."""

    def test_exit_on_premium_decline(self):
        """Exit when net premium drops >50%."""
        rule = NetPremiumDeclineExitRule(decline_threshold_pct=50.0)
        position = MockPosition(entry_premium_window=500000.0)

        # Flow with low bullish premium (significant decline)
        flow = [
            MockFlow(aggressor="ASK", put_call="C", premium_usd=100000.0),
            MockFlow(aggressor="ASK", put_call="P", premium_usd=50000.0),  # Bearish
        ]

        signal = rule.should_exit(position, flow)
        assert signal is not None
        assert signal.rule_id == "exit_net_premium_decline_v1"

    def test_no_exit_when_premium_stable(self):
        """No exit when premium is stable."""
        rule = NetPremiumDeclineExitRule(decline_threshold_pct=50.0)
        position = MockPosition(entry_premium_window=500000.0)

        # High bullish premium
        flow = [MockFlow(aggressor="ASK", put_call="C", premium_usd=600000.0)]

        signal = rule.should_exit(position, flow)
        assert signal is None


class TestVolumeOIDivergenceExitRule:
    """Test Rule 3: Volume/OI Divergence."""

    def test_exit_when_oi_unchanged(self):
        """Exit when OI doesn't increase despite volume."""
        rule = VolumeOIDivergenceExitRule(oi_increase_threshold_pct=20.0)
        position = MockPosition(entry_oi=5000.0)

        # OI barely changed
        context = {"current_oi": 5100.0}  # Only 2% increase

        signal = rule.should_exit(position, [], context)
        assert signal is not None
        assert signal.rule_id == "exit_volume_oi_divergence_v1"

    def test_no_exit_when_oi_increases(self):
        """No exit when OI increases significantly."""
        rule = VolumeOIDivergenceExitRule(oi_increase_threshold_pct=20.0)
        position = MockPosition(entry_oi=5000.0)

        # OI increased by 25%
        context = {"current_oi": 6250.0}

        signal = rule.should_exit(position, [], context)
        assert signal is None


class TestWaningMomentumExitRule:
    """Test Rule 4: Waning Momentum."""

    def test_exit_when_momentum_drops(self):
        """Exit when sweep frequency drops significantly."""
        rule = WaningMomentumExitRule(momentum_drop_threshold_pct=70.0, window_minutes=15)
        position = MockPosition(entry_sweep_count=10)

        # Only 2 recent sweeps (80% drop)
        now = datetime.now(UTC)
        flow = [
            MockFlow(flow_ts_utc=now - timedelta(minutes=5), is_sweep="true"),
            MockFlow(flow_ts_utc=now - timedelta(minutes=10), is_sweep="true"),
        ]

        signal = rule.should_exit(position, flow)
        assert signal is not None
        assert signal.rule_id == "exit_waning_momentum_v1"

    def test_no_exit_when_momentum_stable(self):
        """No exit when sweep frequency stable."""
        rule = WaningMomentumExitRule(momentum_drop_threshold_pct=70.0, window_minutes=15)
        position = MockPosition(entry_sweep_count=5)

        # 4 recent sweeps (only 20% drop)
        now = datetime.now(UTC)
        flow = [MockFlow(flow_ts_utc=now - timedelta(minutes=i), is_sweep="true") for i in range(4)]

        signal = rule.should_exit(position, flow)
        assert signal is None


class TestIVContractionExitRule:
    """Test Rule 5: IV Contraction."""

    def test_exit_on_iv_drop(self):
        """Exit when IV drops significantly."""
        rule = IVContractionExitRule(iv_drop_threshold=10.0)
        position = MockPosition(entry_iv=0.30)

        # IV dropped from 30% to 18% (12 points drop)
        context = {"current_iv": 0.18}

        signal = rule.should_exit(position, [], context)
        assert signal is not None
        assert signal.rule_id == "exit_iv_contraction_v1"
        assert signal.urgency == "IMMEDIATE"

    def test_exit_before_earnings(self):
        """Exit when earnings approaching."""
        rule = IVContractionExitRule(earnings_hours_threshold=24)
        position = MockPosition(entry_iv=0.25)

        # Earnings in 12 hours
        earnings = datetime.now(UTC) + timedelta(hours=12)
        context = {"next_earnings_date": earnings.isoformat()}

        signal = rule.should_exit(position, [], context)
        assert signal is not None
        assert "Earnings" in signal.reason

    def test_no_exit_when_iv_stable(self):
        """No exit when IV is stable."""
        rule = IVContractionExitRule(iv_drop_threshold=10.0)
        position = MockPosition(entry_iv=0.25)

        # IV only dropped 3 points
        context = {"current_iv": 0.22}

        signal = rule.should_exit(position, [], context)
        assert signal is None


class TestOpposingClusterExitRule:
    """Test Rule 6: Opposing Clusters."""

    def test_exit_on_opposing_cluster(self):
        """Exit when cluster of opposing trades appears."""
        rule = OpposingClusterExitRule(min_cluster_count=5, min_premium_per_trade=10000.0)
        position = MockPosition(direction="LONG")

        # 6 opposing trades
        now = datetime.now(UTC)
        flow = [
            MockFlow(
                flow_ts_utc=now - timedelta(minutes=i),
                aggressor="ASK",
                put_call="P",
                premium_usd=20000.0,
            )
            for i in range(6)
        ]

        signal = rule.should_exit(position, flow)
        assert signal is not None
        assert signal.rule_id == "exit_opposing_cluster_v1"

    def test_no_exit_when_few_opposing(self):
        """No exit when only a few opposing trades."""
        rule = OpposingClusterExitRule(min_cluster_count=5, min_premium_per_trade=10000.0)
        position = MockPosition(direction="LONG")

        # Only 3 opposing trades
        now = datetime.now(UTC)
        flow = [
            MockFlow(
                flow_ts_utc=now - timedelta(minutes=i),
                aggressor="ASK",
                put_call="P",
                premium_usd=20000.0,
            )
            for i in range(3)
        ]

        signal = rule.should_exit(position, flow)
        assert signal is None


class TestGetDefaultExitRules:
    """Test factory function."""

    def test_returns_all_rules(self):
        """Should return all 6 exit rules."""
        rules = get_default_exit_rules()
        assert len(rules) == 6

    def test_rule_ids_unique(self):
        """All rules should have unique IDs."""
        rules = get_default_exit_rules()
        rule_ids = [r.rule_id for r in rules]
        assert len(rule_ids) == len(set(rule_ids))
