# Orion — API Reference

FastAPI admin / dashboard API. All routes live in
`src/orion/api/main.py`. Schemas in `src/orion/api/schemas.py`, auth in
`src/orion/api/auth.py`, deps in `src/orion/api/deps.py`.

## Base URL & auth

- **Base URL:** `http://localhost:8000` (default port).
- **Auth header:** `x-api-key: <ORION_API_KEY>`. If `ORION_API_KEY` is unset,
  authenticated endpoints return a server-configuration error.
- **Error envelope:** routes raise either standard FastAPI `HTTPException`
  (`{"detail": "..."}`) or `OrionError`, which the global exception handler
  converts to:

  ```json
  {
    "success": false,
    "error": {
      "code": "ORION_ERROR_CODE",
      "message": "Human-readable message",
      "details": {}
    }
  }
  ```

- **Correlation ID:** every request gets a `x-trace-id` header echoed back and
  bound into the structured-log context for the request.

## Endpoint catalog

Grouped by tag. Source line numbers are stable references into `api/main.py`.

### System

| Method | Path | Notes |
|---|---|---|
| GET | `/` | Basic API metadata |
| GET | `/health` | Liveness — returns `{"status": "ok"}` |

### Solvers

| Method | Path | Query params |
|---|---|---|
| GET | `/solvers` | `skip` (int), `limit` (int), `active_only` (bool) |
| GET | `/solvers/{solver_id}` | — |

### Metrics & experiments

| Method | Path | Query params |
|---|---|---|
| GET | `/metrics` | `solver_id` (str), `dataset_tag` (str), `limit` (int) |
| GET | `/experiments` | List meta-search experiments |

### Promotions (solver lifecycle)

| Method | Path | Notes |
|---|---|---|
| GET | `/promotions` | List pending promotion recommendations |
| POST | `/promotions/{recommendation_id}/approve` | Requires `reviewed_by` query param |
| POST | `/promotions/{recommendation_id}/reject`  | Requires `reviewed_by` query param |

Approving moves a solver to the next lifecycle stage
(`paper → limited_live → scaled_live`). See
[`project-overview-pdr.md`](project-overview-pdr.md#solver-lifecycle).

### Search & data lookups

| Method | Path | Notes |
|---|---|---|
| GET | `/search` | RAG hybrid search with metadata filters. **503** when pgvector or embeddings are unavailable. |
| GET | `/events/{event_id}` | Fetch Bronze `EventEnvelope` by id |
| GET | `/candidates/{candidate_id}` | Fetch `CandidateTrade` row |
| GET | `/rollups` | Query rollups by ticker / period |
| GET | `/rollups/{ticker}/{period}/{timestamp_utc}` | Fetch single rollup row |
| GET | `/flows` | Options-flow rows with filters. **503** when Heber flow reads fail |

### Dashboard

| Method | Path | Notes |
|---|---|---|
| GET | `/dashboard/summary` | Portfolio summary |
| GET | `/dashboard/positions` | Open Orion positions (filtered by `orion_` prefix) |
| GET | `/dashboard/sectors` | Sector concentration |
| GET | `/dashboard/alerts` | Recent risk alerts |
| POST | `/dashboard/equity` | Push manual equity adjustment |
| POST | `/dashboard/reset` | Operator reset (resets dashboard state — does **not** flatten positions) |

### Admin — circuit breaker

| Method | Path | Notes |
|---|---|---|
| GET  | `/admin/circuit-breaker` | Current breaker state |
| POST | `/admin/circuit-breaker/reset` | Force-close a tripped breaker |
| POST | `/admin/circuit-breaker/open`  | Force-open the global breaker (kill switch) |

`/admin/circuit-breaker/open` is a kill switch — use it to halt all
order submission immediately. The position monitor still runs and exits
positions per its rules.

## Response shapes

- Most read endpoints return Pydantic models from `api/schemas.py`
  (`SolverResponse`, `SolverMetricsResponse`, `ExperimentResponse`,
  `PromotionRecommendationResponse`, …).
- `/search` and `/flows` may return `503` with the standard error envelope
  when their backing data sources are degraded — clients should treat 503 as
  retryable.

## Rate limiting

No application-level rate limiter. Enforce externally (Gateway, reverse proxy,
or ingress) if you need one.

## Local exploration

```bash
# Boot the API alongside the rest of the stack
docker compose up -d api    # if the api service is configured in compose

# Or run the FastAPI app directly during development
uv run uvicorn orion.api.main:app --reload --port 8000

# OpenAPI / Swagger UI
open http://localhost:8000/docs
open http://localhost:8000/redoc
```

## Adding a new endpoint

1. Define the request/response model in `api/schemas.py`.
2. Add the route in `api/main.py` with a tag (`tags=["..."]`).
3. Use the `OrionError` envelope, not freelance HTTP errors, for domain
   failures.
4. Add a test under `tests/api/`.
5. Update this doc — include the path, method, params, and any 5xx semantics.

## Related

- [`system-architecture.md`](system-architecture.md) — where the API sits in
  the data flow
- [`configuration-guide.md`](configuration-guide.md) — `ORION_API_KEY` and
  related vars
- Preserved older spec: `API_REFERENCE.md` (kept as-is, but this file is the
  current reference)
