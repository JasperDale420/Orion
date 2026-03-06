from orion.processing.rules.base import TradingRule
from orion.storage.models_gold import CandidateTrade, TradeDirection
from orion.storage.models_silver import SilverSignal


class BullishSweepRule(TradingRule):
    """
    PRD 9.1 Step 1: Bullish Sweep + Confirming Dark
    - large call sweep (premium >= X)
    - aggressor=ASK (or price >= mid)
    - DTE 7-30d
    - delta in [0.3, 0.6] (if available)
    - concurrent dark pool prints (simplified for v1: just check flow)
    """

    def __init__(self, min_premium: float = 10000.0):
        super().__init__(rule_id="rule_bullish_sweep_v1")
        self.min_premium = min_premium

    def evaluate(self, signal: SilverSignal) -> CandidateTrade | None:
        if signal.signal_type != "UW_FLOW":
            return None

        # Logic: Check if signal is a Flow event with specific characteristics
        # Signal 'features' dict holds normalized fields from Silver

        feat = signal.features
        if not feat:
            return None

        # 1. Filter for UW Flows only (v1 simplification)
        # Assuming our Feature Engine passes raw flow params in 'features' for now
        # or we look at specific columns if it's a vector.
        # For this slice, we assume 'features' contains the raw-ish columns
        # or we'd need to fetch the underlying event.
        # Let's assume FeatureEngine passes 'meta' or fields directly.

        # Check basic criteria
        # "sweep" flag usually passed
        is_sweep = feat.get("is_sweep", False)
        if not is_sweep:
            return None

        # Call vs Put
        if feat.get("put_call") != "CALL":
            return None

        # Premium Size
        premium = feat.get("premium", 0)
        if premium < self.min_premium:
            return None

        # Aggressor
        aggressor = feat.get("aggressor_ind") or feat.get("aggressor") or ""
        if aggressor not in ["ASK", "ABOVE_ASK"]:
            # relaxed check: or price >= mid logic if available
            return None

        # DTE
        dte = feat.get("dte", 0)
        if not (7 <= dte <= 30):
            return None

        # Delta (optional)
        delta = feat.get("delta")
        if delta is not None:
            if not (0.3 <= abs(delta) <= 0.6):
                return None

        candidate = self._create_candidate(
            signal=signal,
            direction=TradeDirection.LONG.value,
            confidence=0.7,
            evidence_extras={
                "event_ids": [feat.get("event_id")] if feat.get("event_id") else [],
                "source_event_id": feat.get("source_event_id"),
                "premium": premium,
                "dte": dte,
                "reason": "Bullish Sweep confirmed",
            },
        )
        candidate.source = "UW"
        candidate.execution_params = {"limit_price": feat.get("underlying_price")}
        return candidate


class BearishPutPressureRule(TradingRule):
    """
    PRD 9.1: Bearish Put Pressure
    - put premium burst
    - aggressor=ASK on puts
    - short DTE (e.g. < 14d)
    """

    def __init__(self, min_premium: float = 10000.0):
        super().__init__(rule_id="rule_bearish_put_pressure_v1")
        self.min_premium = min_premium

    def evaluate(self, signal: SilverSignal) -> CandidateTrade | None:
        if signal.signal_type != "UW_FLOW":
            return None

        feat = signal.features
        if not feat:
            return None

        # Call vs Put
        if feat.get("put_call") != "PUT":
            return None

        # Premium
        premium = feat.get("premium", 0)
        if premium < self.min_premium:
            return None

        # Aggressor (buying puts = bearish)
        aggressor = feat.get("aggressor_ind") or feat.get("aggressor") or ""
        if aggressor not in ["ASK", "ABOVE_ASK"]:
            return None

        # DTE: Short term
        dte = feat.get("dte", 999)
        if dte > 14:
            return None

        candidate = self._create_candidate(
            signal=signal,
            direction=TradeDirection.SHORT.value,
            confidence=0.65,
            evidence_extras={
                "event_ids": [feat.get("event_id")] if feat.get("event_id") else [],
                "source_event_id": feat.get("source_event_id"),
                "premium": premium,
                "dte": dte,
            },
        )
        candidate.source = "UW"
        candidate.execution_params = {"limit_price": feat.get("underlying_price")}
        return candidate


