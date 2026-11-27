"""Map-source selector for :class:`PhotoMapView`.

The source modes and the selector row are one concern: switching the mode
re-resolves the runtime through the view's ``_handle_map_source_changed``.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QToolButton, QWidget

from ....i18n import tr


_MAP_SOURCE_LOCAL = "local"

_MAP_SOURCE_GAODE = "gaode_standard"

_MAP_SOURCE_ESRI = "esri_streets"

_MAP_SOURCE_CARTO = "carto_voyager"

_MAP_SOURCE_OSM = "osm_standard"

_MAP_SOURCE_APPLE = "apple_mapkit"

_ONLINE_MAP_SOURCE_MODES = {
    _MAP_SOURCE_APPLE,
    _MAP_SOURCE_GAODE,
    _MAP_SOURCE_ESRI,
    _MAP_SOURCE_CARTO,
    _MAP_SOURCE_OSM,
}



class PhotoMapSourceMixin:
    """Build the local/online map-source selector row."""

    def _build_source_bar(self) -> QWidget:
        bar = QWidget(self)
        bar.setObjectName("mapSourceBar")
        bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        bar.setStyleSheet(
            "QWidget#mapSourceBar { background: #f6f8fa; border: none; }"
            "QLabel { color: #263238; font-size: 12px; }"
            "QComboBox {"
            " min-width: 160px;"
            " padding: 4px 28px 4px 8px;"
            " color: #1f2933;"
            " background: #ffffff;"
            " border: 1px solid #aeb8c2;"
            " border-radius: 4px;"
            "}"
            "QComboBox:hover { border-color: #7d8b99; }"
            "QComboBox::drop-down {"
            " width: 22px;"
            " border-left: 1px solid #c6ced6;"
            " background: #eef2f5;"
            "}"
            "QComboBox QAbstractItemView {"
            " color: #1f2933;"
            " background: #ffffff;"
            " selection-color: #ffffff;"
            " selection-background-color: #2f6fed;"
            " border: 1px solid #aeb8c2;"
            " outline: 0;"
            "}"
        )
        row = QHBoxLayout(bar)
        row.setContentsMargins(10, 6, 10, 6)
        row.setSpacing(8)
        label = QLabel(tr("map.source_label"), bar)
        row.addWidget(label)
        selector = QComboBox(bar)
        selector.addItem(tr("map.source_local"), _MAP_SOURCE_LOCAL)
        selector.addItem(tr("map.source_gaode"), _MAP_SOURCE_GAODE)
        selector.addItem(tr("map.source_esri"), _MAP_SOURCE_ESRI)
        selector.addItem(tr("map.source_carto"), _MAP_SOURCE_CARTO)
        selector.addItem(tr("map.source_osm"), _MAP_SOURCE_OSM)
        selector.addItem(tr("map.source_apple"), _MAP_SOURCE_APPLE)
        source_index = selector.findData(self._map_source_mode)
        selector.setCurrentIndex(max(0, source_index))
        selector.currentIndexChanged.connect(self._handle_map_source_changed)
        row.addWidget(selector)

        self.btn_person_filter = QToolButton(bar)
        self.btn_person_filter.setText("👤 人物")
        self.btn_person_filter.setCheckable(True)
        self.btn_person_filter.setAutoRaise(True)
        self.btn_person_filter.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.btn_person_filter.setFixedHeight(22)
        self.btn_person_filter.setStyleSheet(
            "QToolButton { font-size: 11px; padding: 1px 4px; border: none; }"
            "QToolButton:checked { background: #d0d0d0; border-radius: 3px; }"
        )
        row.addWidget(self.btn_person_filter)

        self.btn_trail_toggle = QToolButton(bar)
        self.btn_trail_toggle.setText("🗺️ 轨迹")
        self.btn_trail_toggle.setCheckable(True)
        self.btn_trail_toggle.setAutoRaise(True)
        self.btn_trail_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.btn_trail_toggle.setFixedHeight(22)
        self.btn_trail_toggle.setStyleSheet(
            "QToolButton { font-size: 11px; padding: 1px 4px; border: none; }"
            "QToolButton:checked { background: #d0d0d0; border-radius: 3px; }"
        )
        row.addWidget(self.btn_trail_toggle)

        row.addStretch(1)
        show_all = QPushButton("显示所有照片", bar)
        show_all.setCursor(Qt.CursorShape.PointingHandCursor)
        show_all.setVisible(False)
        show_all.setStyleSheet(
            "QPushButton {"
            " padding: 4px 10px;"
            " color: #1f2933;"
            " background: #ffffff;"
            " border: 1px solid #aeb8c2;"
            " border-radius: 4px;"
            " font-size: 12px;"
            "}"
            "QPushButton:hover { background: #eef2f5; border-color: #7d8b99; }"
        )
        show_all.clicked.connect(self.showAllRequested.emit)
        row.addWidget(show_all)
        self._map_source_selector = selector
        self._show_all_button = show_all
        return bar

