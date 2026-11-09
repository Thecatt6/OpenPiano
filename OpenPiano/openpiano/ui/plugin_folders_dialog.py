from __future__ import annotations

import os
from pathlib import Path
from typing import Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from openpiano.core.theme import ThemePalette
from openpiano.ui.modal_utils import message_box_stylesheet


class PluginFoldersDialog(QDialog):

    def __init__(self, parent: QWidget | None, theme: ThemePalette, default_folders: Sequence[str],
                 folders: Sequence[str]) -> None:
        super().__init__(parent)
        self.setWindowTitle("Plug-in Folders")
        self.setModal(True)
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)

        always = QLabel("Always searched:\n" + "\n".join(f"   {folder}" for folder in default_folders), self)
        always.setTextFormat(Qt.PlainText)
        always.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(always)

        layout.addWidget(QLabel("Additional folders:", self))
        self.folder_list = QListWidget(self)
        self.folder_list.setObjectName("pluginFolderList")
        self.folder_list.setMinimumHeight(110)
        for folder in folders:
            self.folder_list.addItem(str(folder))
        self.folder_list.itemSelectionChanged.connect(self._refresh_buttons)
        layout.addWidget(self.folder_list)

        actions = QHBoxLayout()
        self.add_button = QPushButton("Add Folder…", self)
        self.add_button.clicked.connect(self._add_folder)
        self.remove_button = QPushButton("Remove", self)
        self.remove_button.clicked.connect(self._remove_selected)
        actions.addWidget(self.add_button)
        actions.addWidget(self.remove_button)
        actions.addStretch(1)
        layout.addLayout(actions)

        note = QLabel("Sub-folders are searched too. Plug-ins are rescanned when you choose OK.", self)
        note.setWordWrap(True)
        layout.addWidget(note)

        self.button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        layout.addWidget(self.button_box)

        self.setStyleSheet(message_box_stylesheet(theme) + f"""
            #pluginFolderList {{
                background: {theme.app_bg};
                color: {theme.text_primary};
                border: 1px solid {theme.border};
                border-radius: 6px;
                selection-background-color: {theme.accent};
                selection-color: #FFFFFF;
            }}
        """)
        self._refresh_buttons()

    def _refresh_buttons(self) -> None:
        self.remove_button.setEnabled(bool(self.folder_list.selectedItems()))

    def folders(self) -> list[str]:
        return [self.folder_list.item(index).text() for index in range(self.folder_list.count())]

    def add_folder(self, folder: str) -> bool:
        text = str(folder or "").strip()
        if not text:
            return False
        key = os.path.normcase(os.path.abspath(text))
        if any(os.path.normcase(os.path.abspath(existing)) == key for existing in self.folders()):
            return False
        self.folder_list.addItem(text)
        return True

    def _add_folder(self) -> None:
        start = self.folders()[-1] if self.folders() else str(Path.home())
        folder = QFileDialog.getExistingDirectory(self, "Choose a Plug-in Folder", start)
        if folder:
            self.add_folder(os.path.normpath(folder))

    def _remove_selected(self) -> None:
        for item in self.folder_list.selectedItems():
            self.folder_list.takeItem(self.folder_list.row(item))
        self._refresh_buttons()
