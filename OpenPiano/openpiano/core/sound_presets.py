from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Iterable, Mapping, Sequence

from openpiano.core.config import (
    INSTRUMENT_BANK_MAX,
    INSTRUMENT_BANK_MIN,
    INSTRUMENT_PRESET_MAX,
    INSTRUMENT_PRESET_MIN,
)
from openpiano.core.keymap import (
    Binding,
    binding_from_id,
    binding_reserved_reason,
    binding_to_id,
    binding_to_inline_label,
)
from openpiano.core.normalize import clamp_int
from openpiano.core.split import (
    SplitZone,
    default_split_zones,
    normalize_split_zones,
    note_name,
    split_zones_payload,
)

MAX_SOUND_PRESETS = 8
SOUND_PRESET_NAME_LIMIT = 32

TRIGGER_NOTE = "note"
TRIGGER_CONTROL = "control"
TRIGGER_PROGRAM = "program"
_TRIGGER_KINDS = (TRIGGER_NOTE, TRIGGER_CONTROL, TRIGGER_PROGRAM)

# The pedals keep their meaning; they cannot be turned into preset switches.
RESERVED_CONTROLLERS = frozenset({64, 66, 67, 120, 121, 123})


@dataclass(frozen=True, slots=True)
class MidiTrigger:
    """What has to arrive from the MIDI keyboard to recall a preset: a key, a pad or button
    that sends a controller, or a program change. An empty kind means nothing is assigned."""

    kind: str = ""
    channel: int = 0
    number: int = 0

    @property
    def assigned(self) -> bool:
        return self.kind in _TRIGGER_KINDS


@dataclass(frozen=True, slots=True)
class SoundPreset:
    """A stored sound: the main instrument and the keyboard split, with what recalls it: a key
    of the MIDI keyboard, a key of the computer keyboard, or both."""

    stored: bool = False
    name: str = ""
    summary: str = ""
    instrument_id: str = ""
    bank: int = 0
    preset: int = 0
    zones: tuple[SplitZone, ...] = field(default_factory=default_split_zones)
    trigger: MidiTrigger = field(default_factory=MidiTrigger)
    # A key of the computer keyboard, alone or with Ctrl, Shift or Alt; None when not assigned.
    pc_key: Binding | None = None


def default_sound_presets() -> tuple[SoundPreset, ...]:
    return tuple(SoundPreset() for _ in range(MAX_SOUND_PRESETS))


def default_preset_name(index: int) -> str:
    return f"Preset {int(index) + 1}"


def _clean_text(value: Any, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:limit].strip()


def normalize_trigger(value: Any) -> MidiTrigger:
    if isinstance(value, MidiTrigger):
        raw: Mapping[str, Any] = {"kind": value.kind, "channel": value.channel, "number": value.number}
    elif isinstance(value, Mapping):
        raw = value
    else:
        return MidiTrigger()
    kind = raw.get("kind")
    if kind not in _TRIGGER_KINDS:
        return MidiTrigger()
    try:
        number = int(raw.get("number"))
    except (TypeError, ValueError):
        return MidiTrigger()
    if not 0 <= number <= 127 or (kind == TRIGGER_CONTROL and number in RESERVED_CONTROLLERS):
        return MidiTrigger()
    return MidiTrigger(kind=kind, channel=clamp_int(raw.get("channel"), 0, 15, default=0), number=number)


def pc_key_refusal(binding: Binding | None) -> str | None:
    """Why a key of the computer keyboard cannot recall a preset; None when it can."""
    if binding is None or binding[0] != "keyboard" or not binding[1].startswith("scan:"):
        return "This key cannot be used. Press another one."
    return binding_reserved_reason(binding)


def normalize_pc_key(value: Any) -> Binding | None:
    """A computer key as stored in the settings (its text form) or as held in memory."""
    if isinstance(value, str):
        binding = binding_from_id(value)
    elif isinstance(value, tuple) and len(value) == 5 and all(isinstance(part, str) for part in value[:2]):
        binding = binding_from_id(binding_to_id(value))
    else:
        return None
    return binding if pc_key_refusal(binding) is None else None


