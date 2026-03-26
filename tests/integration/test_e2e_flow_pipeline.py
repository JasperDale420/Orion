"""
End-to-end integration test for the Orion flow pipeline.

Exercises the full data flow without needing a running container or market hours:
  Heber Silver flow_alerts → BronzeEvent(UW_FLOW) → FeatureEngine
    → SilverSignals → RuleEngine → CandidateTrades → MLScorer

Run: cd /Users/jacobmcmillan/Empire/Orion && python /tmp/test_e2e_flow_pipeline.py
"""

import os
import sys

# Ensure Orion source is on the path
sys.path.insert(0, "/Users/jacobmcmillan/Empire/Orion/src")
os.environ.setdefault(
    "HEBER_DATA_ROOT", "/Users/jacobmcmillan/Empire/Heber/.local_heber_data/data.backfill-20260311-164326"
)

from datetime import UTC, datetime
import uuid

PASS = "✅"
FAIL = "❌"
WARN = "⚠️"

results: list[tuple[str, str, str]] = []


def log(icon: str, stage: str, detail: str) -> None:
    results.append((icon, stage, detail))
    print(f"  {icon} {stage}: {detail}")


print("\n" + "=" * 70)
print("  ORION END-TO-END FLOW PIPELINE TEST")
print("=" * 70 + "\n")

# ──────────────────────────────────────────────────────────────────────
# STAGE 1: Read flow alerts from Heber
# ──────────────────────────────────────────────────────────────────────
print("── Stage 1: Heber Flow Alert Ingestion ──")
try:
    from orion.clients.heber_reader import HeberReader

    reader = HeberReader()
    df = reader.read_flow(symbols=None, asof_time=datetime.now(UTC))
    if df.empty:
        log(FAIL, "Heber Read", "No flow alerts found in Heber Silver")
    else:
        log(PASS, "Heber Read", f"{len(df)} flow alerts loaded from silver/feed=flow_alerts")
        tickers = df["underlying"].dropna().unique().tolist() if "underlying" in df.columns else []
        log(PASS, "Tickers Found", f"{len(tickers)} unique tickers: {sorted(tickers)[:10]}")
except Exception as e:
    log(FAIL, "Heber Read", f"CRASHED: {e}")
    df = None

# ──────────────────────────────────────────────────────────────────────
# STAGE 2: Convert to BronzeEvents
# ──────────────────────────────────────────────────────────────────────
print("\n── Stage 2: BronzeEvent Conversion ──")
bronze_events = []
try:
    from orion.storage.models import BronzeEvent
    from orion.ingestion.service import IngestionService

    if df is not None and not df.empty:
        now = datetime.now(UTC)
        for _, row in df.iterrows():
            event = IngestionService._heber_row_to_event(row, now)
            if event:
                bronze_events.append(event)

        log(PASS, "Conversion", f"{len(bronze_events)} BronzeEvents created from {len(df)} rows")

        if bronze_events:
            sample = bronze_events[0]
            log(PASS, "Sample Event", f"ticker={sample.ticker}, type={sample.event_type}, source={sample.source}")
    else:
        log(FAIL, "Conversion", "No dataframe to convert")
except Exception as e:
    log(FAIL, "Conversion", f"CRASHED: {e}")

# ──────────────────────────────────────────────────────────────────────
# STAGE 3: FeatureEngine → SilverSignals
# ──────────────────────────────────────────────────────────────────────
print("\n── Stage 3: FeatureEngine Processing ──")
signals = []
try:
    from orion.processing.feature_engine import FeatureEngine

    engine = FeatureEngine()

    # First update flow state
    engine.process_uw_flow(bronze_events)
    log(PASS, "Flow State", "FeatureEngine internal flow state updated")

    # Then generate signals
    signals = engine.process_uw_flow_events(bronze_events)
    if signals:
        log(PASS, "Signals", f"{len(signals)} SilverSignals generated")
        s = signals[0]
        log(PASS, "Sample Signal", f"ticker={s.ticker}, type={s.signal_type}, features_count={len(s.features or {})}")

        # Check feature quality
        features = s.features or {}
        key_fields = ["premium_usd", "put_call", "is_sweep", "strike", "expiry"]
        present = [f for f in key_fields if f in features and features[f] is not None]
        missing = [f for f in key_fields if f not in features or features[f] is None]
        if missing:
            log(WARN, "Feature Quality", f"Missing key fields: {missing}")
        else:
            log(PASS, "Feature Quality", f"All key fields present: {present}")
    else:
        log(FAIL, "Signals", "ZERO SilverSignals produced from flow events")
except Exception as e:
    import traceback

    log(FAIL, "FeatureEngine", f"CRASHED: {e}")
    traceback.print_exc()

