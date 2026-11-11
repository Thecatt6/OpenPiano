from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from openpiano.core.sound_presets import MAX_SOUND_PRESETS, SOUND_PRESET_NAME_LIMIT
from openpiano.core.theme import ThemePalette
from openpiano.ui.modal_utils import apply_dialog_button_cursors, message_box_stylesheet
from openpiano.ui.split_panel import ElidedLabel

ACTION_STORE = "store"
ACTION_RECALL = "recall"
ACTION_CLEAR = "clear"
ACTION_RENAME = "rename"
ACTION_LEARN = "learn"
ACTION_CANCEL_LEARN = "cancel_learn"
ACTION_UNASSIGN = "unassign"
ACTION_LEARN_PC = "learn_pc"
ACTION_UNASSIGN_PC = "unassign_pc"

_INTRO = (
    "Choose a sound in the main window (instrument, bank and preset, keyboard split), then "
    "store it in a slot here. Give the slot a key, pad or button of your MIDI keyboard, a key "
    "of the computer keyboard, or both: pressing it brings that sound back. A key used this "
    "way no longer plays its note."
)
_IDLE_HINT = ("Presets can also be recalled from this window with the mouse. A key of the computer "
              "keyboard works while OpenPiano is the window in front.")
_ASSIGN_TEXT = "Assign Key…"
_LEARNING_TEXT = "Press a key…"
_EMPTY_TEXT = "Empty"
# The longest computer key shown in full; a longer one is cut short and told in the tooltip.
_WIDEST_PC_KEY = "Ctrl + Shift + F12"


@dataclass(frozen=True, slots=True)
class SoundPresetRow:
    stored: bool = False
    name: str = ""
    summary: str = ""
    key: str = ""
    pc_key: str = ""


class _PresetRowWidgets:

    def __init__(self, dialog: "SoundPresetsDialog", grid: QGridLayout, index: int) -> None:
        row = index + 1
        self.number = QLabel(str(index + 1), dialog)
        self.number.setObjectName("presetNumber")
        self.number.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.name = QLineEdit(dialog)
        self.name.setObjectName("presetName")
        self.name.setMaxLength(SOUND_PRESET_NAME_LIMIT)
        self.name.setFixedWidth(150)
        self.name.setAccessibleName(f"Name of preset {index + 1}")
        self.summary = ElidedLabel(dialog)
        self.summary.setObjectName("presetSummary")
        self.summary.setMinimumWidth(230)
        self.key = QPushButton(_ASSIGN_TEXT, dialog)
        self.key.setObjectName("presetKey")
        self.key.setAccessibleName(f"MIDI key of preset {index + 1}")
        self.unassign = QPushButton("×", dialog)
        self.unassign.setObjectName("presetUnassign")
        self.unassign.setToolTip("Remove the MIDI key from this preset")
        self.unassign.setAccessibleName(f"Remove the MIDI key of preset {index + 1}")
        self.pc_key = QPushButton(_ASSIGN_TEXT, dialog)
        self.pc_key.setObjectName("presetKey")
        self.pc_key.setAccessibleName(f"Computer key of preset {index + 1}")
        self.pc_unassign = QPushButton("×", dialog)
        self.pc_unassign.setObjectName("presetUnassign")
        self.pc_unassign.setToolTip("Remove the computer key from this preset")
        self.pc_unassign.setAccessibleName(f"Remove the computer key of preset {index + 1}")
        self.recall = QPushButton("Recall", dialog)
        self.recall.setToolTip("Switch to this sound now")
        self.store = QPushButton("Store", dialog)
        self.store.setToolTip("Store the sound that is set in the main window in this slot")
        self.clear = QPushButton("Clear", dialog)
        self.clear.setToolTip("Empty this slot")
        for button in (self.key, self.unassign, self.pc_key, self.pc_unassign,
                       self.recall, self.store, self.clear):
            button.setAutoDefault(False)
            button.setDefault(False)
            button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        widgets = (self.number, self.name, self.summary, self.key, self.unassign,
                   self.pc_key, self.pc_unassign, self.recall, self.store, self.clear)
        for column, widget in enumerate(widgets):
            grid.addWidget(widget, row, column)

        self.name.editingFinished.connect(lambda: dialog._on_name_edited(index))
        self.key.clicked.connect(lambda: dialog._on_key_clicked(index))
        self.unassign.clicked.connect(lambda: dialog.action.emit(ACTION_UNASSIGN, index, ""))
        self.pc_key.clicked.connect(lambda: dialog._on_pc_key_clicked(index))
        self.pc_unassign.clicked.connect(lambda: dialog.action.emit(ACTION_UNASSIGN_PC, index, ""))
        self.recall.clicked.connect(lambda: dialog.action.emit(ACTION_RECALL, index, ""))
        self.store.clicked.connect(lambda: dialog._on_store_clicked(index))
        self.clear.clicked.connect(lambda: dialog._on_clear_clicked(index))


