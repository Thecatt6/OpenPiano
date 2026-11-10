from __future__ import annotations

import threading
import time
from collections import deque
from typing import Callable

from PySide6.QtCore import QObject, Qt, Signal


class _Job:

    def __init__(self, function: Callable[[], object]) -> None:
        self._function = function
        self._lock = threading.Lock()
        self._started = False
        self._cancelled = False
        self.finished = threading.Event()
        self.result: object = None
        self.error: BaseException | None = None

    def cancel(self) -> bool:
        with self._lock:
            if self._started:
                return False
            self._cancelled = True
        self.finished.set()
        return True

    def run(self) -> None:
        with self._lock:
            if self._cancelled:
                return
            self._started = True
        try:
            self.result = self._function()
        except BaseException as error:
            self.error = error
        finally:
            self.finished.set()


class MainThreadInvoker(QObject):
    """Runs work on the thread that owns this object and hands the result back to the caller.

    Work normally runs from the event loop. While that loop is not running, for example
    during shutdown, the owning thread has to call drain() itself."""

    _pending = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._thread_id = threading.get_ident()
        self._lock = threading.Lock()
        self._jobs: deque[_Job] = deque()
        self._draining = False
        self._pending.connect(self.drain, Qt.ConnectionType.QueuedConnection)

    def is_main_thread(self) -> bool:
        return threading.get_ident() == self._thread_id

    def call(self, function: Callable[[], object], *, cancel: threading.Event | None = None,
             timeout: float | None = None):
        if self.is_main_thread():
            return function()
        job = _Job(function)
        with self._lock:
            self._jobs.append(job)
        try:
            self._pending.emit()
        except RuntimeError:
            pass
        deadline = None if timeout is None else time.monotonic() + float(timeout)
        while not job.finished.wait(0.02):
            if cancel is not None and cancel.is_set() and job.cancel():
                raise InterruptedError("Cancelled before the main thread could run the request.")
            if deadline is not None and time.monotonic() >= deadline and job.cancel():
                raise TimeoutError("The main thread did not respond in time.")
        if job.error is not None:
            raise job.error
        return job.result

    def drain(self) -> None:
        # A job may spin the event loop itself; the outer call keeps going afterwards, so a
        # nested call must not start a second job in the middle of the first.
        if not self.is_main_thread() or self._draining:
            return
        self._draining = True
        try:
            while True:
                with self._lock:
                    if not self._jobs:
                        return
                    job = self._jobs.popleft()
                job.run()
        finally:
            self._draining = False
