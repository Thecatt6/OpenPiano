from __future__ import annotations

from typing import Callable, Sequence

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGridLayout,
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
)
from openpiano.core.theme import ThemePalette
from openpiano.ui.combo_options import zone_program_options
from openpiano.ui.key_range_picker import KeyRangeButton, KeyRangePopup, OtherRange
from openpiano.ui.styled_controls import ChevronComboBox

ScaleFunc = Callable[[int], int]
ThemeProvider = Callable[[], ThemePalette]


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


class ElidedLabel(QLabel):
    """One line of text that is shortened with an ellipsis when there is no room for it, and
    never asks the layout for room. The full text is then offered as the tooltip."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full_text = ""
        self._tooltip = ""
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setMinimumWidth(0)

    def full_text(self) -> str:
        return self._full_text

    def set_full_text(self, text: str, tooltip: str = "") -> None:
        self._full_text = str(text or "")
        self._tooltip = str(tooltip or "")
        self._refresh()

    def _refresh(self) -> None:
        shown = self.fontMetrics().elidedText(self._full_text, Qt.ElideRight, max(0, self.contentsRect().width()))
        if shown != self.text():
            self.setText(shown)
        self.setToolTip(self._tooltip or (self._full_text if shown != self._full_text else ""))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._refresh()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() in (QEvent.FontChange, QEvent.StyleChange):
            self._refresh()


# Narrowest the two lists of a zone may get, in letters, and the widest their pop-ups may get.
_SOUND_MIN_LETTERS = 11
_PRESET_MIN_LETTERS = 9
_POPUP_MAX_WIDTH = 460

# Columns of the zone table.
(_COLUMN_SWATCH, _COLUMN_ZONE, _COLUMN_KEYS, _COLUMN_SOUND, _COLUMN_PRESET, _COLUMN_OCTAVE,
 _COLUMN_VOLUME, _COLUMN_VOLUME_VALUE) = range(8)


class SplitZoneControls(QObject):
    """The row of controls that describes one split zone."""

    changed = Signal(int, object)
    editorRequested = Signal(int)

    def __init__(self, index: int, card: QWidget, grid: QGridLayout, row: int, scale: ScaleFunc,
                 theme: ThemeProvider) -> None:
        super().__init__(card)
        self.index = index
        self._card = card
        self._scale = scale
        self._theme = theme
        self._enabled = False
        self._ready = False
        self._instrument_id = ""
        self._low = SPLIT_NOTE_MIN
        self._high = SPLIT_NOTE_MAX
        self._others: list[OtherRange] = []
        self._range_popup: KeyRangePopup | None = None
        self._range_popup_scale = 0
        color = SPLIT_ZONE_COLORS[index % len(SPLIT_ZONE_COLORS)]
        self._color = color

        # Created left to right, which is also the order Tab moves through them.
        self.swatch = QFrame(card)
        self.swatch.setObjectName("splitSwatch")
        self.swatch.setStyleSheet(f"#splitSwatch {{ background: {color}; border-radius: 3px; }}")
        self.swatch.setToolTip("Colour of this zone's marker above the keys.")
        self.enable_checkbox = QCheckBox(f"Zone {index + 1}", card)
        self.enable_checkbox.setObjectName("settingCheckbox")
        self.enable_checkbox.setToolTip(f"Switch zone {index + 1} on or off.")
        self.enable_checkbox.toggled.connect(lambda checked: self._emit(enabled=bool(checked)))
        self.range_button = KeyRangeButton(scale, card)
        self.range_button.setAccessibleName(f"Zone {index + 1} key range")
        self.range_button.setToolTip(f"Choose the keys of zone {index + 1} on a picture of the keyboard.")
        self.range_button.clicked.connect(self.open_range_picker)
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
        self.editor_button = QPushButton("Plug-in Window", card)
        self.editor_button.setObjectName("actionButton")
        self.editor_button.setToolTip("Open this zone's plug-in window, where the plug-in's sounds are chosen.")
        self.editor_button.clicked.connect(lambda: self.editorRequested.emit(self.index))
        self.editor_button.setVisible(False)
        self.status_label = ElidedLabel(card)
        self.status_label.setObjectName("settingHint")
        self.status_label.setVisible(False)
        self.octave_combo = ChevronComboBox(card)
        self.octave_combo.setObjectName("splitCombo")
        self.octave_combo.setAccessibleName(f"Zone {index + 1} octave shift")
        self.octave_combo.setToolTip("Octave shift applied to this zone only.")
        for octave in range(SPLIT_OCTAVE_MIN, SPLIT_OCTAVE_MAX + 1):
            self.octave_combo.addItem("Octave 0" if octave == 0 else f"Octave {octave:+d}", userData=octave)
        self.octave_combo.currentIndexChanged.connect(lambda _index: self._emit_combo(self.octave_combo, "octave"))
        self.volume_slider = QSlider(Qt.Horizontal, card)
        self.volume_slider.setObjectName("splitVolumeSlider")
        self.volume_slider.setAccessibleName(f"Zone {index + 1} volume")
        self.volume_slider.setToolTip("Volume of this zone relative to the main volume.")
        self.volume_slider.setRange(0, round(MASTER_VOLUME_MAX * 100))
        self.volume_slider.setValue(100)
        self.volume_slider.valueChanged.connect(self._on_volume_changed)
        self.volume_value = QLabel("100%", card)
        self.volume_value.setObjectName("settingValue")
        self.volume_value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        grid.addWidget(self.swatch, row, _COLUMN_SWATCH, Qt.AlignVCenter)
        grid.addWidget(self.enable_checkbox, row, _COLUMN_ZONE)
        grid.addWidget(self.range_button, row, _COLUMN_KEYS)
        grid.addWidget(self.instrument_combo, row, _COLUMN_SOUND)
        # One place, three occupants: the preset list of a SoundFont, the window button of a
        # plug-in, or a message while the sound is loading or could not be loaded.
        grid.addWidget(self.program_combo, row, _COLUMN_PRESET)
        grid.addWidget(self.editor_button, row, _COLUMN_PRESET)
        grid.addWidget(self.status_label, row, _COLUMN_PRESET)
        grid.addWidget(self.octave_combo, row, _COLUMN_OCTAVE)
        grid.addWidget(self.volume_slider, row, _COLUMN_VOLUME)
        grid.addWidget(self.volume_value, row, _COLUMN_VOLUME_VALUE)
        self.octave_combo.setSizeAdjustPolicy(
            ChevronComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        for combo in (self.instrument_combo, self.program_combo):
            # Their share of the row is set by the table, not by the length of their names;
            # the list that drops down is made wide enough to read instead.
            combo.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            combo.set_elide_current_text(True)
            combo.popupAboutToShow.connect(lambda combo=combo: self._fit_popup_to_names(combo))
        self.instrument_combo.setPlaceholderText("Choose a sound")
        self.program_combo.setPlaceholderText("Preset")
        self.refresh_metrics()
        self._refresh_enabled()

    def refresh_metrics(self) -> None:
        swatch = max(8, self._scale(10))
        self.swatch.setFixedSize(swatch, swatch)
        self._refresh_range_button()
        self.octave_combo.setMinimumWidth(self._combo_width(self.octave_combo, "Octave +3"))
        self.instrument_combo.setMinimumWidth(self._combo_width(self.instrument_combo, "n" * _SOUND_MIN_LETTERS))
        self.program_combo.setMinimumWidth(self._combo_width(self.program_combo, "n" * _PRESET_MIN_LETTERS))
        # (The width of the value next to it comes from the style sheet, like every other
        # value label; a width set here would be replaced whenever the style is re-applied.)
        self.volume_slider.setMinimumWidth(self._scale(72))

    def _combo_width(self, combo: ChevronComboBox, widest_text: str) -> int:
        # The style sheet's padding and drop-down button take a share of the width that
        # depends on the theme metrics, so it is measured instead of assumed.
        option = QStyleOptionComboBox()
        combo.initStyleOption(option)
        field = combo.style().subControlRect(QStyle.CC_ComboBox, option, QStyle.SC_ComboBoxEditField, combo)
        chrome = option.rect.width() - field.width() if field.isValid() else self._scale(70)
        return combo.fontMetrics().horizontalAdvance(widest_text) + max(0, chrome) + self._scale(8)

    def _fit_popup_to_names(self, combo: ChevronComboBox) -> None:
        """Widens the list that drops down so that its names can be read in full, however
        narrow the combo box itself is."""
        view = combo.view()
        if view is None or combo.count() <= 0:
            return
        metrics = view.fontMetrics()
        widest = max(metrics.horizontalAdvance(combo.itemText(index)) for index in range(combo.count()))
        scroll_bar = view.style().pixelMetric(QStyle.PM_ScrollBarExtent, None, view)
        # Room for the selection marker, the margins of a row and the scroll bar; bold text
        # runs a little wider than the regular font measured here.
        wanted = round(widest * 1.08) + self._scale(30) + scroll_bar
        view.setMinimumWidth(max(combo.width(), min(wanted, self._scale(_POPUP_MAX_WIDTH))))

    def _refresh_range_button(self) -> None:
        self.range_button.show_range(self._low, self._high, self._color, self._theme())

    def open_range_picker(self) -> None:
        popup = self._range_popup
        built_for = self._scale(1000)
        if popup is not None and built_for != self._range_popup_scale:
            # Its sizes belong to the interface scale it was made at.
            popup.deleteLater()
            popup = None
        if popup is None:
            # A child of the card, so that it takes the window's fonts and colours.
            popup = KeyRangePopup(self._scale, self._card)
            popup.rangeChanged.connect(lambda low, high: self._emit(low=int(low), high=int(high)))
            self._range_popup = popup
            self._range_popup_scale = built_for
        popup.configure(
            title=f"Zone {self.index + 1} keys",
            low=self._low,
            high=self._high,
            color=self._color,
            others=self._others,
            theme=self._theme(),
        )
        popup.open_below(self.range_button)

    def range_picker(self) -> KeyRangePopup | None:
        popup = self._range_popup
        return popup if popup is not None and popup.isVisible() else None

    def feed_note(self, note: int) -> bool:
        popup = self._range_popup
        return popup is not None and popup.feed_note(note)

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
        for widget in (self.octave_combo, self.volume_slider):
            widget.setEnabled(self._enabled)
        self.instrument_combo.setEnabled(self.instrument_combo.count() > 0)
        self.program_combo.setEnabled(self._enabled and self._ready and self.program_combo.count() > 0)
        self.editor_button.setEnabled(self._enabled and self._ready)

    def set_zone(self, zone: SplitZone, others: Sequence[OtherRange] = ()) -> None:
        self._enabled = bool(zone.enabled)
        self._instrument_id = zone.instrument_id
        self._low = int(zone.low)
        self._high = int(zone.high)
        self._others = list(others)
        blocked = self.enable_checkbox.blockSignals(True)
        self.enable_checkbox.setChecked(self._enabled)
        self.enable_checkbox.blockSignals(blocked)
        self._refresh_range_button()
        popup = self.range_picker()
        if popup is not None:
            popup.set_range(self._low, self._high)
            popup.set_other_ranges(self._others)
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
        self.status_label.set_full_text(text, text if status == "error" else "")
        options = zone_program_options(programs or {}, program_names) if self._ready else []
        _fill_combo(self.program_combo, options, (int(bank), int(preset)))
        # A plug-in chooses its sounds in its own window, so it gets that button instead.
        plugin_ready = self._ready and is_plugin
        self.status_label.setVisible(bool(text))
        self.program_combo.setVisible(not text and not plugin_ready)
        self.editor_button.setVisible(not text and plugin_ready)
        self._refresh_enabled()


def other_zone_ranges(zones: Sequence[SplitZone], index: int) -> list[OtherRange]:
    """The ranges of the other zones that are switched on, with their marker colours."""
    return [
        (int(zone.low), int(zone.high), SPLIT_ZONE_COLORS[position % len(SPLIT_ZONE_COLORS)])
        for position, zone in enumerate(zones)
        if position != index and zone.enabled
    ]


SPLIT_HINT = ("Each zone plays its own sound on its keys; every other key plays the main instrument. "
              "Where zones overlap, the lowest-numbered zone plays.")


def configure_split_grid(grid: QGridLayout, scale: ScaleFunc) -> None:
    grid.setContentsMargins(6, scale(2), 6, scale(2))
    grid.setHorizontalSpacing(scale(8))
    grid.setVerticalSpacing(scale(6))


def create_split_card(
    card: QFrame, card_layout: QVBoxLayout, scale: ScaleFunc, zone_count: int, theme: ThemeProvider,
) -> tuple[list[SplitZoneControls], QLabel, QGridLayout]:
    """Fills the Keyboard Split card: one row per zone, then a line that explains the zones."""
    grid = QGridLayout()
    configure_split_grid(grid, scale)
    grid.setColumnStretch(_COLUMN_SOUND, 4)
    grid.setColumnStretch(_COLUMN_PRESET, 3)
    grid.setColumnStretch(_COLUMN_VOLUME, 2)
    zones = [SplitZoneControls(index, card, grid, index, scale, theme) for index in range(zone_count)]
    card_layout.addLayout(grid)
    hint_row = QHBoxLayout()
    hint = ElidedLabel(card)
    hint.setObjectName("settingHint")
    hint.set_full_text(SPLIT_HINT)
    hint_row.addWidget(hint, 1)
    card_layout.addLayout(hint_row)
    return zones, hint, grid


class PluginCardControls(QObject):

    rescanRequested = Signal()
    foldersRequested = Signal()
    editorRequested = Signal()
    detailsRequested = Signal()

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
        self.details_button = QPushButton("Details…", card)
        self.details_button.setObjectName("actionButton")
        self.details_button.setToolTip("Show which plug-ins were found and why any could not be loaded.")
        self.details_button.clicked.connect(self.detailsRequested.emit)
        button_row.addWidget(self.rescan_button)
        button_row.addWidget(self.folders_button)
        button_row.addWidget(self.details_button)
        button_row.addWidget(self.editor_button)
        button_row.addStretch(1)
        card_layout.addLayout(button_row)
        self._details = ""

    def set_status(self, text: str, details: str, *, available: bool, scanning: bool) -> None:
        self.status_label.setText(text)
        self._details = str(details or "")
        # The full report can run to many lines; it opens with the Details button instead.
        self.status_label.setToolTip("Details… lists what was found and why a plug-in was left out."
                                     if self._details else "")
        self.rescan_button.setEnabled(available and not scanning)
        self.folders_button.setEnabled(available)

    def report_text(self) -> str:
        return "\n\n".join(part for part in (self.status_label.text(), self._details) if part)

    def set_editor_available(self, available: bool) -> None:
        self.editor_button.setEnabled(bool(available))
