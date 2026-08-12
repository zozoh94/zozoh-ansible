# qBittorrent Maintenance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automate qBittorrent housekeeping on the odroid — delete completed torrents past ratio 2 (only when confirmed in the media library), archive stale low-ratio torrents from SSD to DATA, and deduplicate the resulting DATA copies.

**Architecture:** A stdlib-only Python sidecar container (in the VPN network namespace, reaching qBittorrent at `127.0.0.1:8080` with no auth) runs one pass daily at 05:00 and decides per torrent using the filesystem hardlink count (`nlink`) as the "is it in the library?" signal. Two coordinated edits to the host cron `move_videos.sh` preserve those hardlinks during seeding and dedup the DATA copies. Full spec: `docs/superpowers/specs/2026-08-12-qbittorrent-maintenance-design.md`.

**Tech Stack:** Python 3 (stdlib only: `urllib`, `os`, `json`, `time`, `datetime`), pytest (dev only, via venv), Bash, Docker Compose, btrfs hardlinks.

## Global Constraints

- **Sidecar script: Python standard library only.** No `pip install` at runtime, no third-party imports in `qbit_maintenance.py`. (pytest is dev-time only.)
- **Read-only on data disks from the sidecar.** The sidecar only `stat`s files; every destructive action (delete, move) is performed by qBittorrent via its API. `move_videos.sh` is the only thing that writes to the data disks.
- **Never delete on uncertainty.** If a torrent's main file cannot be `stat`'d, or `nlink` is not `> 1` for a `/downloads` torrent, keep it and log.
- **`DRY_RUN=true` by default.** First deployment must run in dry-run; flip to `false` only after log review.
- **Thresholds (exact):** delete when `ratio > 2.0`; archive when `save_path == /downloads` AND `age ≥ 14` days AND `ratio < 2.0`. Ratio exactly `2.0` triggers neither.
- **Verified environment facts** (do not re-derive): `WebUI\LocalHostAuth=false` (no auth from localhost); radarr/sonarr/lidarr hardlink on import; SSD=`/dev/sdb2`, DATA=`/dev/sda1`, both btrfs; `/mnt/DATA/Downloads` ↔ `/mnt/DATA/Videos/*` hardlink works (same subvolume); Jellyfin scans both SSD and DATA video paths.

## File Structure

Developed and unit-tested locally in the repo (version-controlled), then deployed to the odroid:

- `odroid/torrent-maintenance/qbit_maintenance.py` — the sidecar script. Pure decision/helpers separated from I/O for testability.
- `odroid/torrent-maintenance/tests/test_qbit_maintenance.py` — pytest unit tests.
- `odroid/torrent-maintenance/README.md` — deploy notes (paths, env, dry-run flip).

Deployed to the odroid (not in repo; edited in place with sudo, backups first):

- `/opt/torrent/maintenance/qbit_maintenance.py` — copy of the script.
- `/opt/torrent/docker-compose.yaml` — add `qbit-maintenance` service.
- `/home/enzo/move_videos.sh` — add `-links 1` filter + `dedup_downloads_old`.

**Script internal structure** (`qbit_maintenance.py`), so the pure parts are unit-testable and the I/O is thin:

- `load_config(environ) -> Config` — read env vars with defaults.
- `largest_file(files) -> dict` — pick the max-`size` entry from a torrent's file list.
- `file_nlink(path) -> int | None` — `os.stat(path).st_nlink`, or `None` on `OSError`.
- `decide(*, ratio, age_seconds, save_path, nlink, cfg) -> (action, reason)` — pure rule engine returning `action ∈ {"delete","archive","keep"}`.
- `seconds_until_hour(target_hour, now) -> int` — seconds from `now` (a `datetime`) to the next local `target_hour:00`.
- `QbitClient` — thin urllib wrapper: `completed()`, `files(hash)`, `delete(hash)`, `set_location(hash, location)`. Takes an injectable `request` callable for testing.
- `run_once(client, cfg, stat_nlink, log) -> None` — orchestration; `stat_nlink` and `log` injected for tests.
- `main(argv, environ)` — parse `--once`, build config/client, loop with `seconds_until_hour` sleeps (or one pass for `--once`).

---

### Task 1: Project scaffold + `decide()` rule engine (TDD)

**Files:**
- Create: `odroid/torrent-maintenance/qbit_maintenance.py`
- Create: `odroid/torrent-maintenance/tests/test_qbit_maintenance.py`
- Create: `odroid/torrent-maintenance/tests/__init__.py` (empty)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `Config` — a `dataclass` with fields `delete_ratio: float=2.0`, `move_ratio: float=2.0`, `move_age_days: int=14`, `downloads_path: str="/downloads"`, `archive_path: str="/downloads-old"`, `qbit_url: str="http://127.0.0.1:8080"`, `run_at_hour: int=5`, `dry_run: bool=True`.
  - `decide(*, ratio: float, age_seconds: float, save_path: str, nlink: int | None, cfg: Config) -> tuple[str, str]` returning `(action, reason)` with `action` one of `"delete"`, `"archive"`, `"keep"`.

