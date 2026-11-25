"""Custom delegate and animations for the album sidebar tree."""

from __future__ import annotations

import math
from dataclasses import dataclass

from PySide6.QtCore import (
    QEasingCurve,
    QModelIndex,
    QObject,
    QRect,
    QRectF,
    QSize,
    Qt,
    QVariantAnimation,
    QPersistentModelIndex,
)
from PySide6.QtGui import QColor, QFont, QFontMetrics, QIcon, QPalette, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QStyledItemDelegate, QStyleOptionViewItem, QTreeView, QStyle, QWidget

from ..icon import load_icon
from ..models.album_tree_model import AlbumTreeItem, AlbumTreeModel, AlbumTreeRole, NodeType
from ..palette import (
    SIDEBAR_ACCENT_BAR_HEIGHT,
    SIDEBAR_ACCENT_BAR_RADIUS,
    SIDEBAR_ACCENT_BAR_WIDTH,
    SIDEBAR_ACCENT_COLOR_HEX,
    SIDEBAR_ACCENT_COLOR_DARK_HEX,
    SIDEBAR_BRANCH_CONTENT_GAP,
    SIDEBAR_HIGHLIGHT_MARGIN_X,
    SIDEBAR_HIGHLIGHT_MARGIN_Y,
    SIDEBAR_ICON_SIZE,
    SIDEBAR_ICON_TEXT_GAP,
    SIDEBAR_INDENT_PER_LEVEL,
    SIDEBAR_INDICATOR_SIZE,
    SIDEBAR_INDICATOR_SLOT_WIDTH,
    SIDEBAR_LEFT_PADDING,
    SIDEBAR_ROW_HEIGHT,
    SIDEBAR_ROW_RADIUS,
    SIDEBAR_SELECTED_BACKGROUND,
    SIDEBAR_SEPARATOR_COLOR,
    SIDEBAR_SEPARATOR_HEIGHT,
)


@dataclass(slots=True)
class _IndicatorState:
    """Track the rendering state for a branch indicator."""

    angle: float = 0.0
    animation: QVariantAnimation | None = None


@dataclass(slots=True)
class _PaintState:
    """Hold derived data needed to render a single tree row."""

    index: QModelIndex
    rect: QRect
    node_type: NodeType
    tree_view: QTreeView | None
    is_enabled: bool
    is_selected: bool
    is_hover: bool
    indentation: int


class BranchIndicatorController(QObject):
    """Animate branch indicators in sync with the tree view state."""

    def __init__(self, tree: QTreeView) -> None:
        super().__init__(tree)
        self._tree = tree
        self._states: dict[QPersistentModelIndex, _IndicatorState] = {}
        self._duration = 180

        self._tree.expanded.connect(self._on_expanded)
        self._tree.collapsed.connect(self._on_collapsed)

        model = tree.model()
        if model is not None:
            model.modelAboutToBeReset.connect(self._clear_states)

    def angle_for_index(self, index: QModelIndex) -> float:
        """Return the current angle associated with *index*."""

        self._cleanup_invalid_states()
        if not index.isValid():
            return 0.0
        key = QPersistentModelIndex(index)
        state = self._states.get(key)
        if state is None:
            angle = 90.0 if self._tree.isExpanded(index) else 0.0
            state = _IndicatorState(angle=angle)
            self._states[key] = state
        return state.angle

    def _start_animation(self, index: QModelIndex, target_angle: float) -> None:
        self._cleanup_invalid_states()
        if not index.isValid():
            return
        key = QPersistentModelIndex(index)
        state = self._states.get(key)
        if state is None:
            state = _IndicatorState(angle=target_angle)
            self._states[key] = state

        if math.isclose(state.angle, target_angle, abs_tol=0.5):
            state.angle = target_angle
            return

        if state.animation is not None:
            state.animation.stop()

        animation = QVariantAnimation(self)
        animation.setStartValue(state.angle)
        animation.setEndValue(target_angle)
        animation.setDuration(self._duration)
        animation.setEasingCurve(QEasingCurve.Type.InOutQuad)

        index_copy = QModelIndex(index)

        def _on_value_changed(value: float) -> None:
            state.angle = float(value)
            self._tree.viewport().update(self._tree.visualRect(index_copy))

        def _on_finished() -> None:
            state.animation = None
            state.angle = target_angle
            self._tree.viewport().update(self._tree.visualRect(index_copy))

        animation.valueChanged.connect(_on_value_changed)
        animation.finished.connect(_on_finished)
        state.animation = animation
        animation.start()

    def _on_expanded(self, index: QModelIndex) -> None:
        self._start_animation(index, 90.0)

    def _on_collapsed(self, index: QModelIndex) -> None:
        self._start_animation(index, 0.0)

    def _clear_states(self) -> None:
        for state in self._states.values():
            if state.animation is not None:
                state.animation.stop()
        self._states.clear()

    def _cleanup_invalid_states(self) -> None:
        invalid = [key for key in self._states.keys() if not key.isValid()]
        for key in invalid:
            state = self._states.pop(key)
            if state.animation is not None:
                state.animation.stop()


