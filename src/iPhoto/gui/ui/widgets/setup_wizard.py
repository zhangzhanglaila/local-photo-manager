"""First-run setup wizard.

Replaces the single-page welcome dialog with a step-by-step flow: the user is told what
each folder is for before choosing it, and optional AI capabilities can be skipped. The
layout follows the same pattern as other desktop setup wizards: step list on the left,
one focused card per step on the right, navigation at the bottom.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ....agent.services import search_models
from ....i18n import tr
from ....utils.pathutils import get_custom_workspace_base
from .search_model_dialog import SearchModelDialog, model_summary, model_title

_LOGGER = logging.getLogger(__name__)

CLIP_MODEL_MB = 582
FACE_MODEL_MB = 325
OCR_MODEL_MB = 13


ACCENT = "#7b5e3b"
ACCENT_SOFT = "#efe7dc"
PANEL = "#faf8f5"
BORDER = "#e6e1d8"
MUTED = "#8a857c"

STYLE_SHEET = f"""
QDialog {{ background: white; }}
QWidget#sidePanel {{ background: {PANEL}; }}
QListWidget#setupSteps {{
    background: transparent; border: none; outline: none;
    padding: 12px;
}}
QListWidget#setupSteps::item {{
    border: none; border-radius: 10px; margin: 2px 0;
}}
QListWidget#setupSteps::item:selected {{ background: {ACCENT}; }}
QLabel#stepTitle {{ font-size: 14px; font-weight: 600; color: #2b2b2b; }}
QLabel#stepSub {{ font-size: 11px; color: {MUTED}; }}
QLabel#stepTitleActive {{ font-size: 14px; font-weight: 600; color: white; }}
QLabel#stepSubActive {{ font-size: 11px; color: #efe7dc; }}
QLabel#cardTitle {{ font-size: 24px; font-weight: 700; color: #1f1f1f; }}
QLabel#cardSub {{ font-size: 13px; color: {MUTED}; }}
QLabel#hint {{ font-size: 12px; color: {MUTED}; }}
QLineEdit {{
    border: 1px solid {BORDER}; border-radius: 8px; padding: 9px 12px;
    background: white; font-size: 13px;
}}
QListWidget#folders {{
    border: 1px solid {BORDER}; border-radius: 8px; background: white;
}}
QPushButton#ghost {{
    border: 1px solid {BORDER}; border-radius: 8px; padding: 8px 14px;
    background: white; color: #444; font-size: 13px;
}}
QPushButton#ghost:hover {{ background: #f5f2ec; }}
QPushButton#primary {{
    border: none; border-radius: 8px; padding: 10px 22px;
    background: {ACCENT}; color: white; font-size: 13px; font-weight: 600;
}}
QPushButton#primary:disabled {{ background: #c9c1b4; }}
QPushButton#back {{
    border: none; background: transparent; color: {MUTED}; font-size: 13px;
    padding: 8px 12px;
}}
QPushButton#back:hover {{ color: {ACCENT}; }}
QCheckBox {{ font-size: 13px; color: #333; spacing: 8px; }}
QLabel#privacy {{ font-size: 12px; color: {MUTED}; }}
"""


def _app_icon_path():
    """Return the packaged icon (Explorer-readable copy first)."""

    import os
    from pathlib import Path

    local = os.environ.get("LOCALAPPDATA")
    candidates = []
    if local:
        candidates.append(Path(local) / "iPhoto" / "app.png")
        candidates.append(Path(local) / "iPhoto" / "app.ico")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


class _StepItem(QWidget):
    """One row in the left step list: numbered badge + title + subtitle."""

    def __init__(self, number: int, title: str, subtitle: str, parent=None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(10)

        self._badge = QLabel(str(number), self)
        self._badge.setFixedSize(24, 24)
        self._badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._badge, 0, Qt.AlignmentFlag.AlignTop)

        column = QVBoxLayout()
        column.setSpacing(2)
        self._title = QLabel(title, self)
        self._title.setObjectName("stepTitle")
        self._sub = QLabel(subtitle, self)
        self._sub.setObjectName("stepSub")
        self._sub.setWordWrap(True)
        column.addWidget(self._title)
        column.addWidget(self._sub)
        layout.addLayout(column, 1)

        self._number = number
        self.set_state("pending")

    def set_state(self, state: str) -> None:
        if state == "done":
            self._badge.setText("\u2713")
            self._badge.setStyleSheet(
                f"border-radius: 12px; background: {ACCENT}; color: white; font-size: 12px;"
            )
            self._title.setObjectName("stepTitle")
            self._sub.setObjectName("stepSub")
        elif state == "active":
            self._badge.setText(str(self._number))
            self._badge.setStyleSheet(
                "border-radius: 12px; background: white; color: #2b2b2b; font-size: 12px;"
            )
            self._title.setObjectName("stepTitleActive")
            self._sub.setObjectName("stepSubActive")
        else:
            self._badge.setText(str(self._number))
            self._badge.setStyleSheet(
                f"border-radius: 12px; background: transparent; color: {MUTED};"
                f" border: 1px solid {BORDER}; font-size: 12px;"
            )
            self._title.setObjectName("stepTitle")
            self._sub.setObjectName("stepSub")
        for widget in (self._title, self._sub):
            widget.style().unpolish(widget)
            widget.style().polish(widget)


class SetupWizard(QDialog):
    """Guided first-run configuration: library folder, workspace, AI options."""

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        initial_library: Optional[Path] = None,
        initial_workspace: Optional[Path] = None,
        settings=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("setup.title"))
        self.setMinimumSize(760, 520)
        self.resize(860, 580)
        self.setModal(True)

        self._settings = settings
        self._library_paths: list[Path] = [initial_library] if initial_library else []
        self._workspace_base: Optional[Path] = initial_workspace
        self._enable_clip = True
        self._enable_face = True
        self._enable_ocr = True

        self._steps: list[tuple[str, str]] = [
            (tr("setup.step_welcome"), tr("setup.step_welcome_sub")),
            (tr("setup.step_library"), tr("setup.step_library_sub")),
            (tr("setup.step_workspace"), tr("setup.step_workspace_sub")),
            (tr("setup.step_ai"), tr("setup.step_ai_sub")),
            (tr("setup.step_done"), tr("setup.step_done_sub")),
        ]

        self._build_ui()
        self._sync_navigation()

    # ------------------------------------------------------------------ layout
    def _build_ui(self) -> None:
        self.setStyleSheet(STYLE_SHEET)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        side = QWidget(self)
        side.setObjectName("sidePanel")
        side.setFixedWidth(250)
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(0, 0, 0, 0)

        header = QWidget(side)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(20, 22, 16, 10)
        header_layout.setSpacing(10)

        self._logo = QLabel(header)
        self._logo.setFixedSize(34, 34)
        logo_path = _app_icon_path()
        if logo_path is not None:
            from PySide6.QtGui import QPixmap

            pixmap = QPixmap(str(logo_path))
            if not pixmap.isNull():
                self._logo.setPixmap(
                    pixmap.scaled(34, 34, Qt.AspectRatioMode.KeepAspectRatio,
                                  Qt.TransformationMode.SmoothTransformation)
                )
        header_layout.addWidget(self._logo)

        brand = QVBoxLayout()
        brand.setSpacing(0)
        title = QLabel("iPhoto", header)
        title.setStyleSheet("font-size: 16px; font-weight: 700; color: #2b2b2b;")
        brand.addWidget(title)
        setup = QLabel("SETUP", header)
        setup.setStyleSheet("font-size: 10px; font-weight: 600; color: #a09a90; letter-spacing: 1px;")
        brand.addWidget(setup)
        header_layout.addLayout(brand, 1)
        side_layout.addWidget(header)

        self._step_list = QListWidget(side)
        self._step_list.setObjectName("setupSteps")
        self._step_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._step_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        for index, (title, subtitle) in enumerate(self._steps):
            item = QListWidgetItem()
            item.setSizeHint(QSize(226, 64))
            self._step_list.addItem(item)
            self._step_list.setItemWidget(item, _StepItem(index + 1, title, subtitle))
        self._step_list.currentRowChanged.connect(self._on_step_changed)
        side_layout.addWidget(self._step_list, 1)
        body.addWidget(side)

        content = QWidget(self)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        self._pages = QStackedWidget(content)
        self._pages.addWidget(self._page_welcome())
        self._pages.addWidget(self._page_library())
        self._pages.addWidget(self._page_workspace())
        self._pages.addWidget(self._page_ai())
        self._pages.addWidget(self._page_done())
        content_layout.addWidget(self._pages, 1)
        body.addWidget(content, 1)

        root.addLayout(body, 1)

        footer = QHBoxLayout()
        footer.setContentsMargins(24, 10, 24, 18)
        privacy = QLabel(tr("setup.privacy_note"), self)
        privacy.setObjectName("privacy")
        footer.addWidget(privacy)
        footer.addStretch(1)
        self._back_button = QPushButton(tr("setup.back"), self)
        self._back_button.setObjectName("back")
        self._back_button.clicked.connect(self._go_back)
        self._next_button = QPushButton(tr("setup.next"), self)
        self._next_button.setObjectName("primary")
        self._next_button.setDefault(True)
        self._next_button.clicked.connect(self._go_next)
        footer.addWidget(self._back_button)
        footer.addWidget(self._next_button)
        root.addLayout(footer)

        self._step_list.setCurrentRow(0)

    def _card(self, title: str, subtitle: str, body: QWidget) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(36, 34, 36, 24)
        layout.setSpacing(6)

        heading = QLabel(title, page)
        heading.setObjectName("cardTitle")
        layout.addWidget(heading)

        sub = QLabel(subtitle, page)
        sub.setObjectName("cardSub")
        sub.setWordWrap(True)
        layout.addWidget(sub)
        layout.addSpacing(18)

        card = QWidget(page)
        # Scope the border to the card itself: an unscoped stylesheet applies to every
        # child widget, which drew a line under each checkbox.
        card.setObjectName("formCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(20, 20, 20, 20)
        card_layout.addWidget(body)
        card.setStyleSheet(
            f"QWidget#formCard {{ background: white; border: 1px solid {BORDER}; "
            f"border-radius: 12px; }}"
        )
        layout.addWidget(card)
        layout.addStretch(1)
        return page

    def _path_row(self, edit: QLineEdit, *, extra: Optional[QPushButton] = None) -> QWidget:
        row = QWidget(self)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(edit, 1)
        if extra is not None:
            layout.addWidget(extra)
        browse = QPushButton(tr("setup.browse"), row)
        browse.clicked.connect(lambda: self._browse_into(edit))
        layout.addWidget(browse)
        return row

    # ------------------------------------------------------------------- pages
    def _page_welcome(self) -> QWidget:
        body = QWidget(self)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 0, 0)
        text = QLabel(tr("setup.welcome_body"), body)
        text.setWordWrap(True)
        layout.addWidget(text)
        layout.addStretch(1)
        return self._card(tr("setup.welcome_title"), tr("setup.welcome_sub"), body)

    def _page_library(self) -> QWidget:
        body = QWidget(self)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 0, 0)

        # A library can span several folders, so collect a list instead of one path.
        self._library_list = QListWidget(self)
        self._library_list.setMinimumHeight(150)
        for path in self._library_paths:
            self._library_list.addItem(str(path))
        layout.addWidget(self._library_list)

        buttons = QHBoxLayout()
        add = QPushButton(tr("setup.add_folder"), body)
        add.clicked.connect(self._add_library_folder)
        remove = QPushButton(tr("setup.remove_folder"), body)
        remove.clicked.connect(self._remove_library_folder)
        detect = QPushButton(tr("setup.detect"), body)
        detect.clicked.connect(self._detect_library)
        buttons.addWidget(add)
        buttons.addWidget(remove)
        buttons.addWidget(detect)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        hint = QLabel(tr("setup.library_hint"), body)
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #6b7280;")
        layout.addWidget(hint)
        layout.addStretch(1)
        return self._card(tr("setup.step_library"), tr("setup.step_library_sub"), body)

    def _page_workspace(self) -> QWidget:
        body = QWidget(self)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 0, 0)

        self._workspace_edit = QLineEdit(self)
        self._workspace_edit.setPlaceholderText(tr("setup.workspace_placeholder"))
        default_base = get_custom_workspace_base()
        if self._workspace_base is not None:
            self._workspace_edit.setText(str(self._workspace_base))
        elif default_base is not None:
            self._workspace_edit.setText(str(default_base))

        reset = QPushButton(tr("setup.reset_default"), body)
        reset.clicked.connect(self._reset_workspace_default)
        layout.addWidget(self._path_row(self._workspace_edit, extra=reset))

        hint = QLabel(tr("setup.workspace_hint"), body)
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #6b7280;")
        layout.addWidget(hint)
        layout.addStretch(1)
        return self._card(tr("setup.step_workspace"), tr("setup.step_workspace_sub"), body)

    def _page_ai(self) -> QWidget:
        body = QWidget(self)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        stored = (
            self._settings.get(search_models.SETTINGS_KEY, None)
            if self._settings is not None
            else None
        )
        self._search_model_key = search_models.resolve_spec(stored).key
        self._clip_check = QCheckBox(
            tr("setup.ai_clip", mb=CLIP_MODEL_MB), body
        )
        self._clip_check.setChecked(True)
        self._clip_check.toggled.connect(self._sync_model_row)
        self._model_label = QLabel("", body)
        self._model_label.setStyleSheet("color: #6b7280;")
        self._model_button = QPushButton(tr("search.model.choose"), body)
        self._model_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._model_button.clicked.connect(self._choose_search_model)
        self._model_row = QWidget(body)
        model_row_layout = QHBoxLayout(self._model_row)
        model_row_layout.setContentsMargins(24, 0, 0, 0)
        model_row_layout.setSpacing(8)
        model_row_layout.addWidget(self._model_label, 1)
        model_row_layout.addWidget(self._model_button, 0)
        self._sync_model_row()
        self._face_check = QCheckBox(
            tr("setup.ai_face", mb=FACE_MODEL_MB), body
        )
        self._face_check.setChecked(True)
        self._ocr_check = QCheckBox(
            tr("setup.ai_ocr", mb=OCR_MODEL_MB), body
        )
        self._ocr_check.setChecked(True)
        for box in (self._clip_check, self._face_check, self._ocr_check):
            layout.addWidget(box)
            if box is self._clip_check:
                layout.addWidget(self._model_row)

        hint = QLabel(tr("setup.ai_hint"), body)
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #6b7280;")
        layout.addWidget(hint)
        layout.addStretch(1)
        return self._card(tr("setup.step_ai"), tr("setup.step_ai_sub"), body)

    def _sync_model_row(self) -> None:
        """Show the chosen model next to the semantic-search toggle."""

        enabled = self._clip_check.isChecked()
        self._model_row.setVisible(enabled)
        spec = search_models.resolve_spec(self._search_model_key)
        self._model_label.setText(
            tr("search.model.current", name=model_title(spec))
            + "  ·  "
            + model_summary(spec)
        )

    def _choose_search_model(self) -> None:
        dialog = SearchModelDialog(self, current_key=self._search_model_key)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._search_model_key = dialog.selected_key()
            self._sync_model_row()

    def _page_done(self) -> QWidget:
        body = QWidget(self)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 0, 0)
        self._summary = QLabel("", body)
        self._summary.setWordWrap(True)
        layout.addWidget(self._summary)
        layout.addStretch(1)
        return self._card(tr("setup.step_done"), tr("setup.step_done_sub"), body)

    # -------------------------------------------------------------- behaviour
    def _browse_into(self, edit: QLineEdit) -> None:
        start = edit.text().strip() or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, tr("setup.browse"), start)
        if chosen:
            edit.setText(chosen)

    def _add_library_folder(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, tr("setup.add_folder"), str(Path.home()))
        if chosen and all(
            self._library_list.item(i).text() != chosen
            for i in range(self._library_list.count())
        ):
            self._library_list.addItem(chosen)

    def _remove_library_folder(self) -> None:
        for item in self._library_list.selectedItems():
            self._library_list.takeItem(self._library_list.row(item))

    def _detect_library(self) -> None:
        for candidate in (Path("D:/APPLE"), Path.home() / "Pictures"):
            if candidate.is_dir():
                existing = {
                    self._library_list.item(i).text()
                    for i in range(self._library_list.count())
                }
                if str(candidate) not in existing:
                    self._library_list.addItem(str(candidate))
                return

    def _reset_workspace_default(self) -> None:
        base = get_custom_workspace_base()
        if base is not None:
            self._workspace_edit.setText(str(base))
            return
        documents = Path.home() / "Documents"
        self._workspace_edit.setText(str(documents / "iPhotronWorkspace"))

    def _on_step_changed(self, index: int) -> None:
        if index < 0:
            return
        self._refresh_step_states(index)
        self._pages.setCurrentIndex(index)
        if index == len(self._steps) - 1:
            self._summary.setText(self._summary_text())
        self._sync_navigation()

    def _refresh_step_states(self, active: int) -> None:
        for row in range(self._step_list.count()):
            item = self._step_list.item(row)
            widget = self._step_list.itemWidget(item)
            if widget is None or not hasattr(widget, "set_state"):
                continue
            if row == active:
                widget.set_state("active")
            elif row < active:
                widget.set_state("done")
            else:
                widget.set_state("pending")

    def _go_back(self) -> None:
        row = self._step_list.currentRow()
        if row > 0:
            self._step_list.setCurrentRow(row - 1)

    def _go_next(self) -> None:
        self._capture_step()
        row = self._step_list.currentRow()
        if row >= len(self._steps) - 1:
            self.accept()
            return
        self._step_list.setCurrentRow(row + 1)

    def _capture_step(self) -> None:
        row = self._step_list.currentRow()
        if row == 1:
            self._library_paths = [
                Path(self._library_list.item(i).text())
                for i in range(self._library_list.count())
            ]
        elif row == 2:
            text = self._workspace_edit.text().strip()
            self._workspace_base = Path(text) if text else None
        elif row == 3:
            self._enable_clip = self._clip_check.isChecked()
            self._enable_face = self._face_check.isChecked()
            self._enable_ocr = self._ocr_check.isChecked()
            self.search_model_key = self._search_model_key

    def _sync_navigation(self) -> None:
        row = self._step_list.currentRow()
        self._back_button.setEnabled(row > 0)
        last = row >= len(self._steps) - 1
        self._next_button.setText(tr("setup.finish") if last else tr("setup.next"))

    def _summary_text(self) -> str:
        parts = [
            tr(
                "setup.summary_library",
                path=", ".join(str(p) for p in self._library_paths) or tr("setup.not_set"),
            ),
            tr("setup.summary_workspace", path=str(self._workspace_base or tr("setup.not_set"))),
        ]
        enabled = [
            name
            for name, flag in (
                ("语义搜索", self._enable_clip),
                ("人脸识别", self._enable_face),
                ("文字识别", self._enable_ocr),
            )
            if flag
        ]
        parts.append(tr("setup.summary_ai", items="、".join(enabled) or tr("setup.ai_none")))
        return "\n".join(parts)

    # ----------------------------------------------------------------- results
    def library_paths(self) -> list[Path]:
        return list(self._library_paths)

    def library_path(self) -> Optional[Path]:
        return self._library_paths[0] if self._library_paths else None

    def workspace_base(self) -> Optional[Path]:
        return self._workspace_base

    def selected_search_model(self) -> str:
        return self._search_model_key

    def ai_options(self) -> dict[str, bool]:
        return {
            "semantic_search_enabled": self._enable_clip,
            "face_scan_enabled": self._enable_face,
            "ocr_scan_enabled": self._enable_ocr,
        }

    def _center_on_screen(self) -> None:
        """Place the dialog in the middle of the screen so it cannot open off-screen."""

        screen = self.screen()
        if screen is None:
            return
        geometry = self.frameGeometry()
        geometry.moveCenter(screen.availableGeometry().center())
        self.move(geometry.topLeft())

    def show_and_wait(self) -> bool:
        """Run modally and return True when the user completed the wizard."""

        self._center_on_screen()
        self.raise_()
        self.activateWindow()
        return self.exec() == QDialog.DialogCode.Accepted