- [ ] **Step 1: Set up the dev environment**

```bash
mkdir -p odroid/torrent-maintenance/tests
cd odroid/torrent-maintenance
python3 -m venv .venv
.venv/bin/pip install -q pytest
touch tests/__init__.py
```

Add `odroid/torrent-maintenance/.venv/` to the repo's `.gitignore` (create the file if absent, append the line).

- [ ] **Step 2: Write the failing tests for `decide()`**

Create `odroid/torrent-maintenance/tests/test_qbit_maintenance.py`:

```python
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from qbit_maintenance import Config, decide

CFG = Config()
DAY = 86400

def d(**kw):
    base = dict(ratio=0.0, age_seconds=0, save_path="/downloads", nlink=2, cfg=CFG)
    base.update(kw)
    return decide(**base)[0]

def test_delete_when_ratio_above_2_and_in_library():
    assert d(ratio=2.1, save_path="/downloads", nlink=2) == "delete"

def test_keep_when_ratio_above_2_but_not_in_library():
    assert d(ratio=2.1, save_path="/downloads", nlink=1) == "keep"

def test_keep_when_ratio_above_2_but_unstatable():
    assert d(ratio=2.1, save_path="/downloads", nlink=None) == "keep"

def test_delete_downloads_old_without_nlink_check():
    assert d(ratio=2.1, save_path="/downloads-old", nlink=1) == "delete"

def test_ignore_other_paths_even_above_ratio():
    assert d(ratio=5.0, save_path="/downloads/movies", nlink=1) == "keep"

def test_archive_old_low_ratio_imported():
    assert d(ratio=1.0, age_seconds=15*DAY, save_path="/downloads", nlink=2) == "archive"

def test_no_archive_before_14_days():
    assert d(ratio=1.0, age_seconds=13*DAY, save_path="/downloads", nlink=2) == "keep"

def test_no_archive_when_not_imported():
    assert d(ratio=1.0, age_seconds=20*DAY, save_path="/downloads", nlink=1) == "keep"

def test_ratio_exactly_2_neither_deletes_nor_archives():
    assert d(ratio=2.0, age_seconds=20*DAY, save_path="/downloads", nlink=2) == "keep"

def test_archive_only_from_downloads_not_downloads_old():
    assert d(ratio=1.0, age_seconds=20*DAY, save_path="/downloads-old", nlink=2) == "keep"

def test_save_path_trailing_slash_normalised():
    assert d(ratio=2.1, save_path="/downloads/", nlink=2) == "delete"
```

- [ ] **Step 3: Run the tests to verify they fail**

```bash
cd odroid/torrent-maintenance && .venv/bin/pytest tests/ -v
```
Expected: FAIL — `ImportError: cannot import name 'Config'` (module/functions not yet defined).

- [ ] **Step 4: Implement `Config` and `decide()`**

Create `odroid/torrent-maintenance/qbit_maintenance.py`:

```python
"""qBittorrent maintenance sidecar. Python stdlib only."""
from dataclasses import dataclass


@dataclass
class Config:
    delete_ratio: float = 2.0
    move_ratio: float = 2.0
    move_age_days: int = 14
    downloads_path: str = "/downloads"
    archive_path: str = "/downloads-old"
    qbit_url: str = "http://127.0.0.1:8080"
    run_at_hour: int = 5
    dry_run: bool = True


def _norm(path):
    p = path.rstrip("/")
    return p if p else "/"


def decide(*, ratio, age_seconds, save_path, nlink, cfg):
    """Return (action, reason). action in {'delete','archive','keep'}."""
    sp = _norm(save_path)
    imported = nlink is not None and nlink > 1

    if ratio > cfg.delete_ratio:
        if sp == _norm(cfg.archive_path):
            return ("delete", "ratio>max in archive area")
        if sp == _norm(cfg.downloads_path):
            if imported:
                return ("delete", "ratio>max, confirmed in library")
            return ("keep", "ratio>max but not confirmed in library")
        return ("keep", "ratio>max but out-of-scope path")

    if (sp == _norm(cfg.downloads_path)
            and age_seconds >= cfg.move_age_days * 86400
            and ratio < cfg.move_ratio):
        if imported:
            return ("archive", "old and low ratio, confirmed imported")
        return ("keep", "old and low ratio but not imported")

    return ("keep", "no rule applies")
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
cd odroid/torrent-maintenance && .venv/bin/pytest tests/ -v
```
Expected: PASS (11 passed).

- [ ] **Step 6: Commit**

```bash
git add odroid/torrent-maintenance/qbit_maintenance.py odroid/torrent-maintenance/tests/ .gitignore
git commit -m "feat(torrent-maintenance): decision rule engine with tests"
```

---

### Task 2: `largest_file()`, `file_nlink()`, `load_config()` helpers (TDD)