class ZeroDTESweepRule(TradingRule):
    """
    0DTE Entry Signal based on price target analysis.

    Optimal criteria from backtest:
    - Time: 14:00-15:00 UTC (market open) - 80% avg max return
    - Direction: Puts preferred (0% stop rate, +75% avg max return)
    - Premium: $100-150K (50% hit targets, 17% stopped)
    - Must be a sweep
    - DTE = 0

    Exit targets:
    - Profit target: 50% (avg +80% return when hit)
    - Stop loss: 20%
    """

    def __init__(
        self,
        min_premium: float = 100000.0,
        max_premium: float = 150000.0,
        market_open_hour_utc: int = 14,
        prefer_puts: bool = True,
    ):
        super().__init__(rule_id="rule_0dte_sweep_v1")
        self.min_premium = min_premium
        self.max_premium = max_premium
        self.market_open_hour_utc = market_open_hour_utc
        self.prefer_puts = prefer_puts

    def evaluate(self, signal: SilverSignal) -> CandidateTrade | None:
        if signal.signal_type != "UW_FLOW":
            return None

        feat = signal.features
        if not feat:
            return None

        # Must be a sweep
        is_sweep = feat.get("is_sweep", False)
        if not is_sweep:
            return None

        # DTE must be 0
        dte = feat.get("dte", 999)
        if dte != 0:
            return None

        # Premium in sweet spot ($100-150K)
        premium = feat.get("premium", 0)
        if premium < self.min_premium or premium > self.max_premium:
            return None

        # Aggressor = ASK (buying)
        aggressor = feat.get("aggressor_ind") or feat.get("aggressor") or ""
        if aggressor not in ["ASK", "ABOVE_ASK"]:
            return None

        # Time filter: market open hour (14:00 UTC = 9:00 ET)
        signal_hour = None
        if signal.event_ts_utc:
            signal_hour = signal.event_ts_utc.hour
        if signal_hour is not None and signal_hour != self.market_open_hour_utc:
            # Still allow, but lower confidence for non-optimal times
            pass

        # Direction preference
        put_call = feat.get("put_call", "")
        is_put = put_call == "PUT"

        # Calculate confidence based on criteria matching
        confidence = 0.7
        if signal_hour == self.market_open_hour_utc:
            confidence += 0.1  # Market open bonus
        if is_put:
            confidence += 0.1  # Puts have 0% stop rate historically

        # Trade direction: LONG puts = bearish underlying, LONG calls = bullish
        direction = TradeDirection.LONG.value

        candidate = self._create_candidate(
            signal=signal,
            direction=direction,
            confidence=confidence,
            evidence_extras={
                "event_ids": [feat.get("event_id")] if feat.get("event_id") else [],
                "source_event_id": feat.get("source_event_id"),
                "premium": premium,
                "dte": dte,
                "put_call": put_call,
                "hour_utc": signal_hour,
                "reason": f"0DTE {put_call} sweep at market open",
            },
        )
        candidate.source = "UW"
        candidate.execution_params = {
            "limit_price": feat.get("underlying_price"),
            "profit_target_pct": 50.0,  # Based on analysis: +80% avg when hit
            "stop_loss_pct": 20.0,  # Based on analysis
        }
        return candidate


