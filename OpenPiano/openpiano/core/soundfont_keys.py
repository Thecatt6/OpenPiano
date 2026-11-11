"""Which keys a SoundFont preset can play, read from the preset tables of the file.

The engine starts a voice for a key only where a zone of the preset and a zone of the
instrument that zone points to both include the key, and the sample behind it is usable.
The rules followed here are the ones FluidSynth 2.5 applies when it loads a file
(fluid_sffile.c and fluid_defsfont.c), so that a key reported as silent is one the engine
really leaves silent.
"""
from __future__ import annotations

import struct
from collections import OrderedDict
from pathlib import Path
from typing import Sequence

from openpiano.core.split import SplitZone, zone_index_for_note, zone_output_note

_GEN_INSTRUMENT = 41
_GEN_KEY_RANGE = 43
_GEN_VEL_RANGE = 44
_GEN_SAMPLE_ID = 53

_PRESET_RECORD = 38
_INSTRUMENT_RECORD = 22
_BAG_RECORD = 4
_GENERATOR_RECORD = 4
_SAMPLE_RECORD = 46

# Sample types: mono, right, left, linked, compressed (SF3) and ROM. A ROM sample or one with
# any other flag is not played.
_SAMPLE_COMPRESSED = 0x10
_SAMPLE_ROM = 0x8000
_SAMPLE_KNOWN_FLAGS = 0x1 | 0x2 | 0x4 | 0x8 | _SAMPLE_COMPRESSED | _SAMPLE_ROM

_TABLES = (b"phdr", b"pbag", b"pgen", b"inst", b"ibag", b"igen", b"shdr")
_MAX_TABLE_BYTES = 64 * 1024 * 1024
_MAX_CACHED_FILES = 6

# Key low, key high, velocity low, velocity high.
_Range = tuple[int, int, int, int]
_WHOLE_RANGE: _Range = (0, 127, 0, 127)


def _read_tables(path: Path) -> tuple[dict[bytes, bytes], int]:
    """The preset tables of a SoundFont file and the size in bytes of its sample data.

    The sample data itself, nearly all of the file, is not read.
    """
    tables: dict[bytes, bytes] = {}
    sample_bytes = -1
    with path.open("rb") as stream:
        header = stream.read(12)
        if len(header) != 12 or header[:4] != b"RIFF" or header[8:] != b"sfbk":
            raise ValueError("Not a SoundFont file.")
        end = 8 + struct.unpack_from("<I", header, 4)[0]
        for kind, start, stop in _chunks(stream, 12, end, lists=True):
            if kind == b"sdta":
                for tag, position, length in _chunks(stream, start, stop):
                    if tag == b"smpl":
                        sample_bytes = length
            elif kind == b"pdta":
                for tag, position, length in _chunks(stream, start, stop):
                    if tag not in _TABLES:
                        continue
                    if length > _MAX_TABLE_BYTES:
                        raise ValueError("Unexpected SoundFont table size.")
                    stream.seek(position)
                    tables[tag] = stream.read(length)
                    if len(tables[tag]) != length:
                        raise ValueError("The SoundFont ends inside a table.")
    missing = [tag.decode() for tag in _TABLES if tag not in tables]
    if missing or sample_bytes < 0:
        raise ValueError("The SoundFont lacks its sample data or some preset tables.")
    return tables, sample_bytes


def _chunks(stream, start: int, end: int, *, lists: bool = False):
    """The chunks between two file positions, as (tag, position of the content, length).

    With lists, only the LIST chunks, as (list type, start of what they hold, its end).
    """
    position = start
    while position + 8 <= end:
        stream.seek(position)
        head = stream.read(8)
        if len(head) != 8:
            return
        tag, length = struct.unpack("<4sI", head)
        if not lists:
            yield tag, position + 8, length
        elif tag == b"LIST" and length >= 4:
            yield stream.read(4), position + 12, position + 8 + length
        position += 8 + length + (length & 1)


def _zones(bags: bytes, generators: bytes, first: int, end: int, target_generator: int) -> list[tuple[_Range, int]]:
    """The zones first..end-1 of a preset or of an instrument, as (range, target).

    The target is the instrument a preset zone plays, or the sample an instrument zone plays.
    A zone without target is the global zone when it comes first, and gives its range to the
    zones after it that have none of their own; anywhere else it is discarded. A key range
    counts only as the first generator of its zone, a velocity range only if at most a key
    range precedes it, and nothing counts after the target.
    """
    zones: list[tuple[_Range, int]] = []
    inherited = _WHOLE_RANGE
    last_bag = len(bags) // _BAG_RECORD - 1
    generator_count = len(generators) // _GENERATOR_RECORD
    for position, bag in enumerate(range(first, min(end, last_bag))):
        generator_start = struct.unpack_from("<H", bags, bag * _BAG_RECORD)[0]
        generator_end = struct.unpack_from("<H", bags, (bag + 1) * _BAG_RECORD)[0]
        key_low, key_high, velocity_low, velocity_high = inherited
        level = 0
        target = None
        for index in range(generator_start, min(generator_end, generator_count)):
            operator, amount = struct.unpack_from("<HH", generators, index * _GENERATOR_RECORD)
            if operator == _GEN_KEY_RANGE:
                if level == 0:
                    level = 1
                    key_low, key_high = amount & 0xFF, amount >> 8
            elif operator == _GEN_VEL_RANGE:
                if level <= 1:
                    level = 2
                    velocity_low, velocity_high = amount & 0xFF, amount >> 8
            elif operator == target_generator:
                target = amount
                break
            else:
                level = 2
        if target is None:
            if position == 0:
                inherited = (key_low, key_high, velocity_low, velocity_high)
            continue
        zones.append(((key_low, key_high, velocity_low, velocity_high), target))
    return zones


