"""Sidebar widget presenting the Basic Library tree."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from PySide6.QtCore import (
    QObject,
    QEvent,
    QModelIndex,
    QPoint,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QCursor,
    QDragEnterEvent,
    QDragMoveEvent,
    QDropEvent,
    QFont,
    QPainter,
    QPalette,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QLabel,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from ....library.runtime_controller import LibraryRuntimeController
from ....config import ALL_PHOTOS_TITLE as _ALL_PHOTOS_TITLE
from ....config import HOME_TITLE as _HOME_TITLE
from ....i18n import tr
from ...services.pinned_items_service import PinnedItemsService, PinnedSidebarItem
from ..icon import load_icon
from ..models.album_tree_model import AlbumTreeModel, AlbumTreeRole, NodeType
from ..delegates.album_sidebar_delegate import (
    AlbumSidebarDelegate,
    BranchIndicatorController,
)
from ..menus.album_sidebar_menu import show_context_menu
from ..styles import modern_scrollbar_style
from ..palette import (
    SIDEBAR_BRANCH_CONTENT_GAP,
    SIDEBAR_SELECTED_BACKGROUND,
    SIDEBAR_ICON_COLOR,
    SIDEBAR_ICON_SIZE,
    SIDEBAR_ICON_TEXT_GAP,
    SIDEBAR_INDENT_PER_LEVEL,
    SIDEBAR_INDICATOR_HOTZONE_MARGIN,
    SIDEBAR_INDICATOR_SIZE,
    SIDEBAR_LEFT_PADDING,
    SIDEBAR_LAYOUT_MARGIN,
    SIDEBAR_LAYOUT_SPACING,
    SIDEBAR_ROW_HEIGHT,
    SIDEBAR_ROW_RADIUS,
    SIDEBAR_TEXT_COLOR,
    SIDEBAR_TREE_MIN_WIDTH,
    SIDEBAR_TREE_STYLESHEET,
)
from ....utils.logging import diag

_logger = logging.getLogger(__name__)

# Menu titles carry a keyboard accelerator ("设置(&S)"); a plain label shows the
# mnemonic verbatim, so it is stripped for the footer row.
_MENU_ACCELERATOR_RE = re.compile(r"\s*\(&[A-Za-z0-9]\)\s*$")


def _strip_menu_accelerator(text: str) -> str:
    """Return *text* without a trailing ``(&X)`` mnemonic."""

    return _MENU_ACCELERATOR_RE.sub("", text)


class _DropAwareTree(QTreeView):
    """Tree view that accepts drops of external media files onto albums."""

    filesDropped = Signal(Path, object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._model: AlbumTreeModel | None = None
        self.setAcceptDrops(True)
        self.setDragEnabled(False)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)
        self.setDefaultDropAction(Qt.DropAction.CopyAction)
        self.setDropIndicatorShown(True)

    # ------------------------------------------------------------------
    # Qt overrides
    # ------------------------------------------------------------------
    def setModel(self, model) -> None:
        super().setModel(model)
        if isinstance(model, AlbumTreeModel):
            self._model = model

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if self._should_accept_event(event):
            event.acceptProposedAction()
            return
        event.ignore()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        if self._should_accept_event(event):
            event.acceptProposedAction()
            return
        event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        target = self._resolve_target_path(event)
        paths = self._extract_local_files(event)
        if target is None or not paths:
            event.ignore()
            return
        event.acceptProposedAction()
        self.filesDropped.emit(target, paths)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _should_accept_event(self, event: QDragEnterEvent | QDragMoveEvent) -> bool:
        if self._extract_local_files(event) == []:
            return False
        target = self._resolve_target_path(event)
        return target is not None

    def _resolve_target_path(
        self, event: QDragEnterEvent | QDragMoveEvent | QDropEvent
    ) -> Path | None:
        model = self._model or self.model()
        if not isinstance(model, AlbumTreeModel):
            return None
        index = self.indexAt(self._event_pos(event))
        if not index.isValid():
            return None
        item = model.item_from_index(index)
        if item is None or item.album is None:
            return None
        if item.node_type not in {NodeType.ALBUM, NodeType.SUBALBUM}:
            return None
        return item.album.path

    def _extract_local_files(
        self, event: QDragEnterEvent | QDragMoveEvent | QDropEvent
    ) -> list[Path]:
        mime = event.mimeData()
        if mime is None:
            return []
        urls = getattr(mime, "urls", None)
        if not callable(urls):
            return []
        seen: set[Path] = set()
        paths: list[Path] = []
        for url in urls():
            if not url.isLocalFile():
                continue
            local = Path(url.toLocalFile()).expanduser()
            if local in seen:
                continue
            seen.add(local)
            paths.append(local)
        return paths

    def _event_pos(self, event) -> QPoint:
        if hasattr(event, "position"):
            return event.position().toPoint()
        if hasattr(event, "pos"):
            return event.pos()
        return QPoint()


class _SidebarHairline(QWidget):
    """Hairline divider used above the sidebar footer."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # The extra height gives the rule breathing room without a layout spacer.
        self.setFixedHeight(13)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        colour = QColor(self.palette().color(QPalette.ColorRole.WindowText))
        colour.setAlpha(24)
        painter = QPainter(self)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(colour)
        painter.drawRect(0, 6, self.width(), 1)


