from __future__ import annotations

from typing import Callable, Sequence

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QIcon, QKeyEvent, QMouseEvent, QPainter, QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from openpiano.core.split import SPLIT_NOTE_MAX, SPLIT_NOTE_MIN, note_name
from openpiano.core.theme import ThemePalette

ScaleFunc = Callable[[int], int]
OtherRange = tuple[int, int, str]

_BLACK_PITCH_CLASSES = frozenset({1, 3, 6, 8, 10})
_BLACK_KEY_WIDTH = 0.62
_BLACK_KEY_HEIGHT = 0.62

RANGE_HINT = (
    "Drag across the keys to choose the range, or click a key to move the nearer end. "
    "With a MIDI keyboard, play the lowest and the highest key."
)


def is_black_key(note: int) -> bool:
    return int(note) % 12 in _BLACK_PITCH_CLASSES


def key_range_text(low: int, high: int) -> str:
    return f"{note_name(low)} – {note_name(high)}"


def _clamp_note(note: int) -> int:
    return min(max(int(note), SPLIT_NOTE_MIN), SPLIT_NOTE_MAX)


def _mix(base: QColor, tint: QColor, amount: float) -> QColor:
    keep = 1.0 - amount
    return QColor(
        round(base.red() * keep + tint.red() * amount),
        round(base.green() * keep + tint.green() * amount),
        round(base.blue() * keep + tint.blue() * amount),
    )


_WHITE_NOTES = tuple(note for note in range(SPLIT_NOTE_MIN, SPLIT_NOTE_MAX + 1) if not is_black_key(note))
_BLACK_NOTES = tuple(note for note in range(SPLIT_NOTE_MIN, SPLIT_NOTE_MAX + 1) if is_black_key(note))
_WHITE_INDEX = {note: index for index, note in enumerate(_WHITE_NOTES)}


class KeyColors:
    """The colours a keyboard drawing takes from the theme."""

    def __init__(self, theme: ThemePalette | None = None) -> None:
        self.white = QColor(theme.white_key if theme else "#E8E3D7")
        self.black = QColor(theme.black_key if theme else "#15161A")
        self.panel = QColor(theme.panel_bg if theme else "#141416")
        self.border = QColor(theme.border if theme else "#2A2A2D")
        self.text = QColor(theme.text_secondary if theme else "#B7B7BC")


def _white_rect(area: QRectF, note: int) -> QRectF:
    width = area.width() / len(_WHITE_NOTES)
    index = _WHITE_INDEX[note]
    left = round(index * width)
    right = round((index + 1) * width)
    return QRectF(area.left() + left, area.top(), right - left, area.height())


def _black_rect(area: QRectF, note: int) -> QRectF:
    # A black key straddles the edge between the white keys on either side of it.
    white = area.width() / len(_WHITE_NOTES)
    edge = round((_WHITE_INDEX[note - 1] + 1) * white)
    width = max(1.0, round(white * _BLACK_KEY_WIDTH))
    return QRectF(area.left() + edge - width / 2.0, area.top(), width, round(area.height() * _BLACK_KEY_HEIGHT))


def key_rect(area: QRectF, note: int) -> QRectF:
    return _black_rect(area, note) if is_black_key(note) else _white_rect(area, note)


