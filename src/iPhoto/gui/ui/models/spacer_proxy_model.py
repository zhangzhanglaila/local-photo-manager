"""Proxy model that injects spacer rows for the filmstrip view."""

from __future__ import annotations

import logging

from PySide6.QtCore import (
    QAbstractProxyModel,
    QModelIndex,
    QObject,
    Qt,
    QSize,
)

from .roles import Roles

_LOGGER = logging.getLogger(__name__)
_LOGGER.debug("SpacerProxyModel v2 loaded — deferred reset, epoch tracking")


class SpacerProxyModel(QAbstractProxyModel):
    """Wrap an asset model and expose leading/trailing spacer rows."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._spacer_size = QSize(0, 0)
        self._is_resetting = False
        self._reset_epoch = 0  # incremented on each reset; stale indices have old epoch

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def set_spacer_width(self, width: int) -> None:
        """Update spacer width and notify views when it changes."""

        width = max(0, width)
        if self._spacer_size.width() == width:
            return

        self._spacer_size.setWidth(width)

        if self._is_resetting:
            return

        source = self.sourceModel()
        if source is None:
            return

        try:
            source_rows = source.rowCount()
        except Exception:
            return
        if source_rows <= 0:
            return

        first_idx = self.index(0, 0)
        last_idx = self.index(source_rows + 1, 0)
        roles = [Qt.ItemDataRole.SizeHintRole]
        self.dataChanged.emit(first_idx, first_idx, roles)
        self.dataChanged.emit(last_idx, last_idx, roles)

    # ------------------------------------------------------------------
    # QAbstractProxyModel overrides
    # ------------------------------------------------------------------
    def setSourceModel(self, source_model) -> None:
        if source_model is self:
            raise ValueError(
                "Circular reference detected: SpacerProxyModel cannot be its own source."
            )

        # Detect indirect cycles if the source is another proxy that points back to us.
        # This isn't exhaustive (doesn't walk the full chain) but catches the most
        # common mistake of `proxy.setSourceModel(proxy_that_wraps_proxy)`.
        candidate = source_model
        while hasattr(candidate, "sourceModel"):
            candidate = candidate.sourceModel()
            if candidate is self:
                raise ValueError(
                    "Circular reference detected: "
                    "SpacerProxyModel source chain leads back to self."
                )

        previous = self.sourceModel()
        if previous is not None:
            try:
                previous.modelReset.disconnect(self._handle_source_reset)
                previous.rowsInserted.disconnect(self._handle_source_reset)
                previous.rowsRemoved.disconnect(self._handle_source_reset)
                previous.dataChanged.disconnect(self._handle_source_data_changed)
            except (RuntimeError, TypeError):  # pragma: no cover - Qt disconnect noise
                pass

        super().setSourceModel(source_model)

        if source_model is not None:
            source_model.modelReset.connect(self._handle_source_reset)
            source_model.rowsInserted.connect(self._handle_source_reset)
            source_model.rowsRemoved.connect(self._handle_source_reset)
            source_model.dataChanged.connect(self._handle_source_data_changed)

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        if parent.isValid() or self._is_resetting:
            return 0
        source = self.sourceModel()
        if source is None:
            return 0
        try:
            count = source.rowCount(parent)
        except Exception:
            return 0
        return count + 2 if count > 0 else 0

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        if self._is_resetting:
            return 0
        source = self.sourceModel()
        if source is None:
            return 0
        try:
            return source.columnCount(parent)
        except Exception:
            return 0

    def mapToSource(self, proxy_index: QModelIndex) -> QModelIndex:  # noqa: N802
        if self._is_resetting:
            return QModelIndex()
        source = self.sourceModel()
        if source is None or not proxy_index.isValid():
            return QModelIndex()

        # Runtime safety: if source somehow became self (or a wrapper leading to self),
        # prevent infinite recursion and crash. This can happen if the model graph
        # is mutated dynamically in ways `setSourceModel` couldn't catch initially.
        if source is self:
            return QModelIndex()

        row = proxy_index.row()
        try:
            count = source.rowCount()
        except Exception:
            return QModelIndex()
        if not (1 <= row <= count):
            return QModelIndex()
        try:
            return source.index(row - 1, proxy_index.column())
        except Exception:
            return QModelIndex()

    def mapFromSource(self, source_index: QModelIndex) -> QModelIndex:  # noqa: N802
        if self._is_resetting or not source_index.isValid():
            return QModelIndex()
        return self.index(source_index.row() + 1, source_index.column())

    def index(
        self, row: int, column: int, parent: QModelIndex = QModelIndex()
    ) -> QModelIndex:
        if parent.isValid():
            return QModelIndex()
        return self.createIndex(row, column)

    def parent(self, _index: QModelIndex) -> QModelIndex:  # noqa: N802
        return QModelIndex()

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):  # noqa: N802
        if not index.isValid() or self._is_resetting:
            return None

        source = self.sourceModel()
        if source is None:
            return None

        row = index.row()

        # Get source row count safely — this can crash at the C++ level
        # if the source model is in an inconsistent state.
        try:
            source_count = source.rowCount()
        except Exception:
            return None
        if source_count < 0:
            return None

        last_row = source_count + 1  # proxy has source_count + 2 rows
        if row in {0, last_row} and last_row >= 0:
            if role == Roles.IS_SPACER:
                return True
            if role in (Qt.ItemDataRole.SizeHintRole, Qt.SizeHintRole):
                return QSize(self._spacer_size.width(), self._spacer_size.height())
            if role == Qt.ItemDataRole.DisplayRole:
                return None
            return None

        # Guard against stale indices: revalidate bounds before mapping.
        if source_count <= 0 or row < 1 or row > source_count:
            return None

        try:
            source_index = self.mapToSource(index)
        except Exception:
            return None
        if not source_index.isValid():
            return None
        try:
            return source.data(source_index, role)
        except Exception:
            return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlags:  # noqa: N802
        if not index.isValid() or self._is_resetting:
            return Qt.NoItemFlags
        try:
            if bool(self.data(index, Roles.IS_SPACER)):
                return Qt.NoItemFlags
            source_index = self.mapToSource(index)
            source = self.sourceModel()
            if source is None or not source_index.isValid():
                return Qt.NoItemFlags
            return source.flags(source_index)
        except Exception:
            return Qt.NoItemFlags

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _handle_source_reset(self, *args, **_kwargs) -> None:  # pragma: no cover - Qt signal glue
        self._is_resetting = True
        self._reset_epoch += 1
        try:
            self.beginResetModel()
            # Clear _is_resetting BEFORE endResetModel so that rowCount()
            # returns the correct value when the view rebuilds its items.
            # After beginResetModel() Qt has already discarded all stale
            # persistent indices, so it is safe to allow proxy→source
            # mapping again.
            self._is_resetting = False
            self.endResetModel()
        except Exception:
            _LOGGER.debug("_handle_source_reset failed", exc_info=True)
            self._is_resetting = False

    def _handle_source_data_changed(
        self,
        top_left: QModelIndex,
        bottom_right: QModelIndex,
        roles: list[int] | None = None,
    ) -> None:
        """Forward data changes from the source model to the proxy."""

        if self._is_resetting or not top_left.isValid() or not bottom_right.isValid():
            return

        try:
            proxy_top_left = self.mapFromSource(top_left)
            proxy_bottom_right = self.mapFromSource(bottom_right)
        except Exception:
            return

        if not proxy_top_left.isValid() or not proxy_bottom_right.isValid():
            return

        # ``dataChanged`` signal signature requires roles to be a list or empty.
        # Passing None directly can cause issues with some Qt bindings/versions.
        safe_roles = roles if roles is not None else []
        self.dataChanged.emit(proxy_top_left, proxy_bottom_right, safe_roles)
