"""How much of the library the local AI scans have covered.

Faces and text recognition report the same way semantic search does — "how many
of the photos are done" — so the settings page can show a real number instead of
guessing whether the scan ever ran.

Everything here is a read-only aggregation over the databases that other
subsystems own. This module therefore contains no SQL and no schema knowledge:
the photo population comes from the asset index, face attempts from
``assets.face_status``, text attempts from the OCR index, and the people count
from the face index.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ...cache.index_store import read_index
from ...people.status import FACE_STATUS_DONE, FACE_STATUS_FAILED, FACE_STATUS_SKIPPED

_LOGGER = logging.getLogger(__name__)

# ``skipped`` counts as finished: it means "this photo cannot have faces" (a
# video, say), and leaving it in the denominator made the scan look stalled at
# 99 % forever. ``failed`` is finished too — retrying forever is a separate
# control.
FACE_FINISHED_STATUSES = (FACE_STATUS_DONE, FACE_STATUS_FAILED, FACE_STATUS_SKIPPED)


def face_coverage(library_root: Path) -> tuple[int, int]:
    """Return ``(finished, total)`` for the face scan."""

    with read_index(library_root) as store:
        if store is None:
            return (0, 0)
        try:
            finished = store.count_photos_by_face_status(FACE_FINISHED_STATUSES)
            total = store.photo_count()
        except Exception:  # pragma: no cover - display only
            _LOGGER.debug("could not read face coverage", exc_info=True)
            return (0, 0)
        return (finished, total)


def ocr_coverage(library_root: Path) -> tuple[int, int]:
    """Return ``(attempted, total)`` for the text scan."""

    with read_index(library_root) as store:
        if store is None:
            return (0, 0)
        try:
            photos = store.photo_count()
        except Exception:  # pragma: no cover - display only
            _LOGGER.debug("could not count photos", exc_info=True)
            return (0, 0)

    from ...ai.db.ocr_database import OCRRepository

    if not OCRRepository.db_path(library_root).exists():
        return (0, photos)
    repository = OCRRepository(library_root)
    try:
        return (repository.count_processed(), photos)
    except Exception:  # pragma: no cover - display only
        _LOGGER.debug("could not read OCR coverage", exc_info=True)
        return (0, photos)
    finally:
        repository.close()


def people_count(library_root: Path) -> int:
    """Number of clustered people (0 when the face scan never ran)."""

    from ...people.service import PeopleService

    try:
        return PeopleService(library_root).person_count()
    except Exception:  # pragma: no cover - display only
        _LOGGER.debug("could not count people", exc_info=True)
        return 0


__all__ = ["face_coverage", "ocr_coverage", "people_count"]