def paint_keys(painter: QPainter, area: QRectF, low: int, high: int, zone_color: QColor,
               colors: KeyColors, *, hover: int | None = None) -> None:
    """Draws the 88 keys in ``area`` with the keys from ``low`` to ``high`` in the zone colour."""
    white_width = area.width() / len(_WHITE_NOTES)
    # Gaps between keys only where a key is wide enough to keep some colour next to them.
    gap = 1 if white_width >= 4 else 0
    selected_white = _mix(colors.white, zone_color, 0.62)
    idle_white = _mix(colors.white, colors.panel, 0.42)
    selected_black = _mix(colors.black, zone_color, 0.72)
    # Dark enough to outline white keys on a light panel as well as on a dark one.
    outline = _mix(idle_white, colors.black, 0.45)
    painter.fillRect(area, outline)
    for note in _WHITE_NOTES:
        fill = selected_white if low <= note <= high else idle_white
        if note == hover:
            fill = fill.lighter(118)
        painter.fillRect(_white_rect(area, note).adjusted(0, 0, -gap, 0), fill)
    for note in _BLACK_NOTES:
        rect = _black_rect(area, note)
        fill = selected_black if low <= note <= high else colors.black
        if note == hover:
            fill = fill.lighter(150)
        if gap:
            painter.fillRect(rect, outline)
            rect = rect.adjusted(1, 0, -1, -1)
        painter.fillRect(rect, fill)
    painter.setPen(outline)
    painter.drawRect(area.adjusted(0, 0, -1, -1))


def key_range_thumbnail(low: int, high: int, color: str, theme: ThemePalette, size: QSize, pixel_ratio: float) -> QPixmap:
    ratio = max(1.0, float(pixel_ratio))
    pixmap = QPixmap(round(size.width() * ratio), round(size.height() * ratio))
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    # Painted in device pixels so that the narrow keys stay crisp on scaled displays.
    paint_keys(painter, QRectF(0, 0, pixmap.width(), pixmap.height()), int(low), int(high), QColor(color), KeyColors(theme))
    painter.end()
    pixmap.setDevicePixelRatio(ratio)
    return pixmap


