# PRD Regime Upgrade Patch (v2)

This patch upgrades the PRD “Multi‑Strategy Intraday Scalping System (Equities, Alpaca + Unusual Whales)” from a single **SPY‑only BULL/BEAR/CHOP** label to a **multi‑axis market context + per‑symbol micro‑context** regime system.

It keeps the original design goals:
- vertical-slice architecture
- deterministic behavior
- robust, structured logging
- agent-driven config improvements

---

## 0) Why change the current regime approach?

**Current behavior in the PRD**
- One global regime label derived from SPY intraday bars:
  - features: `cum_ret`, `trend_score = abs(cum_ret)/vol`
  - label: `BULL / BEAR / CHOP`
  - smoothing: majority vote over last K
- Strategies are routed via `strategies_by_regime` in `StrategyEngine`.

**Main issues**
1. **A single label collapses distinct worlds**
   “CHOP” can mean low-vol driftless noise or high-vol whipsaw. Those are opposite for sizing and execution risk.

2. **Global SPY regime ≠ symbol regime**
   AAPL can be trending hard on news while SPY chops.

3. **Hard gating causes missed opportunity and mode errors**
   ORB and momentum setups can exist even in a “CHOP” market label; conversely mean-reversion gets murdered during volatility shocks.

4. **No confidence / uncertainty handling**
   Early session classifications are unstable; the system should express “I’m not sure yet” and size down accordingly.

---

## 1) PRD edits: new “Regime” concept

### 1.1 Replace “Market Regime Detector” with “Market Context & Regime Service”

Rename component **3. Market Regime Detector** → **3. Market Context & Regime Service**.

**Outputs**
- A *vector* of discrete states (“axes”) + continuous features
- A confidence score per axis
- A compact derived label for humans (optional), e.g. `BULL/BEAR/CHOP` remains as a *display* tag only

---

## 2) Domain model changes

### 2.1 Replace `Regime` enum

Remove:
```python
class Regime(str, Enum):
    BULL = "bull"
    BEAR = "bear"
    CHOP = "chop"
```

Add axis enums:

```python
from enum import Enum

class TrendRegime(str, Enum):
    UP = "up"
    DOWN = "down"
    FLAT = "flat"

class VolRegime(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    SHOCK = "shock"      # “stop trading / extreme caution” zone

class LiquidityRegime(str, Enum):
    GOOD = "good"
    THIN = "thin"
    STRESSED = "stressed"  # spreads/impact unacceptable

class RiskRegime(str, Enum):
    RISK_ON = "risk_on"
    NEUTRAL = "neutral"
    RISK_OFF = "risk_off"

class SessionRegime(str, Enum):
    PREMARKET = "premarket"
    OPENING = "opening"
    MIDDAY = "midday"
    POWER_HOUR = "power_hour"
    CLOSE = "close"
```

### 2.2 Add `MarketRegimeSnapshot`

```python
from dataclasses import dataclass
from datetime import datetime
from typing import Dict

@dataclass(frozen=True)
class MarketRegimeSnapshot:
    time: datetime

    # key symbols used
    index_symbol: str             # e.g. "SPY"
    vol_symbol: str | None        # e.g. "VXX" (optional but recommended)

    # discrete regimes (axes)
    trend: TrendRegime
    vol: VolRegime
    liquidity: LiquidityRegime
    risk: RiskRegime
    session: SessionRegime

    # continuous features (always logged)
    cum_ret: float
    trend_strength: float
    realized_vol: float
    vol_of_vol: float
    liquidity_score: float
    risk_score: float

    # deterministic uncertainty
    confidence: Dict[str, float]  # per axis, 0..1

    # reproducibility
    model_version: str            # bump when logic/thresholds change
```

### 2.3 Update `MarketState`

Replace:
```python
@dataclass
class MarketState:
    time: datetime
    regime: Regime
    index_symbol: str
    index_price: float
    index_return: float
    realized_vol: float
    ...
```

With:
```python
@dataclass
class MarketState:
    time: datetime
    regime: MarketRegimeSnapshot

    index_price: float
    index_return: float

    daily_pnl: float
    risk_mode: RiskMode
    meta: Dict[str, Any]
```

### 2.4 Update `Signal` and logging payloads

Replace `Signal.regime: Regime` with a compact tag map:

