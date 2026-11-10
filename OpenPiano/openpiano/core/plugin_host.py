from __future__ import annotations

import ctypes
import hashlib
import json
import os
import threading
import time
from collections import deque
from pathlib import Path
from typing import Callable

from openpiano.core.audio_levels import normalize_volume
from openpiano.core.audio_output import default_output_driver, list_output_drivers
from openpiano.core.audio_engine import AUDIO_SAMPLE_RATE
from openpiano.core.normalize import clamp_int
from openpiano.core.plugin_probe import plugin_binary_path
from openpiano.core.plugin_registry import PluginInfo

PLUGIN_BLOCK_SIZE = 2048
PLUGIN_STATE_DIR_NAME = "plugin_states"
PLUGIN_LOAD_GUARD_FILE_NAME = "plugin_loading.json"
_FLOAT_SIZE = ctypes.sizeof(ctypes.c_float)
_MIDI_QUEUE_LIMIT = 4096
_EDITOR_CLOSE_TIMEOUT = 10.0
_RELEASE_TIMEOUT = 20.0

# Plug-ins must be destroyed on the thread that created them. One that cannot be handed back
# to that thread is parked here for the rest of the process instead of being torn down elsewhere.
_orphaned_plugins: list[object] = []


class DirectInvoker:
    """Runs plug-in work on the calling thread. OpenPiano itself uses MainThreadInvoker."""

    def is_main_thread(self) -> bool:
        return True

    def call(self, function: Callable[[], object], *, cancel=None, timeout: float | None = None):
        return function()


class PluginStateStore:

    def __init__(self, folder: Path) -> None:
        self._folder = Path(folder)

    def path_for(self, slot: str, instrument_id: str) -> Path:
        digest = hashlib.sha1(str(instrument_id).encode("utf-8")).hexdigest()[:20]
        safe_slot = "".join(char for char in str(slot) if char.isalnum()) or "main"
        return self._folder / f"{safe_slot}-{digest}.bin"

    def load(self, slot: str, instrument_id: str) -> bytes | None:
        try:
            data = self.path_for(slot, instrument_id).read_bytes()
        except OSError:
            return None
        return data or None

    def save(self, slot: str, instrument_id: str, data: bytes) -> None:
        if not data:
            return
        target = self.path_for(slot, instrument_id)
        temporary = target.with_suffix(".tmp")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_bytes(bytes(data))
            os.replace(temporary, target)
        except OSError:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def clear(self, slot: str, instrument_id: str) -> None:
        try:
            self.path_for(slot, instrument_id).unlink(missing_ok=True)
        except OSError:
            pass

    def clear_slot(self, slot: str) -> None:
        """Forget what every plug-in saved under this slot."""
        safe_slot = "".join(char for char in str(slot) if char.isalnum())
        if not safe_slot:
            return
        try:
            for path in self._folder.glob(f"{safe_slot}-*.bin"):
                path.unlink(missing_ok=True)
        except OSError:
            pass


