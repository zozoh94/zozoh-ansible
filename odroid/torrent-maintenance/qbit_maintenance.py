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