def normalize_sound_preset(value: Any) -> SoundPreset:
    if isinstance(value, SoundPreset):
        raw: Mapping[str, Any] = {name: getattr(value, name) for name in SoundPreset.__slots__}
    elif isinstance(value, Mapping):
        raw = value
    else:
        return SoundPreset()
    if raw.get("stored") is not True:
        return SoundPreset()
    instrument_id = raw.get("instrument_id")
    return SoundPreset(
        stored=True,
        name=_clean_text(raw.get("name"), SOUND_PRESET_NAME_LIMIT),
        summary=_clean_text(raw.get("summary"), 400),
        instrument_id=instrument_id.strip() if isinstance(instrument_id, str) else "",
        bank=clamp_int(raw.get("bank"), INSTRUMENT_BANK_MIN, INSTRUMENT_BANK_MAX, default=0),
        preset=clamp_int(raw.get("preset"), INSTRUMENT_PRESET_MIN, INSTRUMENT_PRESET_MAX, default=0),
        zones=normalize_split_zones(raw.get("zones")),
        trigger=normalize_trigger(raw.get("trigger")),
        pc_key=normalize_pc_key(raw.get("pc_key")),
    )


def normalize_sound_presets(value: Any) -> tuple[SoundPreset, ...]:
    items: Sequence[Any] = value if isinstance(value, (list, tuple)) else ()
    presets: list[SoundPreset] = []
    used: set[MidiTrigger] = set()
    used_pc_keys: set[Binding] = set()
    for index in range(MAX_SOUND_PRESETS):
        preset = normalize_sound_preset(items[index] if index < len(items) else None)
        if preset.trigger.assigned:
            # One key recalls one preset.
            if preset.trigger in used:
                preset = replace(preset, trigger=MidiTrigger())
            else:
                used.add(preset.trigger)
        if preset.pc_key is not None:
            if preset.pc_key in used_pc_keys:
                preset = replace(preset, pc_key=None)
            else:
                used_pc_keys.add(preset.pc_key)
        presets.append(preset)
    return tuple(presets)


def sound_presets_payload(presets: Iterable[SoundPreset]) -> list[dict[str, object]]:
    payload: list[dict[str, object]] = []
    for preset in normalize_sound_presets(list(presets)):
        if not preset.stored:
            payload.append({"stored": False})
            continue
        payload.append({
            "stored": True,
            "name": preset.name,
            "summary": preset.summary,
            "instrument_id": preset.instrument_id,
            "bank": preset.bank,
            "preset": preset.preset,
            "zones": split_zones_payload(preset.zones),
            "trigger": {
                "kind": preset.trigger.kind,
                "channel": preset.trigger.channel,
                "number": preset.trigger.number,
            } if preset.trigger.assigned else None,
            "pc_key": binding_to_id(preset.pc_key) if preset.pc_key is not None else None,
        })
    return payload


def trigger_from_event(kind: str, channel: int, number: int, value: int) -> MidiTrigger | None:
    """The trigger a MIDI message stands for, or None when it cannot switch presets: releases,
    a controller going back to zero, and the pedals."""
    if kind == "note_on":
        trigger_kind = TRIGGER_NOTE
    elif kind == "controller":
        if int(value) <= 0 or int(number) in RESERVED_CONTROLLERS:
            return None
        trigger_kind = TRIGGER_CONTROL
    elif kind == "program_change":
        trigger_kind = TRIGGER_PROGRAM
    else:
        return None
    if not (0 <= int(channel) <= 15 and 0 <= int(number) <= 127):
        return None
    return MidiTrigger(kind=trigger_kind, channel=int(channel), number=int(number))


def preset_index_for_trigger(presets: Sequence[SoundPreset], trigger: MidiTrigger | None) -> int | None:
    if trigger is None or not trigger.assigned:
        return None
    for index, preset in enumerate(presets):
        if preset.stored and preset.trigger == trigger:
            return index
    return None


def trigger_label(trigger: MidiTrigger) -> str:
    if not trigger.assigned:
        return ""
    channel = f"Ch {trigger.channel + 1}"
    if trigger.kind == TRIGGER_NOTE:
        return f"{note_name(trigger.number)} · {channel}"
    if trigger.kind == TRIGGER_CONTROL:
        return f"CC {trigger.number} · {channel}"
    return f"Program {trigger.number + 1} · {channel}"