```python
@dataclass
class Signal:
    ...
    regime_tags: Dict[str, str]     # {"trend":"up","vol":"high","risk":"risk_off",...}
    regime_confidence: Dict[str, float]
    ...
```

You do **not** need to carry the full MarketRegimeSnapshot into every object at runtime; you *do* need to log enough to reproduce and analyze.

---

## 3) Market Context & Regime Service: v1 logic (deterministic)

### 3.1 Inputs

Always-on subscriptions (count toward the 30 ticker WebSocket limit):
- SPY (index)
- **One** volatility proxy ETF if available in your data feed: VXX (preferred) or similar
- Optional but helpful: QQQ, IWM (broad risk proxy triangulation)

Session/time inputs:
- exchange timezone: America/New_York
- market open/close times

### 3.2 Features

Keep your existing `cum_ret` and `trend_score`, and add:

- **vol baseline**: rolling median vol over last M windows (intraday adaptive)
- **vol-of-vol**: rolling std of realized_vol
- **shock flag**:
  - |1-min return| > k * rolling_vol
  - OR 1-min range% > threshold
- **liquidity proxy** (bars-only fallback):
  - dollar_volume = close * volume
  - range_pct = (high - low)/close
  - liquidity_score = dollar_volume / (range_pct + eps)
  - If you have quotes: use effective spread / NBBO spread directly (better).
- **risk score** (simple v1):
  - risk_score = a*(SPY return) - b*(VXX return)
  - if VXX unavailable: use SPY return + SPY realized_vol increase

### 3.3 Classification per axis

**Trend**
- if `trend_strength < trend_flat_thresh` → FLAT
- else sign(cum_ret) → UP/DOWN

**Vol**
- compute z = realized_vol / (baseline_vol + eps)
- z < low_thresh → LOW
- low_thresh <= z < high_thresh → NORMAL
- high_thresh <= z < shock_thresh → HIGH
- z >= shock_thresh OR shock_flag → SHOCK

**Liquidity**
- liquidity_score quantiles:
  - top quantile → GOOD
  - middle → THIN
  - bottom + shock_flag → STRESSED

**Risk**
- risk_score thresholds:
  - above +t → RISK_ON
  - below -t → RISK_OFF
  - else NEUTRAL

**Session**
- derived from wall clock:
  - opening = first 30–60 min
  - midday = lunch hours
  - power_hour = last 60 min

### 3.4 Smoothing / hysteresis (per axis, not one global vote)

Replace the single majority vote with:
- per-axis hysteresis
- minimum hold time (e.g., do not switch vol regime more than once per X minutes unless SHOCK)

This reduces “regime flapping” in exactly the environments that trigger overtrading.

---

## 4) Strategy selection changes (most important PRD edit)

### 4.1 Remove `strategies_by_regime` routing

Current PRD uses:
```python
regime_strats = set(self.strategies_by_regime.get(regime, []))
active = allowed ∩ regime_strats
```

Replace with a deterministic **StrategyActivationPolicy** that evaluates rules:

```python
@dataclass(frozen=True)
class StrategyActivationPolicy:
    # allowed lists; empty => “no constraint”
    session: list[SessionRegime]
    trend: list[TrendRegime]
    vol: list[VolRegime]
    liquidity: list[LiquidityRegime]
    risk: list[RiskRegime]

    # optional numeric constraints
    min_confidence: float = 0.0     # require regime confidence
```

Each strategy has an activation policy in config, and the StrategyEngine does:

1. check symbol is allowed by scanner
2. check market regime axes match activation policy
3. check any strategy-specific prerequisites (flow availability, ATR, spread, etc.)
4. run strategy

### 4.2 Configuration example (strategies.yaml)

```yaml
VWAPReversion:
  enabled: true
  activation:
    session: [opening, midday, power_hour]
    trend: [flat]
    vol: [low, normal]
    liquidity: [good, thin]
    risk: [risk_on, neutral]
    min_confidence: 0.60

ORB:
  enabled: true
  activation:
    session: [opening]
    vol: [normal, high]
    liquidity: [good]
    min_confidence: 0.40
  requirements:
    flow_required: false   # set true if you want ORB only when flow is present
```

This is strictly more expressive than BULL/BEAR/CHOP without becoming a scientific paper.

---

## 5) Risk management changes

### 5.1 Add regime-based risk scaling

Add to `risk.yaml`:

