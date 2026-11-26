"""How much of the library the local AI scans have covered.

Faces and text recognition report the same way semantic search does — "how many
of the photos are done" — so the settings page can show a real number instead of
guessing whether the scan ever ran.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

_LOGGER = logging.getLogger(__name__)


def _work_dir(library_root: Path) -> Path:
    from ...utils.pathutils import ensure_work_dir

    return Path(ensure_work_dir(library_root))


def _index_db(library_root: Path) -> Path | None:
    try:
        from ...cache.index_store import IndexStore

        path = Path(IndexStore(library_root).path)
    except Exception:  # pragma: no cover - defensive
        return None
    return path if path.exists() else None


def _photo_total(connection: sqlite3.Connection) -> int:
    row = connection.execute(
        "SELECT COUNT(*) FROM assets WHERE media_type = 0"
        " AND (live_role IS NULL OR live_role = 0)"
    ).fetchone()
    return int(row[0]) if row else 0


def face_coverage(library_root: Path) -> tuple[int, int]:
    """Return ``(finished, total)`` for the face scan.

    ``skipped`` counts as finished: it means "this photo cannot have faces"
    (a video, say), and leaving it in the denominator made the scan look stalled
    at 99 % forever.
    """

    db = _index_db(library_root)
    if db is None:
        return (0, 0)
    connection = sqlite3.connect(str(db))
    try:
        total = _photo_total(connection)
        row = connection.execute(
            "SELECT COUNT(*) FROM assets WHERE media_type = 0"
            " AND (live_role IS NULL OR live_role = 0)"
            " AND face_status IN ('done', 'failed', 'skipped')"
        ).fetchone()
        return (int(row[0]) if row else 0, total)
    except sqlite3.DatabaseError:
        _LOGGER.debug("could not read face coverage", exc_info=True)
        return (0, 0)
    finally:
        connection.close()


def ocr_coverage(library_root: Path) -> tuple[int, int]:
    """Return ``(attempted, total)`` for the text scan."""

    db = _index_db(library_root)
    photos = 0
    if db is not None:
        connection = sqlite3.connect(str(db))
        try:
            photos = _photo_total(connection)
        except sqlite3.DatabaseError:
            _LOGGER.debug("could not count photos", exc_info=True)
        finally:
            connection.close()

    ocr_db = _work_dir(library_root) / "ocr_index.db"
    if not ocr_db.exists():
        return (0, photos)
    connection = sqlite3.connect(str(ocr_db))
    try:
        row = connection.execute("SELECT COUNT(*) FROM ocr_processed").fetchone()
        return (int(row[0]) if row else 0, photos)
    except sqlite3.DatabaseError:
        _LOGGER.debug("could not read OCR coverage", exc_info=True)
        return (0, photos)
    finally:
        connection.close()


def people_count(library_root: Path) -> int:
    """Number of clustered people (0 when the face scan never ran)."""

    path = _work_dir(library_root) / "faces" / "face_index.db"
    if not path.exists():
        return 0
    connection = sqlite3.connect(str(path))
    try:
        row = connection.execute("SELECT COUNT(*) FROM persons").fetchone()
        return int(row[0]) if row else 0
    except sqlite3.DatabaseError:
        return 0
    finally:
        connection.close()


__all__ = ["face_coverage", "ocr_coverage", "people_count"]
