import json
import os
from pathlib import Path

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for tree tests", exc_type=ImportError)
pytest.importorskip("PySide6.QtWidgets", reason="Qt widgets not available", exc_type=ImportError)

from PySide6.QtCore import QModelIndex
from PySide6.QtWidgets import QApplication

from iPhoto.gui.services.pinned_items_service import PinnedItemsService
from iPhoto.config import HOME_TITLE
from iPhoto.i18n import tr
from iPhoto.gui.ui.models.album_tree_model import AlbumTreeModel, AlbumTreeRole, NodeType
from iPhoto.library.runtime_controller import LibraryRuntimeController
from iPhoto.settings.manager import SettingsManager


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def _create_album(root: Path, title: str, *, child: str | None = None) -> Path:
    album_dir = root / title
    album_dir.mkdir(parents=True, exist_ok=True)
    manifest = album_dir / ".iphoto.album.json"
    manifest.write_text(json.dumps({"schema": "iPhoto/album@1", "title": title}), encoding="utf-8")
    if child is not None:
        child_dir = album_dir / child
        child_dir.mkdir(parents=True, exist_ok=True)
        (child_dir / ".iphoto.album").touch()
        return child_dir
    return album_dir


def _find_child(model: AlbumTreeModel, parent_index, title: str):
    for row in range(model.rowCount(parent_index)):
        index = model.index(row, 0, parent_index)
        if model.data(index) == title:
            return index
    return None


def test_skeleton_when_unbound(qapp: QApplication) -> None:
    """An unbound library still renders the full sidebar skeleton.

    The first frame used to be a single "Bind Basic Library…" row, which made the
    window look like a different, unfinished app until the library resolved. Only
    the album list is genuinely missing before binding.
    """

    manager = LibraryRuntimeController()
    model = AlbumTreeModel(manager)
    qapp.processEvents()

    titles = [model.data(model.index(row, 0)) for row in range(model.rowCount())]
    assert titles[:2] == [tr("sidebar.home"), tr("sidebar.all_photos")]
    assert tr("sidebar.videos") in titles and tr("sidebar.location") in titles
    assert tr("sidebar.albums") in titles
    assert tr("sidebar.recently_deleted") in titles

    albums_index = _find_child(model, QModelIndex(), tr("sidebar.albums"))
    assert albums_index is not None
    assert model.data(albums_index, AlbumTreeRole.NODE_TYPE) == NodeType.HEADER
    # The "+" on the section header is how an unbound library gets bound.
    assert model.data(albums_index, AlbumTreeRole.HEADER_ACTION) == "bind_library"
    assert model.rowCount(albums_index) == 0, "no albums before the library is bound"


def test_model_populates_albums(tmp_path: Path, qapp: QApplication) -> None:
    root = tmp_path / "Library"
    root.mkdir()
    album_dir = _create_album(root, "Trip", child="Day1")
    manager = LibraryRuntimeController()
    manager.bind_path(root)
    qapp.processEvents()
    model = AlbumTreeModel(manager)
    qapp.processEvents()

    # The smart collections are a flat list at the root level: the sidebar's own
    # "Basic Library" section title already labels them, so a bold header row
    # would be a second, redundant heading.
    root_titles = [model.data(model.index(row, 0)) for row in range(model.rowCount())]
    assert "Basic Library" not in root_titles
    assert root_titles[0] == tr("sidebar.home")
    people_index = _find_child(model, QModelIndex(), tr("sidebar.people"))
    location_index = _find_child(model, QModelIndex(), tr("sidebar.location"))
    assert people_index is not None
    assert location_index is not None
    assert people_index.row() < location_index.row()

    # Albums is promoted to a section header, so it must be discovered directly
    # under the root model index rather than nested inside the smart collections.
    # Keeping the test explicit ensures the hierarchy change remains intentional
    # and prevents regressions back to the nested layout when the model refresh
    # logic evolves.
    albums_index = _find_child(model, QModelIndex(), tr("sidebar.albums"))
    assert albums_index is not None
    # Validating the node type ensures the delegate will render the correct font
    # weight and icon treatment associated with header entries, which was the
    # original motivation for promoting the section.
    assert model.data(albums_index, AlbumTreeRole.NODE_TYPE) == NodeType.HEADER
    albums_item = model.item_from_index(albums_index)
    assert albums_item is not None
    # The Albums header must reference the dedicated folder SVG so the sidebar
    # renders the platform-consistent icon instead of a generic glyph.
    assert albums_item.icon_name == "folder.svg"
    # Each bound library is itself a node under "Albums", so its albums hang one
    # level lower and sub-albums one lower again.
    library_index = _find_child(model, albums_index, root.name)
    assert library_index is not None
    assert model.data(library_index, AlbumTreeRole.NODE_TYPE) == NodeType.ALBUM
    trip_index = _find_child(model, library_index, "Trip")
    assert trip_index is not None
    assert model.data(trip_index, AlbumTreeRole.NODE_TYPE) == NodeType.SUBALBUM
    child_index = _find_child(model, trip_index, "Day1")
    assert child_index is not None
    assert model.data(child_index, AlbumTreeRole.NODE_TYPE) == NodeType.SUBALBUM

    mapped_index = model.index_for_path(album_dir)
    assert mapped_index.isValid()
    assert model.data(mapped_index) == "Day1"


