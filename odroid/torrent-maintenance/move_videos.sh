#!/bin/bash
# Daily move script: SSD -> DATA drive
# Moves new files from SSD to DATA for Films and Séries
# After moving, triggers a Jellyfin library scan to update the database

LOG="/var/tmp/video-move.log"
SRC_BASE="/mnt/ssd/videos"
DST_BASE="/mnt/DATA/Videos"
JELLYFIN_API_KEY="0a5ea1f5782540fe879514bcf3d81163"

move_files() {
    local src="$1"
    local dst="$2"
    if [ ! -d "$src" ]; then
        echo "$(date '+%Y-%m-%d %H:%M:%S') - Source directory '$src' does not exist, skipping." >> "$LOG"
        return 1
    fi
    if [ ! -d "$dst" ]; then
        echo "$(date '+%Y-%m-%d %H:%M:%S') - Destination directory '$dst' does not exist, skipping." >> "$LOG"
        return 1
    fi
    echo "$(date '+%Y-%m-%d %H:%M:%S') - Moving files from '$src' to '$dst'" >> "$LOG"
    # Only archive files no longer seeded by a torrent (nlink == 1). Actively
    # seeded imports are hardlinked (nlink >= 2) and stay on SSD until their
    # torrent is gone, so the sidecar's nlink library-check stays reliable.
    find "$src" -type f -links 1 -printf '%P\0' \
      | rsync -a --remove-source-files --from0 --files-from=- "$src/" "$dst/" >> "$LOG" 2>&1
    # Clean up empty directories left behind in source
    find "$src" -mindepth 1 -type d -empty -delete >> "$LOG" 2>&1
    echo "$(date '+%Y-%m-%d %H:%M:%S') - Done moving '$src' -> '$dst'" >> "$LOG"
}

# Deduplicate DATA: a film/series seed in /downloads-old and its library copy
# under movies-archive/series-archive are byte-identical files on the same DATA
# btrfs subvolume. Merge them into one physical copy via hardlink (cmp-verified).
dedup_downloads_old() {
    local seed_dir="/mnt/DATA/Downloads"
    local roots=("/mnt/DATA/Videos/Films" "/mnt/DATA/Videos/Séries")
    echo "$(date '+%Y-%m-%d %H:%M:%S') - dedup /downloads-old start" >> "$LOG"
    find "$seed_dir" -type f -links 1 -print0 | while IFS= read -r -d '' f; do
        local sz fino done_flag
        sz=$(stat -c %s "$f"); fino=$(stat -c %i "$f"); done_flag=""
        for root in "${roots[@]}"; do
            [ -d "$root" ] || continue
            while IFS= read -r -d '' c; do
                if [ "$(stat -c %i "$c")" = "$fino" ]; then done_flag=1; break; fi
                if cmp -s "$f" "$c"; then
                    if ln -f "$c" "$f.dedup.$$" && mv -f "$f.dedup.$$" "$f"; then
                        echo "$(date '+%Y-%m-%d %H:%M:%S') - deduped '$f' -> '$c'" >> "$LOG"
                    fi
                    done_flag=1; break
                fi
            done < <(find "$root" -type f -size "${sz}c" -print0)
            [ -n "$done_flag" ] && break
        done
    done
    echo "$(date '+%Y-%m-%d %H:%M:%S') - dedup /downloads-old done" >> "$LOG"
}

move_files "$SRC_BASE/Films" "$DST_BASE/Films"
move_files "$SRC_BASE/Séries" "$DST_BASE/Séries"

dedup_downloads_old

# Trigger Jellyfin library scan to update database after file moves
echo "$(date '+%Y-%m-%d %H:%M:%S') - Triggering Jellyfin library scan" >> "$LOG"
docker exec jellyfin curl -s -X POST -H "X-Emby-Token: $JELLYFIN_API_KEY" http://localhost:8096/Library/Refresh >> "$LOG" 2>&1
echo "$(date '+%Y-%m-%d %H:%M:%S') - Jellyfin library scan triggered" >> "$LOG"