class SwingEntryRule(TradingRule):
    """
    SWING Entry Signal (4-14 DTE) based on price target analysis.

    Structural criteria (ticker-agnostic):
    - Direction: Puts only (25% win rate vs 10% for calls)
    - Premium: $50-75K (24% win rate, smaller = less crowding)
    - Time: 14:00 or 17:00 UTC (31% win rate)
    - DTE: 4-14 days (SWING bucket)
    - Must be a sweep with ASK aggressor

    Exit strategy: Use flow-based exits (sentiment reversal)
    to avoid stops rather than hard profit targets.
    """

    def __init__(
        self,
        min_premium: float = 50000.0,
        max_premium: float = 75000.0,
        optimal_hours_utc: tuple = (14, 17),
    ):
        super().__init__(rule_id="rule_swing_entry_v1")
        self.min_premium = min_premium
        self.max_premium = max_premium
        self.optimal_hours_utc = optimal_hours_utc

    def evaluate(self, signal: SilverSignal) -> CandidateTrade | None:
        if signal.signal_type != "UW_FLOW":
            return None

        feat = signal.features
        if not feat:
            return None

        # Must be a sweep
        is_sweep = feat.get("is_sweep", False)
        if not is_sweep:
            return None

        # DTE must be 4-14 (SWING bucket)
        dte = feat.get("dte", 999)
        if dte < 4 or dte > 14:
            return None

        # PUTS ONLY - 2.5x better win rate than calls
        put_call = feat.get("put_call", "")
        if put_call != "PUT":
            return None

        # Premium in sweet spot ($50-75K)
        premium = feat.get("premium", 0)
        if premium < self.min_premium or premium > self.max_premium:
            return None

        # Aggressor = ASK (buying puts)
        aggressor = feat.get("aggressor_ind") or feat.get("aggressor") or ""
        if aggressor not in ["ASK", "ABOVE_ASK"]:
            return None

        # Calculate confidence based on hour
        signal_hour = signal.event_ts_utc.hour if signal.event_ts_utc else None

        confidence = 0.65
        if signal_hour in self.optimal_hours_utc:
            confidence += 0.15  # Optimal hour bonus (31% win rate)

        # DTE 5-6 bonus (41% win rate)
        if 5 <= dte <= 6:
            confidence += 0.1

        candidate = self._create_candidate(
            signal=signal,
            direction=TradeDirection.LONG.value,
            confidence=confidence,
            evidence_extras={
                "event_ids": [feat.get("event_id")] if feat.get("event_id") else [],
                "source_event_id": feat.get("source_event_id"),
                "premium": premium,
                "dte": dte,
                "put_call": put_call,
                "hour_utc": signal_hour,
                "reason": f"SWING put sweep ${premium / 1000:.0f}K DTE={dte}",
            },
        )
        candidate.source = "UW"
        candidate.execution_params = {
            "limit_price": feat.get("underlying_price"),
            "exit_strategy": "FLOW_BASED",  # Use sentiment reversal
            "profit_target_pct": 50.0,  # Backup if flow doesn't trigger
            "stop_loss_pct": 20.0,
        }
        return candidate


class ShortSwingEntryRule(TradingRule):
    """
    SHORT_SWING Entry Signal (1-3 DTE) based on price target analysis.

    Structural criteria (ticker-agnostic):
    - Direction: CALLS preferred (14% win rate vs 8% for puts)
    - Premium: $75-100K (43% win rate)
    - Time: 14:00 UTC (market open)
    - DTE: 1-3 days (SHORT_SWING bucket)
    - Must be a sweep with ASK aggressor

    Warning: High stop rate (58%). Use flow-based exits.
    """

    def __init__(
        self,
        min_premium: float = 75000.0,
        max_premium: float = 100000.0,
        optimal_hours_utc: tuple = (14,),
    ):
        super().__init__(rule_id="rule_short_swing_entry_v1")
        self.min_premium = min_premium
        self.max_premium = max_premium
        self.optimal_hours_utc = optimal_hours_utc

    def evaluate(self, signal: SilverSignal) -> CandidateTrade | None:
        if signal.signal_type != "UW_FLOW":
            return None

        feat = signal.features
        if not feat:
            return None

        is_sweep = feat.get("is_sweep", False)
        if not is_sweep:
            return None

        dte = feat.get("dte", 999)
        if dte < 1 or dte > 3:
            return None

        put_call = feat.get("put_call", "")
        if put_call != "CALL":
            return None

        premium = feat.get("premium", 0)
        if premium < self.min_premium or premium > self.max_premium:
            return None

        aggressor = feat.get("aggressor_ind") or feat.get("aggressor") or ""
        if aggressor not in ["ASK", "ABOVE_ASK"]:
            return None

        signal_hour = signal.event_ts_utc.hour if signal.event_ts_utc else None
        confidence = 0.60
        if signal_hour in self.optimal_hours_utc:
            confidence += 0.1

        candidate = self._create_candidate(
            signal=signal,
            direction=TradeDirection.LONG.value,
            confidence=confidence,
            evidence_extras={
                "event_ids": [feat.get("event_id")] if feat.get("event_id") else [],
                "source_event_id": feat.get("source_event_id"),
                "premium": premium,
                "dte": dte,
                "put_call": put_call,
                "hour_utc": signal_hour,
                "reason": f"SHORT_SWING call sweep ${premium / 1000:.0f}K DTE={dte}",
            },
        )
        candidate.source = "UW"
        candidate.execution_params = {
            "limit_price": feat.get("underlying_price"),
            "exit_strategy": "FLOW_BASED",
            "profit_target_pct": 50.0,
            "stop_loss_pct": 20.0,
        }
        return candidate