**Files:**
- Modify: `odroid/torrent-maintenance/qbit_maintenance.py`
- Modify: `odroid/torrent-maintenance/tests/test_qbit_maintenance.py`

**Interfaces:**
- Consumes: `Config` (Task 1).
- Produces:
  - `largest_file(files: list[dict]) -> dict | None` — the entry with the greatest `size`; `None` if list empty.
  - `file_nlink(path: str) -> int | None` — `os.stat(path).st_nlink`, or `None` on `OSError`.
  - `load_config(environ: dict) -> Config` — parse `DELETE_RATIO`, `MOVE_RATIO`, `MOVE_AGE_DAYS`, `DOWNLOADS_PATH`, `ARCHIVE_PATH`, `QBIT_URL`, `RUN_AT_HOUR`, `DRY_RUN` (truthy = `"true"/"1"/"yes"` case-insensitive), each falling back to the `Config` default.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_qbit_maintenance.py`:

```python
import tempfile
from qbit_maintenance import largest_file, file_nlink, load_config

def test_largest_file_picks_max_size():
    files = [{"name": "a", "size": 10}, {"name": "b", "size": 99}, {"name": "c", "size": 5}]
    assert largest_file(files)["name"] == "b"

def test_largest_file_empty_is_none():
    assert largest_file([]) is None

def test_file_nlink_single_file_is_1():
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "f")
        open(p, "w").close()
        assert file_nlink(p) == 1

def test_file_nlink_hardlink_is_2():
    with tempfile.TemporaryDirectory() as td:
        a = os.path.join(td, "a"); b = os.path.join(td, "b")
        open(a, "w").close(); os.link(a, b)
        assert file_nlink(a) == 2

def test_file_nlink_missing_is_none():
    assert file_nlink("/no/such/path/xyz") is None

def test_load_config_defaults():
    cfg = load_config({})
    assert cfg.delete_ratio == 2.0 and cfg.run_at_hour == 5 and cfg.dry_run is True

def test_load_config_overrides():
    cfg = load_config({"DELETE_RATIO": "3", "RUN_AT_HOUR": "5", "DRY_RUN": "false",
                       "MOVE_AGE_DAYS": "7", "ARCHIVE_PATH": "/x"})
    assert cfg.delete_ratio == 3.0 and cfg.dry_run is False
    assert cfg.move_age_days == 7 and cfg.archive_path == "/x"
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd odroid/torrent-maintenance && .venv/bin/pytest tests/ -v -k "largest or nlink or load_config"
```
Expected: FAIL — `ImportError` for the new names.

- [ ] **Step 3: Implement the helpers**

Add to `qbit_maintenance.py` (top: add `import os`):

```python
import os


def largest_file(files):
    if not files:
        return None
    return max(files, key=lambda f: f.get("size", 0))


def file_nlink(path):
    try:
        return os.stat(path).st_nlink
    except OSError:
        return None


def _as_bool(v):
    return str(v).strip().lower() in ("true", "1", "yes")


