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
