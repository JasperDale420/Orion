# Rollback Strategy

This document describes the rollback procedures for Orion deployments.

## Docker-Based Deployments

### Version Pinning Strategy

All Docker images are tagged with:
1. **Git SHA**: `orion:abc1234` - Immutable, tied to specific commit
2. **Semantic version**: `orion:v1.2.3` - Release versions
3. **Latest**: `orion:latest` - Current stable (not for production)

**Best practice**: Always deploy specific SHA or version tags in production.

### Quick Rollback Procedure

```bash
# 1. Identify the previous working version
docker images | grep orion

# 2. Stop current containers
docker compose down

# 3. Update the compose image tag to previous version
# Or set via environment variable:
export ORION_VERSION=<previous-sha-or-tag>

# 4. Start with previous version
docker compose up -d
```

### Rolling Back to Specific Commit

```bash
# Find the commit that was last deployed successfully
git log --oneline -10

# Checkout that commit
git checkout <commit-sha>

# Rebuild and deploy
docker compose build
docker compose up -d
```

## Database Migrations

### Before Rolling Back

1. **Check for irreversible migrations**: Review `alembic/versions/` for any data-destructive migrations
2. **Backup current state**: `pg_dump orion > backup_$(date +%Y%m%d_%H%M%S).sql`

### Rolling Back Migrations

```bash
# List current migration
alembic current

# View migration history
alembic history

# Rollback one migration
alembic downgrade -1

# Rollback to specific revision
alembic downgrade <revision-id>
```

**⚠️ Warning**: Some migrations may be irreversible (data deleted, columns dropped). Always review migration scripts before downgrading.

## Reduce Scope Without Full Rollback

For non-critical issues, narrow what the system does instead of a full rollback:

```bash
# Revert to paper mode (the safe default): execution stops treating the run as
# live, so no live orders are submitted (see execution_engine `_is_live`).
export ORION_STAGE=paper

# Restart services
docker compose restart
```

Risk limits (daily loss, max positions, Greeks, sector concentration) are
configured via the `ORION_RISK_*` environment variables — see `RiskSettings`
in `src/orion/config.py`.

## Rollback Checklist

- [ ] Identify the failure (logs, metrics, alerts)
- [ ] Determine rollback scope (full vs reduce-scope via `ORION_STAGE=paper`)
- [ ] Notify stakeholders
- [ ] Backup current database state
- [ ] Execute rollback procedure
- [ ] Verify service health
- [ ] Document incident

## Emergency Contacts

| Role | Responsibility |
|------|----------------|
| On-call engineer | Initial response, rollback execution |
| Tech lead | Approval for production changes |
| Database admin | Migration rollbacks |