def test_model_inserts_pinned_section_between_library_and_albums(tmp_path: Path, qapp: QApplication) -> None:
    root = tmp_path / "Library"
    root.mkdir()
    album_dir = _create_album(root, "Trips")
    manager = LibraryRuntimeController()
    manager.bind_path(root)
    qapp.processEvents()

    settings = SettingsManager(path=tmp_path / "settings.json")
    settings.load()
    pinned_service = PinnedItemsService(settings)
    pinned_service.pin_album(album_dir, "Trips", library_root=root)
    pinned_service.pin_person("person-a", "Alice", library_root=root)

    model = AlbumTreeModel(manager)
    model.set_pinned_service(pinned_service)
    qapp.processEvents()

    pinned_index = _find_child(model, QModelIndex(), tr("sidebar.pinned"))
    albums_index = _find_child(model, QModelIndex(), tr("sidebar.albums"))
    assert pinned_index is not None
    assert albums_index is not None
    assert pinned_index.row() < albums_index.row()
    assert model.data(pinned_index, AlbumTreeRole.NODE_TYPE) == NodeType.HEADER

    alice_index = _find_child(model, pinned_index, "Alice")
    trips_index = _find_child(model, pinned_index, "Trips")
    assert alice_index is not None
    assert trips_index is not None
    assert model.data(alice_index, AlbumTreeRole.NODE_TYPE) == NodeType.PINNED_PERSON
    assert model.data(trips_index, AlbumTreeRole.NODE_TYPE) == NodeType.PINNED_ALBUM