class _SoundFontTables:

    def __init__(self, tables: dict[bytes, bytes], sample_bytes: int) -> None:
        self._tables = tables
        self._sample_bytes = sample_bytes
        self._instrument_zones: dict[int, tuple[_Range, ...]] = {}
        self._keys: dict[tuple[int, int], frozenset[int] | None] = {}

    def _sample_usable(self, index: int) -> bool:
        samples = self._tables[b"shdr"]
        # The last record of every table only marks its end.
        if not 0 <= index < len(samples) // _SAMPLE_RECORD - 1:
            return False
        start, end = struct.unpack_from("<II", samples, index * _SAMPLE_RECORD + 20)
        kind = struct.unpack_from("<H", samples, index * _SAMPLE_RECORD + 44)[0]
        if kind & _SAMPLE_ROM or kind & ~_SAMPLE_KNOWN_FLAGS:
            return False
        # A compressed sample is placed by bytes, any other by 16-bit words.
        room = self._sample_bytes
        if not kind & _SAMPLE_COMPRESSED:
            if room % 2:
                return False
            room //= 2
        last = end - 1 if end > 0 else 0
        return start < last <= room

    def _zones_of_instrument(self, index: int) -> tuple[_Range, ...]:
        cached = self._instrument_zones.get(index)
        if cached is not None:
            return cached
        instruments = self._tables[b"inst"]
        ranges: tuple[_Range, ...] = ()
        if 0 <= index < len(instruments) // _INSTRUMENT_RECORD - 1:
            first = struct.unpack_from("<H", instruments, index * _INSTRUMENT_RECORD + 20)[0]
            end = struct.unpack_from("<H", instruments, (index + 1) * _INSTRUMENT_RECORD + 20)[0]
            ranges = tuple(
                zone for zone, sample in _zones(self._tables[b"ibag"], self._tables[b"igen"], first, end, _GEN_SAMPLE_ID)
                if self._sample_usable(sample)
            )
        self._instrument_zones[index] = ranges
        return ranges

    def keys(self, bank: int, preset: int) -> frozenset[int] | None:
        address = (int(bank), int(preset))
        if address not in self._keys:
            self._keys[address] = self._find_keys(*address)
        return self._keys[address]

    def _find_keys(self, bank: int, preset: int) -> frozenset[int] | None:
        presets = self._tables[b"phdr"]
        found = False
        keys: set[int] = set()
        for index in range(len(presets) // _PRESET_RECORD - 1):
            number, bank_number, first = struct.unpack_from("<HHH", presets, index * _PRESET_RECORD + 20)
            if (bank_number, number) != (bank, preset):
                continue
            found = True
            end = struct.unpack_from("<H", presets, (index + 1) * _PRESET_RECORD + 24)[0]
            for preset_zone, instrument in _zones(self._tables[b"pbag"], self._tables[b"pgen"], first, end, _GEN_INSTRUMENT):
                for instrument_zone in self._zones_of_instrument(instrument):
                    key_low = max(preset_zone[0], instrument_zone[0])
                    key_high = min(preset_zone[1], instrument_zone[1], 127)
                    # Velocity 0 is a note-off, so a zone has to accept some velocity from 1 up.
                    velocity_low = max(preset_zone[2], instrument_zone[2], 1)
                    velocity_high = min(preset_zone[3], instrument_zone[3], 127)
                    if velocity_low <= velocity_high:
                        keys.update(range(key_low, key_high + 1))
        # A preset that plays nothing at all is more likely a file read wrongly than an
        # instrument, and must not grey out the whole keyboard.
        return frozenset(keys) if found and keys else None


_cache: "OrderedDict[tuple[str, int, int], _SoundFontTables | None]" = OrderedDict()


def playable_keys(path: Path | str, bank: int, preset: int) -> frozenset[int] | None:
    """The MIDI notes (0-127) a preset of a SoundFont file can play at some velocity.

    None means it cannot be told: the file cannot be read or is not laid out as expected, or
    it has no such preset. Nothing should be shown as silent then.
    """
    try:
        file = Path(path)
        stat = file.stat()
        identity = (str(file), stat.st_size, stat.st_mtime_ns)
    except OSError:
        return None
    if identity in _cache:
        _cache.move_to_end(identity)
        tables = _cache[identity]
    else:
        try:
            tables = _SoundFontTables(*_read_tables(file))
        except (OSError, ValueError, struct.error):
            tables = None
        _cache[identity] = tables
        while len(_cache) > _MAX_CACHED_FILES:
            _cache.popitem(last=False)
    if tables is None:
        return None
    try:
        return tables.keys(bank, preset)
    except struct.error:
        return None


def silent_notes(main_keys: frozenset[int] | None, zones: Sequence[SplitZone],
                 zone_keys: Sequence[frozenset[int] | None]) -> frozenset[int]:
    """The keys of the keyboard that make no sound with the sounds that are loaded.

    main_keys and zone_keys[i] are the notes the main sound and each split zone can play, or
    None where that is unknown (a plug-in, a sound that is not ready). A key belongs to the
    first enabled zone that contains it, which plays it shifted by the zone's octaves, and to
    the main sound otherwise.
    """
    silent: set[int] = set()
    for note in range(128):
        index = zone_index_for_note(zones, note)
        if index is None:
            if main_keys is not None and note not in main_keys:
                silent.add(note)
            continue
        played = zone_output_note(zones[index], note)
        keys = zone_keys[index] if index < len(zone_keys) else None
        if played is None or (keys is not None and played not in keys):
            silent.add(note)
    return frozenset(silent)
