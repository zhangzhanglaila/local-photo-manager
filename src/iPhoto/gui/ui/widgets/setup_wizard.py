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

from ....i18n import tr
from ....utils.pathutils import get_custom_workspace_base

_LOGGER = logging.getLogger(__name__)

CLIP_MODEL_MB = 582
FACE_MODEL_MB = 325
OCR_MODEL_MB = 13


class SetupWizard(QDialog):
    """Guided first-run configuration: library folder, workspace, AI options."""

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        initial_library: Optional[Path] = None,
        initial_workspace: Optional[Path] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("setup.title"))
        self.setMinimumSize(760, 520)
        self.resize(860, 580)
        self.setModal(True)

        self._library_path: Optional[Path] = initial_library
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
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        self._step_list = QListWidget(self)
        self._step_list.setFixedWidth(240)
        self._step_list.setObjectName("setupSteps")
        self._step_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        for index, (title, subtitle) in enumerate(self._steps):
            item = QListWidgetItem(f"{index + 1}.  {title}\n     {subtitle}")
            item.setSizeHint(QSize(220, 56))
            self._step_list.addItem(item)
        self._step_list.currentRowChanged.connect(self._on_step_changed)
        body.addWidget(self._step_list)

        self._pages = QStackedWidget(self)
        self._pages.addWidget(self._page_welcome())
        self._pages.addWidget(self._page_library())
        self._pages.addWidget(self._page_workspace())
        self._pages.addWidget(self._page_ai())
        self._pages.addWidget(self._page_done())
        body.addWidget(self._pages, 1)

        root.addLayout(body, 1)

        footer = QHBoxLayout()
        footer.setContentsMargins(16, 8, 16, 16)
        footer.addWidget(QLabel(tr("setup.privacy_note")))
        footer.addStretch(1)
        self._back_button = QPushButton(tr("setup.back"), self)
        self._back_button.clicked.connect(self._go_back)
        self._next_button = QPushButton(tr("setup.next"), self)
        self._next_button.setDefault(True)
        self._next_button.clicked.connect(self._go_next)
        footer.addWidget(self._back_button)
        footer.addWidget(self._next_button)
        root.addLayout(footer)

        self._step_list.setCurrentRow(0)

    def _card(self, title: str, subtitle: str, body: QWidget) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(12)

        heading = QLabel(title, page)
        heading.setStyleSheet("font-size: 22px; font-weight: 600;")
        layout.addWidget(heading)

        sub = QLabel(subtitle, page)
        sub.setWordWrap(True)
        sub.setStyleSheet("color: #6b7280;")
        layout.addWidget(sub)
        layout.addSpacing(8)
        layout.addWidget(body)
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

        self._library_edit = QLineEdit(self)
        self._library_edit.setPlaceholderText(tr("setup.library_placeholder"))
        if self._library_path is not None:
            self._library_edit.setText(str(self._library_path))

        detect = QPushButton(tr("setup.detect"), body)
        detect.clicked.connect(self._detect_library)
        layout.addWidget(self._path_row(self._library_edit, extra=detect))

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

        self._clip_check = QCheckBox(
            tr("setup.ai_clip", mb=CLIP_MODEL_MB), body
        )
        self._clip_check.setChecked(True)
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

        hint = QLabel(tr("setup.ai_hint"), body)
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #6b7280;")
        layout.addWidget(hint)
        layout.addStretch(1)
        return self._card(tr("setup.step_ai"), tr("setup.step_ai_sub"), body)

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

    def _detect_library(self) -> None:
        candidates = [Path.home() / "Pictures"]
        appdata = Path.home() / "AppData" / "Local"
        for candidate in (Path("D:/APPLE"), appdata / "iPhoto"):
            candidates.append(candidate)
        for candidate in candidates:
            if candidate.is_dir():
                self._library_edit.setText(str(candidate))
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
        self._pages.setCurrentIndex(index)
        if index == len(self._steps) - 1:
            self._summary.setText(self._summary_text())
        self._sync_navigation()

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
            text = self._library_edit.text().strip()
            self._library_path = Path(text) if text else None
        elif row == 2:
            text = self._workspace_edit.text().strip()
            self._workspace_base = Path(text) if text else None
        elif row == 3:
            self._enable_clip = self._clip_check.isChecked()
            self._enable_face = self._face_check.isChecked()
            self._enable_ocr = self._ocr_check.isChecked()

    def _sync_navigation(self) -> None:
        row = self._step_list.currentRow()
        self._back_button.setEnabled(row > 0)
        last = row >= len(self._steps) - 1
        self._next_button.setText(tr("setup.finish") if last else tr("setup.next"))

    def _summary_text(self) -> str:
        parts = [
            tr("setup.summary_library", path=str(self._library_path or tr("setup.not_set"))),
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
    def library_path(self) -> Optional[Path]:
        return self._library_path

    def workspace_base(self) -> Optional[Path]:
        return self._workspace_base

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
