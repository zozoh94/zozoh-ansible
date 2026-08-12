# qBittorrent maintenance — design

**Date:** 2026-08-12
**Host:** odroid (`enzo@home.zozoh.fr:2222`), Docker stack in `/opt/torrent`
**Target:** container `qbittorrent` (compose service `torrent`)

## Goal

Automatic housekeeping for the `qbittorrent` instance:

1. **Delete** any completed torrent whose ratio **> 2** — remove the torrent **and its files** — but only once the content is confirmed present in the media library (never destroy an un-imported unique copy).
2. **Archive** any completed torrent still in `/downloads` **≥ 14 days after it was added** whose ratio is still **< 2**: move its files from `/downloads` (SSD) to `/downloads-old` (DATA) and keep seeding from there.
3. **Deduplicate on DATA**: a film/series seeding from `/downloads-old` and its library copy under `movies-archive`/`series-archive` are two byte-identical files on the same DATA filesystem. Merge them into a single physical copy via hardlink.

## Environment facts (verified)

- `qbittorrent` runs `network_mode: service:vpn` (gluetun); WebUI `:8080` is not host-published. `WebUI\LocalHostAuth=false` → API calls from `127.0.0.1` inside the VPN namespace need **no credentials** (WebUI password is a PBKDF2 hash, plaintext unknown).
- `DisableAutoTMMByDefault=true`, `DefaultSavePath=/downloads`. No native ratio limits set.
- Filesystems: SSD `/dev/sdb2` (btrfs) holds `/downloads`, `/downloads/movies`, `/downloads/series`, `/downloads/musics`. DATA `/dev/sda1` (btrfs) holds `/downloads-old`, `/downloads/*-archive`. SSD and DATA are separate filesystems (hardlinks cannot cross them).
- radarr/sonarr/lidarr **use hardlinks** (default `CopyUsingHardlinks=true`, unchanged — key absent from each app's `Config` table). On import the library copy is a hardlink of the seed copy on SSD (`nlink ≥ 2`, one physical copy).
- Jellyfin's Films and Séries libraries scan **both** SSD and DATA paths (`/media/ssd/videos/Films` + `/media/DATA/Videos/Films`, idem Séries). Media is visible whether on SSD or DATA.
- On DATA, `/mnt/DATA/Downloads` and `/mnt/DATA/Videos/{Films,Séries}` are plain directories of the **same btrfs subvolume** — **hardlink between them works** (tested: `ln` OK, `cp --reflink` OK).
- Nightly host cron `move_videos.sh` (`0 3 * * *`) does `rsync --remove-source-files` of `/mnt/ssd/videos/{Films,Séries}` → `/mnt/DATA/Videos/{Films,Séries}`, then triggers a Jellyfin rescan via `docker exec jellyfin`.

Container ↔ host path map (relevant subset):

| Container path | Host path | Disk |
|---|---|---|
| `/downloads` | `/mnt/ssd/Downloads` | SSD |
| `/downloads-old` | `/mnt/DATA/Downloads` | DATA |
| `/downloads/movies-archive` | `/mnt/DATA/Videos/Films` | DATA |
| `/downloads/series-archive` | `/mnt/DATA/Videos/Séries` | DATA |

## Design principle

Keep the seed⇄library hardlink intact for as long as the torrent seeds. A plain **`nlink > 1`** check on the torrent's main file then reliably answers "imported / present in library" — no filename matching, no content hashing for the delete decision. This requires one coordinated change to `move_videos.sh` so it does not break the hardlink of a still-seeding file.

## Component 1 — `move_videos.sh`: archive only un-seeded files (host)

Archive only files with **`nlink == 1`** (no torrent seeds them anymore); leave actively-seeded files (`nlink ≥ 2`) on SSD until their torrent is gone. Empty-dir cleanup and Jellyfin rescan unchanged.

Replace, inside `move_files()`:

```bash
rsync --remove-source-files -av "$src/" "$dst/" >> "$LOG" 2>&1
```
with:
```bash
find "$src" -type f -links 1 -printf '%P\0' \
  | rsync -a --remove-source-files --from0 --files-from=- "$src/" "$dst/" >> "$LOG" 2>&1
```

`%P` = path relative to `$src`; NUL-terminated → any filename safe. Existing `find … -type d -empty -delete` and Jellyfin-scan steps stay.

- **Space impact of this change: none.** The seed copy was already on SSD in both schemes; the hardlink shares one inode with the library copy (one physical copy). The change only avoids the redundant DATA copy while seeding. Media stays visible in Jellyfin throughout (both paths scanned).
- Original script backed up before editing.
- TV seasons may temporarily split across SSD/DATA (some episodes seeding, some archived) — cosmetic; Jellyfin scans both.

## Component 2 — `move_videos.sh`: DATA dedup (host, appended)

After the archive moves, deduplicate: for each film/series seed in `/downloads-old`, find its byte-identical copy under the DATA video archives and hardlink them into one physical copy. Runs in the same nightly cron, after Component 1, before the Jellyfin scan.

New function, called after the two `move_files` calls:

```bash
dedup_downloads_old() {
    local seed_dir="/mnt/DATA/Downloads"
    local roots=("/mnt/DATA/Videos/Films" "/mnt/DATA/Videos/Séries")
    find "$seed_dir" -type f -links 1 -print0 | while IFS= read -r -d '' f; do
        local sz fino; sz=$(stat -c %s "$f"); fino=$(stat -c %i "$f")
        local done=""
        for root in "${roots[@]}"; do
            [ -d "$root" ] || continue
            while IFS= read -r -d '' c; do
                [ "$(stat -c %i "$c")" = "$fino" ] && { done=1; break; }   # already same inode
                if cmp -s "$f" "$c"; then
                    if ln -f "$c" "$f.dedup.$$" && mv -f "$f.dedup.$$" "$f"; then
                        echo "$(date '+%F %T') - deduped '$f' -> '$c'" >> "$LOG"
                    fi
                    done=1; break
                fi
            done < <(find "$root" -type f -size "${sz}c" -print0)
            [ -n "$done" ] && break
        done
    done
}
```

- **Correctness/safety:** a hardlink is only made after `cmp -s` proves the files are byte-identical, so the seed's content is unchanged and qBittorrent keeps seeding uninterrupted. Byte-identical files are safe to share regardless of which torrent/movie they belong to.
- **Idempotent:** `-links 1` skips already-deduped seeds (they have `nlink ≥ 2`); an already-shared inode is also skipped explicitly. Atomic replace via temp-link + `mv -f`.
- **Scope:** only files matching a copy under `Films`/`Séries` archives are touched → naturally limited to films/series; other `/downloads-old` files are left alone.
- **I/O cost:** `cmp` reads both files fully on a match. Size prefilter (`find -size ${sz}c`) limits candidates. Bounded by newly-archived items per night; the **first run** processes the existing backlog once (heavier).
- **Interaction with delete rule:** after dedup the `/downloads-old` seed shares its inode with the archive copy (`nlink 2`). When the delete rule later removes that torrent with `deleteFiles=true`, only the `/downloads-old` link is unlinked; the `movies-archive`/`series-archive` copy survives (`nlink 2→1`). Library stays safe.

## Component 3 — sidecar `qbit-maintenance` (compose)

New service in `/opt/torrent/docker-compose.yaml`:

- `image: python:3-alpine`, `network_mode: service:vpn`, `depends_on: [vpn, torrent]`, `restart: always`.
- **Stdlib-only** Python script (`urllib`, `os`, `json`) bind-mounted from the host. No `pip install`.
- Loop: run one pass, then `sleep ${INTERVAL_SECONDS:-86400}`. First pass on start.
- Reaches qBittorrent at `http://127.0.0.1:8080` (same namespace, no auth).

**Mounts:**
- `/mnt/ssd/Downloads:/downloads:ro` — read-only, to `stat` `nlink` of torrent files. The sidecar never writes to data disks; qBittorrent performs every delete/move.
- `/opt/torrent/maintenance:/app` — the script and the persistent `maintenance.log` (logs also go to stdout → `docker logs qbit-maintenance`).

### Per-pass logic

Fetch `GET /api/v2/torrents/info?filter=completed`. Normalise each torrent's `save_path` (strip trailing slash). Evaluate in order:

1. **Delete rule** — if `ratio > DELETE_RATIO` (2.0):
   - `save_path == /downloads-old` → `POST /api/v2/torrents/delete` `deleteFiles=true` (safe by the invariant below — no re-check).
   - `save_path == /downloads` → confirm the **largest file** has `os.stat(path).st_nlink > 1`; if so delete with `deleteFiles=true`, else keep and log `kept: not confirmed in library`.
   - any other `save_path` → ignore (out of scope).
2. **Archive rule** (only if the delete rule did not fire) — if `save_path == /downloads` **and** `(now - added_on) ≥ MOVE_AGE_DAYS*86400` (14 d) **and** `ratio < MOVE_RATIO` (2.0):
   - confirm largest file `nlink > 1` (imported); if so `POST /api/v2/torrents/setLocation` `location=/downloads-old`, else keep in `/downloads` and log `kept in /downloads: not imported`.

**Largest file**: `GET /api/v2/torrents/files?hash=…`; pick the max-`size` entry; full path = `save_path` + `/` + file `name`, mapping 1:1 into the `/downloads` mount. If the path cannot be stat'd → treat as not confirmed → keep + log. Never delete on uncertainty.

### Safety invariant

A torrent enters `/downloads-old` **only** via the archive rule, which requires `nlink > 1` (confirmed imported). Library copies are never deleted — only moved SSD→DATA (Component 1) and possibly hardlinked (Component 2). Therefore a torrent in `/downloads-old` always has a surviving library copy, so deleting it at ratio > 2 without re-check is always safe. (Torrents a user manually places directly in `/downloads-old` bypass this verification — accepted; the rules target the automated flow.)

### Scope

Torrents seeding from any other path (`/downloads/movies`, `/downloads/series`, `/downloads/musics`, `/downloads/books`, …) are never touched. Music (lidarr, stays on SSD) is covered by `nlink` if ever under `/downloads`; books (imported cross-disk to DATA, `nlink == 1`) are intentionally never auto-deleted (safe side).

## Configuration (sidecar env vars, defaults)

| Var | Default | Meaning |
|---|---|---|
| `QBIT_URL` | `http://127.0.0.1:8080` | qBittorrent API base |
| `DELETE_RATIO` | `2.0` | delete when `ratio >` this |
| `MOVE_AGE_DAYS` | `14` | archive when torrent age ≥ this |
| `MOVE_RATIO` | `2.0` | archive when `ratio <` this |
| `DOWNLOADS_PATH` | `/downloads` | active download root |
| `ARCHIVE_PATH` | `/downloads-old` | archive destination |
| `INTERVAL_SECONDS` | `86400` | sleep between passes |
| `DRY_RUN` | `true` | when true: log intended actions, call nothing destructive |

## Rollout / safety

1. Edit `move_videos.sh` (backup first). Verify with `rsync -n` that a seeded file (`nlink ≥ 2`) is skipped while an `nlink == 1` file moves. Verify `dedup_downloads_old` on a known film pair: same inode after run, `cmp` still equal, qBittorrent recheck still OK.
2. Deploy the sidecar with **`DRY_RUN=true`**. Inspect `maintenance.log` / `docker logs`: each torrent prints its would-be action (`WOULD delete`, `WOULD archive`, `kept: …`). Sanity-check against a few hand-verified torrents.
3. Flip `DRY_RUN=false` and recreate only the sidecar. No other container recreated.

## Testing

- **Unit (TDD, local, no qBittorrent):** pure functions with temp files.
  - largest-file selection over a files list.
  - decision function `(ratio, age_days, save_path, nlink) → delete | archive | keep`, table-driven, incl. boundaries: ratio exactly 2 (neither), age exactly 14 d, `/downloads` vs `/downloads-old` vs other, `nlink==1` vs `>1`.
  - `nlink` check: create a hardlink → detected; single file → not.
- **Dedup (local btrfs-agnostic logic):** two byte-identical temp files of equal size in separate dirs → after dedup they share an inode; two same-size different-content files → left untouched (`cmp` differs); already-linked pair → skipped.
- **Integration:** first sidecar pass in `DRY_RUN=true`; confirm log lines match hand-checked expectations for one imported+seeding torrent, one old low-ratio torrent (archive), one recent torrent (stay), one high-ratio imported torrent (delete).

## Files touched

- New: `/opt/torrent/maintenance/qbit_maintenance.py`
- Edit: `/opt/torrent/docker-compose.yaml` (add `qbit-maintenance` service; backup first)
- Edit: `/home/enzo/move_videos.sh` (add `-links 1` filter + `dedup_downloads_old`; backup first)
