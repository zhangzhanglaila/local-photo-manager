"""Shared colour utilities and constants for the Qt GUI layer."""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette, QFont
from PySide6.QtWidgets import QWidget

# --- Sidebar colour palette -------------------------------------------------
# Accent used by the navigation sidebar for the active row (left bar, icon and
# emphasis). It follows the same warm tone as the Home page wordmark instead of
# the system blue, so the two surfaces read as one product.
SIDEBAR_ACCENT_COLOR_HEX = "#7B5E3B"
SIDEBAR_ACCENT_COLOR = QColor(SIDEBAR_ACCENT_COLOR_HEX)
SIDEBAR_ACCENT_COLOR_DARK_HEX = "#C9A86C"

# macOS system blue, still exposed for the palette's Link role (legacy icons and
# tests rely on it) even though navigation icons are tinted from the text colour.
SIDEBAR_ICON_COLOR_HEX = "#007AFF"
SIDEBAR_ICON_COLOR = QColor(SIDEBAR_ICON_COLOR_HEX)

# Core structural colours. The sidebar is a near-white surface with a hairline
# divider, and rows use a muted grey label until they are selected.
SIDEBAR_BACKGROUND_COLOR = QColor("#F9F9F9")
SIDEBAR_TEXT_COLOR = QColor("#0D0D0D")
SIDEBAR_TITLE_COLOR_HEX = "#0D0D0D"
SIDEBAR_SECTION_TEXT_COLOR = QColor(110, 110, 128)
SIDEBAR_SECONDARY_TEXT_COLOR = QColor("#6E6E80")
SIDEBAR_DISABLED_TEXT_COLOR = QColor(60, 60, 67, 90)
SIDEBAR_SEPARATOR_COLOR = QColor(0, 0, 0, 23)

# Interaction feedback colours. Hover and selection share the same pill, which
# is what the reference sidebar does: the active row is told apart by its weight
# and accent bar rather than by a stronger fill.
SIDEBAR_HOVER_BACKGROUND = QColor("#ECECEC")
SIDEBAR_SELECTED_BACKGROUND = QColor("#ECECEC")

# --- Sidebar metrics --------------------------------------------------------
# Roomy 40 pt rows with 20 pt glyphs: the sidebar carries the whole navigation,
# so it needs the breathing room of a full application sidebar rather than the
# compact 32 pt list it used to be.
SIDEBAR_ROW_HEIGHT = 40
SIDEBAR_ROW_RADIUS = 10
SIDEBAR_SEPARATOR_HEIGHT = 22
SIDEBAR_LEFT_PADDING = 14
SIDEBAR_ICON_TEXT_GAP = 12
SIDEBAR_BRANCH_CONTENT_GAP = 6
SIDEBAR_INDICATOR_HOTZONE_MARGIN = 4
SIDEBAR_INDENT_PER_LEVEL = 20  # One step per album level; 32 made the gap between the
# section header and the first album dwarf the steps below it.
SIDEBAR_INDICATOR_SLOT_WIDTH = 22
SIDEBAR_INDICATOR_SIZE = 16
SIDEBAR_ICON_SIZE = 20

# Margins around the rounded selection pill. The tree view itself is already
# inset horizontally, so the pill spans the row and only needs a 1 pt breathing
# gap vertically between neighbouring rows.
SIDEBAR_HIGHLIGHT_MARGIN_X = 0
SIDEBAR_HIGHLIGHT_MARGIN_Y = 1

# Left accent bar drawn against the selected pill's leading edge.
SIDEBAR_ACCENT_BAR_WIDTH = 3
SIDEBAR_ACCENT_BAR_HEIGHT = 16
SIDEBAR_ACCENT_BAR_RADIUS = 1.5

# Tree level styling shared by the widget and delegate.
SIDEBAR_TREE_MIN_WIDTH = 220
SIDEBAR_TREE_STYLESHEET = (
    "QTreeView { background: transparent; border: none; }"
    "QTreeView::item { border: 0px; padding: 0px; margin: 0px; }"
    "QTreeView::branch { image: none; }"
)

# Default layout chrome values for the sidebar wrapper widget. The horizontal
# inset leaves room for the selection pill so it never touches the window edge.
SIDEBAR_LAYOUT_MARGIN = (10, 12, 10, 12)
SIDEBAR_LAYOUT_SPACING = 0

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
    "SIDEBAR_ACCENT_COLOR_HEX",
    "SIDEBAR_ACCENT_COLOR",
    "SIDEBAR_ACCENT_COLOR_DARK_HEX",
    "SIDEBAR_ICON_COLOR_HEX",
    "SIDEBAR_ICON_COLOR",
    "SIDEBAR_BACKGROUND_COLOR",
    "SIDEBAR_TEXT_COLOR",
    "SIDEBAR_TITLE_COLOR_HEX",
    "SIDEBAR_SECTION_TEXT_COLOR",
    "SIDEBAR_SECONDARY_TEXT_COLOR",
    "SIDEBAR_DISABLED_TEXT_COLOR",
    "SIDEBAR_SEPARATOR_COLOR",
    "SIDEBAR_HOVER_BACKGROUND",
    "SIDEBAR_SELECTED_BACKGROUND",
    "SIDEBAR_ROW_HEIGHT",
    "SIDEBAR_SEPARATOR_HEIGHT",
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
    "SIDEBAR_ACCENT_BAR_WIDTH",
    "SIDEBAR_ACCENT_BAR_HEIGHT",
    "SIDEBAR_ACCENT_BAR_RADIUS",
    "SIDEBAR_TREE_MIN_WIDTH",
    "SIDEBAR_TREE_STYLESHEET",
    "SIDEBAR_LAYOUT_MARGIN",
    "SIDEBAR_LAYOUT_SPACING",
    "viewer_surface_color",
]
