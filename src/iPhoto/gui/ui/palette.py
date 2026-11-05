"""Shared colour utilities and constants for the Qt GUI layer."""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette, QFont
from PySide6.QtWidgets import QWidget

# --- Sidebar colour palette -------------------------------------------------
# macOS system blue, used for key sidebar affordances and icon tinting.
SIDEBAR_ICON_COLOR_HEX = "#007AFF"
SIDEBAR_ICON_COLOR = QColor(SIDEBAR_ICON_COLOR_HEX)

# Core structural colours that govern the sidebar look and feel. The sidebar is a
# neutral translucent grey in macOS rather than the blue-tinted surface this used
# to be, and separators are hairlines instead of 15% black.
SIDEBAR_BACKGROUND_COLOR = QColor("#F0F0F3")
SIDEBAR_TEXT_COLOR = QColor("#1D1D1F")
SIDEBAR_TITLE_COLOR_HEX = "#1D1D1F"
SIDEBAR_SECTION_TEXT_COLOR = QColor(60, 60, 67, 153)
SIDEBAR_DISABLED_TEXT_COLOR = QColor(60, 60, 67, 90)
SIDEBAR_SEPARATOR_COLOR = QColor(0, 0, 0, 23)

# Interaction feedback colours for hover, selection, and focus states.
SIDEBAR_HOVER_BACKGROUND = QColor(0, 0, 0, 12)
SIDEBAR_SELECTED_BACKGROUND = QColor(0, 0, 0, 26)

# --- Sidebar metrics --------------------------------------------------------
# These values ensure the delegate, tree view, and hit testing all share the
# same geometry assumptions. macOS sidebar rows are 28-32 pt tall with a small
# 6 pt selection radius; the old 36 pt rows and 10 pt radius read as "web app".
SIDEBAR_ROW_HEIGHT = 32
SIDEBAR_ROW_RADIUS = 6
SIDEBAR_LEFT_PADDING = 14
SIDEBAR_ICON_TEXT_GAP = 9
SIDEBAR_BRANCH_CONTENT_GAP = 6
SIDEBAR_INDICATOR_HOTZONE_MARGIN = 4
SIDEBAR_INDENT_PER_LEVEL = 32  # ⭐ 增加缩进：从22px增加到32px，使APPLE与子文件夹层级更明显
SIDEBAR_INDICATOR_SLOT_WIDTH = 22
SIDEBAR_INDICATOR_SIZE = 16
SIDEBAR_ICON_SIZE = 24

# Margins around the rounded selection pill.
SIDEBAR_HIGHLIGHT_MARGIN_X = 6
SIDEBAR_HIGHLIGHT_MARGIN_Y = 4

# Tree level styling shared by the widget and delegate.
SIDEBAR_TREE_MIN_WIDTH = 220
SIDEBAR_TREE_STYLESHEET = (
    "QTreeView { background: transparent; border: none; }"
    "QTreeView::item { border: 0px; padding: 0px; margin: 0px; }"
    "QTreeView::branch { image: none; }"
)

# Default layout chrome values for the sidebar wrapper widget.
SIDEBAR_LAYOUT_MARGIN = (12, 12, 12, 12)
SIDEBAR_LAYOUT_SPACING = 8

# ---Edit Sidebar metrics --------------------------------------------------------
Edit_SIDEBAR_FONT = QFont()
Edit_SIDEBAR_FONT.setPointSize(10)
Edit_SIDEBAR_FONT.setWeight(QFont.Weight.Bold)

Edit_SIDEBAR_SUB_FONT = QFont()
Edit_SIDEBAR_SUB_FONT.setPointSize(8)
Edit_SIDEBAR_SUB_FONT.setWeight(QFont.Weight.Medium)

def viewer_surface_color(widget: QWidget) -> str:
    """Return the name of the palette-derived viewer surface colour.

    Using the palette keeps every media canvas perfectly aligned with the
    surrounding chrome, eliminating the subtle mismatches that appear when a
    hard-coded hex value is used instead.  ``widget`` is any control that lives
    inside the detail panel; its palette already reflects the final window
    styling so deriving the colour from it guarantees an exact match.
    """

    background_role = widget.backgroundRole()
    if background_role == QPalette.ColorRole.NoRole:
        background_role = QPalette.ColorRole.Window
    return widget.palette().color(background_role).name()


__all__ = [
    "SIDEBAR_ICON_COLOR_HEX",
    "SIDEBAR_ICON_COLOR",
    "SIDEBAR_BACKGROUND_COLOR",
    "SIDEBAR_TEXT_COLOR",
    "SIDEBAR_TITLE_COLOR_HEX",
    "SIDEBAR_SECTION_TEXT_COLOR",
    "SIDEBAR_DISABLED_TEXT_COLOR",
    "SIDEBAR_SEPARATOR_COLOR",
    "SIDEBAR_HOVER_BACKGROUND",
    "SIDEBAR_SELECTED_BACKGROUND",
    "SIDEBAR_ROW_HEIGHT",
    "SIDEBAR_ROW_RADIUS",
    "SIDEBAR_LEFT_PADDING",
    "SIDEBAR_ICON_TEXT_GAP",
    "SIDEBAR_BRANCH_CONTENT_GAP",
    "SIDEBAR_INDICATOR_HOTZONE_MARGIN",
    "SIDEBAR_INDENT_PER_LEVEL",
    "SIDEBAR_INDICATOR_SLOT_WIDTH",
    "SIDEBAR_INDICATOR_SIZE",
    "SIDEBAR_ICON_SIZE",
    "SIDEBAR_HIGHLIGHT_MARGIN_X",
    "SIDEBAR_HIGHLIGHT_MARGIN_Y",
    "SIDEBAR_TREE_MIN_WIDTH",
    "SIDEBAR_TREE_STYLESHEET",
    "SIDEBAR_LAYOUT_MARGIN",
    "SIDEBAR_LAYOUT_SPACING",
    "viewer_surface_color",
]
