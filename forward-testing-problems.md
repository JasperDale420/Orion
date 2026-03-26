# Orion System Health Report — 2026-03-13

## TL;DR

**Orion is NOT actively forward testing.** The system's services are running but in a degraded, non-functional state across all critical paths: model training has no data, the EOD agent's LLM calls are failing, execution is unauthorized, and feature enrichment is stuck on static fallbacks.

---

## 1. Are the Training Models Actually Running?

### Model Files on Disk

20 `.pkl` models exist in [models/](file:///Users/jacobmcmillan/Empire/Orion/models):

| Category | Last Trained | Models |
|---|---|---|
| 0DTE | **Jan 20, 2026** | `exit`, `avoid_stop`, `hit_target_50`, `hit_target_100`, `quick_winner` |
| SHORT_SWING | Jan 20 – Mar 3 | Most retrained Mar 3, originals from Jan 20 |
| SWING | **Mar 3, 2026** | All 5 targets |
| POSITION | **Mar 3, 2026** | All 5 targets |

> [!CAUTION]
> Models are **10 days stale** (SWING/POSITION) to **53 days stale** (0DTE). No retraining has succeeded since March 3.

### Pattern Miner (Model Training Service)

The `orion_pattern_miner` container runs hourly and is scheduled for weekly full re-training (next: Monday 2026-03-16). However, **every training attempt fails** because:

1. **Zero training data from Heber**: Every bucket (0DTE, SHORT_SWING, SWING, POSITION) logs:
   ```
   Raw outcomes size: 0 → "No Heber outcomes available for pattern-miner training"
   ```
2. **Missing dependency for exit classifier**:
   ```
   ModuleNotFoundError: No module named 'aiofiles'
   ```
   This crashes exit classifier training on every run.

**Result**: 0 entry models trained. Exit classifiers fail completely. The existing `.pkl` files are leftovers from the last successful training run on Jan 20 / Mar 3.

---

## 2. Can the System Read Incoming Data and Make Decisions?

### Ingestion (✅ Partially Working)

The `orion_ingestion` container **is alive** and actively building rollups every ~60 seconds for 10 tickers:
- SPY, QQQ, IWM, NVDA, TSLA, AAPL, AMD, MSFT, AMZN, GOOGL

However, most tickers only process **3 source bars** per cycle (GOOGL processes 503). VIXY consistently has no Silver Signals.

### Feature Enrichment (❌ Broken)

The `orion_feature_enrichment` container is running but **stuck on static fallback** with a streak of **8,400+ consecutive non-Heber cycles**:

```
"Ticker discovery has consecutive non-Heber source cycles" (streak: 8493)
```

This means the feature enrichment engine cannot dynamically discover tickers from Heber — it falls back to a hardcoded list of 10 tickers. Additionally:
- **VIX proxy**: Writing 0 records for 72+ consecutive cycles ("No VIXY data available")
- Regime snapshots show: `trend=flat, vol=normal, risk=neutral, session=close, vix=normal` — indicating the system's view of market state is likely stale/default

### Execution Engine (❌ Completely Broken)

The `orion_execution` container is **spamming unauthorized errors** every ~1 second:

```json
"Failed to fetch recent fills: {\"message\": \"unauthorized.\"}"
```

The Alpaca API credentials are invalid/expired. No fills can be polled, no orders can be placed. This service is effectively dead.

### Signal Generation & Decision Making

Based on the EOD reports (Feb 26 – Mar 13), the system is generating **zero signals, zero decisions, zero trades** consistently. The Mar 2 report noted:
> "Zero signal generation - system may be over-filtered or misconfigured"
> "Data ingestion or preprocessing may have failed silently"

### Is This Forward Testing?

**No.** Forward testing requires the system to:
1. Receive live data ✅ (ingestion works, partially)
2. Transform data into features ❌ (stuck on fallback)
3. Run models on features to generate signals ❌ (no signals generated)
4. Make trade decisions ❌ (zero decisions)
5. Execute trades (even paper) ❌ (Alpaca unauthorized)
6. Track outcomes ❌ (no data for pattern miner)

The system is in a **circular failure mode**: no trades → no outcomes → no training data → no model updates → stale models → no signals.

---

## 3. EOD Agent — Reports, Suggestions, and Code Changes

### How It Works

The [EODReviewAgent](file:///Users/jacobmcmillan/Empire/Orion/src/orion/agents/eod_review_agent.py) runs via `main_eod.py` in Docker. It:

1. Waits until 30 minutes after market close (4:30 PM ET → 20:30 UTC)
2. Queries the database for the day's decisions, signals, trades, orders, fills, DLQ events, bronze events, and silver signals
3. Computes drift metrics (PSI on feature distributions), slippage analysis, volatility regime classification, performance breakdowns
4. Sends all this data to an LLM (model `glm-5` via AI Gateway) for analysis
5. Writes an EOD report markdown file and proposes `SolverEdits` for the meta-search layer
6. If solver_mutation proposals are generated, the MetaSearchAgent runs a refinement loop (backtest → refine → promote to paper)

### What's Actually Happening

The EOD agent runs **twice per day** (once mid-day around 1:30 PM UTC, once at 8:30 PM UTC) — looking at the logs from Mar 12-13:

| Timestamp (UTC) | Date | Outcome |
|---|---|---|
| Mar 12 13:30 | 2026-03-12 | ❌ LLM Failed: "Attempt to overwrite 'args' in LogRecord" → 0 proposals |
| Mar 12 20:30 | 2026-03-12 | ❌ LLM Failed: JSON parse error (line 199) → 0 proposals |
| Mar 13 13:30 | 2026-03-13 | ❌ LLM Failed: "Attempt to overwrite 'args' in LogRecord" → 0 proposals |
| Mar 13 20:30 | 2026-03-13 | ❌ LLM Failed: "Attempt to overwrite 'args' in LogRecord" → 0 proposals |

**Every LLM call is failing.** Two distinct errors:

1. **`Attempt to overwrite 'args' in LogRecord`** — a Python logging conflict (likely structlog/stdlib collision inside the LLM client)
2. **JSON parse failure** — the LLM response isn't valid JSON (truncated or malformed)

### Generated Reports

Recent reports are essentially empty:

- **Mar 13**: `Error: "Attempt to overwrite 'args' in LogRecord"` (49 bytes)
- **Mar 12**: `Test Analysis` (13 bytes)
- **Mar 11**: 49 bytes
- **Mar 10**: 13 bytes
- **Mar 3-9**: All 98 bytes ("No analysis generated" placeholder)

The **last substantive report** was around **Feb 26**, which noted zero trading activity and data ingestion lag of 17+ hours.

### Suggestions / Proposals

**Zero proposals have been generated** since the LLM failures started. The EOD agent is designed to produce `SolverEdit` proposals that modify strategy parameters, but it has produced **none**.

### Code Changes by Agents

**The EOD agent has not made any code changes.** It writes `SolverEdits` to the database and YAML proposal files — these are *strategy configuration changes*, not source code changes. But even these haven't been produced due to the LLM failures.

The only [weekly evolution artifact](file:///Users/jacobmcmillan/Empire/Orion/artifacts/reports/weekly_evolution_2026-01-16.json) is from January 16, 2026.

---

## 4. Additional Warnings

- **`orion_pattern_miner` is unhealthy** in Docker (all other services report healthy)
- **Embedding dimension mismatch** in RAG vector store: model returns 768-dim embeddings but store expects 1536 → falls back to Python rank (degraded search)
- The `ORION_API_KEY` environment variable is not set (Docker compose warning)

---

## Summary of Blocking Issues

| Issue | Service | Impact | Fix Needed |
|---|---|---|---|
| Alpaca API unauthorized | Execution | No trades, no fill data | Rotate/fix API credentials |
| Heber data pipeline broken | Pattern Miner, Feature Enrichment | No training data, no feature discovery | Debug Heber connection/reader |
| Missing `aiofiles` in Docker image | Pattern Miner | Exit classifier training crashes | Add to `pyproject.toml` + rebuild image |
| LLM `args` LogRecord conflict | EOD Agent | No analysis, no proposals | Fix logging config in `codex_client.py` |
| Embedding dimension mismatch (768 vs 1536) | EOD Agent (RAG) | Degraded context retrieval | Align embedding model with vector store config |
