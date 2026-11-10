from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Iterable, Sequence

from openpiano.core.audio_levels import normalize_volume
from openpiano.core.config import (
    DEFAULT_MASTER_VOLUME,
    INSTRUMENT_BANK_MAX,
    INSTRUMENT_BANK_MIN,
    INSTRUMENT_PRESET_MAX,
    INSTRUMENT_PRESET_MIN,
)
from openpiano.core.keymap import MIDI_END_88, MIDI_START_88
from openpiano.core.normalize import clamp_int

MAX_SPLIT_ZONES = 3
SPLIT_NOTE_MIN = MIDI_START_88
SPLIT_NOTE_MAX = MIDI_END_88
SPLIT_OCTAVE_MIN = -3
SPLIT_OCTAVE_MAX = 3
SPLIT_ZONE_COLORS: tuple[str, ...] = ("#4FA3FF", "#F5A742", "#5FCF80")

_DEFAULT_RANGES: tuple[tuple[int, int], ...] = ((21, 59), (72, 108), (48, 71))
_NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


@dataclass(frozen=True, slots=True)
class SplitZone:

    enabled: bool = False
    low: int = SPLIT_NOTE_MIN
    high: int = SPLIT_NOTE_MAX
    instrument_id: str = ""
    bank: int = 0
    preset: int = 0
    volume: float = DEFAULT_MASTER_VOLUME
    octave: int = 0

    def contains(self, note: int) -> bool:
        return self.low <= int(note) <= self.high


def note_name(note: int) -> str:
    value = int(note)
    return f"{_NOTE_NAMES[value % 12]}{value // 12 - 1}"


def default_split_zones() -> tuple[SplitZone, ...]:
    zones = []
    for index in range(MAX_SPLIT_ZONES):
        low, high = _DEFAULT_RANGES[index % len(_DEFAULT_RANGES)]
        zones.append(SplitZone(low=low, high=high))
    return tuple(zones)


def normalize_split_zone(value: Any, default: SplitZone) -> SplitZone:
    if isinstance(value, SplitZone):
        raw = {name: getattr(value, name) for name in SplitZone.__slots__}
    elif isinstance(value, dict):
        raw = value
    else:
        return default
    low = clamp_int(raw.get("low"), SPLIT_NOTE_MIN, SPLIT_NOTE_MAX, default=default.low)
    high = clamp_int(raw.get("high"), SPLIT_NOTE_MIN, SPLIT_NOTE_MAX, default=default.high)
    if low > high:
        low, high = high, low
    enabled = raw.get("enabled")
    instrument_id = raw.get("instrument_id")
    return SplitZone(
        enabled=enabled if isinstance(enabled, bool) else default.enabled,
        low=low,
        high=high,
        instrument_id=instrument_id.strip() if isinstance(instrument_id, str) else default.instrument_id,
        bank=clamp_int(raw.get("bank"), INSTRUMENT_BANK_MIN, INSTRUMENT_BANK_MAX, default=default.bank),
        preset=clamp_int(raw.get("preset"), INSTRUMENT_PRESET_MIN, INSTRUMENT_PRESET_MAX, default=default.preset),
        volume=normalize_volume(raw.get("volume", default.volume)),
        octave=clamp_int(raw.get("octave"), SPLIT_OCTAVE_MIN, SPLIT_OCTAVE_MAX, default=default.octave),
    )


def normalize_split_zones(value: Any) -> tuple[SplitZone, ...]:
    defaults = default_split_zones()
    items: Sequence[Any] = value if isinstance(value, (list, tuple)) else ()
    return tuple(
        normalize_split_zone(items[index] if index < len(items) else None, defaults[index])
        for index in range(MAX_SPLIT_ZONES)
    )


def split_zones_payload(zones: Iterable[SplitZone]) -> list[dict[str, object]]:
    return [
        {name: getattr(zone, name) for name in SplitZone.__slots__}
        for zone in normalize_split_zones(list(zones))
    ]


def update_split_zone(zones: Sequence[SplitZone], index: int, **changes: Any) -> tuple[SplitZone, ...]:
    current = normalize_split_zones(list(zones))
    if not 0 <= int(index) < MAX_SPLIT_ZONES:
        return current
    known = {name: value for name, value in changes.items() if name in SplitZone.__slots__}
    zone = current[index]
    # Keep the range valid when only one end moves past the other.
    if "low" in known and "high" not in known:
        low = clamp_int(known["low"], SPLIT_NOTE_MIN, SPLIT_NOTE_MAX, default=zone.low)
        known["low"], known["high"] = low, max(low, zone.high)
    elif "high" in known and "low" not in known:
        high = clamp_int(known["high"], SPLIT_NOTE_MIN, SPLIT_NOTE_MAX, default=zone.high)
        known["high"], known["low"] = high, min(high, zone.low)
    updated = normalize_split_zone(replace(zone, **known), zone)
    return tuple(updated if position == index else item for position, item in enumerate(current))


def zone_index_for_note(zones: Sequence[SplitZone], note: int) -> int | None:
    # The lowest-numbered enabled zone wins where ranges overlap.
    for index, zone in enumerate(zones):
        if zone.enabled and zone.contains(note):
            return index
    return None


def zone_output_note(zone: SplitZone, note: int) -> int | None:
    shifted = int(note) + 12 * int(zone.octave)
    return shifted if 0 <= shifted <= 127 else None


def zone_gain(master_volume: float, zone: SplitZone) -> float:
    return normalize_volume(normalize_volume(master_volume) * normalize_volume(zone.volume))
