# torrent-maintenance

Sidecar for the odroid `qbittorrent` stack. See design:
`docs/superpowers/specs/2026-08-12-qbittorrent-maintenance-design.md`.

## Rules (daily 05:00 pass)
- ratio > 2 + confirmed in library (`nlink>1`, or path `/downloads-old`) → delete + files.
- in `/downloads`, age ≥ 14 d, ratio < 2, imported (`nlink>1`) → move to `/downloads-old`.

## Deploy
1. `scp qbit_maintenance.py enzo@home.zozoh.fr:/tmp/ && ssh … sudo install -D -m755 /tmp/qbit_maintenance.py /opt/torrent/maintenance/qbit_maintenance.py`
2. Add the `qbit-maintenance` service to `/opt/torrent/docker-compose.yaml` (see plan Task 9).
3. First run: `DRY_RUN=true` (default). Inspect: `docker logs qbit-maintenance` and `/opt/torrent/maintenance/maintenance.log`, or force a pass now: `docker exec qbit-maintenance python /app/qbit_maintenance.py --once`.
4. When logs look right, set `DRY_RUN=false` in the compose service env and `docker compose up -d qbit-maintenance`.

## Env
`DELETE_RATIO=2.0 MOVE_RATIO=2.0 MOVE_AGE_DAYS=14 DOWNLOADS_PATH=/downloads ARCHIVE_PATH=/downloads-old QBIT_URL=http://127.0.0.1:8080 RUN_AT_HOUR=5 DRY_RUN=true`

## Coordinated host change
`move_videos.sh` (host cron, 03:00) is edited to (a) archive SSD→DATA only files with `nlink==1` (preserves the seed⇄library hardlink while seeding, so the sidecar's `nlink` check is reliable), and (b) dedup `/downloads-old` film/series seeds against their `movies-archive`/`series-archive` copies via `cmp` + hardlink. Reference copy: `move_videos.sh` in this directory.

## Tests
`python3 -m venv .venv && .venv/bin/pip install pytest && .venv/bin/pytest tests/ -v`
(this machine lacks `python3-venv`; `virtualenv .venv` or `uv venv` work too.)
