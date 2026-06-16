"""RiskManager.seed_equity_baseline — caps the equity baseline to Orion's
allocated slice.

The Alpaca paper account ($1M) is shared across ~6-10 systems. Seeding Orion's
current/starting/peak equity from the full account made sizing (max premium 2%,
max order 5%) compute off $1M — ~10x the intended slice, the root cause of the
5/26 over-exposure (39 positions in a day). The baseline must cap to the
allocated slice.
"""

from orion.config import RiskSettings
from orion.execution.risk.manager import RiskManager


def test_seed_caps_equity_to_allocated_slice():
    rm = RiskManager(config=RiskSettings(allocated_equity=100_000.0))
    rm.seed_equity_baseline(1_003_765.0)  # full shared account
    assert rm.current_equity == 100_000.0
    assert rm.starting_equity == 100_000.0
    assert rm.peak_equity == 100_000.0
    assert rm._equity_seeded is True
    assert rm._peak_equity_seeded is True


def test_seed_uses_gateway_equity_when_below_allocated():
    rm = RiskManager(config=RiskSettings(allocated_equity=100_000.0))
    rm.seed_equity_baseline(50_000.0)
    assert rm.current_equity == 50_000.0
    assert rm.peak_equity == 50_000.0


def test_seed_is_once_only():
    rm = RiskManager(config=RiskSettings(allocated_equity=100_000.0))
    rm.seed_equity_baseline(100_000.0)
    rm.seed_equity_baseline(999.0)  # second seed must be ignored
    assert rm.current_equity == 100_000.0
    assert rm.peak_equity == 100_000.0


def test_seed_no_cap_when_allocated_disabled():
    rm = RiskManager(config=RiskSettings(allocated_equity=None))
    rm.seed_equity_baseline(1_003_765.0)
    assert rm.current_equity == 1_003_765.0


def test_allocated_equity_defaults_to_100k():
    """Safe-by-default: the cap is on even if no env override is set."""
    assert RiskSettings().allocated_equity == 100_000.0
