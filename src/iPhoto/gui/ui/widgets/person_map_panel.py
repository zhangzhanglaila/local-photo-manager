"""Person selection panel for map filtering."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, List, Optional

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .flow_layout import FlowLayout

if TYPE_CHECKING:
    from ...people.records import PersonSummary


class PersonCard(QFrame):
    """A small card showing a person's avatar and name."""

    clicked = Signal(str)  # person_id

    def __init__(
        self,
        person_id: str,
        name: str,
        thumbnail_path: Optional[Path] = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.person_id = person_id
        self.person_name = name
        self._selected = False
        self._setup_ui(name, thumbnail_path)

    def _setup_ui(self, name: str, thumbnail_path: Optional[Path]) -> None:
        self.setFixedSize(80, 100)
        self._update_style(False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)

        # Avatar
        self._avatar = QLabel()
        self._avatar.setFixedSize(64, 64)
        self._avatar.setScaledContents(True)
        self._avatar.setAlignment(Qt.AlignCenter)
        self._avatar.setStyleSheet(
            "background: #e0e0e0; border-radius: 32px; font-size: 24px;"
        )

        if thumbnail_path and Path(thumbnail_path).exists():
            pixmap = QPixmap(str(thumbnail_path))
            if not pixmap.isNull():
                self._avatar.setPixmap(pixmap)
        else:
            self._avatar.setText("👤")

        layout.addWidget(self._avatar, 0, Qt.AlignCenter)

        # Name
        display_name = name if name and name != "未命名" else "未命名"
        self._name_label = QLabel(display_name)
        self._name_label.setAlignment(Qt.AlignCenter)
        self._name_label.setStyleSheet("font-size: 11px;")
        self._name_label.setMaximumWidth(72)
        layout.addWidget(self._name_label, 0, Qt.AlignCenter)

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self._update_style(selected)

    def _update_style(self, selected: bool) -> None:
        if selected:
            self.setStyleSheet(
                "PersonCard { border: 2px solid #2196F3; border-radius: 6px; background: #E3F2FD; }"
            )
        else:
            self.setStyleSheet(
                "PersonCard { border: 1px solid #ccc; border-radius: 6px; }"
            )

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.person_id)
        super().mousePressEvent(event)


class PersonMapPanel(QWidget):
    """Panel showing person avatars for map filtering.

    Signals
    -------
    personSelected(str)
        Emitted when a person is selected (person_id).
    personDeselected()
        Emitted when the selection is cleared.
    """

    personSelected = Signal(str)
    personDeselected = Signal()

    def __init__(
        self,
        people_service,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._people_service = people_service
        self._cards: List[PersonCard] = []
        self._selected_person_id: Optional[str] = None
        self._setup_ui()
        self._load_persons()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # Title
        title = QLabel("按人物筛选")
        title.setStyleSheet("font-weight: bold; font-size: 13px;")
        layout.addWidget(title)

        # Search box
        self._search = QLineEdit()
        self._search.setPlaceholderText("搜索人物...")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._filter_list)
        layout.addWidget(self._search)

        # Scrollable grid of person cards — uses FlowLayout so cards
        # wrap responsively to fit the panel width instead of being cut off.
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self._grid_widget = QWidget()
        self._grid_layout = FlowLayout(self._grid_widget, margin=2, h_spacing=4, v_spacing=4)
        self._scroll.setWidget(self._grid_widget)
        layout.addWidget(self._scroll, 1)

        # "Show all" button
        self._btn_all = QPushButton("显示全部")
        self._btn_all.clicked.connect(self._on_deselect)
        layout.addWidget(self._btn_all)

    def _load_persons(self) -> None:
        """Load person list from the people service."""
        try:
            summaries = self._people_service.list_clusters(include_hidden=False)
        except Exception:
            summaries = []

        for s in summaries:
            name = getattr(s, "name", None) or "未命名"
            person_id = getattr(s, "person_id", "")
            thumbnail_path = getattr(s, "thumbnail_path", None)

            card = PersonCard(person_id, name, thumbnail_path)
            card.clicked.connect(self._on_card_clicked)
            self._grid_layout.addWidget(card)
            self._cards.append(card)

    def _on_card_clicked(self, person_id: str) -> None:
        """Handle person card click."""
        if self._selected_person_id == person_id:
            # Deselect
            self._on_deselect()
            return

        self._selected_person_id = person_id
        for card in self._cards:
            card.set_selected(card.person_id == person_id)
        self.personSelected.emit(person_id)

    def _on_deselect(self) -> None:
        """Clear selection."""
        self._selected_person_id = None
        for card in self._cards:
            card.set_selected(False)
        self.personDeselected.emit()

    def _filter_list(self, text: str) -> None:
        """Filter person cards by name."""
        text_lower = text.lower()
        for card in self._cards:
            visible = not text or text_lower in card.person_name.lower()
            card.setVisible(visible)

    @property
    def selected_person_id(self) -> Optional[str]:
        return self._selected_person_id