def test_model_pinned_album_uses_current_album_title_over_custom_label(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    root = tmp_path / "Library"
    root.mkdir()
    album_dir = _create_album(root, "Trips")
    manager = LibraryRuntimeController()
    manager.bind_path(root)
    qapp.processEvents()

    settings = SettingsManager(path=tmp_path / "settings.json")
    settings.load()
    pinned_service = PinnedItemsService(settings)
    pinned_service.pin_album(album_dir, "Trips", library_root=root)
    assert pinned_service.rename_item(
        kind="album",
        item_id=album_dir,
        label="Best Trips",
        library_root=root,
    )

    model = AlbumTreeModel(manager)
    model.set_pinned_service(pinned_service)
    qapp.processEvents()

    pinned_item = pinned_service.items_for_library(root)[0]
    pinned_index = model.index_for_pinned_item(pinned_item)
    pinned_tree_item = model.item_from_index(pinned_index)

    assert pinned_index.isValid()
    assert pinned_tree_item is not None
    assert pinned_tree_item.album is not None
    assert pinned_tree_item.album.path == album_dir
    assert model.data(pinned_index) == "Trips"


def test_model_omits_pinned_section_when_empty(tmp_path: Path, qapp: QApplication) -> None:
    root = tmp_path / "Library"
    root.mkdir()
    _create_album(root, "Trips")
    manager = LibraryRuntimeController()
    manager.bind_path(root)
    qapp.processEvents()

    model = AlbumTreeModel(manager)
    qapp.processEvents()

    pinned_index = _find_child(model, QModelIndex(), tr("sidebar.pinned"))
    assert pinned_index is None


def test_model_keeps_missing_pinned_entities_visible_until_clicked(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    root = tmp_path / "Library"
    root.mkdir()
    manager = LibraryRuntimeController()
    manager.bind_path(root)
    qapp.processEvents()

    settings = SettingsManager(path=tmp_path / "settings.json")
    settings.load()
    pinned_service = PinnedItemsService(settings)
    pinned_service.pin_album(root / "Missing Album", "Missing Album", library_root=root)
    pinned_service.pin_person("missing-person", "Ghost", library_root=root)
    pinned_service.pin_group("missing-group", "Group 1", library_root=root)

    model = AlbumTreeModel(manager)
    model.set_pinned_service(pinned_service)
    qapp.processEvents()

    pinned_index = _find_child(model, QModelIndex(), tr("sidebar.pinned"))
    assert pinned_index is not None
    assert _find_child(model, pinned_index, "Missing Album") is not None
    assert _find_child(model, pinned_index, "Ghost") is not None
    assert _find_child(model, pinned_index, "Group 1") is not None


def test_albums_header_carries_inline_action(tmp_path: Path, qapp: QApplication) -> None:
    """The bind action lives on the section header, not as an album-like row.

    Rendered as a normal child row it looked like an album called
    "Set Basic Library…" and could be selected like one.
    """

    library_root = tmp_path / "Library"
    library_root.mkdir()
    _create_album(library_root, "Trips")

    manager = LibraryRuntimeController()
    manager.bind_path(library_root)
    model = AlbumTreeModel(manager)
    qapp.processEvents()
    try:
        header = _find_child(model, QModelIndex(), tr("sidebar.albums"))
        assert header is not None
        assert model.data(header, AlbumTreeRole.NODE_TYPE) == NodeType.HEADER
        assert model.data(header, AlbumTreeRole.HEADER_ACTION) == "bind_library"

        titles = [model.data(model.index(row, 0, header)) for row in range(model.rowCount(header))]
        assert tr("action.set_basic_library") not in titles, "no action row inside the album list"
        assert titles, "albums must still be listed"
    finally:
        manager.shutdown()

def test_header_action_rect_is_inside_the_row(qapp: QApplication) -> None:
    """The inline "＋" must be a hit target inside the header row, not decoration."""

    from PySide6.QtCore import QRect

    from iPhoto.gui.ui.delegates.album_sidebar_delegate import (
        HEADER_ACTION_SIZE,
        AlbumSidebarDelegate,
    )

    row = QRect(0, 0, 260, 32)
    box = AlbumSidebarDelegate.header_action_rect(row)

    assert box.width() == HEADER_ACTION_SIZE
    assert row.contains(box), "the action must be clickable within its row"
    assert box.right() < row.right(), "leave a margin on the right edge"


def test_sidebar_indent_steps_are_uniform() -> None:
    """Every album level must advance by the same amount.

    32 px made the jump from the section header to the first album dwarf the steps
    below it, which read as a broken hierarchy.
    """

    from iPhoto.gui.ui.palette import (
        SIDEBAR_INDENT_PER_LEVEL,
        SIDEBAR_INDICATOR_SLOT_WIDTH,
    )

    assert SIDEBAR_INDENT_PER_LEVEL == 20
    # Header (no chevron) and first level (chevron) differ by exactly one step.
    header_icon = SIDEBAR_INDICATOR_SLOT_WIDTH
    level1_icon = SIDEBAR_INDENT_PER_LEVEL + SIDEBAR_INDICATOR_SLOT_WIDTH
    level2_icon = 2 * SIDEBAR_INDENT_PER_LEVEL + SIDEBAR_INDICATOR_SLOT_WIDTH
    assert level1_icon - header_icon == SIDEBAR_INDENT_PER_LEVEL
    assert level2_icon - level1_icon == SIDEBAR_INDENT_PER_LEVEL

def test_sidebar_action_hint_is_an_in_widget_label(tmp_path: Path, qapp: QApplication) -> None:
    """The hint must be our own label, not a native ``QToolTip``.

    On Windows the native popup takes its background from the system colour scheme
    and its text colour from the app palette, which produced an unreadable black
    box; a label keeps both colours under our control.
    """

    from PySide6.QtCore import QPoint

    from iPhoto.gui.ui.widgets.album_sidebar import AlbumSidebar

    library_root = tmp_path / "Library"
    library_root.mkdir()
    _create_album(library_root, "Trips")

    manager = LibraryRuntimeController()
    manager.bind_path(library_root)
    sidebar = AlbumSidebar(manager, None)
    sidebar.resize(260, 480)
    sidebar.show()
    qapp.processEvents()
    try:
        sidebar._show_action_hint(QPoint(200, 200))
        qapp.processEvents()

        hint = sidebar._action_hint
        assert hint.isVisible() is True
        assert hint.parent() is sidebar, "the hint is painted by the sidebar itself"
        assert hint.width() > 0 and hint.height() > 0

        sidebar._hide_action_hint()
        assert hint.isVisible() is False
    finally:
        sidebar.close()
        manager.shutdown()


def test_model_shows_active_search_entry(tmp_path: Path, qapp: QApplication) -> None:
    """An active search gets its own sidebar row that survives refreshes."""

    library_root = tmp_path / "Library"
    library_root.mkdir()
    manager = LibraryRuntimeController()
    manager.bind_path(library_root)
    qapp.processEvents()
    model = AlbumTreeModel(manager)
    qapp.processEvents()

    model.set_search_state("cat", loading=True)
    qapp.processEvents()
    loading_index = model.search_index()
    assert loading_index.isValid()
    assert model.data(loading_index, AlbumTreeRole.NODE_TYPE) == NodeType.SEARCH
    assert model.data(loading_index) == tr("sidebar.search.loading", query="cat")
    # The entry is the first row, above the library header.
    assert loading_index.row() == 0

    model.set_search_state("cat", count=3)
    qapp.processEvents()
    assert model.data(model.search_index()) == tr(
        "sidebar.search.results", query="cat", count=3
    )

    # Background tree rebuilds (scan chunks) must keep the entry.
    model.refresh()
    qapp.processEvents()
    assert model.search_index().isValid()

    model.clear_search()
    qapp.processEvents()
    assert not model.search_index().isValid()


def test_model_search_entry_reports_failure(tmp_path: Path, qapp: QApplication) -> None:
    library_root = tmp_path / "Library"
    library_root.mkdir()
    manager = LibraryRuntimeController()
    manager.bind_path(library_root)
    qapp.processEvents()
    model = AlbumTreeModel(manager)
    qapp.processEvents()

    model.set_search_state("cat", failed=True)

    assert model.data(model.search_index()) == tr(
        "sidebar.search.failed", query="cat"
    )
