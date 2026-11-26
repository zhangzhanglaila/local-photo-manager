"""Context menu helpers for the album sidebar widget."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QPoint, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QMenu, QTreeView, QWidget

from ..ui.widgets import dialogs
from ...errors import LibraryError
from ...i18n import tr
from ...library.runtime_controller import LibraryRuntimeController
from ...library.tree import AlbumNode
from ..ui.models.album_tree_model import AlbumTreeItem, AlbumTreeModel, NodeType
from .style import apply_menu_style


def _apply_main_window_menu_style(menu: QMenu, anchor: QWidget | None) -> None:
    """Backward-compatible alias for the shared menu styling helper."""

    apply_menu_style(menu, anchor)


def _create_styled_input_dialog(
    parent: QWidget, title: str, label: str, text: str = ""
) -> tuple[str, bool]:
    """Prompt for a single line of text using the app-styled input card.

    Kept as a module-level helper because callers and tests patch it by name.
    """

    from ..ui.widgets.input_dialog import prompt_text

    return prompt_text(parent, title=title, label=label, text=text)


class AlbumSidebarContextMenu(QMenu):
    """Context menu providing album management actions."""

    def __init__(
        self,
        parent: QWidget,
        tree: QTreeView,
        model: AlbumTreeModel,
        library: LibraryRuntimeController,
        item: AlbumTreeItem,
        set_pending_selection: Callable[[Path | None], None],
        on_bind_library: Callable[[], None],
        on_exclude_album: Callable[[Path], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self._tree = tree
        self._model = model
        self._library = library
        self._item = item
        self._set_pending_selection = set_pending_selection
        self._on_bind_library = on_bind_library
        self._on_exclude_album = on_exclude_album
        # Ensure the popup renders with rounded opaque styling by reusing the palette-aware rules
        # published by the main window whenever they are available.
        _apply_main_window_menu_style(self, parent)
        self._build_menu()

    def _build_menu(self) -> None:
        if self._item.node_type in {NodeType.HEADER, NodeType.SECTION}:
            self.addAction(tr("menu.new_album"), self._prompt_new_album)
        if self._item.node_type == NodeType.ALBUM:
            self.addAction(self._album_pin_label(), self._toggle_album_pin)
            self.addSeparator()
            self.addAction(
                tr("menu.new_sub_album"),
                lambda: self._prompt_new_album(self._item),
            )
            self.addAction(
                tr("menu.rename_album"),
                lambda: self._prompt_rename_album(self._item),
            )
            self.addSeparator()
            self.addAction(
                tr("menu.show_in_file_manager"),
                lambda: self._reveal_path(self._item.album),
            )
            if self._on_exclude_album is not None and self._item.album is not None:
                self.addAction(
                    tr("menu.exclude_from_scan"),
                    lambda: self._on_exclude_album(self._item.album.path),
                )
        if self._item.node_type == NodeType.SUBALBUM:
            self.addAction(self._album_pin_label(), self._toggle_album_pin)
            self.addSeparator()
            self.addAction(
                tr("menu.rename_album"),
                lambda: self._prompt_rename_album(self._item),
            )
            self.addSeparator()
            self.addAction(
                tr("menu.show_in_file_manager"),
                lambda: self._reveal_path(self._item.album),
            )
            if self._on_exclude_album is not None and self._item.album is not None:
                self.addAction(
                    tr("menu.exclude_from_scan"),
                    lambda: self._on_exclude_album(self._item.album.path),
                )
        if self._item.node_type == NodeType.PINNED_ALBUM:
            if self._item.album is not None:
                self.addAction(
                    tr("menu.rename_album"),
                    lambda: self._prompt_rename_album(self._item),
                )
            else:
                self.addAction(tr("menu.rename"), self._prompt_rename_pinned_item)
            self.addSeparator()
            self.addAction(tr("menu.unpin"), self._unpin_sidebar_item)
        if self._item.node_type in {
            NodeType.PINNED_PERSON,
            NodeType.PINNED_GROUP,
        }:
            self.addAction(tr("menu.rename"), self._prompt_rename_pinned_item)
            self.addSeparator()
            self.addAction(tr("menu.unpin"), self._unpin_sidebar_item)
        if self._item.node_type == NodeType.ACTION:
            self.addAction(tr("action.set_basic_library"), self._on_bind_library)

    def _album_pin_label(self) -> str:
        album = self._item.album
        if album is None:
            return tr("menu.pin_album")
        return tr("menu.unpin_album") if self._is_album_pinned(album.path) else tr("menu.pin_album")

    def _toggle_album_pin(self) -> None:
        pinned_service = self._model._pinned_service
        album = self._item.album
        library_root = self._library.root()
        if pinned_service is None or album is None or library_root is None:
            return
        if self._is_album_pinned(album.path):
            pinned_service.unpin(
                kind="album",
                item_id=str(album.path),
                library_root=library_root,
            )
            return
        pinned_service.pin_album(
            album.path,
            album.title,
            library_root=library_root,
        )

    def _unpin_sidebar_item(self) -> None:
        pinned_service = self._model._pinned_service
        pinned_item = self._item.pinned_item
        library_root = self._library.root()
        if pinned_service is None or pinned_item is None or library_root is None:
            return
        pinned_service.unpin(
            kind=pinned_item.kind,
            item_id=pinned_item.item_id,
            library_root=library_root,
        )

    def _prompt_rename_pinned_item(self) -> None:
        pinned_service = self._model._pinned_service
        pinned_item = self._item.pinned_item
        library_root = self._library.root()
        if pinned_service is None or pinned_item is None or library_root is None:
            return

        name, ok = _create_styled_input_dialog(
            self.parentWidget(),
            tr("dialog.rename_pinned_item"),
            tr("dialog.new_pinned_label"),
            text=self._item.title or pinned_item.label,
        )
        if not ok:
            return
        target_name = name.strip()
        if not target_name:
            dialogs.show_warning(self.parentWidget(), tr("msg.pinned_label_empty"))
            return
        pinned_service.rename_item(
            kind=pinned_item.kind,
            item_id=pinned_item.item_id,
            label=target_name,
            library_root=library_root,
        )

    def _is_album_pinned(self, album_path: Path) -> bool:
        pinned_service = self._model._pinned_service
        if pinned_service is None:
            return False
        return pinned_service.is_pinned(
            kind="album",
            item_id=str(album_path),
            library_root=self._library.root(),
        )

    def _prompt_new_album(self, parent_item: AlbumTreeItem | None = None) -> None:
        base_item = parent_item
        if base_item is None:
            index = self._tree.currentIndex()
            base_item = self._model.item_from_index(index)

        if base_item is None:
            return

        name, ok = _create_styled_input_dialog(self.parentWidget(), tr("dialog.new_album"), tr("dialog.album_name"))
        if not ok:
            return
        target_name = name.strip()
        if not target_name:
            dialogs.show_warning(self.parentWidget(), tr("msg.album_name_empty"))
            return
        try:
            if base_item.node_type == NodeType.ALBUM and base_item.album is not None:
                node = self._library.create_subalbum(base_item.album, target_name)
            else:
                node = self._library.create_album(target_name)
        except LibraryError as exc:  # pragma: no cover - GUI feedback
            dialogs.show_warning(self.parentWidget(), str(exc))
            return
        self._set_pending_selection(node.path)

    def _prompt_rename_album(self, item: AlbumTreeItem) -> None:
        if item.album is None:
            return
        current_title = item.album.title
        name, ok = _create_styled_input_dialog(
            self.parentWidget(),
            tr("dialog.rename_album"),
            tr("dialog.new_album_name"),
            text=current_title,
        )
        if not ok:
            return
        target_name = name.strip()
        if not target_name:
            dialogs.show_warning(self.parentWidget(), tr("msg.album_name_empty"))
            return
        try:
            self._library.rename_album(item.album, target_name)
        except LibraryError as exc:  # pragma: no cover - GUI feedback
            dialogs.show_warning(self.parentWidget(), str(exc))
            return
        self._set_pending_selection(item.album.path.parent / target_name)

    @staticmethod
    def _reveal_path(album: AlbumNode | None) -> None:
        if album is None:
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(album.path)))


def show_context_menu(
    parent: QWidget,
    point: QPoint,
    tree: QTreeView,
    model: AlbumTreeModel,
    library: LibraryRuntimeController,
    set_pending_selection: Callable[[Path | None], None],
    on_bind_library: Callable[[], None],
    on_exclude_album: Callable[[Path], None] | None = None,
) -> None:
    """Display the context menu for the album sidebar."""

    index = tree.indexAt(point)
    global_pos = tree.viewport().mapToGlobal(point)

    if not index.isValid():
        menu = QMenu(parent)
        _apply_main_window_menu_style(menu, parent)

        menu.addAction(tr("action.set_basic_library"), on_bind_library)
        menu.exec(global_pos)
        return

    item = model.item_from_index(index)
    if item is None:
        return

    menu = AlbumSidebarContextMenu(
        parent,
        tree,
        model,
        library,
        item,
        set_pending_selection,
        on_bind_library,
        on_exclude_album=on_exclude_album,
    )
    if not menu.isEmpty():
        menu.exec(global_pos)


__all__ = ["AlbumSidebarContextMenu", "show_context_menu"]
