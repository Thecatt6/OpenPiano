from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QStyle,
    QStyleOptionComboBox,
    QVBoxLayout,
    QWidget,
)

from openpiano.core.config import MASTER_VOLUME_MAX
from openpiano.core.split import (
    SPLIT_NOTE_MAX,
    SPLIT_NOTE_MIN,
    SPLIT_OCTAVE_MAX,
    SPLIT_OCTAVE_MIN,
    SPLIT_ZONE_COLORS,
    SplitZone,
    note_name,
)
from openpiano.ui.combo_options import zone_program_options
from openpiano.ui.styled_controls import ChevronComboBox

ScaleFunc = Callable[[int], int]


def _set_combo_data(combo: ChevronComboBox, value: object) -> None:
    blocked = combo.blockSignals(True)
    for index in range(combo.count()):
        if combo.itemData(index, role=Qt.UserRole) == value:
            combo.setCurrentIndex(index)
            break
    combo.blockSignals(blocked)


def _fill_combo(combo: ChevronComboBox, options: list[tuple[str, object]], selected: object) -> None:
    blocked = combo.blockSignals(True)
    current = [(combo.itemText(index), combo.itemData(index, role=Qt.UserRole)) for index in range(combo.count())]
    if current != options:
        combo.clear()
        for label, data in options:
            combo.addItem(label, userData=data)
    target = -1
    for index in range(combo.count()):
        if combo.itemData(index, role=Qt.UserRole) == selected:
            target = index
            break
    combo.setCurrentIndex(target)
    combo.blockSignals(blocked)


