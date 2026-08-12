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
