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


from qbit_maintenance import make_logger

def test_make_logger_writes_stdout_and_file(capsys):
    with tempfile.TemporaryDirectory() as td:
        lf = os.path.join(td, "m.log")
        log = make_logger(lf)
        log("hello world")
        assert "hello world" in capsys.readouterr().out
        with open(lf) as fh:
            assert "hello world" in fh.read()


import subprocess, sys as _sys

def test_running_as_script_defines_all_names(tmp_path):
    # Regression: main() -> run_once() -> decide() must not NameError because of
    # def-order relative to the __main__ guard. Point at a closed port so the
    # only possible failure is a connection error, never a NameError.
    env = dict(os.environ)
    env.update({"QBIT_URL": "http://127.0.0.1:9", "DRY_RUN": "true",
                "LOG_FILE": str(tmp_path / "m.log")})
    script = os.path.join(os.path.dirname(os.path.dirname(__file__)), "qbit_maintenance.py")
    r = subprocess.run([_sys.executable, script, "--once"],
                       capture_output=True, text=True, env=env, timeout=30)
    assert "NameError" not in r.stderr
    assert "start dry_run=True" in r.stdout