# ──────────────────────────────────────────────────────────────────────
# STAGE 4: RuleEngine → CandidateTrades
# ──────────────────────────────────────────────────────────────────────
print("\n── Stage 4: RuleEngine Evaluation ──")
candidates = []
try:
    from orion.processing.rule_engine import RuleEngine

    rule_engine = RuleEngine()
    log(PASS, "Rules Loaded", f"{len(rule_engine.rules)} rules registered")

    for rule in rule_engine.rules:
        rule_name = type(rule).__name__
        log(PASS, "  Rule", f"{rule_name}")

    if signals:
        candidates = rule_engine.process_signals(signals)
        if candidates:
            log(PASS, "Candidates", f"{len(candidates)} CandidateTrades generated!")
            for c in candidates[:5]:
                log(
                    PASS,
                    "  Candidate",
                    f"ticker={c.ticker}, rule={c.rule_id}, "
                    f"direction={c.direction}, confidence={getattr(c, 'confidence', 'N/A')}",
                )
        else:
            log(
                WARN,
                "Candidates",
                "ZERO candidates from rules — signals may not match rule criteria "
                "(e.g., sweep requirement, minimum premium, DTE range)",
            )
            # Show why no rules fired by checking signal features
            if signals:
                s = signals[0]
                f = s.features or {}
                log(
                    WARN,
                    "  Debug",
                    f"Sample signal: premium_usd={f.get('premium_usd')}, "
                    f"is_sweep={f.get('is_sweep')}, put_call={f.get('put_call')}, "
                    f"aggressor_ind={f.get('aggressor_ind')}",
                )
    else:
        log(FAIL, "Candidates", "No signals to evaluate")
except Exception as e:
    import traceback

    log(FAIL, "RuleEngine", f"CRASHED: {e}")
    traceback.print_exc()

# ──────────────────────────────────────────────────────────────────────
# STAGE 5: ML Scorer
# ──────────────────────────────────────────────────────────────────────
print("\n── Stage 5: ML Model Scoring ──")
try:
    from orion.ml.scorer import MLScorer, MultiTargetScorer

    # Load single-target scorer
    scorer = MLScorer()
    loaded = scorer.get_loaded_models()
    log(PASS if loaded else FAIL, "Models Loaded", f"{len(loaded)} bucket models: {loaded}")
    log(
        PASS if not scorer.use_heuristic else WARN,
        "Scorer Mode",
        "ML-based" if not scorer.use_heuristic else "Heuristic fallback (no models)",
    )

    # Score the first few flow events directly
    if bronze_events:
        sample_flows = []
        for event in bronze_events[:5]:
            flow_dict = dict(event.payload)
            flow_dict["dte"] = 7  # Default to SWING bucket if not in payload
            if "expiry" in flow_dict:
                from datetime import date

                try:
                    exp = date.fromisoformat(str(flow_dict["expiry"]))
                    today = date.today()
                    flow_dict["dte"] = max((exp - today).days, 0)
                except (ValueError, TypeError):
                    pass
            sample_flows.append(flow_dict)

        scores = scorer.score_batch(sample_flows)
        for i, (flow, score) in enumerate(zip(sample_flows, scores, strict=False)):
            ticker = flow.get("ticker", "?")
            dte = flow.get("dte", "?")
            premium = flow.get("premium_usd", flow.get("premium", "?"))
            log(PASS, f"  Score {i + 1}", f"{ticker} (DTE={dte}, premium={premium}) → score={score:.4f}")

        # Multi-target scoring
        multi = MultiTargetScorer()
        signal = multi.get_trade_signal(sample_flows[0])
        log(PASS, "Multi-Target", f"Recommendation: {signal['recommendation']}")
        for target, s in signal["scores"].items():
            log(PASS, f"  {target}", f"{s:.4f}")

except Exception as e:
    import traceback

    log(FAIL, "ML Scorer", f"CRASHED: {e}")
    traceback.print_exc()

# ──────────────────────────────────────────────────────────────────────
# SUMMARY
# ──────────────────────────────────────────────────────────────────────
print("\n" + "=" * 70)
print("  PIPELINE TEST SUMMARY")
print("=" * 70)
pass_count = sum(1 for r in results if r[0] == PASS)
fail_count = sum(1 for r in results if r[0] == FAIL)
warn_count = sum(1 for r in results if r[0] == WARN)
print(f"\n  {PASS} Passed: {pass_count}")
print(f"  {FAIL} Failed: {fail_count}")
print(f"  {WARN} Warnings: {warn_count}")

if fail_count == 0:
    print("\n  🎉 END-TO-END PIPELINE IS FUNCTIONAL")
elif fail_count <= 2:
    print(f"\n  ⚡ PIPELINE PARTIALLY WORKING — {fail_count} stages need attention")
else:
    print(f"\n  💥 PIPELINE BROKEN — {fail_count} stages failed")
print()
