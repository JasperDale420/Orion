# API Reference

> **See [`api-reference.md`](api-reference.md)** for the current reference —
> this file is an older spec kept as-is for history. Notably, it still lists a
> `GET /search` endpoint that has since been removed (see `CHANGELOG.md`,
> "Delete the LLM solver-evolution machinery").

## Authentication

Most endpoints require header:

- `x-api-key: <ORION_API_KEY>`

If `ORION_API_KEY` is unset, authenticated endpoints return server configuration error.

## Base URL

Default local URL:

- `http://localhost:8000`

## Endpoints

### System

#### `GET /`

Description: Basic API metadata.

#### `GET /health`

Description: Health status check.

Response example:

```json
{"status":"ok"}
```

### Solvers

#### `GET /solvers`

Description: List registered solvers.

Parameters:
- `skip` (int, optional)
- `limit` (int, optional)
- `active_only` (bool, optional)

#### `GET /solvers/{solver_id}`

Description: Fetch single solver by ID.

### Metrics & Experiments

#### `GET /metrics`

Description: Query solver metrics.

Parameters:
- `solver_id` (string, optional)
- `dataset_tag` (string, optional)
- `limit` (int, optional)

#### `GET /experiments`

Description: List meta-search experiments.

### Promotions

#### `GET /promotions`

Description: List promotion recommendations.

#### `POST /promotions/{recommendation_id}/approve`

Description: Approve pending recommendation.

Parameters:
- `reviewed_by` (query, required)

#### `POST /promotions/{recommendation_id}/reject`

Description: Reject pending recommendation.

Parameters:
- `reviewed_by` (query, required)

### Search & Market Data

#### `GET /search`

Description: RAG/hybrid search endpoint with metadata filters.
Returns `503` when the vector store or embedding backend is unavailable.

#### `GET /events/{event_id}`

Description: Fetch Bronze event envelope by ID.

#### `GET /candidates/{candidate_id}`

Description: Fetch candidate trade record by ID.

#### `GET /rollups`

Description: Query rollup rows by ticker/period.

#### `GET /rollups/{ticker}/{period}/{timestamp_utc}`

Description: Fetch one rollup row.

#### `GET /flows`

Description: Query options flow rows with filters.
Returns `503` when Heber flow reads fail or the backing data source is unavailable.

### Dashboard

#### `GET /dashboard/summary`
#### `GET /dashboard/positions`
#### `GET /dashboard/sectors`
#### `GET /dashboard/alerts`
#### `POST /dashboard/equity`
#### `POST /dashboard/reset`

Description: Portfolio/risk dashboard read/write endpoints.

## Rate Limiting

No explicit application-level rate limiter is currently documented in this service. If needed, enforce externally (gateway, proxy, or ingress).

## Error Format

Two common response shapes:

1. Standard FastAPI HTTP errors:

```json
{"detail":"..."}
```

2. Orion domain error envelope:

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
