"""One list of folders for downloads and the media engine.

Each row in ``download_paths`` can be a place qBittorrent downloads into
(``downloads``), a place the media engine scans and encodes (``scan``), or both.
qBittorrent may see a folder under a different path than this container does, so
a row keeps both: ``path`` as qBittorrent sees it, and ``local_path`` here. When
``local_path`` is empty it comes from the path mappings.

The media engine's folder list (``media.config.MEDIA_PATHS``) is derived from
the scanned rows by ``refresh()``. It used to be its own setting (``media_paths``,
or ``MEDIA_PATHS`` in .env); ``merge_once()`` folds that into these rows on
upgrade, and ``adopt_env()`` picks up folders added later with setup.sh --media.
"""
import logging
import os

from sqlalchemy.orm import Session

from ..models import DownloadPath, Setting

logger = logging.getLogger(__name__)

MERGED_KEY = "folders_merged"
REMOVED_KEY = "folders_removed"      # .env folders you deleted here; not re-added on restart


def _norm(path: str | None) -> str:
    return os.path.normpath(path) if path else ""


def _get(db: Session, key: str) -> str:
    row = db.query(Setting).filter(Setting.key == key).first()
    return (row.value or "") if row else ""


def _set(db: Session, key: str, value: str) -> None:
    row = db.query(Setting).filter(Setting.key == key).first()
    if row:
        row.value = value
    else:
        db.add(Setting(key=key, value=value))


def local_of(db: Session, dp: DownloadPath) -> str:
    """Where this folder is inside ScreenMate's container."""
    if dp.local_path:
        return _norm(dp.local_path)
    from .pipeline import map_path
    return _norm(map_path(db, dp.path))


def scan_roots(db: Session) -> list[str]:
    """The folders the media engine works in: scanned rows, outermost only."""
    paths = sorted({local_of(db, dp) for dp in db.query(DownloadPath).filter(DownloadPath.scan.is_(True))} - {""})
    roots: list[str] = []
    for p in paths:  # sorted, so a parent always comes before its children
        if not any(p == r or p.startswith(r.rstrip("/") + "/") for r in roots):
            roots.append(p)
    return roots


def refresh(db: Session) -> list[str]:
    """Point the media engine at the scanned folders; keep the old setting in step."""
    from .media import config

    roots = scan_roots(db)
    config.MEDIA_PATHS = roots
    _set(db, "media_paths", config.serialize("media_paths", roots))
    db.commit()
    return roots


def _covered(db: Session, local: str) -> DownloadPath | None:
    """The row whose folder here is exactly `local`, if any."""
    for dp in db.query(DownloadPath).all():
        if local_of(db, dp) == local:
            return dp
    return None


def _add_library(db: Session, local: str) -> DownloadPath:
    dp = DownloadPath(name=os.path.basename(local.rstrip("/")) or local, path=local, local_path=local,
                      downloads=False, scan=True, is_default=False)
    db.add(dp)
    db.flush()
    return dp


def merge_once(db: Session) -> None:
    """Fold the old, separate media folders into the Folders list (first start after upgrade).

    * Download folders keep downloading, and are scanned when they exist here.
    * Each old media folder that isn't one of them becomes a library-only folder,
      so nothing that was scanned before stops being scanned.
    """
    if _get(db, MERGED_KEY):
        return
    from .media import config

    old_roots = [_norm(p) for p in config.MEDIA_PATHS if p]
    # The built-in default (/media with no MEDIA_PATHS set) only counts if it exists.
    explicit = bool(_get(db, "media_paths")) or "MEDIA_PATHS" in os.environ
    for dp in db.query(DownloadPath).all():
        dp.downloads = True
        dp.scan = os.path.isdir(local_of(db, dp))
    for root in old_roots:
        if not explicit and not os.path.isdir(root):
            continue
        hit = _covered(db, root)
        if hit:
            hit.scan = True
        else:
            _add_library(db, root)
    _set(db, MERGED_KEY, "1")
    db.commit()
    logger.info("Folders: merged %d media folder(s) into the Folders list", len(old_roots))


def adopt_env(db: Session) -> None:
    """Folders named in MEDIA_PATHS (.env, setup.sh --media) appear in the list."""
    from .media import config

    raw = os.environ.get("MEDIA_PATHS", "")
    removed = set(filter(None, _get(db, REMOVED_KEY).split(",")))
    for path in (_norm(p) for p in config._parse_paths(raw)):
        if path and path not in removed and not _covered(db, path):
            _add_library(db, path)
            logger.info("Folders: added %s from MEDIA_PATHS", path)
    db.commit()


def forget_env(db: Session, local: str) -> None:
    """A folder you deleted here stays deleted even though .env still names it."""
    from .media import config

    env = {_norm(p) for p in config._parse_paths(os.environ.get("MEDIA_PATHS", ""))}
    if local in env:
        removed = set(filter(None, _get(db, REMOVED_KEY).split(",")))
        removed.add(local)
        _set(db, REMOVED_KEY, ",".join(sorted(removed)))


def set_scan_paths(db: Session, paths: list[str]) -> list[str]:
    """The old media-folders setting, applied to the Folders list (kept for API compatibility):
    exactly these folders are scanned; folders not yet listed are added as library folders."""
    wanted = [_norm(p) for p in paths if str(p).strip()]
    for dp in db.query(DownloadPath).all():
        dp.scan = local_of(db, dp) in wanted
    for p in wanted:
        if not _covered(db, p):
            _add_library(db, p)
    db.commit()
    return refresh(db)


def download_folder(db: Session, path_id: int | None) -> DownloadPath | None:
    """The folder to send a torrent to: the one chosen, if it still takes downloads,
    otherwise the default download folder."""
    if path_id:
        dp = db.query(DownloadPath).filter(DownloadPath.id == path_id).first()
        if dp is not None and dp.downloads:
            return dp
    return (db.query(DownloadPath)
            .filter(DownloadPath.is_default.is_(True), DownloadPath.downloads.is_(True)).first())


def startup(db: Session) -> list[str]:
    merge_once(db)
    adopt_env(db)
    return refresh(db)
