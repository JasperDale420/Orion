#!/usr/bin/env bash
# Database Backup for Orion
# Reaches into the running timescaledb container and creates a compressed pg_dump.
# Old backups (> 7 days) are automatically deleted.
# Recommended cron schedule:
# 0 2 * * * /path/to/Orion/scripts/db_backup.sh >> /path/to/Orion/logs/db_backup.log 2>&1

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

BACKUP_DIR="${ROOT_DIR}/backups"
mkdir -p "$BACKUP_DIR"

CONTAINER_NAME="orion_db"
DB_USER="orion"
DB_NAME="orion_db"
TIMESTAMP=$(date -u +"%Y%m%d_%H%M%S")
BACKUP_FILE="${BACKUP_DIR}/${DB_NAME}_${TIMESTAMP}.sql.gz"

echo "=== Orion DB Backup Run at $(date -u +"%Y-%m-%dT%H:%M:%SZ") ==="

# Ensure the container is running
if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    echo "Container $CONTAINER_NAME is not running. Aborting backup."
    exit 1
fi

echo "Running pg_dump on ${DB_NAME}..."

# We use docker exec to run pg_dump inside the container and pipe the output to gzip on the host
docker exec "$CONTAINER_NAME" pg_dump -U "$DB_USER" "$DB_NAME" | gzip > "$BACKUP_FILE"

echo "Backup successful: $BACKUP_FILE"
echo "File size: $(ls -lh "$BACKUP_FILE" | awk '{print $5}')"

# Clean up backups older than 7 days
echo "Cleaning up backups older than 7 days in $BACKUP_DIR..."
# Using find to delete files ending in .sql.gz older than 7 days
find "$BACKUP_DIR" -type f -name "${DB_NAME}_*.sql.gz" -mtime +7 -exec rm {} \;

echo "Backup process complete."