class PluginLoadGuard:
    """Notes which plug-ins are being loaded. An entry that survives into the next start means
    OpenPiano died while loading that plug-in, so it should not be loaded automatically again."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()
        self._loading: list[str] = []

    def _write(self) -> None:
        try:
            if self._loading:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                self._path.write_text(json.dumps(self._loading), encoding="utf-8")
            else:
                self._path.unlink(missing_ok=True)
        except OSError:
            pass

    def begin(self, instrument_id: str) -> None:
        with self._lock:
            self._loading.append(str(instrument_id))
            self._write()

    def end(self, instrument_id: str) -> None:
        with self._lock:
            try:
                self._loading.remove(str(instrument_id))
            except ValueError:
                return
            self._write()

    def take_interrupted(self) -> set[str]:
        with self._lock:
            try:
                payload = json.loads(self._path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                payload = []
            try:
                self._path.unlink(missing_ok=True)
            except OSError:
                pass
            if not isinstance(payload, list):
                return set()
            return {item for item in payload if isinstance(item, str) and item}


class _EditorCloseSignal:
    """What the plug-in window polls to find out whether it should close."""

    def __init__(self, on_poll: Callable[[], None] | None = None) -> None:
        self._event = threading.Event()
        self._on_poll = on_poll

    def set(self) -> None:
        self._event.set()

    def is_set(self) -> bool:
        if self._on_poll is not None and not self._event.is_set():
            try:
                self._on_poll()
            except Exception:
                pass
        return self._event.is_set()


def _load_pedalboard_plugin(info: PluginInfo):
    import pedalboard

    if not os.path.exists(info.path):
        raise FileNotFoundError(f"Plug-in not found: {info.path}")
    # No warm-up: it would block the interface while it plays a test note, and the app's own
    # event loop already gives the plug-in the idle time the warm-up exists to provide.
    return pedalboard.VST3Plugin(plugin_binary_path(info.path), plugin_name=info.name or None,
                                 initialization_timeout=0.0)


def _default_output(render, sample_rate: int):
    from openpiano.core.fluidsynth_loader import configure_dll_search_paths, ensure_fluidsynth_loaded
    from openpiano.core.plugin_output import CallbackOutput

    configure_dll_search_paths()
    module, _error = ensure_fluidsynth_loaded()
    return CallbackOutput(render, sample_rate, fluidsynth_module=module)


class PluginAudioEngine:
    """Plays one plug-in instrument behind the same interface as the SoundFont engine.

    The plug-in is created, edited and released on the application's main thread; notes are
    queued from there and rendered in blocks on the audio thread."""

    accepts_stop_event = True
    is_plugin = True

    def __init__(
        self,
        plugin: PluginInfo,
        *,
        invoker=None,
        slot: str = "main",
        state_store: PluginStateStore | None = None,
        load_guard: PluginLoadGuard | None = None,
        initial_state: bytes | None = None,
        sample_rate: int = AUDIO_SAMPLE_RATE,
        output_factory: Callable[[Callable[[int, int, int], None], int], object] | None = None,
        loader: Callable[[PluginInfo], object] | None = None,
    ) -> None:
        self.plugin_info = plugin
        self.sample_rate = int(sample_rate)
        self.master_volume = 1.0
        self._gain = 1.0
        self._invoker = invoker or DirectInvoker()
        self._slot = str(slot)
        self._state_store = state_store
        # Settings to start from instead of the ones last saved for this slot: a sound preset.
        self._initial_state = bytes(initial_state) if initial_state else None
        # Held while the plug-in's settings are replaced; the audio thread plays silence then.
        self._state_lock = threading.Lock()
        self._load_guard = load_guard
        self._output_factory = output_factory or _default_output
        self._loader = loader or _load_pedalboard_plugin
        self._plugin = None
        self._channels = 2
        self._midi: deque[bytes] = deque(maxlen=_MIDI_QUEUE_LIMIT)
        self._active_notes: set[int] = set()
        self._render_failures = 0
        self._output = None
        self._output_driver = default_output_driver()
        self._closed = False
        self._editor_signal: _EditorCloseSignal | None = None
        self._editor_active = False
        self._release_pending = False

    @property
    def plugin_name(self) -> str:
        return self.plugin_info.display_name

    @property
    def editor_open(self) -> bool:
        return self._editor_active

    def set_master_volume(self, volume: float) -> None:
        self.master_volume = normalize_volume(volume)
        self._gain = float(self.master_volume)

    def set_instrument(self, soundfont_path: str = "", bank: int = 0, preset: int = 0, *, stop_event=None) -> None:
        if self._closed:
            raise RuntimeError("Audio engine is closed.")
        if self._plugin is not None:
            return
        self._invoker.call(self._load_on_main, cancel=stop_event)
        if stop_event is not None and stop_event.is_set():
            raise InterruptedError("Plug-in loading cancelled.")

    def _load_on_main(self) -> None:
        if self._closed or self._plugin is not None:
            return
        info = self.plugin_info
        guard = self._load_guard
        if guard is not None:
            guard.begin(info.instrument_id)
        try:
            plugin = self._loader(info)
            if getattr(plugin, "is_instrument", True) is False:
                raise RuntimeError(f"{info.name} is an effect plug-in and cannot be played as an instrument.")
            self._restore_state(plugin)
            channels = self._prepare(plugin)
        finally:
            if guard is not None:
                guard.end(info.instrument_id)
        self._channels = channels
        self._plugin = plugin

    def _render_plugin(self, plugin, events, frames: int, channels: int):
        return plugin(
            events,
            duration=(frames + 0.5) / self.sample_rate,
            sample_rate=self.sample_rate,
            num_channels=channels,
            buffer_size=PLUGIN_BLOCK_SIZE,
            reset=False,
        )

    def _prepare(self, plugin) -> int:
        # The first block makes the plug-in allocate for this sample rate and block size, which
        # is better done here than on the audio thread. It also finds the output channel count.
        first_error: Exception | None = None
        for channels in (2, 1):
            try:
                self._render_plugin(plugin, [], 64, channels)
                return channels
            except ValueError as error:
                first_error = first_error or error
        raise RuntimeError(f"{self.plugin_info.name} has no mono or stereo output: {first_error}")

    def _restore_state(self, plugin) -> None:
        initial, self._initial_state = self._initial_state, None
        if initial is not None:
            try:
                plugin.raw_state = initial
                return
            except Exception:
                pass
        store = self._state_store
        if store is None:
            return
        data = store.load(self._slot, self.plugin_info.instrument_id)
        if data is None:
            return
        try:
            plugin.raw_state = data
        except Exception:
            store.clear(self._slot, self.plugin_info.instrument_id)

    def _save_state(self, plugin) -> None:
        store = self._state_store
        if store is None or plugin is None:
            return
        try:
            data = bytes(plugin.raw_state)
        except Exception:
            return
        store.save(self._slot, self.plugin_info.instrument_id, data)

    def save_state(self) -> None:
        if self._plugin is not None and not self._closed:
            self._invoker.call(lambda: self._save_state(self._plugin), timeout=_RELEASE_TIMEOUT)

    def capture_state(self) -> bytes | None:
        """Main thread: the plug-in's settings as they are now, for a sound preset."""
        plugin = self._plugin
        if plugin is None or self._closed or not self._invoker.is_main_thread():
            return None
        try:
            return bytes(plugin.raw_state) or None
        except Exception:
            return None

    def load_state(self, data: bytes) -> bool:
        """Main thread: give the loaded plug-in other settings, those of a sound preset.
        False when the plug-in refused them."""
        plugin = self._plugin
        if plugin is None or self._closed or not data or not self._invoker.is_main_thread():
            return False
        self.all_notes_off()
        try:
            with self._state_lock:
                plugin.raw_state = bytes(data)
        except Exception:
            return False
        return True

    def select_program(self, bank: int = 0, preset: int = 0) -> None:
        return

    def get_available_programs(self) -> dict[int, list[int]]:
        return {}

    def get_available_program_names(self) -> dict[int, dict[int, str]]:
        return {}

    def get_current_program(self) -> tuple[int, int]:
        return 0, 0

    def note_on(self, midi_note: int, velocity: int = 100) -> None:
        if self._plugin is None or self._closed:
            return
        note = clamp_int(midi_note, 0, 127, default=0)
        if note in self._active_notes:
            self._midi.append(bytes((0x80, note, 0)))
        self._midi.append(bytes((0x90, note, clamp_int(velocity, 1, 127, default=100))))
        self._active_notes.add(note)

    def note_off(self, midi_note: int) -> None:
        note = clamp_int(midi_note, 0, 127, default=0)
        self._active_notes.discard(note)
        if self._plugin is None or self._closed:
            return
        self._midi.append(bytes((0x80, note, 0)))

    def key_release(self, midi_note: int) -> None:
        return

    def set_pedal(self, value: int) -> None:
        # Sustain is applied by OpenPiano, which holds the note-offs back itself. Forwarding the
        # pedal as well would make the plug-in sustain those notes a second time.
        return

    def all_notes_off(self) -> None:
        notes = sorted(self._active_notes)
        self._active_notes.clear()
        if self._plugin is None or self._closed:
            return
        # Note-offs only, no "all notes off" controller (CC 123): for a plug-in that
        # publishes no MIDI controller mapping, the host library applies every controller
        # message to the parameter with id 0, which silently altered (and saved) the sound.
        for note in notes:
            self._midi.append(bytes((0x80, note, 0)))

    def _drain_midi(self) -> list[tuple[bytes, float]]:
        events: list[tuple[bytes, float]] = []
        queue = self._midi
        while True:
            try:
                events.append((queue.popleft(), 0.0))
            except IndexError:
                return events

    def render_into(self, left: int, right: int, frames: int) -> None:
        """Audio thread: write `frames` float samples to each of the two channel addresses."""
        size = frames * _FLOAT_SIZE
        plugin = self._plugin
        if plugin is None or frames <= 0:
            ctypes.memset(left, 0, max(0, size))
            ctypes.memset(right, 0, max(0, size))
            return
        if not self._state_lock.acquire(blocking=False):
            # The plug-in is being given other settings right now.
            ctypes.memset(left, 0, size)
            ctypes.memset(right, 0, size)
            return
        try:
            audio = self._render_plugin(plugin, self._drain_midi(), frames, self._channels)
            import numpy

            gain = self._gain
            if gain != 1.0:
                numpy.multiply(audio, gain, out=audio)
            if not numpy.isfinite(audio).all():
                numpy.nan_to_num(audio, copy=False, nan=0.0, posinf=0.0, neginf=0.0)
            count = min(frames, int(audio.shape[1]))
            copied = count * _FLOAT_SIZE
            ctypes.memmove(left, audio[0].ctypes.data, copied)
            ctypes.memmove(right, audio[-1].ctypes.data, copied)
            if count < frames:
                ctypes.memset(left + copied, 0, size - copied)
                ctypes.memset(right + copied, 0, size - copied)
        except BaseException:
            self._render_failures += 1
            ctypes.memset(left, 0, size)
            ctypes.memset(right, 0, size)
        finally:
            self._state_lock.release()

    def start_output(self) -> None:
        if self._closed:
            raise RuntimeError("Audio engine is closed.")
        if self._output is None:
            self._output = self._output_factory(self.render_into, self.sample_rate)
        self._output_driver = self._output.start(self._output_driver)

    def stop_output(self) -> None:
        if self._output is not None:
            self._output.stop()

    def restart_output(self) -> None:
        if self._closed:
            raise RuntimeError("Audio engine is closed.")
        self.stop_output()
        self.start_output()

    def failure_count(self) -> int:
        count = self._render_failures
        if self._output is not None:
            count += int(self._output.failure_count())
        return count

    def list_output_drivers(self) -> list[str]:
        return list_output_drivers()

    def get_output_driver(self) -> str:
        return self._output_driver

    def set_output_driver(self, driver: str) -> None:
        target = str(driver).strip().lower()
        if target and target != self._output_driver:
            self._output_driver = target
            if self._output is not None and getattr(self._output, "running", False):
                self.restart_output()

    def open_editor(self, on_poll: Callable[[], None] | None = None) -> bool:
        """Show the plug-in's own window and return once it has been closed. Main thread only;
        the application keeps running from inside this call."""
        if not self._invoker.is_main_thread():
            raise RuntimeError("Plug-in windows can only be opened from the main thread.")
        plugin = self._plugin
        if plugin is None or self._closed:
            raise RuntimeError("The plug-in is not loaded.")
        if self._editor_active:
            return False
        signal = _EditorCloseSignal(on_poll)
        self._editor_signal = signal
        self._editor_active = True
        try:
            plugin.show_editor(signal)
        finally:
            self._editor_signal = None
            self._save_state(plugin)
            self._editor_active = False
            if self._release_pending:
                self._release_pending = False
                self._release_on_main()
        return True

    def close_editor(self) -> None:
        signal = self._editor_signal
        if signal is not None:
            signal.set()

    def _release_on_main(self) -> None:
        plugin, self._plugin = self._plugin, None
        if plugin is None:
            return
        self._save_state(plugin)
        del plugin

    def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._active_notes.clear()
        output, self._output = self._output, None
        try:
            if output is not None:
                output.close()
        except BaseException:
            # The audio thread may still be inside the plug-in, so it is not safe to free it.
            if self._plugin is not None:
                _orphaned_plugins.append(self._plugin)
                self._plugin = None
            raise
        self._midi.clear()
        self.close_editor()
        if self._plugin is None:
            return
        if self._invoker.is_main_thread():
            if self._editor_active:
                # Called from inside the plug-in window's own loop; open_editor finishes the job.
                self._release_pending = True
            else:
                self._release_on_main()
            return
        deadline = time.monotonic() + _EDITOR_CLOSE_TIMEOUT
        while self._editor_active and time.monotonic() < deadline:
            time.sleep(0.01)
        try:
            if self._editor_active:
                raise TimeoutError("The plug-in window did not close.")
            self._invoker.call(self._release_on_main, timeout=_RELEASE_TIMEOUT)
        except Exception:
            if self._plugin is not None:
                _orphaned_plugins.append(self._plugin)
                self._plugin = None