def preset_index_for_pc_key(presets: Sequence[SoundPreset], binding: Binding | None) -> int | None:
    if binding is None:
        return None
    for index, preset in enumerate(presets):
        if preset.stored and preset.pc_key == binding:
            return index
    return None


def pc_key_label(binding: Binding | None) -> str:
    return binding_to_inline_label(binding) if binding is not None else ""


def _replace_at(presets: Sequence[SoundPreset], index: int, preset: SoundPreset) -> tuple[SoundPreset, ...]:
    return tuple(preset if position == index else item for position, item in enumerate(presets))


def store_sound_preset(presets: Sequence[SoundPreset], index: int, *, instrument_id: str, bank: int,
                       preset: int, zones: Sequence[SplitZone], summary: str = "") -> tuple[SoundPreset, ...]:
    """Put a sound in a slot. The slot keeps its name and its keys."""
    current = normalize_sound_presets(list(presets))
    if not 0 <= int(index) < MAX_SOUND_PRESETS:
        return current
    previous = current[index]
    stored = normalize_sound_preset(SoundPreset(
        stored=True,
        name=previous.name or default_preset_name(index),
        summary=summary,
        instrument_id=instrument_id,
        bank=bank,
        preset=preset,
        zones=tuple(zones),
        trigger=previous.trigger,
        pc_key=previous.pc_key,
    ))
    return _replace_at(current, index, stored)


def rename_sound_preset(presets: Sequence[SoundPreset], index: int, name: str) -> tuple[SoundPreset, ...]:
    current = normalize_sound_presets(list(presets))
    if not 0 <= int(index) < MAX_SOUND_PRESETS or not current[index].stored:
        return current
    cleaned = _clean_text(name, SOUND_PRESET_NAME_LIMIT) or default_preset_name(index)
    return _replace_at(current, index, replace(current[index], name=cleaned))


def clear_sound_preset(presets: Sequence[SoundPreset], index: int) -> tuple[SoundPreset, ...]:
    current = normalize_sound_presets(list(presets))
    if not 0 <= int(index) < MAX_SOUND_PRESETS:
        return current
    return _replace_at(current, index, SoundPreset())


def assign_trigger(presets: Sequence[SoundPreset], index: int, trigger: MidiTrigger | None) -> tuple[SoundPreset, ...]:
    """Give a stored preset its MIDI key, taking the key away from any preset that had it.
    None removes the preset's key."""
    current = normalize_sound_presets(list(presets))
    if not 0 <= int(index) < MAX_SOUND_PRESETS or not current[index].stored:
        return current
    target = normalize_trigger(trigger)
    updated = []
    for position, item in enumerate(current):
        if position == index:
            updated.append(replace(item, trigger=target))
        elif target.assigned and item.trigger == target:
            updated.append(replace(item, trigger=MidiTrigger()))
        else:
            updated.append(item)
    return tuple(updated)


def assign_pc_key(presets: Sequence[SoundPreset], index: int, binding: Binding | None) -> tuple[SoundPreset, ...]:
    """Give a stored preset its key of the computer keyboard, taking the key away from any
    preset that had it. None removes the preset's key."""
    current = normalize_sound_presets(list(presets))
    if not 0 <= int(index) < MAX_SOUND_PRESETS or not current[index].stored:
        return current
    target = normalize_pc_key(binding)
    updated = []
    for position, item in enumerate(current):
        if position == index:
            updated.append(replace(item, pc_key=target))
        elif target is not None and item.pc_key == target:
            updated.append(replace(item, pc_key=None))
        else:
            updated.append(item)
    return tuple(updated)


def describe_sound(instrument_name: str, program_name: str, zones: Sequence[SplitZone],
                   zone_names: Sequence[str]) -> str:
    """One line that tells a stored sound apart: the main instrument, then each active zone."""
    main = str(instrument_name or "").strip() or "No instrument"
    program = str(program_name or "").strip()
    parts = [f"{main} – {program}" if program else main]
    for index, zone in enumerate(zones):
        if not zone.enabled:
            continue
        name = str(zone_names[index] if index < len(zone_names) else "").strip() or "no sound"
        parts.append(f"Zone {index + 1} ({note_name(zone.low)}–{note_name(zone.high)}): {name}")
    return " · ".join(parts)