class SoundPresetsDialog(QDialog):
    """The list of sound presets: what each slot holds and which keys recall it, one of the MIDI
    keyboard and one of the computer keyboard. It stays open next to the main window, so sounds
    can be set up there and stored here in turn."""

    action = Signal(str, int, str)

    def __init__(self, parent: QWidget | None, theme: ThemePalette) -> None:
        super().__init__(parent)
        self.setWindowTitle("Sound Presets")
        self.setModal(False)
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        self._rows: tuple[SoundPresetRow, ...] = tuple(SoundPresetRow() for _ in range(MAX_SOUND_PRESETS))
        self._learning: int | None = None
        # Whether the key that is waited for is one of the computer keyboard.
        self._learning_pc = False
        self._notice = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        intro = QLabel(_INTRO, self)
        intro.setWordWrap(True)
        layout.addWidget(intro)

        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(2, 1)
        for column, title in ((1, "Name"), (2, "Sound"), (3, "MIDI key"), (5, "PC key")):
            header = QLabel(title, self)
            header.setObjectName("presetHeader")
            grid.addWidget(header, 0, column)
        self._widgets = [_PresetRowWidgets(self, grid, index) for index in range(MAX_SOUND_PRESETS)]
        layout.addLayout(grid)

        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        self.hint_label = QLabel(_IDLE_HINT, self)
        self.hint_label.setObjectName("presetHint")
        self.hint_label.setWordWrap(True)
        bottom.addWidget(self.hint_label, 1)
        self.close_button = QPushButton("Close", self)
        self.close_button.setAutoDefault(False)
        self.close_button.clicked.connect(self.close)
        bottom.addWidget(self.close_button, 0, Qt.AlignBottom)
        layout.addLayout(bottom)

        self._size_key_buttons()
        self.apply_theme(theme)
        self._refresh()
        # Room for a whole description in the Sound column; the layout sets the minimum.
        hint = self.sizeHint()
        self.resize(max(960, hint.width()), hint.height())
        self.close_button.setFocus()

    def _size_key_buttons(self) -> None:
        # Wide enough for the longest text the button shows, so the row does not jump.
        probe = self._widgets[0].key
        probe.ensurePolished()
        widest = 0
        for text in (_ASSIGN_TEXT, _LEARNING_TEXT, "Program 128 · Ch 16", "C#-1 · Ch 16"):
            widest = max(widest, probe.fontMetrics().horizontalAdvance(text))
        width = widest + 30
        pc_widest = max(probe.fontMetrics().horizontalAdvance(text)
                        for text in (_ASSIGN_TEXT, _LEARNING_TEXT, _WIDEST_PC_KEY))
        self._pc_key_text_width = pc_widest
        for widgets in self._widgets:
            widgets.key.setFixedWidth(width)
            widgets.unassign.setFixedWidth(28)
            widgets.pc_key.setFixedWidth(pc_widest + 30)
            widgets.pc_unassign.setFixedWidth(28)

    def apply_theme(self, theme: ThemePalette) -> None:
        self.setStyleSheet(message_box_stylesheet(theme) + f"""
            QLineEdit#presetName {{
                background: {theme.app_bg};
                color: {theme.text_primary};
                border: 1px solid {theme.border};
                border-radius: 6px;
                padding: 4px 7px;
                min-height: 22px;
                selection-background-color: {theme.accent};
                selection-color: #FFFFFF;
            }}
            QLineEdit#presetName:focus {{ border-color: {theme.accent}; }}
            QLineEdit#presetName:disabled {{ color: {theme.text_secondary}; }}
            QLabel#presetHeader, QLabel#presetNumber, QLabel#presetHint {{ color: {theme.text_secondary}; }}
            QLabel#presetSummary[empty="true"] {{ color: {theme.text_secondary}; }}
            QPushButton#presetKey[learning="true"] {{
                background: {theme.accent};
                color: #FFFFFF;
                border-color: {theme.accent};
            }}
            QPushButton#presetUnassign {{ padding: 5px 0; }}
        """)
        self._size_key_buttons()

    def set_presets(self, rows: Sequence[SoundPresetRow], learning_index: int | None = None,
                    learning_pc: bool = False, notice: str = "") -> None:
        """learning_index is the slot that waits for a key, of the computer keyboard when
        learning_pc is set and of the MIDI keyboard otherwise. notice is shown in the line at
        the bottom: why the key just pressed cannot be used, or what a new key does now."""
        padded = list(rows)[:MAX_SOUND_PRESETS]
        padded += [SoundPresetRow()] * (MAX_SOUND_PRESETS - len(padded))
        self._rows = tuple(padded)
        self._learning = learning_index if learning_index is not None and 0 <= learning_index < MAX_SOUND_PRESETS else None
        self._learning_pc = bool(learning_pc) and self._learning is not None
        self._notice = str(notice or "")
        self._refresh()

    def learning_index(self) -> int | None:
        return self._learning

    def learning_pc_key(self) -> bool:
        return self._learning_pc

    def _refresh(self) -> None:
        for index, (row, widgets) in enumerate(zip(self._rows, self._widgets)):
            waiting = self._learning == index
            learning = waiting and not self._learning_pc
            learning_pc = waiting and self._learning_pc
            widgets.name.setEnabled(row.stored)
            if not widgets.name.hasFocus() or not row.stored:
                if widgets.name.text() != row.name:
                    widgets.name.setText(row.name)
            widgets.name.setPlaceholderText("" if row.stored else _EMPTY_TEXT)
            widgets.summary.set_full_text(row.summary if row.stored else "–", row.summary if row.stored else "")
            self._set_property(widgets.summary, "empty", not row.stored)
            widgets.key.setEnabled(row.stored)
            widgets.key.setText(_LEARNING_TEXT if learning else (row.key or _ASSIGN_TEXT))
            widgets.key.setToolTip(
                "Waiting for a key, pad or button of the MIDI keyboard. Click again to cancel." if learning
                else "Store a sound in this slot first." if not row.stored
                else "Click, then press the key, pad or button of the MIDI keyboard that recalls this preset.")
            self._set_property(widgets.key, "learning", learning)
            widgets.unassign.setEnabled(row.stored and bool(row.key) and not learning)
            widgets.pc_key.setEnabled(row.stored)
            shown = widgets.pc_key.fontMetrics().elidedText(row.pc_key, Qt.ElideRight, self._pc_key_text_width)
            widgets.pc_key.setText(_LEARNING_TEXT if learning_pc else (shown or _ASSIGN_TEXT))
            widgets.pc_key.setToolTip(
                "Waiting for a key of the computer keyboard. Click again to cancel." if learning_pc
                else "Store a sound in this slot first." if not row.stored
                else (f"{row.pc_key}\n" if shown != row.pc_key else "")
                + "Click, then press the key of the computer keyboard that recalls this preset, "
                  "alone or with Ctrl, Shift or Alt.")
            self._set_property(widgets.pc_key, "learning", learning_pc)
            widgets.pc_unassign.setEnabled(row.stored and bool(row.pc_key) and not learning_pc)
            widgets.recall.setEnabled(row.stored)
            widgets.clear.setEnabled(row.stored)
        if self._learning is None:
            self.hint_label.setText(self._notice or _IDLE_HINT)
        else:
            name = self._rows[self._learning].name or f"Preset {self._learning + 1}"
            if self._learning_pc:
                text = (f"“{name}”: press the key of the computer keyboard that should recall it, "
                        "alone or with Ctrl, Shift or Alt. Esc cancels.")
            else:
                text = (f"“{name}”: press the key, pad or button of the MIDI keyboard that should "
                        "recall it. Esc cancels.")
            self.hint_label.setText(f"{self._notice} {text}" if self._notice else text)
        apply_dialog_button_cursors(self)

    @staticmethod
    def _set_property(widget: QWidget, name: str, value: bool) -> None:
        if bool(widget.property(name)) == bool(value) and widget.property(name) is not None:
            return
        widget.setProperty(name, bool(value))
        widget.style().unpolish(widget)
        widget.style().polish(widget)
        widget.update()

    def _on_name_edited(self, index: int) -> None:
        row = self._rows[index]
        text = self._widgets[index].name.text()
        if row.stored and " ".join(text.split()) != row.name:
            self.action.emit(ACTION_RENAME, index, text)

    def _on_key_clicked(self, index: int) -> None:
        if self._learning == index and not self._learning_pc:
            self.action.emit(ACTION_CANCEL_LEARN, index, "")
        else:
            self.action.emit(ACTION_LEARN, index, "")

    def _on_pc_key_clicked(self, index: int) -> None:
        if self._learning == index and self._learning_pc:
            self.action.emit(ACTION_CANCEL_LEARN, index, "")
        else:
            self.action.emit(ACTION_LEARN_PC, index, "")

    def _confirm(self, title: str, text: str) -> bool:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle(title)
        box.setText(text)
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.Cancel)
        box.setDefaultButton(QMessageBox.Cancel)
        apply_dialog_button_cursors(box)
        return box.exec() == QMessageBox.Yes

    def _on_store_clicked(self, index: int) -> None:
        row = self._rows[index]
        if row.stored and not self._confirm(
                "Replace Preset",
                f"Replace “{row.name or f'Preset {index + 1}'}” with the sound that is set now?"):
            return
        self.action.emit(ACTION_STORE, index, "")

    def _on_clear_clicked(self, index: int) -> None:
        row = self._rows[index]
        if row.stored and self._confirm("Clear Preset", f"Empty the slot “{row.name or f'Preset {index + 1}'}”?"):
            self.action.emit(ACTION_CLEAR, index, "")

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape and self._learning is not None:
            self.action.emit(ACTION_CANCEL_LEARN, self._learning, "")
            event.accept()
            return
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            # Enter confirms a name; it must not press a button.
            focus = self.focusWidget()
            if isinstance(focus, QLineEdit):
                focus.clearFocus()
            event.accept()
            return
        super().keyPressEvent(event)

    def hideEvent(self, event) -> None:
        if self._learning is not None:
            self.action.emit(ACTION_CANCEL_LEARN, self._learning, "")
        super().hideEvent(event)
