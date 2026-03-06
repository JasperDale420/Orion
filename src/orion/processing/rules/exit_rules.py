"""
Dynamic Exit Rules for Short-Term Options Trades.

Based on "Dynamic Exit Strategies for Short-Term Options Trades Using Unusual Whales Data" PDF.
Implements 6 flow-based exit signals for 0-16 DTE options positions.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ExitSignal:
    """Result of an exit rule evaluation."""

    rule_id: str
    reason: str
    urgency: str  # IMMEDIATE, SOON, CONSIDER
    confidence: float = 0.8
    details: dict[str, Any] | None = None


class ExitRule(ABC):
    """Base class for exit rules."""

    def __init__(self, rule_id: str) -> None:
        self.rule_id = rule_id

    @abstractmethod
    def should_exit(
        self,
        position: Any,  # OpenPosition
        recent_flow: list[Any],  # List of normalized flow records
        context: dict[str, Any] | None = None,
    ) -> ExitSignal | None:
        """
        Evaluate if position should be exited.

        Args:
            position: OpenPosition with entry context
            recent_flow: Recent flow records for the ticker (last 30 min)
            context: Additional context (current IV, OI, etc.)

        Returns:
            ExitSignal if exit triggered, None otherwise
        """
        pass


def parse_expiry_from_flow(flow: Any) -> datetime | None:
    """Parse expiry from flow record."""
    expiry_str = getattr(flow, "expiry", None)
    if not expiry_str:
        return None
    try:
        return datetime.strptime(expiry_str, "%Y-%m-%d").replace(tzinfo=UTC)
    except (ValueError, AttributeError):
        return None


def get_flow_dte(flow: Any) -> int | None:
    """Calculate DTE for a flow record."""
    flow_ts = getattr(flow, "flow_ts_utc", None)
    expiry = parse_expiry_from_flow(flow)
    if not flow_ts or not expiry:
        return None
    dte = (expiry.date() - flow_ts.date()).days
    return max(0, dte)


def classify_dte_bucket(dte: int | None) -> str:
    """Classify DTE into trade type bucket."""
    if dte is None:
        return "UNKNOWN"
    if dte == 0:
        return "0DTE"
    elif dte <= 3:
        return "SHORT_SWING"
    elif dte <= 14:
        return "SWING"
    return "POSITION"


def get_position_dte_bucket(position: Any) -> str:
    """Get DTE bucket for a position."""
    # Position should have entry_ts and expiry or option_chain
    option_chain = getattr(position, "option_chain", None)
    if not option_chain:
        return "UNKNOWN"

    # Parse expiry from option chain (e.g., SPY251224C00500000 -> 2025-12-24)
    try:
        # Format: TICKER + YYMMDD + P/C + STRIKE
        # Find the date portion (6 digits after ticker)
        import re

        match = re.search(r"(\d{6})[PC]", option_chain)
        if match:
            date_str = match.group(1)
            expiry = datetime.strptime(f"20{date_str}", "%Y%m%d").replace(tzinfo=UTC)
            entry_ts = position.entry_ts
            dte = (expiry.date() - entry_ts.date()).days
            return classify_dte_bucket(max(0, dte))
    except (ValueError, AttributeError):
        pass

    return "UNKNOWN"


def flow_matches_position_dte(flow: Any, position_bucket: str) -> bool:
    """Check if flow's DTE bucket matches position's bucket."""
    if position_bucket == "UNKNOWN":
        return True  # No filtering if position bucket unknown

    flow_dte = get_flow_dte(flow)
    flow_bucket = classify_dte_bucket(flow_dte)

    if flow_bucket == "UNKNOWN":
        return True  # No filtering if flow bucket unknown

    # Only match same bucket
    return flow_bucket == position_bucket


class SentimentReversalExitRule(ExitRule):
    """
    Rule 1: Flow Sentiment Reversal

    Exit when large opposing sweep appears after entry.
    E.g., big put sweep after entering long calls.
    """

    def __init__(self, min_opposing_premium: float = 100000.0) -> None:
        super().__init__(rule_id="exit_sentiment_reversal_v1")
        self.min_opposing_premium = min_opposing_premium

    def should_exit(
        self,
        position: Any,
        recent_flow: list[Any],
        context: dict[str, Any] | None = None,
    ) -> ExitSignal | None:
        if not recent_flow:
            return None

        # Determine what "opposing" means based on position direction
        is_long = position.direction == "LONG"

        # Get position's DTE bucket for filtering
        position_bucket = get_position_dte_bucket(position)

        for flow in recent_flow:
            flow_ts = getattr(flow, "flow_ts_utc", None)
            if flow_ts and flow_ts <= position.entry_ts:
                continue  # Skip pre-entry flow

            # Only consider flow with matching DTE bucket
            if not flow_matches_position_dte(flow, position_bucket):
                continue

            premium = getattr(flow, "premium_usd", 0) or 0
            aggressor = getattr(flow, "aggressor", "") or ""
            put_call = getattr(flow, "put_call", "") or ""
            is_sweep = str(getattr(flow, "is_sweep", "")).lower() == "true"

            # Opposing = bearish flow for long, bullish for short
            is_opposing = False
            if is_long:
                # Bearish = ASK-side puts or BID-side calls
                if (put_call == "P" and aggressor == "ASK") or (put_call == "C" and aggressor == "BID"):
                    is_opposing = True
            else:
                # Bullish = ASK-side calls or BID-side puts
                if (put_call == "C" and aggressor == "ASK") or (put_call == "P" and aggressor == "BID"):
                    is_opposing = True

            if is_opposing and is_sweep and premium >= self.min_opposing_premium:
                flow_dte = get_flow_dte(flow)
                return ExitSignal(
                    rule_id=self.rule_id,
                    reason=f"Large opposing sweep: ${premium:,.0f} {put_call} {aggressor} (DTE: {flow_dte})",
                    urgency="IMMEDIATE",
                    confidence=0.85,
                    details={"premium": premium, "put_call": put_call, "aggressor": aggressor, "dte": flow_dte},
                )

        return None


class NetPremiumDeclineExitRule(ExitRule):
    """
    Rule 2: Net Premium Decline

    Exit when net bullish premium drops >50% from entry window.
    """

    def __init__(self, decline_threshold_pct: float = 50.0) -> None:
        super().__init__(rule_id="exit_net_premium_decline_v1")
        self.decline_threshold_pct = decline_threshold_pct

    def should_exit(
        self,
        position: Any,
        recent_flow: list[Any],
        context: dict[str, Any] | None = None,
    ) -> ExitSignal | None:
        if not recent_flow or position.entry_premium_window <= 0:
            return None

        is_long = position.direction == "LONG"

        # Calculate current net premium (bullish - bearish for long)
        bullish_premium = 0.0
        bearish_premium = 0.0

        for flow in recent_flow:
            premium = getattr(flow, "premium_usd", 0) or 0
            aggressor = getattr(flow, "aggressor", "") or ""
            put_call = getattr(flow, "put_call", "") or ""

            # Classify as bullish or bearish
            if (put_call == "C" and aggressor == "ASK") or (put_call == "P" and aggressor == "BID"):
                bullish_premium += premium
            elif (put_call == "P" and aggressor == "ASK") or (put_call == "C" and aggressor == "BID"):
                bearish_premium += premium

        if is_long:
            net_premium = bullish_premium - bearish_premium
        else:
            net_premium = bearish_premium - bullish_premium

        # Compare to entry window
        entry_premium = position.entry_premium_window
        decline_pct = ((entry_premium - net_premium) / entry_premium) * 100 if entry_premium > 0 else 0

        if decline_pct >= self.decline_threshold_pct:
            return ExitSignal(
                rule_id=self.rule_id,
                reason=f"Net premium declined {decline_pct:.0f}% from entry",
                urgency="SOON",
                confidence=0.7,
                details={"entry_premium": entry_premium, "current_net": net_premium, "decline_pct": decline_pct},
            )

        return None


class VolumeOIDivergenceExitRule(ExitRule):
    """
    Rule 3: Volume/OI Divergence

    Exit when OI doesn't increase despite high volume (positions closed, not opened).
    """

    def __init__(self, oi_increase_threshold_pct: float = 20.0) -> None:
        super().__init__(rule_id="exit_volume_oi_divergence_v1")
        self.oi_increase_threshold_pct = oi_increase_threshold_pct

    def should_exit(
        self,
        position: Any,
        recent_flow: list[Any],
        context: dict[str, Any] | None = None,
    ) -> ExitSignal | None:
        ctx = context or {}
        current_oi = ctx.get("current_oi")
        entry_oi = position.entry_oi

        if current_oi is None or entry_oi is None or entry_oi <= 0:
            return None

        # Calculate OI change
        oi_change_pct = ((current_oi - entry_oi) / entry_oi) * 100

        # If OI hasn't increased by threshold, positions are being closed
        if oi_change_pct < self.oi_increase_threshold_pct:
            return ExitSignal(
                rule_id=self.rule_id,
                reason=f"OI only changed {oi_change_pct:.1f}% (threshold: {self.oi_increase_threshold_pct}%)",
                urgency="SOON",
                confidence=0.65,
                details={"entry_oi": entry_oi, "current_oi": current_oi, "oi_change_pct": oi_change_pct},
            )

        return None


class WaningMomentumExitRule(ExitRule):
    """
    Rule 4: Waning Momentum

    Exit when sweep frequency/size drops significantly from entry window.
    """

    def __init__(self, momentum_drop_threshold_pct: float = 70.0, window_minutes: int = 15) -> None:
        super().__init__(rule_id="exit_waning_momentum_v1")
        self.momentum_drop_threshold_pct = momentum_drop_threshold_pct
        self.window_minutes = window_minutes

    def should_exit(
        self,
        position: Any,
        recent_flow: list[Any],
        context: dict[str, Any] | None = None,
    ) -> ExitSignal | None:
        if not recent_flow:
            return None

        entry_sweep_count = position.entry_sweep_count
        if entry_sweep_count <= 0:
            return None

        # Count sweeps in recent window
        now = datetime.now(UTC)
        window_start = now - timedelta(minutes=self.window_minutes)

        recent_sweep_count = 0
        for flow in recent_flow:
            flow_ts = getattr(flow, "flow_ts_utc", None)
            is_sweep = str(getattr(flow, "is_sweep", "")).lower() == "true"

            if flow_ts and flow_ts >= window_start and is_sweep:
                recent_sweep_count += 1

        # Calculate momentum drop
        if entry_sweep_count > 0:
            drop_pct = ((entry_sweep_count - recent_sweep_count) / entry_sweep_count) * 100
        else:
            drop_pct = 0

        if drop_pct >= self.momentum_drop_threshold_pct:
            return ExitSignal(
                rule_id=self.rule_id,
                reason=f"Sweep momentum dropped {drop_pct:.0f}% ({recent_sweep_count} vs {entry_sweep_count} at entry)",
                urgency="CONSIDER",
                confidence=0.6,
                details={
                    "entry_sweep_count": entry_sweep_count,
                    "recent_sweep_count": recent_sweep_count,
                    "drop_pct": drop_pct,
                },
            )

        return None


class IVContractionExitRule(ExitRule):
    """
    Rule 5: IV Contraction

    Exit when IV drops significantly or earnings event approaching.
    """

    def __init__(self, iv_drop_threshold: float = 10.0, earnings_hours_threshold: int = 24) -> None:
        super().__init__(rule_id="exit_iv_contraction_v1")
        self.iv_drop_threshold = iv_drop_threshold
        self.earnings_hours_threshold = earnings_hours_threshold

    def should_exit(
        self,
        position: Any,
        recent_flow: list[Any],
        context: dict[str, Any] | None = None,
    ) -> ExitSignal | None:
        ctx = context or {}

        # Check IV drop
        entry_iv = position.entry_iv
        current_iv = ctx.get("current_iv")

        if entry_iv and current_iv:
            iv_drop = (entry_iv - current_iv) * 100  # Convert to percentage points
            if iv_drop >= self.iv_drop_threshold:
                return ExitSignal(
                    rule_id=self.rule_id,
                    reason=f"IV dropped {iv_drop:.1f} points (from {entry_iv * 100:.1f}% to {current_iv * 100:.1f}%)",
                    urgency="IMMEDIATE",
                    confidence=0.75,
                    details={"entry_iv": entry_iv, "current_iv": current_iv, "iv_drop": iv_drop},
                )

        # Check earnings proximity
        next_earnings = ctx.get("next_earnings_date")
        if next_earnings:
            now = datetime.now(UTC)
            if isinstance(next_earnings, str):
                try:
                    next_earnings = datetime.fromisoformat(next_earnings.replace("Z", "+00:00"))
                except ValueError:
                    next_earnings = None

            if next_earnings:
                hours_to_earnings = (next_earnings - now).total_seconds() / 3600
                if 0 < hours_to_earnings < self.earnings_hours_threshold:
                    return ExitSignal(
                        rule_id=self.rule_id,
                        reason=f"Earnings in {hours_to_earnings:.1f} hours - exit to avoid IV crush",
                        urgency="IMMEDIATE",
                        confidence=0.9,
                        details={"hours_to_earnings": hours_to_earnings, "earnings_date": str(next_earnings)},
                    )

        return None


class OpposingClusterExitRule(ExitRule):
    """
    Rule 6: Opposing Clusters

    Exit when multiple smaller opposing trades accumulate (same strike/expiry).
    """

    def __init__(self, min_cluster_count: int = 5, min_premium_per_trade: float = 10000.0) -> None:
        super().__init__(rule_id="exit_opposing_cluster_v1")
        self.min_cluster_count = min_cluster_count
        self.min_premium_per_trade = min_premium_per_trade

    def should_exit(
        self,
        position: Any,
        recent_flow: list[Any],
        context: dict[str, Any] | None = None,
    ) -> ExitSignal | None:
        if not recent_flow:
            return None

        is_long = position.direction == "LONG"

        opposing_count = 0
        opposing_premium = 0.0

        for flow in recent_flow:
            flow_ts = getattr(flow, "flow_ts_utc", None)
            if flow_ts and flow_ts <= position.entry_ts:
                continue

            premium = getattr(flow, "premium_usd", 0) or 0
            if premium < self.min_premium_per_trade:
                continue

            aggressor = getattr(flow, "aggressor", "") or ""
            put_call = getattr(flow, "put_call", "") or ""
            getattr(flow, "option_chain", "") or ""

            # Note: Could filter by same strike/expiry using option_chain prefix

            # Classify as opposing
            is_opposing = False
            if is_long:
                if (put_call == "P" and aggressor == "ASK") or (put_call == "C" and aggressor == "BID"):
                    is_opposing = True
            else:
                if (put_call == "C" and aggressor == "ASK") or (put_call == "P" and aggressor == "BID"):
                    is_opposing = True

            if is_opposing:
                opposing_count += 1
                opposing_premium += premium

        if opposing_count >= self.min_cluster_count:
            return ExitSignal(
                rule_id=self.rule_id,
                reason=f"Cluster of {opposing_count} opposing trades (${opposing_premium:,.0f} total)",
                urgency="SOON",
                confidence=0.7,
                details={"opposing_count": opposing_count, "opposing_premium": opposing_premium},
            )

        return None


class PriceTargetExitRule(ExitRule):
    """
    Rule 7: Price-Based Exit

    Exit when option price reaches profit target or stop loss.
    Based on 0DTE analysis: 50% target, 20% stop.
    """

    def __init__(self, profit_target_pct: float = 50.0, stop_loss_pct: float = 20.0) -> None:
        super().__init__(rule_id="exit_price_target_v1")
        self.profit_target_pct = profit_target_pct
        self.stop_loss_pct = stop_loss_pct

    def should_exit(
        self,
        position: Any,
        recent_flow: list[Any],
        context: dict[str, Any] | None = None,
    ) -> ExitSignal | None:
        ctx = context or {}

        # Get current option price from context
        current_option_price = ctx.get("current_option_price")
        entry_option_price = getattr(position, "entry_option_price", None)

        if current_option_price is None or entry_option_price is None or entry_option_price <= 0:
            return None

        # Calculate return
        return_pct = ((current_option_price - entry_option_price) / entry_option_price) * 100

        # Check profit target
        if return_pct >= self.profit_target_pct:
            return ExitSignal(
                rule_id=self.rule_id,
                reason=f"Profit target hit: {return_pct:.1f}% (target: {self.profit_target_pct}%)",
                urgency="IMMEDIATE",
                confidence=0.95,
                details={
                    "entry_price": entry_option_price,
                    "current_price": current_option_price,
                    "return_pct": return_pct,
                    "target_type": "PROFIT",
                },
            )

        # Check stop loss
        if return_pct <= -self.stop_loss_pct:
            return ExitSignal(
                rule_id=self.rule_id,
                reason=f"Stop loss hit: {return_pct:.1f}% (stop: -{self.stop_loss_pct}%)",
                urgency="IMMEDIATE",
                confidence=0.95,
                details={
                    "entry_price": entry_option_price,
                    "current_price": current_option_price,
                    "return_pct": return_pct,
                    "target_type": "STOP",
                },
            )

        return None


# Factory function to get all exit rules with default config
def get_default_exit_rules(include_price_target: bool = False) -> list[ExitRule]:
    """Return default exit rules (legacy default excludes price-target rule)."""
    rules: list[ExitRule] = [
        SentimentReversalExitRule(min_opposing_premium=100000.0),
        NetPremiumDeclineExitRule(decline_threshold_pct=50.0),
        VolumeOIDivergenceExitRule(oi_increase_threshold_pct=20.0),
        WaningMomentumExitRule(momentum_drop_threshold_pct=70.0),
        IVContractionExitRule(iv_drop_threshold=10.0),
        OpposingClusterExitRule(min_cluster_count=5),
    ]
    if include_price_target:
        rules.append(PriceTargetExitRule(profit_target_pct=50.0, stop_loss_pct=20.0))
    return rules
