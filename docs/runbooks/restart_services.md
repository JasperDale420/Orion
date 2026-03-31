# Restart Services Runbook

## Prerequisites
- Docker and Docker Compose installed
- Access to the Orion repository

## Restart All Services

```bash
cd /path/to/Orion
docker compose down
docker compose up -d --build
```

## Restart Specific Service

```bash
# List running services
docker compose ps

# Restart a specific service
docker compose restart <service-name>

# Example: restart ingestion
docker compose restart ingestion
```

## Verify Services Running

```bash
# Check container status
docker compose ps

# Check logs for a service
docker compose logs -f --tail=100 <service-name>
```

## Health Check Endpoints

| Service | Health Endpoint |
|---------|-----------------|
| API | `GET /health` |
| Ingestion | Logs `IngestionService.run starting` |

## Rollback to Previous Version

```bash
# Pull specific tag
git checkout <tag-or-commit>
docker compose down
docker compose up -d --build
```