class KeyRangeButton(QPushButton):
    """Shows a zone's range as text next to a small keyboard and opens the picker."""

    def __init__(self, scale: ScaleFunc, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._scale = scale
        self._state: tuple | None = None
        self._measured: tuple | None = None
        self.setObjectName("splitRangeButton")

    def thumbnail_size(self) -> QSize:
        return QSize(max(52, self._scale(104)), max(9, self._scale(15)))

    def show_range(self, low: int, high: int, color: str, theme: ThemePalette) -> None:
        size = self.thumbnail_size()
        ratio = float(self.devicePixelRatioF())
        self.ensurePolished()
        measured = (self.font().toString(), size.width(), size.height())
        state = (int(low), int(high), str(color), theme, ratio, measured)
        if state == self._state:
            return
        self._state = state
        self.setIconSize(size)
        self.setIcon(QIcon(key_range_thumbnail(low, high, color, theme, size, ratio)))
        if measured != self._measured:
            # As wide as the longest range needs, so that the row keeps still while choosing.
            self._measured = measured
            metrics = self.fontMetrics()
            widest = max(range(SPLIT_NOTE_MIN, SPLIT_NOTE_MAX + 1), key=lambda note: metrics.horizontalAdvance(note_name(note)))
            self.setText(key_range_text(widest, widest))
            self.setMinimumWidth(self.sizeHint().width() + max(1, self._scale(2)))
        self.setText(key_range_text(low, high))


class KeyRangeKeyboard(QWidget):
    """A small drawing of the whole keyboard on which a range of keys is chosen."""

    rangeChanged = Signal(int, int)
    previewChanged = Signal()
    hoverChanged = Signal(int)
    dragStarted = Signal()

    def __init__(self, scale: ScaleFunc, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._scale = scale
        self._low = SPLIT_NOTE_MIN
        self._high = SPLIT_NOTE_MAX
        self._preview: tuple[int, int] | None = None
        self._anchor: int | None = None
        self._hover: int | None = None
        self._color = QColor("#4FA3FF")
        self._others: list[tuple[int, int, QColor]] = []
        self._colors = KeyColors()
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setAccessibleName("Key range")
        font = QFont(self.font())
        font.setPixelSize(max(8, self._scale(10)))
        self.setFont(font)

    # --- state -------------------------------------------------------------------------

    def key_range(self) -> tuple[int, int]:
        return self._low, self._high

    def shown_range(self) -> tuple[int, int]:
        return self._preview if self._preview is not None else (self._low, self._high)

    def set_range(self, low: int, high: int) -> None:
        low, high = sorted((_clamp_note(low), _clamp_note(high)))
        if (low, high) != (self._low, self._high):
            self._low, self._high = low, high
            self.update()

    def set_preview(self, low: int | None, high: int | None = None) -> None:
        preview = None
        if low is not None:
            first = _clamp_note(low)
            second = first if high is None else _clamp_note(high)
            preview = (min(first, second), max(first, second))
        if preview != self._preview:
            self._preview = preview
            self.update()
            self.previewChanged.emit()

    def set_zone_color(self, color: str) -> None:
        self._color = QColor(color)
        self.update()

    def set_other_ranges(self, ranges: Sequence[OtherRange]) -> None:
        self._others = [(_clamp_note(low), _clamp_note(high), QColor(color)) for low, high, color in ranges]
        self.update()

    def set_theme(self, theme: ThemePalette) -> None:
        self._colors = KeyColors(theme)
        self.update()

    def dragging(self) -> bool:
        return self._anchor is not None

    def reset_interaction(self) -> None:
        """Forgets a drag or preview that was under way, for when the panel closes."""
        self._anchor = None
        self._set_hover(None)
        self.set_preview(None)

    def commit(self, low: int, high: int) -> None:
        low, high = sorted((_clamp_note(low), _clamp_note(high)))
        changed = (low, high) != (self._low, self._high)
        self._low, self._high = low, high
        self.update()
        if changed:
            self.rangeChanged.emit(low, high)

    # --- geometry ----------------------------------------------------------------------

    def _marker_height(self) -> int:
        return max(3, self._scale(5))

    def _label_height(self) -> int:
        return self.fontMetrics().height() + max(1, self._scale(2))

    def _keys_top(self) -> int:
        return self._marker_height() + max(2, self._scale(3))

    def _keys_height(self) -> int:
        return max(16, self.height() - self._keys_top() - self._label_height())

    def _keys_area(self) -> QRectF:
        return QRectF(0, self._keys_top(), self.width(), self._keys_height())

    def sizeHint(self) -> QSize:
        white = max(8, self._scale(14))
        return QSize(white * len(_WHITE_NOTES), self._keys_top() + max(36, self._scale(64)) + self._label_height())

    def minimumSizeHint(self) -> QSize:
        return QSize(5 * len(_WHITE_NOTES), self._keys_top() + max(28, self._scale(40)) + self._label_height())

    def key_rect(self, note: int) -> QRectF:
        return key_rect(self._keys_area(), note)

    def note_at(self, position: QPoint | QPointF, *, clamp: bool = False) -> int | None:
        area = self._keys_area()
        x = float(position.x())
        y = float(position.y())
        if clamp:
            x = min(max(x, area.left()), area.right() - 1.0)
            y = min(max(y, area.top()), area.bottom() - 1.0)
        elif not area.contains(QPointF(x, y)):
            return None
        if y < area.top() + round(area.height() * _BLACK_KEY_HEIGHT):
            for note in _BLACK_NOTES:
                if _black_rect(area, note).contains(QPointF(x, y)):
                    return note
        index = min(len(_WHITE_NOTES) - 1, max(0, int(x / (area.width() / len(_WHITE_NOTES)))))
        return _WHITE_NOTES[index]

    # --- painting ----------------------------------------------------------------------

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        low, high = self.shown_range()
        area = self._keys_area()
        # The other zones, as thin strips like the ones above the real keys.
        for other_low, other_high, color in self._others:
            left = key_rect(area, other_low).left()
            right = key_rect(area, other_high).right()
            painter.fillRect(QRectF(left, 0, max(1.0, right - left), self._marker_height()), color)
        paint_keys(painter, area, low, high, self._color, self._colors,
                   hover=self._hover if self._anchor is None else None)
        painter.setPen(self._colors.text)
        label_top = area.bottom() + max(1, self._scale(1))
        label_width = area.width() / len(_WHITE_NOTES) * 7
        for note in _WHITE_NOTES:
            if note % 12 == 0:
                painter.drawText(QRectF(key_rect(area, note).left(), label_top, label_width, self._label_height()),
                                 Qt.AlignLeft | Qt.AlignTop, note_name(note))
        painter.end()

    # --- input -------------------------------------------------------------------------

    def _set_hover(self, note: int | None) -> None:
        if note != self._hover:
            self._hover = note
            self.update()
            self.hoverChanged.emit(-1 if note is None else int(note))

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.LeftButton:
            return
        note = self.note_at(event.position())
        if note is None:
            return
        self._anchor = note
        self._set_hover(note)
        self.set_preview(note, note)
        self.dragStarted.emit()
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._anchor is None:
            self._set_hover(self.note_at(event.position()))
            return
        note = self.note_at(event.position(), clamp=True)
        self._set_hover(note)
        self.set_preview(self._anchor, note)
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.LeftButton or self._anchor is None:
            return
        anchor, self._anchor = self._anchor, None
        note = self.note_at(event.position(), clamp=True)
        self.set_preview(None)
        if note is None or note == anchor:
            self._move_nearer_end(anchor)
        else:
            self.commit(anchor, note)
        event.accept()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        # The second of two quick clicks is a click like any other.
        self.mousePressEvent(event)

    def leaveEvent(self, _event) -> None:
        if self._anchor is None:
            self._set_hover(None)

    def _move_nearer_end(self, note: int) -> None:
        if abs(note - self._low) <= abs(note - self._high):
            self.commit(note, self._high)
        else:
            self.commit(self._low, note)


class KeyRangePopup(QFrame):
    """Drop-down panel with the keyboard drawing, opened from a zone's range button."""

    rangeChanged = Signal(int, int)

    def __init__(self, scale: ScaleFunc, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        self._scale = scale
        self._learned: int | None = None
        self._opened_from: QWidget | None = None
        self.setObjectName("splitRangePopup")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFocusPolicy(Qt.StrongFocus)
        layout = QVBoxLayout(self)
        margin = max(6, scale(12))
        layout.setContentsMargins(margin, max(5, scale(10)), margin, max(5, scale(10)))
        layout.setSpacing(max(3, scale(7)))

        header = QHBoxLayout()
        header.setSpacing(max(4, scale(8)))
        self.swatch = QFrame(self)
        self.swatch.setObjectName("splitRangeSwatch")
        swatch = max(8, scale(10))
        self.swatch.setFixedSize(swatch, swatch)
        self.title_label = QLabel("", self)
        self.title_label.setObjectName("splitRangeTitle")
        self.hover_label = QLabel("", self)
        self.hover_label.setObjectName("settingHint")
        self.range_label = QLabel("", self)
        self.range_label.setObjectName("splitRangeValue")
        self.range_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        header.addWidget(self.swatch)
        header.addWidget(self.title_label)
        header.addWidget(self.hover_label)
        header.addStretch(1)
        header.addWidget(self.range_label)
        layout.addLayout(header)

        self.keyboard = KeyRangeKeyboard(scale, self)
        self.keyboard.rangeChanged.connect(self._on_keyboard_range)
        self.keyboard.previewChanged.connect(self._refresh_text)
        self.keyboard.hoverChanged.connect(self._on_hover)
        self.keyboard.dragStarted.connect(self._on_drag_started)
        layout.addWidget(self.keyboard)

        self.hint_label = QLabel(RANGE_HINT, self)
        self.hint_label.setObjectName("settingHint")
        self.hint_label.setWordWrap(True)
        layout.addWidget(self.hint_label)

    def configure(self, *, title: str, low: int, high: int, color: str,
                  others: Sequence[OtherRange], theme: ThemePalette) -> None:
        self.title_label.setText(title)
        self.swatch.setStyleSheet(f"#splitRangeSwatch {{ background: {color}; border-radius: 3px; }}")
        self.keyboard.set_theme(theme)
        self.keyboard.set_zone_color(color)
        self.keyboard.set_other_ranges(others)
        self.set_range(low, high)

    def set_range(self, low: int, high: int) -> None:
        self.keyboard.set_range(low, high)
        self._refresh_text()

    def set_other_ranges(self, others: Sequence[OtherRange]) -> None:
        self.keyboard.set_other_ranges(others)

    def _refresh_text(self) -> None:
        if self._learned is not None and not self.keyboard.dragging():
            self.range_label.setText(f"{note_name(self._learned)} – play the other end")
            return
        low, high = self.keyboard.shown_range()
        count = high - low + 1
        self.range_label.setText(f"{key_range_text(low, high)}  ·  {count} key{'' if count == 1 else 's'}")

    def _on_hover(self, note: int) -> None:
        self.hover_label.setText(note_name(note) if note >= 0 else "")

    def _on_drag_started(self) -> None:
        # Choosing with the mouse replaces a range that was half played on the instrument.
        self._learned = None
        self._refresh_text()

    def _on_keyboard_range(self, low: int, high: int) -> None:
        self._learned = None
        self._refresh_text()
        self.rangeChanged.emit(low, high)

    def feed_note(self, note: int) -> bool:
        """A key played on the instrument while the panel is open: the first one is one end
        of the range, the second one the other."""
        if not self.isVisible():
            return False
        value = _clamp_note(note)
        if self.keyboard.dragging():
            return True
        if self._learned is None:
            self._learned = value
            self.keyboard.set_preview(value, value)
            self._refresh_text()
            return True
        first, self._learned = self._learned, None
        self.keyboard.set_preview(None)
        self.keyboard.commit(first, value)
        self._refresh_text()
        return True

    def open_below(self, anchor: QWidget) -> None:
        self._learned = None
        self._opened_from = anchor
        self.setAttribute(Qt.WA_NoMouseReplay, False)
        self.keyboard.reset_interaction()
        screen = anchor.screen() or QGuiApplication.primaryScreen()
        area = screen.availableGeometry() if screen is not None else None
        margin = max(4, self._scale(6))
        # The fonts come from the window's style sheet; the size is only right once applied.
        self.ensurePolished()
        self.layout().activate()
        size = self.sizeHint()
        if area is not None and size.width() > area.width() - 2 * margin:
            size.setWidth(area.width() - 2 * margin)
        self.resize(size)
        position = anchor.mapToGlobal(QPoint(0, anchor.height() + margin))
        if area is not None:
            if position.y() + size.height() > area.bottom() - margin:
                above = anchor.mapToGlobal(QPoint(0, 0)).y() - size.height() - margin
                position.setY(max(area.top() + margin, above))
            position.setX(max(area.left() + margin, min(position.x(), area.right() - margin - size.width())))
        self.move(position)
        self._refresh_text()
        self.show()
        self.setFocus(Qt.PopupFocusReason)

    def hideEvent(self, event) -> None:
        self._learned = None
        self.keyboard.reset_interaction()
        super().hideEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        anchor = self._opened_from
        if anchor is not None and not self.rect().contains(event.position().toPoint()):
            button_area = QRect(anchor.mapToGlobal(QPoint(0, 0)), anchor.size())
            if button_area.contains(event.globalPosition().toPoint()):
                # A click on the button that opened the panel closes it, and must not be
                # passed on to the button, which would open it again.
                self.setAttribute(Qt.WA_NoMouseReplay, True)
        super().mousePressEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        if key in (Qt.Key_Escape, Qt.Key_Return, Qt.Key_Enter):
            self.hide()
            event.accept()
            return
        if key in (Qt.Key_Left, Qt.Key_Right):
            # Arrows move the top of the range, Shift+arrows the bottom.
            step = -1 if key == Qt.Key_Left else 1
            low, high = self.keyboard.key_range()
            if event.modifiers() & Qt.ShiftModifier:
                low = min(_clamp_note(low + step), high)
            else:
                high = max(_clamp_note(high + step), low)
            self._learned = None
            self.keyboard.set_preview(None)
            self.keyboard.commit(low, high)
            self._refresh_text()
            event.accept()
            return
        super().keyPressEvent(event)
