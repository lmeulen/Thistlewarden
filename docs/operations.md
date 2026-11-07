# Operations, recovery and rollback

Detailed target logging policy, event catalog, and phased implementation are documented in [logging-plan.md](logging-plan.md).

## Readiness and liveness

- `/health`: process liveness; deliberately independent from SQLite.
- `/ready`: SQLite quick check plus immediate write-lock probe.
- `/api/v1/health`: versioned API liveness.

Use `/ready` for container health. HTTP 503 means storage unavailable/corrupt/busy beyond configured timeout.

## Startup order

Production entrypoint:

1. validates environment and local-only topology;
2. acquires process-lifetime database instance lock;
3. migrates schema, creating pre-upgrade backup for existing schema;
4. recovers expired job leases;
5. initializes one shared provider limiter;
6. starts fixed worker threads;
7. starts Waitress with four request threads.

Second process against same database fails before migrations. Shutdown closes HTTP server, waits for workers, closes limiter, then releases lock. Abrupt termination leaves lease to expire; next startup marks recovery warning because repeated provider calls can duplicate cost.

## Backup

Native installation:

```bash
python -m evaluator.cli backup ./backups/evaluator-$(date +%Y%m%d).db
```

PowerShell:

```powershell
python -m evaluator.cli backup ".\backups\evaluator-$(Get-Date -Format yyyyMMdd).db"
```

Docker:

```bash
docker compose exec evaluator python -m evaluator.cli backup /var/lib/thistlewarden/exports/manual-backup.db
```

Copy backup out if needed. Exports volume contains sensitive plaintext run content.

## Restore and rollback

Never overwrite live database in place.

1. Stop app.
2. Restore backup to new file with CLI.
3. Run migration against new path.
4. Run `PRAGMA integrity_check` through startup/readiness.
5. Change `DATABASE_PATH` and restart.
6. Keep old database until acceptance passes.

Docker named-volume rollback requires copying backup into volume under a new filename and changing `DATABASE_PATH`. Do not mount SMB/NFS/CIFS.

## Storage failure

On full disk or permissions loss, writes fail and `/ready` becomes 503. Stop creating runs, free space or repair ownership, preserve database plus `-wal`/`-shm`, then restart. Never delete WAL while app is running.

## Retention

`RUN_RETENTION_DAYS=0` disables automatic cleanup. API/GUI cleanup deletes only terminal runs. Backups and downloaded exports are not managed by retention.
