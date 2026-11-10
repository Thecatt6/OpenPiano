from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Mapping, Sequence

from PySide6.QtCore import QObject, Qt, Signal

from openpiano.core.instrument_registry import InstrumentInfo
from openpiano.core.program_selection import normalize_available_programs
from openpiano.core.split import (
    MAX_SPLIT_ZONES,
    SplitZone,
    default_split_zones,
    normalize_split_zones,
    zone_gain,
    zone_index_for_note,
    zone_output_note,
)
from openpiano.services.audio_session import AudioSession

ZONE_OFF = "off"
ZONE_LOADING = "loading"
ZONE_READY = "ready"
ZONE_ERROR = "error"

SessionFactory = Callable[[int], AudioSession]


@dataclass(slots=True)
class ZoneState:

    status: str = ZONE_OFF
    error: str = ""
    instrument_id: str = ""
    bank: int = 0
    preset: int = 0
    programs: dict[int, list[int]] = field(default_factory=dict)
    program_names: dict[int, dict[int, str]] = field(default_factory=dict)
    is_plugin: bool = False


class _ZoneLink(QObject):
    """Receives one zone's audio session signals on the main thread."""

    def __init__(self, manager: "SplitZoneManager", index: int, session: AudioSession) -> None:
        super().__init__(manager)
        self._manager = manager
        self._index = index
        self.session = session
        session.prepared.connect(self._on_prepared, Qt.ConnectionType.QueuedConnection)
        session.activated.connect(self._on_activated, Qt.ConnectionType.QueuedConnection)
        session.failed.connect(self._on_failed, Qt.ConnectionType.QueuedConnection)

    def _on_prepared(self, result) -> None:
        self._manager._on_prepared(self._index, self, result)

    def _on_activated(self, result) -> None:
        self._manager._on_activated(self._index, self, result)

    def _on_failed(self, result) -> None:
        self._manager._on_failed(self._index, self, result)


class _ZoneRuntime:

    def __init__(self) -> None:
        self.link: _ZoneLink | None = None
        self.engine = None
        self.requested_instrument_id = ""
        self.failed_instrument_id = ""
        self.failure_count = 0
        self.state = ZoneState()


