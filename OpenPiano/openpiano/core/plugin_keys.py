"""Which keys a plug-in instrument leaves silent, found by playing them.

A plug-in does not say which notes it can play, so the only way to know is to play each one
with the output muted and see whether a signal comes out. That is a measurement, not a fact
read from a file: wherever it is in doubt a key is taken to play, so that a key shown as
silent is one that really gave nothing.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from collections import deque
from pathlib import Path
from typing import Callable, Iterable, NamedTuple, Sequence


# The keys of the 88-key keyboard, the largest one shown.
KEYBOARD_LOW = 21
KEYBOARD_HIGH = 108

_BLOCK_SECONDS = 0.02
_VELOCITY = 100
# Below this a block counts as silence (-80 dB of full scale).
_MIN_LEVEL = 1e-4
# A note is heard when the output rises this far above the loudest of the blocks before it.
_RISE = 1.5
_BLOCKS_BEFORE = 5
# A key is only judged while what is still sounding is weak enough for a note to stand out:
# under this share of the quietest note heard, or under this level while none has been heard.
_NOTE_SHARE = 0.25
_ASSUMED_NOTE_LEVEL = 0.003
# A note stands clear of the background once it is this many times the level it had to rise above.
_CLEAR = 8.0
# How long a key is held before it is taken for silent; longer for a plug-in whose notes start
# slowly, up to the second limit.
_ATTACK_SECONDS = 0.3
_LONGEST_ATTACK_SECONDS = 2.0
# A played note is left to fade to this share of its level, for no longer than this.
_RELEASE_RATIO = 0.02
_RELEASE_SECONDS = 0.4
# Longest wait for earlier notes to fade before a key is judged: for each key, and where the
# search starts from the other end of the keyboard.
_FADE_SECONDS = 1.0
_LONG_FADE_SECONDS = 8.0
# Longest wait for the plug-in to fall quiet after the last key.
_SETTLE_SECONDS = 2.0
# The scan gives up after this long and keeps what it has found.
_TIME_LIMIT_SECONDS = 20.0

PLUGIN_SILENT_KEYS_FILE_NAME = "plugin_silent_keys.json"
_STORE_LIMIT = 64

# Plays MIDI messages into the plug-in, renders that many frames, returns the peak of the output.
Render = Callable[[Sequence[bytes], int], float]


def notes_to_scan(octave: int = 0) -> range:
    """The notes the on-screen keys can reach in a sound that is shifted by some octaves."""
    low = max(0, KEYBOARD_LOW + 12 * int(octave))
    high = min(127, KEYBOARD_HIGH + 12 * int(octave))
    return range(low, high + 1)


class KeyScanResult(NamedTuple):
    # The notes that gave no sound.
    silent: frozenset[int]
    # How many notes could not be judged, earlier notes still ringing or time having run out.
    # They are not among the silent ones: they count as playing.
    undecided: int


class _Cancelled(Exception):
    pass


def _blocks(seconds: float) -> int:
    return max(1, int(round(seconds / _BLOCK_SECONDS)))


class _KeyScan:
    """One scan of a plug-in. A key is called silent only when it was judged against a quiet
    enough background and held for long enough; any key in doubt is left undecided."""

    def __init__(self, render: Render, sample_rate: int, cancelled: Callable[[], bool],
                 clock: Callable[[], float]) -> None:
        self._render = render
        self._frames = max(64, int(sample_rate * _BLOCK_SECONDS))
        self._cancelled = cancelled
        self._clock = clock
        self._deadline = clock() + _TIME_LIMIT_SECONDS
        self._before: deque[float] = deque(maxlen=_BLOCKS_BEFORE)
        self._level = 0.0
        self._quietest = 0.0
        self._attack = _ATTACK_SECONDS
        self.heard: set[int] = set()
        # Keys that gave nothing: the level they had to rise above and how long they were held.
        self._unheard: dict[int, tuple[float, float]] = {}
        # The key that is down in the plug-in right now.
        self._down: int | None = None

    def _step(self, events: Sequence[bytes] = ()) -> float:
        if self._cancelled():
            raise _Cancelled()
        self._level = self._render(events, self._frames)
        for event in events:
            self._down = event[1] if event[0] == 0x90 else None
        self._before.append(self._level)
        return self._level

    def let_go(self) -> None:
        """After a scan that was cut short: the key that was down must not sound on."""
        note, self._down = self._down, None
        if note is not None:
            self._render((bytes((0x80, note, 0)),), self._frames)

    def _threshold(self) -> float:
        return max(_MIN_LEVEL, _RISE * max(self._before, default=0.0))

    def _judgeable(self) -> float:
        """A key can be judged while the level a note has to rise above is no higher than this."""
        return max(_MIN_LEVEL, self._quietest * _NOTE_SHARE) if self._quietest else _ASSUMED_NOTE_LEVEL

    def _fade(self, seconds: float) -> bool:
        for _ in range(_blocks(seconds)):
            if self._threshold() <= self._judgeable():
                return True
            self._step()
        return self._threshold() <= self._judgeable()

    def out_of_time(self) -> bool:
        return self._clock() >= self._deadline

    def is_silent(self, note: int) -> bool:
        verdict = self._unheard.get(note)
        return verdict is not None and verdict[0] <= self._judgeable() and verdict[1] >= self._attack

    def decided(self, note: int) -> bool:
        return note in self.heard or self.is_silent(note)

    def judge(self, note: int, fade_seconds: float, *, always: bool = False) -> None:
        """Play one key. With always, also against a loud background, where only hearing the
        note counts and hearing nothing leaves the key undecided."""
        judgeable = self._fade(fade_seconds)
        if not judgeable and not always:
            return
        threshold = self._threshold()
        # Under what is left of earlier notes a new one takes longer to stand out.
        hold = self._attack if threshold <= _MIN_LEVEL else self._attack * 2
        events: Sequence[bytes] = (bytes((0x90, note, _VELOCITY)),)
        onset = 0.0
        for index in range(_blocks(hold)):
            level = self._step(events)
            events = ()
            if level > threshold:
                onset = (index + 1) * _BLOCK_SECONDS
                break
        if onset:
            # A note that starts slowly is held until it stands clear of the background or stops
            # growing, so that its level is known and not just its first trace.
            for _ in range(_blocks(_LONGEST_ATTACK_SECONDS)):
                rising = self._level
                if rising >= threshold * _CLEAR or self._step() <= rising:
                    break
        peak = self._level
        self._step((bytes((0x80, note, 0)),))
        if not onset:
            if judgeable:
                self._unheard[note] = (threshold, hold)
            return
        self.heard.add(note)
        self._unheard.pop(note, None)
        peak = max(peak, self._level)
        for _ in range(_blocks(_RELEASE_SECONDS)):
            if self._level <= max(_MIN_LEVEL, peak * _RELEASE_RATIO):
                break
            peak = max(peak, self._step())
        self._quietest = min(self._quietest, peak) if self._quietest else peak
        if onset * 2 > self._attack:
            # The notes of this plug-in start slowly: keys held for less no longer count as judged.
            self._attack = min(_LONGEST_ATTACK_SECONDS, onset * 4)

    def tap(self, note: int) -> None:
        self._step((bytes((0x90, note, _VELOCITY)),))
        self._step((bytes((0x80, note, 0)),))

    def settle(self) -> None:
        for _ in range(_blocks(_SETTLE_SECONDS)):
            if max(self._before, default=0.0) <= _MIN_LEVEL:
                break
            self._step()


def find_silent_notes(render: Render, notes: Iterable[int], sample_rate: int,
                      cancelled: Callable[[], bool] = lambda: False,
                      clock: Callable[[], float] = time.monotonic) -> KeyScanResult | None:
    """Play each note into a muted plug-in and return those that gave no sound.

    None when it cannot be told: the scan was cancelled, or no note sounded at all, which is
    what a plug-in that is still loading its samples or has no sound selected looks like.
    """
    wanted = sorted({int(note) for note in notes if 0 <= int(note) <= 127})
    scan = _KeyScan(render, sample_rate, cancelled, clock)
    try:
        # What was sounding when the scan began has to fade first.
        scan._step()
        scan._fade(_LONG_FADE_SECONDS)
        # A first note that sounds, looked for from the middle of the keyboard outwards, tells
        # how loud this plug-in's notes are.
        for note in sorted(wanted, key=lambda note: (abs(note - 60), note)):
            if scan.heard or scan.out_of_time():
                break
            scan.judge(note, _FADE_SECONDS, always=True)
        if not scan.heard:
            return None
        # The silent keys are mostly at the two ends of the keyboard: each end is searched
        # inwards up to its first sounding key, before the notes in between pile up their tails.
        for order in (wanted[::-1], wanted):
            fade = _LONG_FADE_SECONDS
            for note in order:
                if note in scan.heard or scan.out_of_time():
                    break
                if not scan.decided(note):
                    scan.judge(note, fade)
                    fade = _FADE_SECONDS
        # The keys in between, then once more those that could not be judged the first time.
        for fade in (_FADE_SECONDS, _LONG_FADE_SECONDS):
            for note in wanted[::-1]:
                if scan.out_of_time():
                    break
                if not scan.decided(note):
                    scan.judge(note, fade)
                    fade = _FADE_SECONDS
        # Keys that switch the playing style of a sampled instrument make no sound and sit at the
        # bottom of the keyboard, the usual style first. Going down them once more leaves the
        # plug-in on the lowest one, whatever the order they were tried in.
        for note in wanted[::-1]:
            if note < min(scan.heard) and scan.is_silent(note):
                scan.tap(note)
        scan.settle()
    except _Cancelled:
        scan.let_go()
        return None
    silent = frozenset(note for note in wanted if scan.is_silent(note))
    return KeyScanResult(silent, len(wanted) - len(silent) - len(scan.heard))


def state_digest(state: bytes) -> str:
    return hashlib.sha256(bytes(state)).hexdigest()


class PluginKeyStore:
    """Remembers the silent keys found for a plug-in, for as long as its settings stay the same.

    A result is filed under the plug-in, the notes that were tried and a digest of the plug-in's
    settings, so it is found again the next time that same sound is loaded and is left behind
    as soon as the sound is changed.
    """

    def __init__(self, path: Path | None) -> None:
        self._path = Path(path) if path is not None else None
        self._entries: dict[str, list[int]] | None = None

    @staticmethod
    def _key(instrument_id: str, state: bytes, notes: Sequence[int]) -> str:
        return f"{instrument_id}|{min(notes)}-{max(notes)}|{state_digest(state)}"

    def _load(self) -> dict[str, list[int]]:
        if self._entries is None:
            entries: dict[str, list[int]] = {}
            try:
                data = json.loads(self._path.read_text(encoding="utf-8")) if self._path is not None else {}
            except (OSError, ValueError):
                data = {}
            stored = data.get("silent_keys") if isinstance(data, dict) else None
            for key, notes in (stored.items() if isinstance(stored, dict) else ()):
                if isinstance(key, str) and isinstance(notes, list):
                    entries[key] = sorted({note for note in notes if type(note) is int and 0 <= note <= 127})
            self._entries = entries
        return self._entries

    def get(self, instrument_id: str, state: bytes | None, notes: Sequence[int]) -> frozenset[int] | None:
        """The silent keys found when these notes were tried with these settings, if known."""
        if not state or not notes:
            return None
        found = self._load().get(self._key(instrument_id, state, notes))
        return frozenset(found) if found is not None else None

    def put(self, instrument_id: str, state: bytes | None, notes: Sequence[int], silent: Iterable[int]) -> None:
        if not state or not notes:
            return
        entries = self._load()
        key = self._key(instrument_id, state, notes)
        entries.pop(key, None)
        entries[key] = sorted(int(note) for note in silent)
        while len(entries) > _STORE_LIMIT:
            entries.pop(next(iter(entries)))
        if self._path is None:
            return
        temporary = self._path.with_suffix(".tmp")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps({"silent_keys": entries}), encoding="utf-8")
            os.replace(temporary, self._path)
        except OSError:
            # A result that cannot be written is only not remembered: the keys are looked for again.
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