class _SidebarFooterRow(QWidget):
    """Footer row that opens a menu, styled like a navigation entry.

    The reference sidebar keeps its global entry (settings) pinned below the
    navigation list, separated by a hairline. It uses the same row geometry as
    the navigation entries so the column reads as one control surface.
    """

    clicked = Signal()

    def __init__(
        self,
        icon_name: str,
        text: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._icon_name = icon_name
        self._hovered = False
        self.setFixedHeight(SIDEBAR_ROW_HEIGHT)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(SIDEBAR_LEFT_PADDING, 0, SIDEBAR_LEFT_PADDING, 0)
        layout.setSpacing(SIDEBAR_ICON_TEXT_GAP)

        self._icon_label = QLabel(self)
        self._icon_label.setFixedSize(SIDEBAR_ICON_SIZE, SIDEBAR_ICON_SIZE)
        self._icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon_label.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )
        layout.addWidget(self._icon_label)

        self._text_label = QLabel(text, self)
        self._text_label.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )
        text_font = QFont(self._text_label.font())
        text_font.setPixelSize(14)
        text_font.setWeight(QFont.Weight.Medium)
        self._text_label.setFont(text_font)
        layout.addWidget(self._text_label, 1)

        self._apply_colours()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def set_text(self, text: str) -> None:
        self._text_label.setText(text)

    # ------------------------------------------------------------------
    # Qt overrides
    # ------------------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802
        if self._hovered:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(SIDEBAR_SELECTED_BACKGROUND))
            painter.drawRoundedRect(
                QRectF(self.rect()), SIDEBAR_ROW_RADIUS, SIDEBAR_ROW_RADIUS
            )
        super().paintEvent(event)

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hovered = True
        self._apply_colours()
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hovered = False
        self._apply_colours()
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        # ``QWidget`` ignores presses by default, which would let the event
        # bubble to the parent and rob this row of the matching release.
        if event.button() == Qt.MouseButton.LeftButton:
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(
            event.position().toPoint()
        ):
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        if event.type() == QEvent.Type.PaletteChange:
            self._apply_colours()
        super().changeEvent(event)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _apply_colours(self) -> None:
        base = QColor(self.palette().color(QPalette.ColorRole.WindowText))
        colour = QColor(base)
        if not self._hovered:
            colour.setAlpha(150)
        self._text_label.setStyleSheet(
            "color: rgba({}, {}, {}, {});".format(
                colour.red(), colour.green(), colour.blue(), colour.alpha()
            )
        )
        pixmap = load_icon(
            self._icon_name,
            color=colour.name(QColor.NameFormat.HexArgb),
            stroke_width=1.8,
        ).pixmap(SIDEBAR_ICON_SIZE, SIDEBAR_ICON_SIZE)
        self._icon_label.setPixmap(pixmap)