class SplitZoneManager(QObject):
    """Owns the extra sounds of a keyboard split: one audio engine per enabled zone, and the
    routing that sends each note to the zone it falls in."""

    zoneStateChanged = Signal(int)
    zoneFailed = Signal(int, str)

    def __init__(self, session_factory: SessionFactory, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._session_factory = session_factory
        self._zones: tuple[SplitZone, ...] = default_split_zones()
        self._master_volume = 1.0
        self._pedal: tuple[int, bool] | None = None
        self._runtimes = [_ZoneRuntime() for _ in range(MAX_SPLIT_ZONES)]
        self._routes: dict[int, tuple[int, object, int | None]] = {}
        self._closing: list[AudioSession] = []
        self._closed = False

    @property
    def zones(self) -> tuple[SplitZone, ...]:
        return self._zones

    @property
    def active(self) -> bool:
        return any(zone.enabled for zone in self._zones)

    def state(self, index: int) -> ZoneState:
        return self._runtimes[index].state

    def engine(self, index: int):
        return self._runtimes[index].engine

    def engines(self) -> list[object]:
        return [runtime.engine for runtime in self._runtimes if runtime.engine is not None]

    def _call(self, index: int, action: Callable[[], None]) -> None:
        try:
            action()
        except Exception as error:
            self._fail(index, str(error))

    def _fail(self, index: int, message: str) -> None:
        runtime = self._runtimes[index]
        runtime.failed_instrument_id = runtime.requested_instrument_id
        self._stop_runtime(index)
        runtime.state = ZoneState(status=ZONE_ERROR, error=message or "Unknown error",
                                  instrument_id=runtime.failed_instrument_id)
        self.zoneStateChanged.emit(index)
        self.zoneFailed.emit(index, runtime.state.error)

    def _release_routes(self, index: int) -> None:
        engine = self._runtimes[index].engine
        for note, (zone_index, route_engine, output_note) in list(self._routes.items()):
            if zone_index != index:
                continue
            # The key is still held: keep the route, emptied, so that its release is not
            # passed on to the main sound, which never played this note.
            self._routes[note] = (index, None, None)
            if output_note is not None and route_engine is not None and route_engine is engine:
                try:
                    route_engine.note_off(output_note)
                except Exception:
                    pass

    def _stop_runtime(self, index: int) -> None:
        runtime = self._runtimes[index]
        self._release_routes(index)
        runtime.engine = None
        runtime.requested_instrument_id = ""
        link, runtime.link = runtime.link, None
        if link is not None:
            link.session.close()
            self._closing.append(link.session)
            link.deleteLater()
        self._prune_closing()

    def _prune_closing(self) -> None:
        remaining: list[AudioSession] = []
        for session in self._closing:
            if session.wait_closed(0):
                session.deleteLater()
            else:
                remaining.append(session)
        self._closing = remaining

    def _apply_pedal(self, engine, *, emit_contacts: bool) -> None:
        setter = getattr(engine, "set_pedal", None)
        if self._pedal is None or not callable(setter):
            return
        value, recapture_owned = self._pedal
        if getattr(engine, "piano_recapture_enabled", False):
            setter(value, recapture_owned=recapture_owned, emit_contacts=emit_contacts)
        else:
            setter(value)

    def configure(self, zones: Sequence[SplitZone], instruments: Mapping[str, InstrumentInfo],
                  master_volume: float) -> None:
        if self._closed:
            return
        previous = self._zones
        self._zones = normalize_split_zones(list(zones))
        self._master_volume = float(master_volume)
        for index, zone in enumerate(self._zones):
            runtime = self._runtimes[index]
            before = previous[index]
            if (zone.low, zone.high, zone.octave, zone.enabled) != (
                    before.low, before.high, before.octave, before.enabled):
                self._release_routes(index)
            if not zone.enabled:
                runtime.failed_instrument_id = ""
                if runtime.link is not None or runtime.state.status != ZONE_OFF:
                    self._stop_runtime(index)
                    runtime.state = ZoneState()
                    self.zoneStateChanged.emit(index)
                continue
            instrument = instruments.get(zone.instrument_id)
            if instrument is None:
                if runtime.link is not None or runtime.state.status != ZONE_ERROR:
                    self._stop_runtime(index)
                    message = ("Choose a sound for this zone." if not zone.instrument_id
                               else "This zone's instrument is no longer available.")
                    runtime.state = ZoneState(status=ZONE_ERROR, error=message, instrument_id=zone.instrument_id)
                    self.zoneStateChanged.emit(index)
                continue
            if runtime.requested_instrument_id != instrument.id:
                if runtime.failed_instrument_id == instrument.id:
                    # Already failed with this sound; turning the zone off and on retries it.
                    continue
                self._request(index, zone, instrument)
                continue
            if runtime.engine is not None and runtime.state.status == ZONE_READY:
                if (zone.bank, zone.preset) != (runtime.state.bank, runtime.state.preset):
                    self._select_program(index, zone.bank, zone.preset)
            if runtime.engine is not None:
                self._call(index, lambda engine=runtime.engine, gain=zone_gain(self._master_volume, zone):
                           engine.set_master_volume(gain))

    def _request(self, index: int, zone: SplitZone, instrument: InstrumentInfo) -> None:
        runtime = self._runtimes[index]
        runtime.failed_instrument_id = ""
        if runtime.link is None:
            session = self._session_factory(index)
            runtime.link = _ZoneLink(self, index, session)
        request = runtime.link.session.request(
            instrument.id, str(instrument.path), zone.bank, zone.preset,
            zone_gain(self._master_volume, zone))
        if request is None:
            self._fail(index, "The audio engine for this zone is no longer available.")
            return
        runtime.requested_instrument_id = instrument.id
        runtime.state = ZoneState(status=ZONE_LOADING, instrument_id=instrument.id)
        self.zoneStateChanged.emit(index)

    def retry(self, index: int, instruments: Mapping[str, InstrumentInfo]) -> None:
        zone = self._zones[index]
        instrument = instruments.get(zone.instrument_id)
        if self._closed or not zone.enabled or instrument is None:
            return
        self._request(index, zone, instrument)

    def _select_program(self, index: int, bank: int, preset: int) -> None:
        runtime = self._runtimes[index]
        engine = runtime.engine
        if engine is None:
            return
        self._release_routes(index)
        try:
            selector = getattr(engine, "select_program", None)
            if callable(selector):
                selector(bank=bank, preset=preset)
            runtime.state.bank, runtime.state.preset = (int(value) for value in engine.get_current_program())
        except Exception as error:
            self._fail(index, str(error))
            return
        self.zoneStateChanged.emit(index)

    def _on_prepared(self, index: int, link: _ZoneLink, result) -> None:
        runtime = self._runtimes[index]
        if self._closed or runtime.link is not link or not link.session.is_latest(result.request):
            return
        # The session retires the previous engine as soon as the new one is accepted.
        self._release_routes(index)
        runtime.engine = None
        link.session.accept(result)

    def _on_activated(self, index: int, link: _ZoneLink, result) -> None:
        runtime = self._runtimes[index]
        try:
            if self._closed or runtime.link is not link:
                return
            engine = result.engine
            zone = self._zones[index]
            try:
                engine.set_master_volume(zone_gain(self._master_volume, zone))
                self._apply_pedal(engine, emit_contacts=False)
                bank, preset = engine.get_current_program()
                programs = normalize_available_programs(engine.get_available_programs())
                names_getter = getattr(engine, "get_available_program_names", None)
                names = names_getter() if callable(names_getter) else {}
                failure_count = getattr(engine, "failure_count", lambda: 0)()
            except Exception as error:
                self._fail(index, str(error))
                return
            runtime.engine = engine
            runtime.failure_count = failure_count
            runtime.state = ZoneState(
                status=ZONE_READY if link.session.is_latest(result.request) else ZONE_LOADING,
                instrument_id=result.request.instrument_id,
                bank=int(bank),
                preset=int(preset),
                programs=programs,
                program_names={int(key): dict(value) for key, value in names.items()} if isinstance(names, dict) else {},
                is_plugin=bool(getattr(engine, "is_plugin", False)),
            )
        finally:
            link.session.acknowledge()
        self.zoneStateChanged.emit(index)

    def _on_failed(self, index: int, link: _ZoneLink, result) -> None:
        runtime = self._runtimes[index]
        if self._closed or runtime.link is not link:
            return
        if not result.terminal and not link.session.is_latest(result.request):
            return
        self._fail(index, str(result.error))

    def set_master_volume(self, volume: float) -> None:
        self._master_volume = float(volume)
        for index, runtime in enumerate(self._runtimes):
            if runtime.engine is not None:
                self._call(index, lambda engine=runtime.engine, gain=zone_gain(self._master_volume, self._zones[index]):
                           engine.set_master_volume(gain))

    def set_pedal(self, value: int, *, recapture_owned: bool = True, emit_contacts: bool = True) -> None:
        self._pedal = (int(value), bool(recapture_owned))
        for index, runtime in enumerate(self._runtimes):
            if runtime.engine is not None:
                self._call(index, lambda engine=runtime.engine: self._apply_pedal(engine, emit_contacts=emit_contacts))

    def note_on(self, note: int, velocity: int) -> bool:
        """Play the note in the zone it belongs to. False means it belongs to the main sound."""
        index = zone_index_for_note(self._zones, note)
        if index is None:
            return False
        runtime = self._runtimes[index]
        engine = runtime.engine if runtime.state.status == ZONE_READY else None
        output_note = zone_output_note(self._zones[index], note)
        previous = self._routes.get(int(note))
        if previous is not None and previous[0] != index:
            self._release_note(int(note))
        self._routes[int(note)] = (index, engine, output_note)
        if engine is not None and output_note is not None:
            self._call(index, lambda: engine.note_on(output_note, velocity=velocity))
        return True

    def _release_note(self, note: int) -> bool:
        route = self._routes.pop(note, None)
        if route is None:
            return False
        index, engine, output_note = route
        if engine is not None and output_note is not None and engine is self._runtimes[index].engine:
            self._call(index, lambda: engine.note_off(output_note))
        return True

    def note_off(self, note: int) -> bool:
        return self._release_note(int(note))

    def key_release(self, note: int) -> bool:
        route = self._routes.get(int(note))
        if route is None:
            return False
        index, engine, output_note = route
        release = getattr(engine, "key_release", None)
        if callable(release) and output_note is not None and engine is self._runtimes[index].engine:
            self._call(index, lambda: release(output_note))
        return True

    def all_notes_off(self) -> None:
        self._routes.clear()
        for index, runtime in enumerate(self._runtimes):
            if runtime.engine is not None:
                self._call(index, runtime.engine.all_notes_off)

    def poll_health(self) -> None:
        for index, runtime in enumerate(self._runtimes):
            engine = runtime.engine
            if engine is None:
                continue
            try:
                count = getattr(engine, "failure_count", lambda: 0)()
            except Exception as error:
                self._fail(index, str(error))
                continue
            if count > runtime.failure_count:
                self._fail(index, "The audio renderer reported a failed block.")
            else:
                runtime.failure_count = count

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._routes.clear()
        for index in range(MAX_SPLIT_ZONES):
            self._stop_runtime(index)

    def wait_closed(self, timeout: float | None = None, pump: Callable[[], None] | None = None) -> bool:
        deadline = None if timeout is None else time.monotonic() + float(timeout)
        while self._closing:
            if pump is not None:
                pump()
            self._closing = [session for session in self._closing if not session.wait_closed(0.01)]
            if self._closing and deadline is not None and time.monotonic() >= deadline:
                return False
        return True

    @property
    def closing(self) -> bool:
        self._prune_closing()
        return bool(self._closing)
