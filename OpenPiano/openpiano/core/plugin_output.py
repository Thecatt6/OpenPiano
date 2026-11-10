from __future__ import annotations

import ctypes
import sys
import threading
from typing import Callable

from openpiano.core.audio_output import list_output_drivers, output_profiles
from openpiano.core.native_audio import NativePeakLimiter, NativeWasapiDriver, native_audio_library

_FLOAT_SIZE = ctypes.sizeof(ctypes.c_float)
_FLOAT_POINTER = ctypes.POINTER(ctypes.c_float)
_FLOAT_BUFFERS = ctypes.POINTER(_FLOAT_POINTER)
_AUDIO_CALLBACK = ctypes.CFUNCTYPE(
    ctypes.c_int, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
    _FLOAT_BUFFERS, ctypes.c_int, _FLOAT_BUFFERS,
)
# Same shape as fluid_synth_write_float, which is what the native limiter expects to pull from.
_WRITE_FLOAT = ctypes.CFUNCTYPE(
    ctypes.c_int, ctypes.c_void_p, ctypes.c_int,
    ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
    ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
)
_FALLBACK_CEILING = 0.95

RenderInto = Callable[[int, int, int], None]


class CallbackOutput:
    """Plays audio produced by a Python render callback through OpenPiano's own output path:
    the native limiter with event-driven WASAPI where available, otherwise a FluidSynth driver."""

    def __init__(self, render: RenderInto, sample_rate: int, *, fluidsynth_module=None) -> None:
        self._render = render
        self.sample_rate = int(sample_rate)
        self._module = fluidsynth_module
        self._lock = threading.RLock()
        self._failures = 0
        self._token = ctypes.create_string_buffer(16)
        self._write_float = _WRITE_FLOAT(self._on_write_float)
        self._audio_callback = None
        self._limiter = None
        self._device = None
        self._settings = None
        self._driver = None
        self.driver_name = ''
        self.profile: dict[str, object] | None = None

    def _on_write_float(self, _synth, frames, left, left_offset, left_stride,
                        right, right_offset, right_stride) -> int:
        # Runs on the audio thread. Nothing may escape into native code, and a failed block
        # must stay silent instead of replaying whatever the buffer held before.
        try:
            if frames <= 0:
                return 0
            left_address = int(left) + int(left_offset) * _FLOAT_SIZE
            right_address = int(right) + int(right_offset) * _FLOAT_SIZE
            try:
                if left_stride != 1 or right_stride != 1:
                    raise ValueError('Interleaved output is not supported.')
                self._render(left_address, right_address, int(frames))
            except BaseException:
                self._failures += 1
                ctypes.memset(left_address, 0, int(frames) * _FLOAT_SIZE)
                ctypes.memset(right_address, 0, int(frames) * _FLOAT_SIZE)
        except BaseException:
            self._failures += 1
        return 0

    def _on_audio_callback(self, _data, frames, _nfx, _fx, nout, out) -> int:
        try:
            if frames <= 0 or nout < 1:
                return 0
            left = ctypes.cast(out[0], ctypes.c_void_p).value
            right = ctypes.cast(out[1 if nout > 1 else 0], ctypes.c_void_p).value
            self._on_write_float(None, frames, left, 0, 1, right, 0, 1)
            import numpy

            for address in {left, right}:
                block = numpy.ctypeslib.as_array((ctypes.c_float * int(frames)).from_address(address))
                numpy.clip(block, -_FALLBACK_CEILING, _FALLBACK_CEILING, out=block)
        except BaseException:
            self._failures += 1
        return 0

    def _attempts(self, preferred: str) -> list[dict[str, object]]:
        attempts: list[dict[str, object]] = []
        target = str(preferred or '').strip().lower()
        if sys.platform == 'win32':
            available = list_output_drivers()
            ordered = [target] if target in available else []
            ordered.extend(driver for driver in available if driver != target)
            native = native_audio_library() is not None
            for driver in ordered:
                attempts.extend(output_profiles(driver, native_callback=native))
        attempts.append({})
        return attempts

    def start(self, preferred: str = '') -> str:
        with self._lock:
            self.stop()
            last_error: Exception | None = None
            for profile in self._attempts(preferred):
                try:
                    self.driver_name = self._start_profile(profile)
                    self.profile = dict(profile)
                    return self.driver_name
                except Exception as error:
                    last_error = error
                    self.stop()
            raise RuntimeError(f'Failed to start the plug-in audio output: {last_error}')

    def _start_profile(self, profile: dict[str, object]) -> str:
        driver = str(profile.get('driver', '')).strip().lower()
        dll = native_audio_library()
        if dll is not None:
            self._limiter = NativePeakLimiter(dll, ctypes.addressof(self._token), self._write_float,
                                              self.sample_rate)
            if (sys.platform == 'win32' and driver == 'wasapi' and profile.get('sample_format') == 'float'
                    and hasattr(dll, 'op_wasapi_open')):
                self._device = NativeWasapiDriver(self._limiter, self.sample_rate, int(profile.get('periods', 3)))
                return driver
        module = self._module
        if module is None:
            raise RuntimeError('FluidSynth audio drivers are unavailable.')
        settings = module.new_fluid_settings()
        if not settings:
            raise RuntimeError('Could not allocate audio driver settings.')
        self._settings = settings
        module.fluid_settings_setnum(settings, b'synth.sample-rate', ctypes.c_double(float(self.sample_rate)))
        if driver:
            module.fluid_settings_setstr(settings, b'audio.driver', driver.encode())
        if sys.platform == 'win32':
            module.fluid_settings_setint(settings, b'audio.period-size', int(profile.get('period_size', 512)))
            module.fluid_settings_setint(settings, b'audio.periods', int(profile.get('periods', 8)))
            module.fluid_settings_setstr(settings, b'audio.sample-format',
                                         str(profile.get('sample_format', '16bits')).encode())
            if driver == 'wasapi':
                module.fluid_settings_setint(settings, b'audio.wasapi.exclusive-mode', 0)
        create_driver = module.cfunc(
            'new_fluid_audio_driver2', ctypes.c_void_p,
            ('settings', ctypes.c_void_p, 1), ('callback', _AUDIO_CALLBACK, 1),
            ('data', ctypes.c_void_p, 1),
        )
        if self._limiter is not None:
            self._audio_callback = ctypes.cast(dll.op_audio_callback, _AUDIO_CALLBACK)
            data = self._limiter.pointer
        else:
            self._audio_callback = _AUDIO_CALLBACK(self._on_audio_callback)
            data = None
        self._driver = create_driver(settings, self._audio_callback, data)
        if not self._driver:
            raise RuntimeError('Could not start an audio driver for plug-in playback.')
        return driver

    def stop(self) -> None:
        with self._lock:
            # The device thread has to be gone before the limiter it pulls from is freed.
            device, self._device = self._device, None
            if device is not None:
                device.stop()
                device.close()
            driver, self._driver = self._driver, None
            if driver:
                self._module.delete_fluid_audio_driver(driver)
            settings, self._settings = self._settings, None
            if settings:
                self._module.delete_fluid_settings(settings)
            limiter, self._limiter = self._limiter, None
            if limiter is not None:
                limiter.close()
            self._audio_callback = None

    def close(self) -> None:
        self.stop()

    @property
    def running(self) -> bool:
        return self._device is not None or bool(self._driver)

    def failure_count(self) -> int:
        with self._lock:
            count = self._failures
            if self._device is not None:
                health = self._device.statistics()
                error = health['error']
                if error:
                    raise RuntimeError(f'Windows audio output failed (0x{error & 0xffffffff:08X}).')
                if health.get('event_timeouts', 0):
                    raise RuntimeError('Windows audio output stopped receiving timely audio-ready events.')
            if self._limiter is not None:
                count += int(self._limiter.statistics()['failures'])
            return count