class SplitZoneControls(QObject):
    """The two rows of controls that describe one split zone."""

    changed = Signal(int, object)
    editorRequested = Signal(int)

    def __init__(self, index: int, card: QWidget, card_layout: QVBoxLayout, scale: ScaleFunc) -> None:
        super().__init__(card)
        self.index = index
        self._scale = scale
        self._enabled = False
        self._ready = False
        self._instrument_id = ""
        color = SPLIT_ZONE_COLORS[index % len(SPLIT_ZONE_COLORS)]

        range_row = QHBoxLayout()
        self.swatch = QFrame(card)
        self.swatch.setObjectName("splitSwatch")
        self.swatch.setStyleSheet(f"#splitSwatch {{ background: {color}; border-radius: 3px; }}")
        self.swatch.setToolTip("Colour of this zone's marker above the keys.")
        self.enable_checkbox = QCheckBox(f"Zone {index + 1}", card)
        self.enable_checkbox.setObjectName("settingCheckbox")
        self.enable_checkbox.toggled.connect(lambda checked: self._emit(enabled=bool(checked)))
        self.low_combo = ChevronComboBox(card)
        self.high_combo = ChevronComboBox(card)
        for combo, key, description in ((self.low_combo, "low", "Lowest key"), (self.high_combo, "high", "Highest key")):
            combo.setObjectName("splitCombo")
            combo.setAccessibleName(f"Zone {index + 1} {description.lower()}")
            combo.setToolTip(f"{description} of zone {index + 1}.")
            for note in range(SPLIT_NOTE_MIN, SPLIT_NOTE_MAX + 1):
                combo.addItem(note_name(note), userData=note)
            combo.currentIndexChanged.connect(lambda _index, c=combo, k=key: self._emit_combo(c, k))
        self.range_separator = QLabel("to", card)
        self.range_separator.setObjectName("settingHint")
        self.octave_combo = ChevronComboBox(card)
        self.octave_combo.setObjectName("splitCombo")
        self.octave_combo.setAccessibleName(f"Zone {index + 1} octave shift")
        self.octave_combo.setToolTip("Octave shift applied to this zone only.")
        for octave in range(SPLIT_OCTAVE_MIN, SPLIT_OCTAVE_MAX + 1):
            self.octave_combo.addItem("Octave 0" if octave == 0 else f"Octave {octave:+d}", userData=octave)
        self.octave_combo.currentIndexChanged.connect(lambda _index: self._emit_combo(self.octave_combo, "octave"))
        self.status_label = QLabel("", card)
        self.status_label.setObjectName("settingHint")
        self.status_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.status_label.setMinimumWidth(0)
        range_row.addWidget(self.swatch)
        range_row.addWidget(self.enable_checkbox)
        range_row.addWidget(self.low_combo)
        range_row.addWidget(self.range_separator)
        range_row.addWidget(self.high_combo)
        range_row.addWidget(self.octave_combo)
        range_row.addWidget(self.status_label, 1)
        card_layout.addLayout(range_row)

        sound_row = QHBoxLayout()
        self.instrument_combo = ChevronComboBox(card)
        self.instrument_combo.setObjectName("splitCombo")
        self.instrument_combo.setAccessibleName(f"Zone {index + 1} sound")
        self.instrument_combo.setToolTip(f"Sound played by zone {index + 1}.")
        self.instrument_combo.currentIndexChanged.connect(self._on_instrument_index_changed)
        self.program_combo = ChevronComboBox(card)
        self.program_combo.setObjectName("splitCombo")
        self.program_combo.setAccessibleName(f"Zone {index + 1} preset")
        self.program_combo.setToolTip("Preset of this zone's SoundFont.")
        self.program_combo.currentIndexChanged.connect(self._on_program_index_changed)
        self.volume_slider = QSlider(Qt.Horizontal, card)
        self.volume_slider.setObjectName("splitVolumeSlider")
        self.volume_slider.setAccessibleName(f"Zone {index + 1} volume")
        self.volume_slider.setToolTip("Volume of this zone relative to the main volume.")
        self.volume_slider.setRange(0, round(MASTER_VOLUME_MAX * 100))
        self.volume_slider.setValue(100)
        self.volume_slider.valueChanged.connect(self._on_volume_changed)
        self.volume_value = QLabel("100%", card)
        self.volume_value.setObjectName("settingValue")
        self.editor_button = QPushButton("Plug-in Window", card)
        self.editor_button.setObjectName("actionButton")
        self.editor_button.setToolTip("Open this zone's plug-in window.")
        self.editor_button.clicked.connect(lambda: self.editorRequested.emit(self.index))
        self.editor_button.setVisible(False)
        sound_row.addWidget(self.instrument_combo, 3)
        sound_row.addWidget(self.program_combo, 2)
        sound_row.addWidget(self.volume_slider, 2)
        sound_row.addWidget(self.volume_value)
        sound_row.addWidget(self.editor_button)
        self._sound_row = sound_row
        card_layout.addLayout(sound_row)
        for combo in (self.low_combo, self.high_combo, self.octave_combo, self.instrument_combo, self.program_combo):
            combo.setSizeAdjustPolicy(ChevronComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.instrument_combo.setMinimumContentsLength(12)
        self.program_combo.setMinimumContentsLength(8)
        self.instrument_combo.setPlaceholderText("Choose a sound")
        self.program_combo.setPlaceholderText("Preset")
        self.refresh_metrics()
        self._refresh_enabled()

    def refresh_metrics(self) -> None:
        swatch = max(8, self._scale(10))
        self.swatch.setFixedSize(swatch, swatch)
        for combo in (self.low_combo, self.high_combo):
            combo.setMinimumWidth(self._combo_width(combo, "C#8"))
        self.octave_combo.setMinimumWidth(self._combo_width(self.octave_combo, "Octave +3"))
        self.volume_slider.setMinimumWidth(self._scale(90))

    def _combo_width(self, combo: ChevronComboBox, widest_text: str) -> int:
        # The style sheet's padding and drop-down button take a share of the width that
        # depends on the theme metrics, so it is measured instead of assumed.
        option = QStyleOptionComboBox()
        combo.initStyleOption(option)
        field = combo.style().subControlRect(QStyle.CC_ComboBox, option, QStyle.SC_ComboBoxEditField, combo)
        chrome = option.rect.width() - field.width() if field.isValid() else self._scale(70)
        return combo.fontMetrics().horizontalAdvance(widest_text) + max(0, chrome) + self._scale(8)

    def _emit(self, **changes: object) -> None:
        self.changed.emit(self.index, changes)

    def _emit_combo(self, combo: ChevronComboBox, key: str) -> None:
        value = combo.currentData(Qt.UserRole)
        if value is not None:
            self._emit(**{key: value})

    def _on_instrument_index_changed(self, _index: int) -> None:
        instrument_id = self.instrument_combo.currentData(Qt.UserRole)
        if isinstance(instrument_id, str) and instrument_id:
            self._emit(instrument_id=instrument_id, bank=0, preset=0)

    def _on_program_index_changed(self, _index: int) -> None:
        program = self.program_combo.currentData(Qt.UserRole)
        if isinstance(program, tuple) and len(program) == 2:
            self._emit(bank=int(program[0]), preset=int(program[1]))

    def _on_volume_changed(self, value: int) -> None:
        self.volume_value.setText(f"{int(value)}%")
        self._emit(volume=int(value) / 100.0)

    def _refresh_enabled(self) -> None:
        for widget in (self.low_combo, self.high_combo, self.octave_combo, self.volume_slider):
            widget.setEnabled(self._enabled)
        self.instrument_combo.setEnabled(self.instrument_combo.count() > 0)
        self.program_combo.setEnabled(self._enabled and self._ready and self.program_combo.count() > 0)
        self.editor_button.setEnabled(self._enabled and self._ready)

    def set_zone(self, zone: SplitZone) -> None:
        self._enabled = bool(zone.enabled)
        self._instrument_id = zone.instrument_id
        blocked = self.enable_checkbox.blockSignals(True)
        self.enable_checkbox.setChecked(self._enabled)
        self.enable_checkbox.blockSignals(blocked)
        _set_combo_data(self.low_combo, int(zone.low))
        _set_combo_data(self.high_combo, int(zone.high))
        _set_combo_data(self.octave_combo, int(zone.octave))
        _set_combo_data(self.instrument_combo, zone.instrument_id)
        blocked = self.volume_slider.blockSignals(True)
        self.volume_slider.setValue(round(zone.volume * 100))
        self.volume_slider.blockSignals(blocked)
        self.volume_value.setText(f"{round(zone.volume * 100)}%")
        self._refresh_enabled()

    def set_instrument_options(self, options: list[tuple[str, object]]) -> None:
        _fill_combo(self.instrument_combo, options, self._instrument_id)
        self._refresh_enabled()

    def set_state(self, *, status: str, error: str = "", programs: dict[int, list[int]] | None = None,
                  program_names: dict[int, dict[int, str]] | None = None, bank: int = 0, preset: int = 0,
                  is_plugin: bool = False) -> None:
        self._ready = status == "ready"
        text = {"loading": "Loading…", "error": error or "Could not load this sound."}.get(status, "")
        self.status_label.setText(text)
        self.status_label.setToolTip(text if status == "error" else "")
        options = zone_program_options(programs or {}, program_names) if self._ready else []
        _fill_combo(self.program_combo, options, (int(bank), int(preset)))
        # A plug-in chooses its sounds in its own window, so it gets that button instead.
        plugin_ready = self._ready and is_plugin
        self.program_combo.setVisible(not plugin_ready)
        self.editor_button.setVisible(plugin_ready)
        # The plug-in's name takes over the room the preset list would have used.
        self._sound_row.setStretchFactor(self.instrument_combo, 5 if plugin_ready else 3)
        self._refresh_enabled()


def create_split_card(
    card: QFrame, card_layout: QVBoxLayout, scale: ScaleFunc, zone_count: int,
) -> tuple[list[SplitZoneControls], QLabel]:
    zones = [SplitZoneControls(index, card, card_layout, scale) for index in range(zone_count)]
    hint_row = QHBoxLayout()
    hint = QLabel(
        "Each zone plays its own sound over a range of keys; every other key keeps the main "
        "instrument. Where zones overlap, the lowest-numbered zone plays.",
        card,
    )
    hint.setObjectName("settingHint")
    hint.setWordWrap(True)
    hint_row.addWidget(hint, 1)
    card_layout.addLayout(hint_row)
    return zones, hint


class PluginCardControls(QObject):

    rescanRequested = Signal()
    foldersRequested = Signal()
    editorRequested = Signal()

    def __init__(self, card: QWidget, card_layout: QVBoxLayout) -> None:
        super().__init__(card)
        status_row = QHBoxLayout()
        self.status_label = QLabel("", card)
        self.status_label.setObjectName("settingHint")
        self.status_label.setWordWrap(True)
        status_row.addWidget(self.status_label, 1)
        card_layout.addLayout(status_row)

        button_row = QHBoxLayout()
        self.rescan_button = QPushButton("Rescan Plug-ins", card)
        self.rescan_button.setObjectName("actionButton")
        self.rescan_button.setToolTip("Search the plug-in folders again and re-check every plug-in.")
        self.rescan_button.clicked.connect(self.rescanRequested.emit)
        self.folders_button = QPushButton("Plug-in Folders…", card)
        self.folders_button.setObjectName("actionButton")
        self.folders_button.setToolTip("Choose additional folders to search for VST3 plug-ins.")
        self.folders_button.clicked.connect(self.foldersRequested.emit)
        self.editor_button = QPushButton("Open Plug-in Window", card)
        self.editor_button.setObjectName("actionButton")
        self.editor_button.setToolTip("Open the window of the plug-in selected as the main instrument.")
        self.editor_button.clicked.connect(self.editorRequested.emit)
        self.editor_button.setEnabled(False)
        button_row.addWidget(self.rescan_button)
        button_row.addWidget(self.folders_button)
        button_row.addWidget(self.editor_button)
        button_row.addStretch(1)
        card_layout.addLayout(button_row)

    def set_status(self, text: str, details: str, *, available: bool, scanning: bool) -> None:
        self.status_label.setText(text)
        self.status_label.setToolTip(details)
        self.rescan_button.setEnabled(available and not scanning)
        self.folders_button.setEnabled(available)

    def set_editor_available(self, available: bool) -> None:
        self.editor_button.setEnabled(bool(available))
