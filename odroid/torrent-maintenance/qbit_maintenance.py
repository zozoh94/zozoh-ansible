"""qBittorrent maintenance sidecar. Python stdlib only."""
import os
from dataclasses import dataclass
from datetime import datetime, timedelta


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


def seconds_until_hour(target_hour, now):
    target = now.replace(hour=target_hour, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return int((target - now).total_seconds())


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
