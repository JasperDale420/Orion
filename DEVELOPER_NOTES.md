# Developer Notes

Practical notes for working in Orion quickly and safely.

## Gotchas

- Migration work can accidentally duplicate polling responsibilities.
- Missing env vars often fail at startup; check `.env` first.
- Be careful with stage flags (`paper` vs live-like modes).

## Performance Considerations

- Use selective queries for high-volume endpoints.
- Keep expensive scans out of hot request paths.
- Prefer batch writes and idempotent upserts in pipelines.

## Debugging Tips

- Start with service-scoped logs via `docker compose logs -f <service>`.
- Trace request/ingest flow with run IDs and trace IDs in structured logs.
- Validate DB and provider connectivity before deep debugging.

## Historical Context

- Orion is transitioning to Data Gateway + Heber-aligned access/storage responsibilities.
- Several legacy toggles remain for phased decommissioning; remove only when replacements are fully verified.