def load_config(environ):
    c = Config()
    return Config(
        delete_ratio=float(environ.get("DELETE_RATIO", c.delete_ratio)),
        move_ratio=float(environ.get("MOVE_RATIO", c.move_ratio)),
        move_age_days=int(environ.get("MOVE_AGE_DAYS", c.move_age_days)),
        downloads_path=environ.get("DOWNLOADS_PATH", c.downloads_path),
        archive_path=environ.get("ARCHIVE_PATH", c.archive_path),
        qbit_url=environ.get("QBIT_URL", c.qbit_url),
        run_at_hour=int(environ.get("RUN_AT_HOUR", c.run_at_hour)),
        dry_run=_as_bool(environ.get("DRY_RUN", "true")),
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd odroid/torrent-maintenance && .venv/bin/pytest tests/ -v
```
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add odroid/torrent-maintenance/
git commit -m "feat(torrent-maintenance): file/config helpers with tests"
```

---

### Task 3: `seconds_until_hour()` scheduler helper (TDD)

**Files:**
- Modify: `odroid/torrent-maintenance/qbit_maintenance.py`
- Modify: `odroid/torrent-maintenance/tests/test_qbit_maintenance.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `seconds_until_hour(target_hour: int, now: datetime) -> int` — seconds from `now` to the next `target_hour:00:00` in `now`'s own (naive local) clock; if `now` is exactly at or past `target_hour` today, returns the wait until tomorrow's occurrence. Always in `(0, 86400]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_qbit_maintenance.py`:

```python
from datetime import datetime
from qbit_maintenance import seconds_until_hour

def test_seconds_until_hour_same_day():
    now = datetime(2026, 8, 12, 3, 0, 0)   # 03:00, target 05:00
    assert seconds_until_hour(5, now) == 2 * 3600

def test_seconds_until_hour_wraps_to_tomorrow():
    now = datetime(2026, 8, 12, 6, 0, 0)   # 06:00, target 05:00 -> next day
    assert seconds_until_hour(5, now) == 23 * 3600

def test_seconds_until_hour_exactly_on_hour_waits_full_day():
    now = datetime(2026, 8, 12, 5, 0, 0)   # exactly 05:00 -> next 05:00
    assert seconds_until_hour(5, now) == 24 * 3600
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd odroid/torrent-maintenance && .venv/bin/pytest tests/ -v -k seconds_until
```
Expected: FAIL — `ImportError: cannot import name 'seconds_until_hour'`.

- [ ] **Step 3: Implement `seconds_until_hour()`**

Add to `qbit_maintenance.py` (top: add `from datetime import datetime, timedelta`):

```python
from datetime import datetime, timedelta


def seconds_until_hour(target_hour, now):
    target = now.replace(hour=target_hour, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return int((target - now).total_seconds())
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd odroid/torrent-maintenance && .venv/bin/pytest tests/ -v -k seconds_until
```
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add odroid/torrent-maintenance/
git commit -m "feat(torrent-maintenance): fixed-hour scheduler helper with tests"
```

---

### Task 4: `QbitClient` urllib wrapper (TDD with injected request)

**Files:**
- Modify: `odroid/torrent-maintenance/qbit_maintenance.py`
- Modify: `odroid/torrent-maintenance/tests/test_qbit_maintenance.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `QbitClient(base_url: str, request=None)` with:
  - `completed() -> list[dict]` — GET `/api/v2/torrents/info?filter=completed`, parsed JSON.
  - `files(torrent_hash: str) -> list[dict]` — GET `/api/v2/torrents/files?hash=…`, parsed JSON.
  - `delete(torrent_hash: str) -> None` — POST `/api/v2/torrents/delete` with `hashes=<hash>&deleteFiles=true`.
  - `set_location(torrent_hash: str, location: str) -> None` — POST `/api/v2/torrents/setLocation` with `hashes=<hash>&location=<loc>`.
  - The injectable `request(method, url, data) -> bytes` defaults to a urllib implementation; tests pass a recorder.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_qbit_maintenance.py`:

```python
import json as _json
from qbit_maintenance import QbitClient

class Recorder:
    def __init__(self, response=b"[]"):
        self.calls = []
        self.response = response
    def __call__(self, method, url, data):
        self.calls.append((method, url, data))
        return self.response

def test_completed_builds_get_and_parses_json():
    rec = Recorder(_json.dumps([{"hash": "AB", "ratio": 3.0}]).encode())
    c = QbitClient("http://x:8080", request=rec)
    out = c.completed()
    assert out[0]["hash"] == "AB"
    method, url, data = rec.calls[0]
    assert method == "GET" and url.endswith("/api/v2/torrents/info?filter=completed")
    assert data is None

def test_files_includes_hash_in_query():
    rec = Recorder(b"[]")
    QbitClient("http://x:8080", request=rec).files("DEAD")
    method, url, data = rec.calls[0]
    assert method == "GET" and "hash=DEAD" in url

def test_delete_posts_deletefiles_true():
    rec = Recorder(b"")
    QbitClient("http://x:8080", request=rec).delete("H1")
    method, url, data = rec.calls[0]
    assert method == "POST" and url.endswith("/api/v2/torrents/delete")
    assert b"hashes=H1" in data and b"deleteFiles=true" in data

def test_set_location_posts_hash_and_location():
    rec = Recorder(b"")
    QbitClient("http://x:8080", request=rec).set_location("H2", "/downloads-old")
    method, url, data = rec.calls[0]
    assert method == "POST" and url.endswith("/api/v2/torrents/setLocation")
    assert b"hashes=H2" in data and b"location=%2Fdownloads-old" in data
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd odroid/torrent-maintenance && .venv/bin/pytest tests/ -v -k "completed or files_includes or delete_posts or set_location"
```
Expected: FAIL — `ImportError: cannot import name 'QbitClient'`.

- [ ] **Step 3: Implement `QbitClient`**

Add to `qbit_maintenance.py` (top: add `import json`, `from urllib import request as _urlreq, parse as _urlparse`):

```python
import json
from urllib import request as _urlreq, parse as _urlparse


def _default_request(method, url, data):
    req = _urlreq.Request(url, data=data, method=method)
    with _urlreq.urlopen(req, timeout=30) as resp:
        return resp.read()


class QbitClient:
    def __init__(self, base_url, request=None):
        self.base = base_url.rstrip("/")
        self._request = request or _default_request

    def _get_json(self, path):
        raw = self._request("GET", self.base + path, None)
        return json.loads(raw or b"[]")

    def _post(self, path, fields):
        data = _urlparse.urlencode(fields).encode()
        self._request("POST", self.base + path, data)

    def completed(self):
        return self._get_json("/api/v2/torrents/info?filter=completed")

    def files(self, torrent_hash):
        return self._get_json("/api/v2/torrents/files?hash=" + _urlparse.quote(torrent_hash))

    def delete(self, torrent_hash):
        self._post("/api/v2/torrents/delete",
                   {"hashes": torrent_hash, "deleteFiles": "true"})

    def set_location(self, torrent_hash, location):
        self._post("/api/v2/torrents/setLocation",
                   {"hashes": torrent_hash, "location": location})
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd odroid/torrent-maintenance && .venv/bin/pytest tests/ -v
```
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add odroid/torrent-maintenance/
git commit -m "feat(torrent-maintenance): qBittorrent API client with tests"
```

---

### Task 5: `run_once()` orchestration (TDD with stub client)

**Files:**
- Modify: `odroid/torrent-maintenance/qbit_maintenance.py`
- Modify: `odroid/torrent-maintenance/tests/test_qbit_maintenance.py`

**Interfaces:**
- Consumes: `Config`, `decide`, `largest_file`, `QbitClient` (Tasks 1–4).
- Produces: `run_once(client, cfg, now_epoch, stat_nlink, log) -> None` where `client` exposes `completed()/files()/delete()/set_location()`, `now_epoch: float` is the current unix time (injected), `stat_nlink(path)->int|None` maps a container file path to its `nlink` (injected; defaults to `file_nlink`), and `log(msg)` is injected. For each completed torrent it computes the largest file's path (`save_path + "/" + name`), calls `decide`, and — unless `cfg.dry_run` — calls `client.delete(hash)` or `client.set_location(hash, cfg.archive_path)`. In dry-run it only logs `WOULD delete/archive/keep`. `age_seconds = now_epoch - torrent["added_on"]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_qbit_maintenance.py`:

```python
from qbit_maintenance import run_once

class StubClient:
    def __init__(self, torrents, files_by_hash):
        self._t = torrents
        self._f = files_by_hash
        self.deleted = []
        self.moved = []
    def completed(self):
        return self._t
    def files(self, h):
        return self._f[h]
    def delete(self, h):
        self.deleted.append(h)
    def set_location(self, h, loc):
        self.moved.append((h, loc))

NOW = 1_000_000_000.0

def _run(torrents, files, nlink_map, dry_run):
    client = StubClient(torrents, files)
    cfg = Config(dry_run=dry_run)
    logs = []
    run_once(client, cfg, NOW,
             stat_nlink=lambda p: nlink_map.get(p),
             log=logs.append)
    return client, logs

def test_run_once_deletes_high_ratio_in_library():
    t = [{"hash": "H", "ratio": 3.0, "added_on": NOW, "save_path": "/downloads"}]
    f = {"H": [{"name": "movie.mkv", "size": 100}]}
    client, _ = _run(t, f, {"/downloads/movie.mkv": 2}, dry_run=False)
    assert client.deleted == ["H"] and client.moved == []

def test_run_once_keeps_high_ratio_not_in_library():
    t = [{"hash": "H", "ratio": 3.0, "added_on": NOW, "save_path": "/downloads"}]
    f = {"H": [{"name": "movie.mkv", "size": 100}]}
    client, _ = _run(t, f, {"/downloads/movie.mkv": 1}, dry_run=False)
    assert client.deleted == [] and client.moved == []

def test_run_once_archives_old_low_ratio():
    added = NOW - 20 * 86400
    t = [{"hash": "H", "ratio": 0.5, "added_on": added, "save_path": "/downloads"}]
    f = {"H": [{"name": "movie.mkv", "size": 100}]}
    client, _ = _run(t, f, {"/downloads/movie.mkv": 2}, dry_run=False)
    assert client.moved == [("H", "/downloads-old")] and client.deleted == []

def test_run_once_deletes_downloads_old_high_ratio_without_stat():
    t = [{"hash": "H", "ratio": 9.0, "added_on": NOW, "save_path": "/downloads-old"}]
    f = {"H": [{"name": "movie.mkv", "size": 100}]}
    client, _ = _run(t, f, {}, dry_run=False)   # nlink not consulted
    assert client.deleted == ["H"]

def test_run_once_dry_run_performs_no_actions_but_logs():
    t = [{"hash": "H", "ratio": 3.0, "added_on": NOW, "save_path": "/downloads"}]
    f = {"H": [{"name": "movie.mkv", "size": 100}]}
    client, logs = _run(t, f, {"/downloads/movie.mkv": 2}, dry_run=True)
    assert client.deleted == [] and client.moved == []
    assert any("WOULD delete" in m for m in logs)

def test_run_once_picks_largest_file_for_nlink():
    t = [{"hash": "H", "ratio": 3.0, "added_on": NOW, "save_path": "/downloads"}]
    f = {"H": [{"name": "sample.mkv", "size": 1}, {"name": "main.mkv", "size": 999}]}
    # only the large file is hardlinked; the small one is not
    client, _ = _run(t, f, {"/downloads/main.mkv": 2, "/downloads/sample.mkv": 1}, dry_run=False)
    assert client.deleted == ["H"]
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd odroid/torrent-maintenance && .venv/bin/pytest tests/ -v -k run_once
```
Expected: FAIL — `ImportError: cannot import name 'run_once'`.

- [ ] **Step 3: Implement `run_once()`**

Add to `qbit_maintenance.py`:

```python
def run_once(client, cfg, now_epoch, stat_nlink=None, log=print):
    stat_nlink = stat_nlink or file_nlink
    for t in client.completed():
        h = t["hash"]
        save_path = t.get("save_path", "")
        ratio = float(t.get("ratio", 0.0))
        age_seconds = now_epoch - float(t.get("added_on", now_epoch))

        biggest = largest_file(client.files(h))
        if biggest is None:
            log(f"keep {h}: no files listed")
            continue
        path = save_path.rstrip("/") + "/" + biggest["name"]
        nlink = stat_nlink(path)

        action, reason = decide(ratio=ratio, age_seconds=age_seconds,
                                save_path=save_path, nlink=nlink, cfg=cfg)
        name = t.get("name", h)
        if action == "keep":
            log(f"keep '{name}': {reason}")
            continue
        verb = "delete" if action == "delete" else "archive"
        if cfg.dry_run:
            log(f"WOULD {verb} '{name}': {reason}")
            continue
        log(f"{verb} '{name}': {reason}")
        if action == "delete":
            client.delete(h)
        else:
            client.set_location(h, cfg.archive_path)
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd odroid/torrent-maintenance && .venv/bin/pytest tests/ -v
```
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add odroid/torrent-maintenance/
git commit -m "feat(torrent-maintenance): per-pass orchestration with tests"
```

---

### Task 6: `main()` entrypoint + scheduling loop + logging

**Files:**
- Modify: `odroid/torrent-maintenance/qbit_maintenance.py`
- Modify: `odroid/torrent-maintenance/tests/test_qbit_maintenance.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `main(argv, environ) -> None`. With `--once` in `argv`, runs a single `run_once` and returns. Otherwise loops forever: `sleep(seconds_until_hour(cfg.run_at_hour, datetime.now()))` then `run_once`. Logs go to stdout and are appended to `/app/maintenance.log` via a `log` function. Also exposes `make_logger(logfile) -> callable`.

- [ ] **Step 1: Write the failing test for the logger**

Append to `tests/test_qbit_maintenance.py`:

```python
from qbit_maintenance import make_logger

def test_make_logger_writes_stdout_and_file(capsys):
    with tempfile.TemporaryDirectory() as td:
        lf = os.path.join(td, "m.log")
        log = make_logger(lf)
        log("hello world")
        assert "hello world" in capsys.readouterr().out
        with open(lf) as fh:
            assert "hello world" in fh.read()
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd odroid/torrent-maintenance && .venv/bin/pytest tests/ -v -k make_logger
```
Expected: FAIL — `ImportError: cannot import name 'make_logger'`.

- [ ] **Step 3: Implement `make_logger()` and `main()`**

Add to `qbit_maintenance.py` (top: add `import sys`, `import time`):

```python
import sys
import time


def make_logger(logfile):
    def log(msg):
        line = time.strftime("%Y-%m-%d %H:%M:%S ") + msg
        print(line, flush=True)
        try:
            with open(logfile, "a") as fh:
                fh.write(line + "\n")
        except OSError:
            pass
    return log


def main(argv, environ):
    cfg = load_config(environ)
    log = make_logger(environ.get("LOG_FILE", "/app/maintenance.log"))
    client = QbitClient(cfg.qbit_url)
    once = "--once" in argv
    log(f"start dry_run={cfg.dry_run} run_at_hour={cfg.run_at_hour} once={once}")
    if once:
        run_once(client, cfg, time.time(), log=log)
        return
    while True:
        wait = seconds_until_hour(cfg.run_at_hour, datetime.now())
        log(f"sleeping {wait}s until {cfg.run_at_hour:02d}:00")
        time.sleep(wait)
        try:
            run_once(client, cfg, time.time(), log=log)
        except Exception as e:  # never let the loop die
            log(f"ERROR during pass: {e!r}")


if __name__ == "__main__":
    main(sys.argv[1:], os.environ)
```

- [ ] **Step 4: Run the full test suite**

```bash
cd odroid/torrent-maintenance && .venv/bin/pytest tests/ -v
```
Expected: PASS (all).

- [ ] **Step 5: Byte-compile check (no syntax/stdlib-import surprises)**

```bash
cd odroid/torrent-maintenance && .venv/bin/python -c "import qbit_maintenance; print('import OK')"
```
Expected: `import OK`.

- [ ] **Step 6: Commit**

```bash
git add odroid/torrent-maintenance/
git commit -m "feat(torrent-maintenance): main loop, fixed-hour schedule, logger"
```

---

### Task 7: Deploy notes README

**Files:**
- Create: `odroid/torrent-maintenance/README.md`

**Interfaces:** none (documentation).

- [ ] **Step 1: Write the README**

Create `odroid/torrent-maintenance/README.md`:

```markdown
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

## Tests
`python3 -m venv .venv && .venv/bin/pip install pytest && .venv/bin/pytest tests/ -v`
```

- [ ] **Step 2: Commit**

```bash
git add odroid/torrent-maintenance/README.md
git commit -m "docs(torrent-maintenance): deploy README"
```

---

### Task 8: `move_videos.sh` — `-links 1` filter + dedup (host, manual verification)

**Files:**
- Modify (on odroid): `/home/enzo/move_videos.sh`

**Interfaces:** none (host bash). Depends on the verified facts: hardlink works between `/mnt/DATA/Downloads` and `/mnt/DATA/Videos/*`.

- [ ] **Step 1: Back up the current script**

```bash
ssh enzo@home.zozoh.fr -p 2222 'cp -a /home/enzo/move_videos.sh /home/enzo/move_videos.sh.bak.$(date +%F)'
```
Expected: no output (success).

- [ ] **Step 2: Dry-run verify the `-links 1` filter selects the right files**

Confirm the new filter would skip an actively-seeded (hardlinked) file and include a standalone one. Run a read-only preview:

```bash
ssh enzo@home.zozoh.fr -p 2222 'for r in /mnt/ssd/videos/Films /mnt/ssd/videos/Séries; do echo "== $r =="; echo "nlink==1 (would move):"; find "$r" -type f -links 1 | head -5; echo "nlink>1 (would stay):"; find "$r" -type f -links +1 | head -5; done'
```
Expected: prints candidate files; `nlink>1` list contains any currently-seeded imports (may be empty right after a full archive run — that is fine).

- [ ] **Step 3: Edit `move_videos.sh`**

Replace the `rsync --remove-source-files -av "$src/" "$dst/" >> "$LOG" 2>&1` line inside `move_files()` with:

```bash
    find "$src" -type f -links 1 -printf '%P\0' \
      | rsync -a --remove-source-files --from0 --files-from=- "$src/" "$dst/" >> "$LOG" 2>&1
```

Add this function definition **above** the `move_files "$SRC_BASE/Films" …` calls:

```bash
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
```

Add the call **after** both `move_files` calls and **before** the Jellyfin scan block:

```bash
dedup_downloads_old
```

- [ ] **Step 4: Syntax-check the edited script**

```bash
ssh enzo@home.zozoh.fr -p 2222 'bash -n /home/enzo/move_videos.sh && echo SYNTAX_OK'
```
Expected: `SYNTAX_OK`.

- [ ] **Step 5: Verify dedup on a synthetic pair (safe, isolated)**

Extract only the `dedup_downloads_old` function into a scratch file (so no `move_files`/Jellyfin side effects run), create two byte-identical files with different names in the two dirs, run just the function, confirm they end up sharing an inode, then clean up:

```bash
ssh enzo@home.zozoh.fr -p 2222 'sudo bash -c "
  awk \"/^dedup_downloads_old\\(\\) \\{/{f=1} f{print} f&&/^\\}/{exit}\" /home/enzo/move_videos.sh > /tmp/probe.sh
  head -c 5000000 /dev/urandom > /mnt/DATA/Videos/Films/.dedup_probe_lib
  cp /mnt/DATA/Videos/Films/.dedup_probe_lib /mnt/DATA/Downloads/.dedup_probe_seed
  echo before: seed=\$(stat -c %i /mnt/DATA/Downloads/.dedup_probe_seed) lib=\$(stat -c %i /mnt/DATA/Videos/Films/.dedup_probe_lib)
  LOG=/tmp/dedup_probe.log bash -c \"source /tmp/probe.sh; dedup_downloads_old\"
  echo after:  seed=\$(stat -c %i /mnt/DATA/Downloads/.dedup_probe_seed) lib=\$(stat -c %i /mnt/DATA/Videos/Films/.dedup_probe_lib)
  cmp -s /mnt/DATA/Downloads/.dedup_probe_seed /mnt/DATA/Videos/Films/.dedup_probe_lib && echo CONTENT_EQUAL
  rm -f /mnt/DATA/Downloads/.dedup_probe_seed /mnt/DATA/Videos/Films/.dedup_probe_lib /tmp/probe.sh
"'
```
Expected: `before` shows two different inodes; `after` shows the **same** inode for both; `CONTENT_EQUAL` printed.

- [ ] **Step 6: Commit the reference copy to the repo**

Pull the edited script into the repo for version history:

```bash
scp -P 2222 enzo@home.zozoh.fr:/home/enzo/move_videos.sh odroid/torrent-maintenance/move_videos.sh
git add odroid/torrent-maintenance/move_videos.sh
git commit -m "feat(torrent-maintenance): move_videos.sh -links 1 filter + DATA dedup"
```

---

### Task 9: Deploy sidecar in dry-run, review, then enable

**Files:**
- Create (on odroid): `/opt/torrent/maintenance/qbit_maintenance.py`
- Modify (on odroid): `/opt/torrent/docker-compose.yaml`

**Interfaces:** Consumes the finished script and the running `qbittorrent`/`gluetun` services.

- [ ] **Step 1: Copy the script to the odroid**

```bash
scp -P 2222 odroid/torrent-maintenance/qbit_maintenance.py enzo@home.zozoh.fr:/tmp/qbit_maintenance.py
ssh enzo@home.zozoh.fr -p 2222 'sudo install -D -m 0755 /tmp/qbit_maintenance.py /opt/torrent/maintenance/qbit_maintenance.py'
```
Expected: no error.

- [ ] **Step 2: Back up the compose file**

```bash
ssh enzo@home.zozoh.fr -p 2222 'sudo cp -a /opt/torrent/docker-compose.yaml /opt/torrent/docker-compose.yaml.bak.$(date +%F)'
```

- [ ] **Step 3: Add the sidecar service to `/opt/torrent/docker-compose.yaml`**

Add under `services:` (sibling of `torrent`), keeping the file's 2-space indentation:

```yaml
  qbit-maintenance:
    image: python:3-alpine
    container_name: qbit-maintenance
    network_mode: service:vpn
    depends_on:
    - vpn
    - torrent
    environment:
    - TZ=Europe/Paris
    - DRY_RUN=true
    - RUN_AT_HOUR=5
    volumes:
    - /mnt/ssd/Downloads:/downloads:ro
    - /opt/torrent/maintenance:/app
    - /etc/localtime:/etc/localtime:ro
    command:
    - python
    - /app/qbit_maintenance.py
    restart: always
```

- [ ] **Step 4: Validate and start the sidecar**

```bash
ssh enzo@home.zozoh.fr -p 2222 'cd /opt/torrent && sudo docker compose config >/dev/null && echo COMPOSE_OK && sudo docker compose up -d qbit-maintenance'
```
Expected: `COMPOSE_OK` then the container starts.

- [ ] **Step 5: Force one dry-run pass and inspect output**

```bash
ssh enzo@home.zozoh.fr -p 2222 'sudo docker exec qbit-maintenance python /app/qbit_maintenance.py --once 2>&1 | tail -40'
```
Expected: `start dry_run=True …` then one `WOULD delete/archive` or `keep …` line per completed torrent. **Manually sanity-check** a few: a high-ratio imported torrent → `WOULD delete`; a >14-day low-ratio torrent → `WOULD archive`; a recent torrent → `keep`.

- [ ] **Step 6: Checkpoint — review with the user**

Stop and show the dry-run log to the user. Do not proceed to Step 7 until they confirm the decisions look correct. This is the last gate before destructive actions become live.

- [ ] **Step 7: Enable live mode**

Set `DRY_RUN=false` in the `qbit-maintenance` service env in `/opt/torrent/docker-compose.yaml`, then:

```bash
ssh enzo@home.zozoh.fr -p 2222 'cd /opt/torrent && sudo docker compose up -d qbit-maintenance && echo ENABLED'
```
Expected: `ENABLED`. The next real pass runs at 05:00; the loop logs its `sleeping …s until 05:00` line (check `docker logs qbit-maintenance`).

- [ ] **Step 8: Commit the compose reference to the repo**

```bash
scp -P 2222 enzo@home.zozoh.fr:/opt/torrent/docker-compose.yaml odroid/torrent-maintenance/docker-compose.reference.yaml
git add odroid/torrent-maintenance/docker-compose.reference.yaml
git commit -m "chore(torrent-maintenance): record deployed compose reference"
```

---

## Self-Review

**Spec coverage:**
- Delete rule (ratio>2, library-confirmed, `/downloads` + `/downloads-old` semantics) → Task 1 `decide`, Task 5 `run_once`, Task 9 deploy. ✓
- Archive rule (14 d, ratio<2, imported) → Task 1 `decide`, Task 5 `run_once`. ✓
- `nlink` library check → Task 2 `file_nlink`, Task 5. ✓
- Component 1 (`move_videos.sh -links 1`) → Task 8. ✓
- Component 2 (DATA dedup, full `cmp`, hardlink) → Task 8. ✓
- Sidecar container, stdlib-only, VPN namespace, ro mounts → Task 6, Task 9. ✓
- Fixed 05:00 schedule + TZ/localtime → Task 3, Task 6, Task 9. ✓
- `--once` for dry-run testing → Task 6, Task 9. ✓
- DRY_RUN default true + review gate + flip → Task 5, Task 9 Steps 5–7. ✓
- Config env vars → Task 2 `load_config`, Task 9 env. ✓
- Safety invariant (only imported torrents reach `/downloads-old`) → enforced by Task 1 archive branch requiring `nlink>1`. ✓

**Placeholder scan:** No TBD/TODO; every code and test step contains full content. ✓

**Type consistency:** `Config` fields, `decide(**kwargs)` signature, `run_once(client, cfg, now_epoch, stat_nlink, log)`, and `QbitClient` method names (`completed/files/delete/set_location`) are used identically across Tasks 1–6 and the tests. ✓
```
