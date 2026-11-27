"""OCR database connection management."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from ...utils.logging import get_logger
from ...utils.pathutils import ensure_work_dir
from ..config import OCR_DB_NAME
from ...config import SQLITE_BUSY_TIMEOUT_S

_LOGGER = get_logger()

_global_instance: OCRRepository | None = None
_global_lock = threading.Lock()


def get_ocr_repository(library_root: Path) -> OCRRepository:
    """Get or create the global OCRRepository singleton."""
    global _global_instance

    with _global_lock:
        resolved_root = library_root.resolve()

        if _global_instance is not None:
            if _global_instance.library_root.resolve() == resolved_root:
                return _global_instance
            _global_instance.close()

        _global_instance = OCRRepository(resolved_root)
        return _global_instance


class OCRRepository:
    """OCR text storage with SQLite FTS5 full-text search.

    Each library root gets its own ``ocr_index.db`` database containing
    the ``ocr_regions`` table and an ``ocr_fts`` FTS5 virtual table for
    full-text search.
    """

    def __init__(self, library_root: Path) -> None:
        self.library_root = library_root
        self.path = self.db_path(library_root)
        self._local = threading.local()
        self._create_schema()

    @staticmethod
    def db_path(library_root: Path) -> Path:
        """Location of the OCR index for *library_root* (no I/O)."""

        return ensure_work_dir(library_root) / OCR_DB_NAME

    # ------------------------------------------------------------------
    # Connection helpers
    # ------------------------------------------------------------------

    def _get_conn(self) -> sqlite3.Connection:
        """Get or create a thread-local database connection."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(str(self.path), timeout=SQLITE_BUSY_TIMEOUT_S)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            self._local.conn = conn
        return self._local.conn

    def _create_schema(self) -> None:
        """Create tables and FTS5 virtual table if they don't exist."""
        conn = self._get_conn()

        conn.executescript("""
            CREATE TABLE IF NOT EXISTS ocr_regions (
                rowid          INTEGER PRIMARY KEY AUTOINCREMENT,
                asset_id       TEXT NOT NULL,
                asset_rel      TEXT NOT NULL,
                text           TEXT NOT NULL,
                confidence     REAL NOT NULL,
                box_x          REAL NOT NULL,
                box_y          REAL NOT NULL,
                box_w          REAL NOT NULL,
                box_h          REAL NOT NULL,
                image_width    INTEGER NOT NULL DEFAULT 0,
                image_height   INTEGER NOT NULL DEFAULT 0,
                detected_at    TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(asset_id, text, box_x, box_y)
            );

            CREATE INDEX IF NOT EXISTS idx_ocr_regions_asset
            ON ocr_regions(asset_id);

            -- Every image the OCR worker touched, including ones with no text.
            -- Without this the worker re-processed the whole library on every
            -- launch because "no regions" left no trace.
            CREATE TABLE IF NOT EXISTS ocr_processed (
                asset_id     TEXT PRIMARY KEY,
                asset_rel    TEXT NOT NULL,
                processed_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
        """)

        # FTS5 virtual table
        try:
            conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS ocr_fts USING fts5(
                    text,
                    content='ocr_regions',
                    content_rowid='rowid',
                    tokenize='unicode61 remove_diacritics 2'
                )
            """)
        except sqlite3.OperationalError as e:
            _LOGGER.warning("FTS5 creation issue (may already exist): %s", e)

        # Triggers to keep FTS in sync
        for trigger_sql in [
            """
            CREATE TRIGGER IF NOT EXISTS ocr_ai AFTER INSERT ON ocr_regions BEGIN
                INSERT INTO ocr_fts(rowid, text) VALUES (new.rowid, new.text);
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS ocr_ad AFTER DELETE ON ocr_regions BEGIN
                INSERT INTO ocr_fts(ocr_fts, rowid, text) VALUES ('delete', old.rowid, old.text);
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS ocr_au AFTER UPDATE ON ocr_regions BEGIN
                INSERT INTO ocr_fts(ocr_fts, rowid, text) VALUES ('delete', old.rowid, old.text);
                INSERT INTO ocr_fts(rowid, text) VALUES (new.rowid, new.text);
            END
            """,
        ]:
            try:
                conn.execute(trigger_sql)
            except sqlite3.OperationalError:
                pass  # Trigger already exists

        conn.commit()

        # Seed the processed marker from existing regions so upgrading does not
        # re-OCR the images that already have text.
        try:
            conn.execute(
                "INSERT OR IGNORE INTO ocr_processed(asset_id, asset_rel) "
                "SELECT DISTINCT asset_id, asset_rel FROM ocr_regions"
            )
            conn.commit()
        except sqlite3.OperationalError:
            pass

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def store_regions(
        self,
        asset_id: str,
        asset_rel: str,
        regions: list,
        image_width: int = 0,
        image_height: int = 0,
    ) -> None:
        """Store OCR regions for an asset.

        Parameters
        ----------
        asset_id : str
            Unique asset identifier.
        asset_rel : str
            Library-relative path.
        regions : list[OCRRegion]
            Detected text regions.
        image_width : int
            Source image width.
        image_height : int
            Source image height.
        """
        if not regions:
            return

        conn = self._get_conn()

        # Delete existing regions for this asset first
        conn.execute("DELETE FROM ocr_regions WHERE asset_id = ?", (asset_id,))

        conn.executemany(
            """
            INSERT INTO ocr_regions
            (asset_id, asset_rel, text, confidence, box_x, box_y, box_w, box_h,
             image_width, image_height)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    asset_id,
                    asset_rel,
                    r.text,
                    r.confidence,
                    r.box_x,
                    r.box_y,
                    r.box_w,
                    r.box_h,
                    image_width,
                    image_height,
                )
                for r in regions
            ],
        )
        conn.commit()

    def store_regions_batch(
        self, items: list[tuple[str, str, list, int, int]]
    ) -> None:
        """Store OCR regions for multiple assets in a single transaction.

        Parameters
        ----------
        items : list[tuple]
            Each tuple is (asset_id, asset_rel, regions, width, height).
        """
        conn = self._get_conn()
        for asset_id, asset_rel, regions, width, height in items:
            if not regions:
                continue
            conn.execute("DELETE FROM ocr_regions WHERE asset_id = ?", (asset_id,))
            conn.executemany(
                """
                INSERT INTO ocr_regions
                (asset_id, asset_rel, text, confidence, box_x, box_y, box_w, box_h,
                 image_width, image_height)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        asset_id,
                        asset_rel,
                        r.text,
                        r.confidence,
                        r.box_x,
                        r.box_y,
                        r.box_w,
                        r.box_h,
                        width,
                        height,
                    )
                    for r in regions
                ],
            )
        conn.commit()

    def delete_by_asset(self, asset_id: str) -> None:
        """Delete all OCR data for an asset."""
        conn = self._get_conn()
        conn.execute("DELETE FROM ocr_regions WHERE asset_id = ?", (asset_id,))
        conn.execute("DELETE FROM ocr_processed WHERE asset_id = ?", (asset_id,))
        conn.commit()

    def mark_processed(self, asset_id: str, asset_rel: str) -> None:
        """Remember that an image was OCR'd, even if it contained no text."""

        conn = self._get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO ocr_processed(asset_id, asset_rel) VALUES (?, ?)",
            (asset_id, asset_rel),
        )
        conn.commit()

    def get_processed_asset_ids(self) -> set[str]:
        """Asset IDs that the OCR worker already looked at."""

        conn = self._get_conn()
        rows = conn.execute("SELECT asset_id FROM ocr_processed").fetchall()
        return {row["asset_id"] for row in rows}

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def search(self, query: str, limit: int = 100) -> list:
        """Full-text search using FTS5.

        Parameters
        ----------
        query : str
            Search query (supports FTS5 syntax).
        limit : int
            Maximum results.

        Returns
        -------
        list[OCRSearchResult]
        """
        from ..ocr.models import OCRSearchResult

        conn = self._get_conn()
        try:
            rows = conn.execute(
                """
                SELECT r.asset_id, r.asset_rel, r.text, r.confidence,
                       rank,
                       snippet(ocr_fts, 0, '<b>', '</b>', '...', 32) as snippet
                FROM ocr_fts
                JOIN ocr_regions r ON ocr_fts.rowid = r.rowid
                WHERE ocr_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (query, limit),
            ).fetchall()

            return [
                OCRSearchResult(
                    asset_id=row["asset_id"],
                    asset_rel=row["asset_rel"],
                    text=row["text"],
                    confidence=row["confidence"],
                    rank=row["rank"],
                    snippet=row["snippet"] or "",
                )
                for row in rows
            ]
        except sqlite3.OperationalError as e:
            _LOGGER.warning("FTS search failed for '%s': %s", query, e)
            return []

    def get_asset_ids_with_ocr(self) -> set[str]:
        """Get set of asset IDs that have OCR data."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT DISTINCT asset_id FROM ocr_regions"
        ).fetchall()
        return {row["asset_id"] for row in rows}

    def count(self) -> int:
        """Return total number of OCR regions."""
        conn = self._get_conn()
        row = conn.execute("SELECT COUNT(*) FROM ocr_regions").fetchone()
        return row[0]

    def count_assets(self) -> int:
        """Return number of assets with OCR data."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT COUNT(DISTINCT asset_id) FROM ocr_regions"
        ).fetchone()
        return row[0]

    def list_assets_with_text(self, limit: int = 500) -> list:
        """Return up to *limit* photos with all their OCR lines.

        The settings browser needs whole photos, not search hits. Grouping in
        SQL would concatenate text without its reading order, so the selected
        assets are fetched once and their regions are joined in Python by
        vertical position.
        """

        from ..ocr.models import OCRAssetText

        conn = self._get_conn()
        rows = conn.execute(
            """
            WITH selected AS (
                SELECT asset_id, MIN(asset_rel) AS asset_rel
                FROM ocr_regions
                GROUP BY asset_id
                ORDER BY asset_rel
                LIMIT ?
            )
            SELECT r.asset_id, s.asset_rel, r.text, r.confidence
            FROM ocr_regions r
            JOIN selected s ON s.asset_id = r.asset_id
            ORDER BY s.asset_rel, r.box_y, r.box_x
            """,
            (max(1, int(limit)),),
        ).fetchall()

        grouped: list = []
        current_id = None
        current_rel = ""
        texts: list[str] = []
        confidences: list[float] = []
        for row in rows:
            asset_id = row["asset_id"]
            if current_id is not None and asset_id != current_id:
                grouped.append(
                    OCRAssetText(
                        asset_id=current_id,
                        asset_rel=current_rel,
                        text="\n".join(texts),
                        confidence=(sum(confidences) / len(confidences))
                        if confidences
                        else 0.0,
                    )
                )
                texts = []
                confidences = []
            current_id = asset_id
            current_rel = row["asset_rel"]
            texts.append(str(row["text"]))
            confidences.append(float(row["confidence"]))
        if current_id is not None:
            grouped.append(
                OCRAssetText(
                    asset_id=current_id,
                    asset_rel=current_rel,
                    text="\n".join(texts),
                    confidence=(sum(confidences) / len(confidences))
                    if confidences
                    else 0.0,
                )
            )
        return grouped

    def count_processed(self) -> int:
        """Number of assets the text scan has attempted.

        ``ocr_processed`` records every attempt, including images where no text
        was found, so it — not the region count — is the coverage numerator.
        """
        conn = self._get_conn()
        row = conn.execute("SELECT COUNT(*) FROM ocr_processed").fetchone()
        return int(row[0]) if row else 0

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close the database connection."""
        if hasattr(self._local, "conn") and self._local.conn is not None:
            try:
                self._local.conn.close()
            except Exception:
                pass
            self._local.conn = None
