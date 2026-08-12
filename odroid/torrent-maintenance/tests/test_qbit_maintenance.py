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