# Shared stroke width override applied to sidebar icons to reduce visual aliasing.
SIDEBAR_ICON_STROKE_WIDTH = 2.0
SIDEBAR_CHECKBOX_SIZE = 16
SIDEBAR_CHECKBOX_ICON_GAP = 6

# Section headers may carry one inline action (currently "bind library"). It is
# drawn inside the header row so the action never looks like an album row.
HEADER_ACTION_SIZE = 20
HEADER_ACTION_MARGIN = 6
HEADER_ACTION_RADIUS = 6.0


class AlbumSidebarDelegate(QStyledItemDelegate):
    """Custom delegate painting the sidebar with a macOS inspired style."""

    def sizeHint(  # noqa: D401 - inherited docstring
        self, option: QStyleOptionViewItem, index: QModelIndex
    ) -> QSize:
        width = option.rect.width()
        if width <= 0:
            width = 200
        node_type = index.data(AlbumTreeRole.NODE_TYPE)
        if node_type == NodeType.SEPARATOR:
            # A hairline does not need a full navigation row's height; the shorter
            # row keeps the album sections close to the collections they follow.
            return QSize(width, SIDEBAR_SEPARATOR_HEIGHT)
        return QSize(width, SIDEBAR_ROW_HEIGHT)

    def paint(
        self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex
    ) -> None:
        painter.save()
        rect = option.rect
        node_type = index.data(AlbumTreeRole.NODE_TYPE) or NodeType.ALBUM

        if node_type == NodeType.SEPARATOR:
            self._draw_separator(painter, rect)
            painter.restore()
            return

        state = self._build_paint_state(option, index, rect, node_type)

        highlight = self._resolve_highlight_colour(state)
        if highlight is not None:
            self._draw_background(painter, rect, highlight)
        if state.is_selected:
            self._draw_active_accent(painter, rect, state)

        font = self._font_for_node(option.font, node_type, selected=state.is_selected)
        painter.setFont(font)

        x = rect.left() + SIDEBAR_LEFT_PADDING + state.indentation
        x = self._draw_branch_indicator(painter, state, x)
        x = self._draw_checkbox(painter, state, x)
        icon = self._get_icon_for_paint_state(index, state)
        x = self._draw_icon(painter, option, icon, x)
        self._draw_text(painter, rect, font, index, state, x)

        if index.data(AlbumTreeRole.HEADER_ACTION):
            self._draw_header_action(painter, rect, state)

        painter.restore()

    @staticmethod
    def header_action_rect(rect: QRect) -> QRect:
        """Return the clickable rect of a header's inline action."""

        size = HEADER_ACTION_SIZE
        return QRect(
            rect.right() - HEADER_ACTION_MARGIN - size,
            rect.center().y() - size // 2,
            size,
            size,
        )

    def _draw_header_action(
        self, painter: QPainter, rect: QRect, state: _PaintState
    ) -> None:
        """Draw the header's inline action as a real button, not decoration.

        A bare glyph read as ornamentation, so the action gets a filled rounded
        plate, a border, and a stronger fill while the row is hovered.
        """

        box = self.header_action_rect(rect)
        colour = self._accent_colour(self._palette_for_state(state))
        hovered = bool(getattr(state, "is_hover", False))

        fill = QColor(colour)
        fill.setAlpha(46 if hovered else 22)
        border = QColor(colour)
        border.setAlpha(180 if hovered else 110)
        stroke = QColor(colour)
        stroke.setAlpha(230)

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setBrush(fill)
        painter.setPen(QPen(border, 1.0))
        painter.drawRoundedRect(
            QRectF(box).adjusted(1.0, 1.0, -1.0, -1.0),
            HEADER_ACTION_RADIUS,
            HEADER_ACTION_RADIUS,
        )

        centre = box.center()
        arm = box.width() // 5
        stroke_pen = QPen(stroke, 1.6)
        stroke_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(stroke_pen)
        painter.drawLine(centre.x() - arm, centre.y(), centre.x() + arm, centre.y())
        painter.drawLine(centre.x(), centre.y() - arm, centre.x(), centre.y() + arm)
        painter.restore()

    @staticmethod
    def _accent_colour(palette: QPalette) -> QColor:
        """Return the active-row accent for the palette's light or dark surface."""

        dark_surface = palette.color(QPalette.ColorRole.Window).lightness() < 128
        return QColor(SIDEBAR_ACCENT_COLOR_DARK_HEX if dark_surface else SIDEBAR_ACCENT_COLOR_HEX)

    def _muted_colour(self, palette: QPalette) -> QColor:
        """Return the muted label colour used by unselected navigation rows."""

        colour = QColor(palette.color(QPalette.ColorRole.Text))
        # ~63 % opacity lands on the reference sidebar's muted grey while still
        # following the active theme's text colour.
        colour.setAlpha(160)
        return colour

    def _content_colour(self, state: _PaintState) -> QColor:
        """Return the colour for the row's glyph.

        The accent is reserved for the active row's icon, which together with the
        left bar marks "you are here"; labels stay in the text colour.
        """

        palette = self._palette_for_state(state)
        if state.is_selected:
            return self._accent_colour(palette)
        if not state.is_enabled:
            return palette.color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text)
        if state.node_type == NodeType.ACTION:
            return self._accent_colour(palette)
        return self._muted_colour(palette)

    def _draw_active_accent(
        self, painter: QPainter, rect: QRect, state: _PaintState
    ) -> None:
        """Draw the short accent bar that marks the active row."""

        if state.node_type in {NodeType.SECTION, NodeType.SEPARATOR}:
            return
        pill = rect.adjusted(
            SIDEBAR_HIGHLIGHT_MARGIN_X,
            SIDEBAR_HIGHLIGHT_MARGIN_Y,
            -SIDEBAR_HIGHLIGHT_MARGIN_X,
            -SIDEBAR_HIGHLIGHT_MARGIN_Y,
        )
        bar = QRectF(
            pill.left(),
            pill.center().y() - SIDEBAR_ACCENT_BAR_HEIGHT / 2,
            SIDEBAR_ACCENT_BAR_WIDTH,
            SIDEBAR_ACCENT_BAR_HEIGHT,
        )
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._accent_colour(self._palette_for_state(state)))
        painter.drawRoundedRect(
            bar, SIDEBAR_ACCENT_BAR_RADIUS, SIDEBAR_ACCENT_BAR_RADIUS
        )
        painter.restore()

    def _get_icon_for_paint_state(self, index: QModelIndex, state: _PaintState) -> QIcon:
        """Return the correct icon for *index* based on the current paint *state*."""

        model = index.model()
        item = index.internalPointer()

        icon = QIcon()
        if isinstance(model, AlbumTreeModel) and isinstance(item, AlbumTreeItem):
            # Re-fetch the icon so the glyph can be tinted for the row's state;
            # the model's default decoration is meant for other consumers.
            colour_name = self._content_colour(state).name(QColor.NameFormat.HexArgb)
            icon = model._icon_for_item(
                item,
                stroke_width=SIDEBAR_ICON_STROKE_WIDTH,
                color=colour_name,
            )
        else:
            # Fall back to whatever the model exposed if the expected types differ.
            data = index.data(Qt.ItemDataRole.DecorationRole)
            if isinstance(data, QIcon):
                icon = data

        return icon

    @staticmethod
    def _depth_for_index(index: QModelIndex) -> int:
        depth = 0
        parent = index.parent()
        while parent.isValid():
            depth += 1
            parent = parent.parent()
        return depth

    def _build_paint_state(
        self,
        option: QStyleOptionViewItem,
        index: QModelIndex,
        rect: QRect,
        node_type: NodeType,
    ) -> _PaintState:
        """Compute the immutable rendering state for the current index."""

        tree_view = self._resolve_tree_view(option)
        is_enabled = bool(option.state & QStyle.StateFlag.State_Enabled)
        is_selected = bool(option.state & QStyle.StateFlag.State_Selected)
        is_hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        depth = self._depth_for_index(index)
        indentation = depth * SIDEBAR_INDENT_PER_LEVEL
        if node_type == NodeType.SUBALBUM:
            # An album lives *inside* a bound library. One extra step makes that
            # obvious next to the library row's expand arrow, which otherwise eats
            # most of the indent and leaves the two rows looking level.
            indentation += SIDEBAR_INDENT_PER_LEVEL

        return _PaintState(
            index=index,
            rect=rect,
            node_type=node_type,
            tree_view=tree_view,
            is_enabled=is_enabled,
            is_selected=is_selected,
            is_hover=is_hover,
            indentation=indentation,
        )

    def _palette_for_state(self, state: _PaintState) -> QPalette:
        """Return the palette driving colours for the current sidebar row."""

        if state.tree_view is not None:
            return state.tree_view.palette()
        parent = self.parent()
        if isinstance(parent, QTreeView):
            return parent.palette()
        # Fall back to the application palette so colours remain predictable even if the delegate
        # paints outside its usual tree view.
        return QPalette()

    def _resolve_tree_view(self, option: QStyleOptionViewItem) -> QTreeView | None:
        """Locate the owning :class:`QTreeView` if one is available."""

        if isinstance(option.widget, QTreeView):
            return option.widget
        parent = self.parent()
        if isinstance(parent, QTreeView):
            return parent
        return None

    def _resolve_highlight_colour(self, state: _PaintState) -> QColor | None:
        """Return the hover or selection pill colour, if the row supports one.

        Hover and selection share the same pill; the active row is told apart by
        its accent bar and heavier label, matching the reference sidebar.
        """

        if state.node_type in {NodeType.SECTION, NodeType.SEPARATOR}:
            return None
        if not state.is_selected and not (state.is_hover and state.is_enabled):
            return None

        palette = self._palette_for_state(state)
        highlight = palette.color(QPalette.ColorRole.Highlight)
        if not highlight.isValid() or highlight == QColor():
            # Fall back to the light-theme constant so headless tests still pick up a visible
            # pill.  This path executes when the view inherits a palette without a highlight
            # role, which can happen in synthetic QWidget setups.
            highlight = QColor(SIDEBAR_SELECTED_BACKGROUND)
        else:
            highlight = QColor(highlight)

        if highlight.alpha() == 0:
            highlight = QColor(SIDEBAR_SELECTED_BACKGROUND)

        dark_surface = palette.color(QPalette.ColorRole.Window).lightness() < 128
        if dark_surface and highlight.lightness() > 200:
            # The shared Highlight role carries the light-theme pill. Painting a
            # near-white plate on a dark sidebar would invert the whole
            # hierarchy, so trade it for the translucent light overlay the dark
            # theme actually uses.
            highlight = QColor(255, 255, 255, 30)

        if state.is_selected:
            return highlight

        hover_colour = QColor(highlight)
        hover_colour.setAlpha(min(highlight.alpha(), 170))
        return hover_colour

    def _draw_background(self, painter: QPainter, rect: QRect, colour: QColor) -> None:
        """Paint the rounded selection pill using *colour*."""

        background_rect = QRectF(
            rect.adjusted(
                SIDEBAR_HIGHLIGHT_MARGIN_X,
                SIDEBAR_HIGHLIGHT_MARGIN_Y,
                -SIDEBAR_HIGHLIGHT_MARGIN_X,
                -SIDEBAR_HIGHLIGHT_MARGIN_Y,
            )
        )
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(colour)
        painter.drawRoundedRect(background_rect, SIDEBAR_ROW_RADIUS, SIDEBAR_ROW_RADIUS)

    def _font_for_node(
        self, base_font: QFont, node_type: NodeType, *, selected: bool = False
    ) -> QFont:
        """Return a correctly styled font for *node_type*.

        Navigation rows are 14 px and medium weight; section headers step down to
        13 px semibold so they read as labels rather than entries. The active row
        gains the semibold weight that, together with the accent bar, marks it.
        """

        font = QFont(base_font)
        font.setPixelSize(13 if node_type == NodeType.HEADER else 14)
        font.setWeight(
            QFont.Weight.DemiBold
            if node_type == NodeType.HEADER or selected
            else QFont.Weight.Medium
        )
        if node_type == NodeType.SECTION:
            font.setCapitalization(QFont.Capitalization.SmallCaps)
        if node_type == NodeType.ACTION:
            font.setItalic(True)
        return font

    def _draw_branch_indicator(self, painter: QPainter, state: _PaintState, x: int) -> int:
        """Draw the disclosure triangle and return the next text origin."""

        tree_view = state.tree_view
        index = state.index
        model = index.model()
        has_children = bool(model is not None and model.hasChildren(index))

        if tree_view is None:
            return x + (SIDEBAR_INDICATOR_SLOT_WIDTH if state.indentation > 0 else 0)

        if not has_children:
            if state.indentation > 0:
                return x + SIDEBAR_INDICATOR_SLOT_WIDTH
            return x

        branch_rect = QRect(
            x,
            state.rect.top() + (state.rect.height() - SIDEBAR_INDICATOR_SIZE) // 2,
            SIDEBAR_INDICATOR_SIZE,
            SIDEBAR_INDICATOR_SIZE,
        )

        controller = getattr(tree_view, "branch_indicator_controller", None)
        angle = (
            controller.angle_for_index(index)
            if controller is not None
            else (90.0 if tree_view.isExpanded(index) else 0.0)
        )

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        palette = self._palette_for_state(state)
        active_colour = palette.color(QPalette.ColorRole.Text)
        active_colour.setAlpha(150)
        disabled_colour = palette.color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text)
        indicator_colour = active_colour if state.is_enabled else disabled_colour
        pen = QPen(indicator_colour)
        pen.setWidth(2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.translate(branch_rect.center())
        painter.rotate(angle)

        path = QPainterPath()
        path.moveTo(-2, -4)
        path.lineTo(2, 0)
        path.lineTo(-2, 4)
        painter.drawPath(path)

        painter.restore()

        return branch_rect.right() + SIDEBAR_BRANCH_CONTENT_GAP

    def _is_checkable_album(self, index: QModelIndex) -> bool:
        """Return True if *index* is a root-level album that should show a checkbox."""
        model = index.model()
        if not isinstance(model, AlbumTreeModel):
            return False
        item = index.internalPointer()
        if not isinstance(item, AlbumTreeItem):
            return False
        return item.node_type == NodeType.ALBUM and item.album is not None and item.album.level == 1

    def _draw_checkbox(self, painter: QPainter, state: _PaintState, x: int) -> int:
        """Draw a checkbox for root-level album nodes and return the next x coordinate."""
        if not self._is_checkable_album(state.index):
            return x

        checked = state.index.data(Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked

        opt = QStyleOptionViewItem()
        opt.rect = QRect(
            x,
            state.rect.top() + (state.rect.height() - SIDEBAR_CHECKBOX_SIZE) // 2,
            SIDEBAR_CHECKBOX_SIZE,
            SIDEBAR_CHECKBOX_SIZE,
        )
        opt.state = QStyle.StateFlag.State_Enabled
        if state.is_enabled:
            opt.state |= QStyle.StateFlag.State_Enabled
        if checked:
            opt.state |= QStyle.StateFlag.State_On
        else:
            opt.state |= QStyle.StateFlag.State_Off

        style = self._resolve_style(state)
        style.drawControl(QStyle.ControlElement.CE_CheckBox, opt, painter)

        return x + SIDEBAR_CHECKBOX_SIZE + SIDEBAR_CHECKBOX_ICON_GAP

    def _resolve_style(self, state: _PaintState):
        """Return the QStyle to use for drawing standard controls."""
        if state.tree_view is not None:
            return state.tree_view.style()
        parent = self.parent()
        if isinstance(parent, QWidget):
            return parent.style()
        from PySide6.QtWidgets import QApplication
        return QApplication.style()

    def _draw_icon(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        icon: QIcon,
        x: int,
    ) -> int:
        """Render *icon* and return the x coordinate following the glyph."""

        if icon.isNull():
            return x

        icon_rect = QRect(
            x,
            option.rect.top() + (option.rect.height() - SIDEBAR_ICON_SIZE) // 2,
            SIDEBAR_ICON_SIZE,
            SIDEBAR_ICON_SIZE,
        )
        icon.paint(painter, icon_rect, Qt.AlignmentFlag.AlignCenter)
        return icon_rect.right() + SIDEBAR_ICON_TEXT_GAP

    def _draw_text(
        self,
        painter: QPainter,
        rect: QRect,
        font: QFont,
        index: QModelIndex,
        state: _PaintState,
        x: int,
    ) -> None:
        """Draw the item text with elision if required."""

        text_colour = self._text_colour_for_state(state)
        painter.setPen(text_colour)

        text = index.data(Qt.ItemDataRole.DisplayRole) or ""
        metrics = QFontMetrics(font)
        text_rect = rect.adjusted(x - rect.left(), 0, -8, 0)
        elided = metrics.elidedText(text, Qt.TextElideMode.ElideRight, text_rect.width())
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, elided)

    def _text_colour_for_state(self, state: _PaintState) -> QColor:
        """Return the correct foreground colour for the row's label."""

        palette = self._palette_for_state(state)
        if state.node_type == NodeType.SECTION:
            return palette.color(QPalette.ColorRole.PlaceholderText)
        if not state.is_enabled:
            return palette.color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text)
        if state.node_type == NodeType.ACTION:
            return self._accent_colour(palette)
        if state.is_selected:
            return palette.color(QPalette.ColorRole.Text)
        return self._muted_colour(palette)

    def _draw_separator(self, painter: QPainter, rect: QRect) -> None:
        """Render a horizontal rule used to group tree sections."""

        palette = self._resolve_separator_palette()
        colour = palette.color(QPalette.ColorRole.Mid)
        if not colour.isValid() or colour.alpha() == 0:
            # Preserve the light-theme aesthetic when the palette does not expose a mid-tone that
            # can double as a separator colour.
            colour = SIDEBAR_SEPARATOR_COLOR

        pen = QPen(colour)
        pen.setWidthF(1.0)
        painter.setPen(pen)
        y = rect.center().y()
        painter.drawLine(
            rect.left() + SIDEBAR_LEFT_PADDING,
            y,
            rect.right() - SIDEBAR_LEFT_PADDING,
            y,
        )

    def _resolve_separator_palette(self) -> QPalette:
        """Return the palette used when painting separators."""

        parent = self.parent()
        if isinstance(parent, QWidget):
            return parent.palette()
        return QPalette()


__all__ = [
    "AlbumSidebarDelegate",
    "BranchIndicatorController",
]