class AlbumSidebar(QWidget):
    """Composite widget exposing library navigation and actions."""

    albumSelected = Signal(Path)
    pinnedItemSelected = Signal(object)
    homeSelected = Signal()
    allPhotosSelected = Signal()
    staticNodeSelected = Signal(str)
    searchEntrySelected = Signal()
    bindLibraryRequested = Signal()
    settingsRequested = Signal()
    excludeAlbumRequested = Signal(Path)
    filesDropped = Signal(Path, object)
    albumCheckStateChanged = Signal(object)

    HOME_TITLE = _HOME_TITLE
    ALL_PHOTOS_TITLE = _ALL_PHOTOS_TITLE

    def __init__(self, library: LibraryRuntimeController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._updating_style = False
        self._library = library
        self._model = AlbumTreeModel(library, self)
        self._pending_selection: Path | None = None
        self._current_selection: Path | None = None
        self._current_static_selection: str | None = None
        self._current_pinned_selection: tuple[str, str] | None = None
        self._current_search_selected = False

        # Give the widget a stable object name so the stylesheet targets only the
        # sidebar shell and does not bleed into child controls such as the tree view.
        self.setObjectName("albumSidebar")
        # ``WA_StyledBackground`` tells Qt to honour our palette/stylesheet even when the
        # parent widgets are translucent (required for the rounded window shell).
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self._tree = _DropAwareTree(self)
        self._tree.setObjectName("albumSidebarTree")
        self._tree.setModel(self._model)
        self._tree.setHeaderHidden(True)
        self._tree.setRootIsDecorated(True)
        self._tree.setUniformRowHeights(False)
        self._tree.setEditTriggers(QTreeView.EditTrigger.NoEditTriggers)
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._show_context_menu)
        self._tree.selectionModel().selectionChanged.connect(self._on_selection_changed)
        self._tree.doubleClicked.connect(self._on_double_clicked)
        self._tree.clicked.connect(self._on_clicked)
        self._tree.setIndentation(0)
        self._tree.setIconSize(QSize(SIDEBAR_ICON_SIZE, SIDEBAR_ICON_SIZE))
        self._tree.setMouseTracking(True)
        self._tree.viewport().installEventFilter(self)
        self._build_action_hint()
        self._tree.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self._tree.setItemDelegate(AlbumSidebarDelegate(self._tree))
        self._indicator_controller = BranchIndicatorController(self._tree)
        self._tree.branch_indicator_controller = self._indicator_controller
        self._tree.setFrameShape(QFrame.Shape.NoFrame)
        self._tree.setAlternatingRowColors(False)
        self._tree.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        tree_palette = self._tree.palette()
        tree_palette.setColor(QPalette.ColorRole.Highlight, SIDEBAR_SELECTED_BACKGROUND)
        tree_palette.setColor(QPalette.ColorRole.Link, SIDEBAR_ICON_COLOR)
        self._tree.setPalette(tree_palette)
        self._tree.setAutoFillBackground(True)

        self._apply_scrollbar_style()

        # Track the minimum width that should apply when the user resizes the splitter manually.
        # The sidebar should never collapse completely in that situation, so we keep a computed
        # "manual" minimum width and only relax it when an animated transition is in progress.
        self._manual_minimum_width = SIDEBAR_TREE_MIN_WIDTH
        self._default_sidebar_maximum_width = super().maximumWidth()
        self._minimum_width_relaxed = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(*SIDEBAR_LAYOUT_MARGIN)
        layout.setSpacing(SIDEBAR_LAYOUT_SPACING)
        layout.addWidget(self._tree, stretch=1)

        # Footer: the application menu lives behind a single "设置" row, mirroring
        # the reference sidebar's pinned settings entry.
        self._footer_rule = _SidebarHairline(self)
        self._settings_row = _SidebarFooterRow(
            "gear.svg", _strip_menu_accelerator(tr("menu.settings")), self
        )
        self._settings_row.clicked.connect(self.settingsRequested.emit)
        layout.addWidget(self._footer_rule)
        layout.addWidget(self._settings_row)


        # Apply the initial manual minimum width so the splitter respects the configured
        # constraint during user-driven resizing.  This call also covers the case where Qt
        # calculates a slightly different minimum width once the layout has been populated.
        self._apply_current_minimum_width()

        self._model.modelReset.connect(self._on_model_reset)
        self._model.dataChanged.connect(self._on_data_changed)
        self._tree.filesDropped.connect(self._on_files_dropped)
        self._expand_defaults()

    def changeEvent(self, event: QEvent) -> None:
        if event.type() == QEvent.Type.PaletteChange:
            if not self._updating_style:
                self._apply_scrollbar_style()
        super().changeEvent(event)

    def _apply_scrollbar_style(self) -> None:
        if not hasattr(self, "_tree"):
            return

        # Explicitly use the sidebar's dark text color to ensure contrast against the
        # fixed light background, regardless of the active application theme.
        scrollbar_style = modern_scrollbar_style(SIDEBAR_TEXT_COLOR, track_alpha=30)

        full_style = SIDEBAR_TREE_STYLESHEET + "\n" + scrollbar_style

        if self._tree.styleSheet() == full_style:
            return

        self._updating_style = True
        try:
            self._tree.setStyleSheet(full_style)
        finally:
            self._updating_style = False

    # ------------------------------------------------------------------
    # Animation helpers
    # ------------------------------------------------------------------
    def relax_minimum_width_for_animation(self) -> None:
        """Temporarily relax the sidebar so splitter animations can collapse it to zero."""

        if self._minimum_width_relaxed:
            return
        self._minimum_width_relaxed = True
        # When the relaxed flag is set we force the minimum width to zero so that the surrounding
        # splitter can animate the pane without being clamped by our manual constraint.
        self._apply_current_minimum_width()

    def restore_minimum_width_after_animation(self) -> None:
        """Reapply the manual minimum width once an animation has completed."""

        if not self._minimum_width_relaxed:
            return
        self._minimum_width_relaxed = False
        # Refresh the manual constraint in case the title text (and therefore the size hint)
        # changed while the sidebar was collapsed, then apply the non-relaxed minimum width.
        self._refresh_manual_minimum_width()
        self.setMaximumWidth(self._default_sidebar_maximum_width)
        self.updateGeometry()

    def _apply_current_minimum_width(self) -> None:
        """Synchronise the widget's minimum width with the current relaxation state."""

        if self._minimum_width_relaxed:
            self.setMinimumWidth(0)
            return
        self.setMinimumWidth(self._manual_minimum_width)

    def _refresh_manual_minimum_width(self) -> None:
        """Reapply the manual minimum width after an animated collapse."""

        self._manual_minimum_width = SIDEBAR_TREE_MIN_WIDTH
        self._apply_current_minimum_width()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def tree_model(self) -> AlbumTreeModel:
        """Expose the underlying model so controllers can query album metadata."""

        return self._model

    def set_pinned_service(self, service: PinnedItemsService | None) -> None:
        """Attach a pinned-items service to the underlying tree model."""

        self._model.set_pinned_service(service)

    def refresh_tree_model(self) -> None:
        """Force the sidebar model to rebuild its current contents."""

        self._model.refresh()

    def retranslate(self) -> None:
        """Refresh translatable strings for the current language."""

        self._settings_row.set_text(_strip_menu_accelerator(tr("menu.settings")))
        self._model.refresh()

    def checked_root_paths(self) -> list[Path]:
        """Return the currently checked root-level album paths."""
        return self._model.checked_root_paths()

    def set_checked_root_paths(self, paths: set[str]) -> None:
        """Restore checked state from a set of path strings."""
        self._model.set_checked_root_paths(paths)

    def _expand_defaults(self) -> None:
        """Expand high-level nodes to match the reference layout."""

        if self._model.rowCount() == 0:
            return
        for row in range(self._model.rowCount()):
            root_index = self._model.index(row, 0)
            if not root_index.isValid():
                continue
            self._tree.expand(root_index)
            for child_row in range(self._model.rowCount(root_index)):
                child = self._model.index(child_row, 0, root_index)
                if child.isValid():
                    self._tree.expand(child)

    def _on_model_reset(self) -> None:
        _logger.debug(
            "_on_model_reset: pending=%s current=%s static=%s library_root=%s",
            self._pending_selection,
            self._current_selection,
            self._current_static_selection,
            self._library.root(),
        )
        # Reconnect selectionChanged after model reset.  ``beginResetModel``
        # / ``endResetModel`` can invalidate Qt's internal selection-model
        # wiring in some Qt versions, causing subsequent user clicks to be
        # silently dropped.
        selection_model = self._tree.selectionModel()
        if selection_model is not None:
            try:
                selection_model.selectionChanged.disconnect(self._on_selection_changed)
            except (RuntimeError, TypeError):
                pass
            selection_model.selectionChanged.connect(self._on_selection_changed)

        self._expand_defaults()
        # Defer selection restoration to the next event loop iteration.
        # Running it synchronously during endResetModel() can trigger
        # navigation cascades (open_all_photos → reset_for_gallery → set_image)
        # while the model/view state is still inconsistent, causing C++ crashes.
        QTimer.singleShot(0, self._restore_selection_after_reset)

    def _restore_selection_after_reset(self) -> None:
        if self._current_search_selected:
            self.select_search_entry(emit_signal=False)
        elif self._pending_selection is not None:
            self.select_path(self._pending_selection)
            self._pending_selection = None
        elif self._current_pinned_selection is not None:
            kind, item_id = self._current_pinned_selection
            self.select_pinned_item(
                PinnedSidebarItem(kind=kind, item_id=item_id, label=""),
                emit_signal=False,
            )
        elif self._current_selection is not None:
            self.select_path(self._current_selection)
        elif self._current_static_selection:
            # Restoring the highlight after a model rebuild must stay a purely
            # visual operation. The rebuild is triggered by background work such
            # as scan progress, so emitting a navigation signal here yanked the
            # user back to the restored node roughly once per scan tick — e.g.
            # out of the map's cluster gallery and back to All Photos.
            self.select_static_node(self._current_static_selection)

    def _on_selection_changed(self, _selected, _deselected) -> None:
        index = self._tree.currentIndex()
        item = self._model.item_from_index(index)
        if item is None:
            _logger.debug("_on_selection_changed: item is None")
            return
        node_type = item.node_type
        _logger.debug("_on_selection_changed: title=%r node_type=%s", item.title, node_type)
        if node_type == NodeType.ACTION:
            self.bindLibraryRequested.emit()
            return
        if node_type == NodeType.HEADER:
            if item.title == tr("sidebar.albums"):
                self._current_pinned_selection = None
                self._current_search_selected = False
                self.staticNodeSelected.emit(tr("sidebar.albums"))
            return
        if node_type == NodeType.SEARCH:
            self._current_selection = None
            self._current_static_selection = None
            self._current_pinned_selection = None
            self._current_search_selected = True
            self.searchEntrySelected.emit()
            return
        if node_type == NodeType.STATIC:
            # Home is reachable without a library: it is the landing view shown
            # while the library paths are still resolving.
            if item.title == self.HOME_TITLE:
                self._current_selection = None
                self._current_static_selection = item.title
                self._current_pinned_selection = None
                self._current_search_selected = False
                _logger.debug("_on_selection_changed: emitting homeSelected")
                self.homeSelected.emit()
                return
            if self._library.root() is None:
                _logger.warning(
                    "_on_selection_changed: library root is None, emitting bindLibraryRequested"
                )
                self.bindLibraryRequested.emit()
                return
            self._current_selection = None
            self._current_static_selection = item.title
            self._current_search_selected = False
            if item.title == self.ALL_PHOTOS_TITLE:
                _logger.debug("_on_selection_changed: emitting allPhotosSelected")
                self.allPhotosSelected.emit()
            else:
                _logger.debug("_on_selection_changed: emitting staticNodeSelected(%r)", item.title)
                self.staticNodeSelected.emit(item.title)
            return
        if node_type in {NodeType.PINNED_ALBUM, NodeType.PINNED_PERSON, NodeType.PINNED_GROUP}:
            pinned_item = item.pinned_item
            if pinned_item is None:
                return
            self._current_selection = None
            self._current_static_selection = None
            self._current_pinned_selection = (pinned_item.kind, pinned_item.item_id)
            self._current_search_selected = False
            self.pinnedItemSelected.emit(pinned_item)
            return
        self._current_static_selection = None
        self._current_pinned_selection = None
        self._current_search_selected = False
        album = item.album
        if album is not None:
            self._current_selection = album.path
            self.albumSelected.emit(album.path)

    def _on_double_clicked(self, index: QModelIndex) -> None:
        item = self._model.item_from_index(index)
        if item is None:
            return
        if item.node_type == NodeType.ACTION:
            self.bindLibraryRequested.emit()

    def eventFilter(self, watched: QObject, event) -> bool:
        """Give the header ＋ a button affordance (cursor + hint)."""

        if getattr(event, "type", None) is not None and watched is self._tree.viewport():
            if event.type() == QEvent.Type.MouseMove:
                index = self._tree.indexAt(event.position().toPoint())
                over_action = False
                if index.isValid() and index.data(AlbumTreeRole.HEADER_ACTION):
                    item_rect = self._tree.visualRect(index)
                    over_action = AlbumSidebarDelegate.header_action_rect(item_rect).contains(
                        event.position().toPoint()
                    )
                if over_action:
                    self._show_action_hint(event.position().toPoint())
                else:
                    self._tree.viewport().unsetCursor()
                    self._hide_action_hint()
            elif event.type() == QEvent.Type.Leave:
                self._tree.viewport().unsetCursor()
                self._hide_action_hint()
        return super().eventFilter(watched, event)

    def _build_action_hint(self) -> None:
        """Create the in-widget hint shown over the header ＋.

        ``QToolTip`` was used first, but on Windows the native popup follows the
        system colour scheme while its text colour comes from the application
        palette, which produced a black box with dark text that no stylesheet could
        fix. Painting our own label keeps the contrast under our control.
        """

        hint = QLabel(self)
        hint.setObjectName("sidebarActionHint")
        hint.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        hint.setStyleSheet(
            "QLabel#sidebarActionHint {"
            "  background-color: #FFFFFF;"
            "  color: #1D1D1F;"
            "  border: 1px solid rgba(0, 0, 0, 0.12);"
            "  border-radius: 6px;"
            "  padding: 3px 8px;"
            "  font-size: 12px;"
            "}"
        )
        hint.hide()
        self._action_hint = hint

    def _show_action_hint(self, viewport_pos: QPoint) -> None:
        hint = getattr(self, "_action_hint", None)
        if hint is None:
            return
        hint.setText(tr("action.set_basic_library"))
        hint.adjustSize()

        # Anchor under the pointer but keep the label inside the sidebar.
        anchor = self._tree.viewport().mapTo(self, viewport_pos + QPoint(12, 18))
        x = max(2, min(self.width() - hint.width() - 2, anchor.x()))
        y = max(2, min(self.height() - hint.height() - 2, anchor.y()))
        hint.move(x, y)
        hint.show()
        hint.raise_()

    def _hide_action_hint(self) -> None:
        hint = getattr(self, "_action_hint", None)
        if hint is not None:
            hint.hide()

    def _on_clicked(self, index: QModelIndex) -> None:
        """Toggle expansion when the branch indicator hot zone is clicked,
        or toggle checkbox when the checkbox area is clicked."""

        if not index.isValid():
            return

        delegate = self._tree.itemDelegate()
        if not isinstance(delegate, AlbumSidebarDelegate):
            return

        try:
            item_rect = self._tree.visualRect(index)
        except Exception:
            return
        if not item_rect.isValid():
            return

        # Inline action on a section header (currently "bind library"): it lives in
        # the header row so it can never be mistaken for an album entry.
        if index.data(AlbumTreeRole.HEADER_ACTION):
            from ..delegates.album_sidebar_delegate import AlbumSidebarDelegate as _Delegate

            action_rect = _Delegate.header_action_rect(item_rect)
            viewport_pos = self._tree.viewport().mapFromGlobal(QCursor.pos())
            if action_rect.contains(viewport_pos):
                self.bindLibraryRequested.emit()
                return

        cursor_pos = QCursor.pos()
        viewport_pos = self._tree.viewport().mapFromGlobal(cursor_pos)

        # Check if click is on the checkbox area (for checkable albums)
        if delegate._is_checkable_album(index):
            from ..delegates.album_sidebar_delegate import (
                SIDEBAR_CHECKBOX_SIZE,
                SIDEBAR_CHECKBOX_ICON_GAP,
            )
            from ..palette import SIDEBAR_INDICATOR_SLOT_WIDTH
            depth = delegate._depth_for_index(index)
            indentation = depth * SIDEBAR_INDENT_PER_LEVEL
            x = item_rect.left() + SIDEBAR_LEFT_PADDING + indentation
            has_children = self._model.hasChildren(index)
            if has_children:
                x += SIDEBAR_INDICATOR_SIZE + SIDEBAR_BRANCH_CONTENT_GAP
            elif indentation > 0:
                x += SIDEBAR_INDICATOR_SLOT_WIDTH
            checkbox_rect = QRect(
                x,
                item_rect.top() + (item_rect.height() - SIDEBAR_CHECKBOX_SIZE) // 2,
                SIDEBAR_CHECKBOX_SIZE,
                SIDEBAR_CHECKBOX_SIZE,
            )
            if checkbox_rect.contains(viewport_pos):
                current = index.data(Qt.ItemDataRole.CheckStateRole)
                new_state = (
                    Qt.CheckState.Unchecked
                    if current == Qt.CheckState.Checked
                    else Qt.CheckState.Checked
                )
                self._model.setData(index, new_state, Qt.ItemDataRole.CheckStateRole)
                return

        # Otherwise check if click is on the branch indicator hot zone
        if not self._model.hasChildren(index):
            return

        depth = delegate._depth_for_index(index)
        indentation = depth * SIDEBAR_INDENT_PER_LEVEL
        indicator_left = item_rect.left() + SIDEBAR_LEFT_PADDING + indentation
        indicator_rect = QRect(
            indicator_left,
            item_rect.top() + (item_rect.height() - SIDEBAR_INDICATOR_SIZE) // 2,
            SIDEBAR_INDICATOR_SIZE,
            SIDEBAR_INDICATOR_SIZE,
        )

        hot_zone = indicator_rect.adjusted(
            -SIDEBAR_INDICATOR_HOTZONE_MARGIN,
            -SIDEBAR_INDICATOR_HOTZONE_MARGIN,
            SIDEBAR_INDICATOR_HOTZONE_MARGIN,
            SIDEBAR_INDICATOR_HOTZONE_MARGIN,
        )
        if not hot_zone.contains(viewport_pos):
            return

        if self._tree.isExpanded(index):
            self._tree.collapse(index)
        else:
            self._tree.expand(index)

    def select_path(self, path: Path) -> None:
        """Select the tree item corresponding to *path* if it exists."""

        index = self._model.index_for_path(path)
        if not index.isValid():
            return

        self._current_static_selection = None
        self._current_pinned_selection = None
        self._current_selection = path
        self._current_search_selected = False

        # When programmatically selecting an album in response to an external event (like
        # 'albumOpened'), we must suppress the sidebar's selection signals.
        # Failing to block signals creates a feedback loop:
        # 1. NavigationController opens Album A -> calls sidebar.select_path(A)
        # 2. sidebar.select_path(A) -> emits albumSelected(A)
        # 3. albumSelected(A) -> triggers NavigationController.open_album(A)
        #
        # While NavigationController has checks for redundant opens, race conditions in
        # rapid switching can bypass them, leading to infinite loops or crashes.
        # Blocking signals ensures this method only updates the visual state without
        # triggering further navigation logic.
        selection_model = self._tree.selectionModel()
        if selection_model is not None:
            selection_model.blockSignals(True)
        try:
            self._tree.setCurrentIndex(index)
        finally:
            if selection_model is not None:
                selection_model.blockSignals(False)

        try:
            self._tree.scrollTo(index)
        except RuntimeError:
            # The tree can already be torn down while the window is closing.
            _logger.debug("album tree scrollTo failed", exc_info=True)

    def select_pinned_item(self, pinned_item: PinnedSidebarItem, emit_signal: bool = False) -> None:
        """Select the sidebar row associated with *pinned_item* when present."""

        index = self._model.index_for_pinned_item(pinned_item)
        if not index.isValid():
            return

        self._current_selection = None
        self._current_static_selection = None
        self._current_pinned_selection = (pinned_item.kind, pinned_item.item_id)
        self._current_search_selected = False

        selection_model = self._tree.selectionModel()
        already_selected = selection_model is not None and self._tree.currentIndex() == index
        if selection_model is not None and not emit_signal:
            selection_model.blockSignals(True)
        try:
            self._tree.setCurrentIndex(index)
        finally:
            if selection_model is not None and not emit_signal:
                selection_model.blockSignals(False)
        try:
            self._tree.scrollTo(index)
        except RuntimeError:
            # The tree can already be torn down while the window is closing.
            _logger.debug("album tree scrollTo failed", exc_info=True)
        if emit_signal and already_selected:
            self.pinnedItemSelected.emit(pinned_item)

    def select_home(self, emit_signal: bool = False) -> None:
        """Select the "Home" static node if it is available."""

        _logger.debug("select_home: emit_signal=%s", emit_signal)
        self.select_static_node(self.HOME_TITLE, emit_signal=emit_signal)

    def select_all_photos(self, emit_signal: bool = False) -> None:
        """Select the "All Photos" static node if it is available."""

        _logger.debug("select_all_photos: emit_signal=%s", emit_signal)
        self.select_static_node(self.ALL_PHOTOS_TITLE, emit_signal=emit_signal)

    def set_search_state(
        self,
        query: str,
        *,
        loading: bool = False,
        count: int | None = None,
        failed: bool = False,
    ) -> None:
        """Create or update the sidebar entry for the active search."""

        self._model.set_search_state(
            query, loading=loading, count=count, failed=failed
        )

    def clear_search(self) -> None:
        """Remove the search entry and move the highlight back to All Photos."""

        self._model.clear_search()
        if self._current_search_selected:
            self._current_search_selected = False
            self.select_all_photos(emit_signal=False)

    def select_search_entry(self, emit_signal: bool = False) -> None:
        """Highlight the active search entry when one is shown."""

        index = self._model.search_index()
        if not index.isValid():
            return
        self._current_selection = None
        self._current_static_selection = None
        self._current_pinned_selection = None
        self._current_search_selected = True

        selection_model = self._tree.selectionModel()
        already_selected = (
            selection_model is not None and self._tree.currentIndex() == index
        )
        if selection_model is not None and not emit_signal:
            selection_model.blockSignals(True)
        try:
            self._tree.setCurrentIndex(index)
        finally:
            if selection_model is not None and not emit_signal:
                selection_model.blockSignals(False)
        try:
            self._tree.scrollTo(index)
        except RuntimeError:
            # The tree can already be torn down while the window is closing.
            _logger.debug("album tree scrollTo failed", exc_info=True)
        if emit_signal and already_selected:
            self.searchEntrySelected.emit()

    def select_static_node(self, title: str, emit_signal: bool = False) -> None:
        """Select the static node matching *title* when present."""

        import logging
        self._current_selection = None
        self._current_static_selection = title
        self._current_pinned_selection = None
        self._current_search_selected = False

        index = self._find_static_index(title)
        if not index.isValid():
            _logger.debug("select_static_node: '%s' not found in model", title)
            logging.getLogger(__name__).warning(
                "[DIAG] select_static_node: '%s' not found, model rowCount=%s root_child_count=%s",
                title, self._model.rowCount(), self._model.rowCount(self._model.index(0, 0)),
            )
            return
        diag(
            "[DIAG] select_static_node: '%s' found, emit_signal=%s", title, emit_signal,
        )

        # Check whether the target index is already selected.  When the caller
        # requests signal emission but the selection model considers the index
        # unchanged, ``selectionChanged`` will not fire and downstream handlers
        # (e.g. loading "All Photos" after binding a library) will be skipped.
        selection_model = self._tree.selectionModel()
        has_selection_model = selection_model is not None
        already_selected = (
            has_selection_model
            and self._tree.currentIndex() == index
        )

        # Block signals for static nodes as well to prevent similar feedback loops
        # when programmatically restoring state (e.g. at startup or after resets).
        should_block = has_selection_model and not emit_signal
        if should_block:
            selection_model.blockSignals(True)
        try:
            self._tree.setCurrentIndex(index)
        finally:
            if should_block:
                selection_model.blockSignals(False)

        # When ``emit_signal`` is requested but the index was already current,
        # ``setCurrentIndex`` is a no-op and ``selectionChanged`` never fires.
        # Manually invoke the handler so the view transition still occurs.
        if emit_signal and already_selected:
            _logger.debug(
                "select_static_node: '%s' was already selected, manually triggering handler",
                title,
            )
            self._on_selection_changed(None, None)

        try:
            self._tree.scrollTo(index)
        except RuntimeError:
            # The tree can already be torn down while the window is closing.
            _logger.debug("album tree scrollTo failed", exc_info=True)

    def _show_context_menu(self, point: QPoint) -> None:
        show_context_menu(
            parent=self,
            point=point,
            tree=self._tree,
            model=self._model,
            library=self._library,
            set_pending_selection=self._set_pending_selection,
            on_bind_library=self.bindLibraryRequested.emit,
            on_exclude_album=self._on_exclude_album,
        )

    def _on_exclude_album(self, path: Path) -> None:
        self.excludeAlbumRequested.emit(path)

    def _set_pending_selection(self, target: Path | None) -> None:
        self._pending_selection = target

    def _find_static_index(self, title: str) -> QModelIndex:
        # The first root row is not always a static entry: an active search entry
        # and the section separators share the root level. Search the root level
        # and its children instead of assuming a fixed layout.
        for row in range(self._model.rowCount()):
            top_index = self._model.index(row, 0)
            if not top_index.isValid():
                continue
            top_item = self._model.item_from_index(top_index)
            if top_item is not None and top_item.title == title:
                return top_index
            for child_row in range(self._model.rowCount(top_index)):
                index = self._model.index(child_row, 0, top_index)
                child = self._model.item_from_index(index)
                if child and child.title == title:
                    return index
        return QModelIndex()

    def _on_data_changed(self, topLeft, bottomRight, roles=None) -> None:
        if roles and Qt.ItemDataRole.CheckStateRole in roles:
            checked = self._model.checked_root_paths()
            self.albumCheckStateChanged.emit(checked)

    def _on_files_dropped(self, target: Path, paths: list[Path]) -> None:
        """Relay drop notifications to consumers of :class:`AlbumSidebar`."""

        if not paths:
            return
        self.filesDropped.emit(target, paths)


__all__ = ["AlbumSidebar"]