```yaml
regime_risk_multipliers:
  vol:
    low: 1.10
    normal: 1.00
    high: 0.60
    shock: 0.00
  liquidity:
    good: 1.00
    thin: 0.75
    stressed: 0.00
  risk:
    risk_on: 1.00
    neutral: 0.85
    risk_off: 0.50
```

RiskManager sizing:
- base_qty determined by entry→stop risk
- final_qty = base_qty * multipliers[vol] * multipliers[liquidity] * multipliers[risk]
- enforce min trade size; otherwise reject signal with reason “REGIME_RISK_ZERO” or “REGIME_RISK_BELOW_MIN”.

### 5.2 Automatic `risk_mode`

In MarketState update loop:
- if vol == SHOCK or liquidity == STRESSED → set `risk_mode = OFF`
- elif vol == HIGH or risk == RISK_OFF → `risk_mode = REDUCED`
- else `risk_mode = NORMAL`

Allow manual override (config) but log overrides loudly.

---

## 6) Scanner changes

Scanner already computes symbol features (ADX, zscore, vwap distance, etc.).
Change the scanner’s dependence on a single regime label:

- Pass the full `MarketRegimeSnapshot` (or at least `regime_tags`) to `StrategyScannerProfile.score()`.
- Add a baseline liquidity/spread filter (critical for scalping) so you don’t nominate symbols that are untradeable under current conditions.
- If flow features are missing, **do not set to neutral** for strategies where flow is a prerequisite. Instead:
  - record `feature_availability.flow = false`
  - allow `min_requirements()` to reject the symbol for flow-dependent strategies

---

## 7) Analytics schema changes

### 7.1 Regime history table

Replace:
- timestamp, regime, cum_ret, trend_score, vol

With:

- timestamp
- model_version
- trend, vol, liquidity, risk, session
- cum_ret, trend_strength, realized_vol, vol_of_vol
- liquidity_score, risk_score
- confidence_json

### 7.2 Trades/signals tables

Replace `regime_at_entry` and `regime_at_exit` with:
- `regime_tags_entry_json`
- `regime_tags_exit_json`
- optional “compact label” column for convenience: `regime_compact_entry` (e.g., “UP/HIGH/RISK_OFF”)

---

## 8) Agent changes

The combinatorial trap: if you do full cartesian products of regimes, you get sparse bins and nonsense.

So adjust Stage 1:

- Compute stats **per axis**:
  - performance by vol regime
  - performance by trend regime
  - performance by liquidity regime
  - performance by session regime
- Then compute **one** or **two** high-impact joint bins only (configurable), e.g.:
  - vol × session
  - vol × trend

Agent outputs:
- disable strategies in specific axis states (e.g., “VWAPReversion disabled when vol=HIGH”)
- adjust risk multipliers downward when a regime becomes toxic

All changes remain config-only.

---

## 9) Vertical slice updates

Update slice plan:

### Slice 1 – Market Context Skeleton
- Subscribe to SPY (+VXX if used)
- Compute MarketRegimeSnapshot
- Log to `regime_history` with model_version
- Unit tests for each axis classification + hysteresis

### Slice 2 – Risk scaling by regime
- No strategy changes yet
- RiskManager applies multipliers + logs sizing inputs
- Paper-trade one strategy to verify drawdown reduction in high-vol/shock regimes

### Slice 3 – Strategy activation rules
- Replace `strategies_by_regime` routing with ActivationPolicy evaluation
- Add tests ensuring deterministic activation across bars

---

## 10) Logging & error taxonomy additions

Add structured fields to all logs touching regimes:

- `regime.model_version`
- `regime.tags` (trend/vol/liquidity/risk/session)
- `regime.confidence`
- `regime.features` (cum_ret, realized_vol, liquidity_score, risk_score)

Add explicit error codes:
- `REGIME_COMPUTE_FAILED`
- `REGIME_DATA_MISSING`
- `REGIME_RISK_ZERO`
- `STRATEGY_ACTIVATION_BLOCKED`

---

## 11) Minimal viable set of changes (if you want it lean)

If you want the smallest change with maximum benefit:
1. Add **vol regime** (LOW/NORMAL/HIGH/SHOCK) + risk multipliers
2. Add **session regime** (OPENING/MIDDAY/POWER_HOUR)
3. Replace hard regime gating with ActivationPolicy (even if it only checks vol+session)

This already fixes most “simple BULL/BEAR/CHOP” failure modes for scalping.
