from __future__ import annotations

import os
import platform
import sys
import threading
from importlib import metadata
from pathlib import Path
from typing import Callable, Iterable

from PySide6.QtCore import QObject, Qt, Signal

from openpiano.core.instrument_registry import InstrumentInfo
from openpiano.core.plugin_host import (
    PLUGIN_LOAD_GUARD_FILE_NAME,
    PLUGIN_STATE_DIR_NAME,
    PluginLoadGuard,
    PluginStateStore,
)
from openpiano.core.plugin_registry import (
    PLUGIN_CACHE_FILE_NAME,
    PluginInfo,
    PluginScanResult,
    cached_scan_result,
    default_plugin_dirs,
    plugin_data_dir,
    plugin_support_available,
    plugins_to_instruments,
    scan_plugins,
)

PLUGIN_SUPPORT_HINT = "VST3 plug-ins need the optional 'pedalboard' package (pip install pedalboard)."


class PluginLibrary(QObject):
    """The plug-ins OpenPiano knows about, kept current by a background scan."""

    changed = Signal()
    statusChanged = Signal()
    _scanProgressReady = Signal(int, int, str)
    _scanFinishedReady = Signal(object)

    def __init__(
        self,
        *,
        data_dir: Path | None = None,
        folders: Iterable[str] = (),
        available: bool | None = None,
        scanner: Callable[..., PluginScanResult] = scan_plugins,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.data_dir = Path(data_dir) if data_dir is not None else plugin_data_dir()
        self.cache_path = self.data_dir / PLUGIN_CACHE_FILE_NAME
        self.state_store = PluginStateStore(self.data_dir / PLUGIN_STATE_DIR_NAME)
        self.load_guard = PluginLoadGuard(self.data_dir / PLUGIN_LOAD_GUARD_FILE_NAME)
        self.available = plugin_support_available() if available is None else bool(available)
        self._scanner = scanner
        self._folders = self._clean_folders(folders)
        self._result = PluginScanResult()
        self._by_instrument_id: dict[str, PluginInfo] = {}
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._rescan_pending: bool | None = None
        self._progress: tuple[int, int, str] | None = None
        self._closed = False
        self._scanProgressReady.connect(self._on_scan_progress, Qt.ConnectionType.QueuedConnection)
        self._scanFinishedReady.connect(self._on_scan_finished, Qt.ConnectionType.QueuedConnection)
        if self.available:
            try:
                self._apply_result(cached_scan_result(self.cache_path, self._folders))
            except Exception:
                self._apply_result(PluginScanResult())

    @staticmethod
    def _clean_folders(folders: Iterable[str]) -> tuple[str, ...]:
        seen: set[str] = set()
        cleaned: list[str] = []
        for folder in folders:
            text = str(folder or "").strip()
            key = os.path.normcase(os.path.abspath(text)) if text else ""
            if key and key not in seen:
                seen.add(key)
                cleaned.append(text)
        return tuple(cleaned)

    def _apply_result(self, result: PluginScanResult) -> bool:
        previous = set(self._by_instrument_id)
        self._result = result
        # Replaced as a whole: audio preparation threads look plug-ins up while this runs.
        self._by_instrument_id = {plugin.instrument_id: plugin for plugin in result.instruments}
        return previous != set(self._by_instrument_id)

    @property
    def folders(self) -> tuple[str, ...]:
        return self._folders

    @property
    def default_folders(self) -> tuple[str, ...]:
        return tuple(str(folder) for folder in default_plugin_dirs())

    @property
    def scanning(self) -> bool:
        return self._thread is not None

    def set_folders(self, folders: Iterable[str]) -> bool:
        cleaned = self._clean_folders(folders)
        if cleaned == self._folders:
            return False
        self._folders = cleaned
        return True

    def instruments(self) -> list[InstrumentInfo]:
        return plugins_to_instruments(self._result.instruments)

    def plugin_for(self, instrument_id: str) -> PluginInfo | None:
        return self._by_instrument_id.get(str(instrument_id))

    def status_text(self) -> str:
        if not self.available:
            return PLUGIN_SUPPORT_HINT
        if self._progress is not None:
            position, total, path = self._progress
            name = Path(path).stem if path else ""
            return f"Scanning plug-ins… {position}/{total}" + (f" ({name})" if name else "")
        if self.scanning:
            return "Looking for plug-ins…"
        instruments = len(self._result.instruments)
        files = len(self._result.files)
        failed = len(self._result.failures)
        if not files:
            return "No VST3 plug-ins found."
        text = f"{instruments} instrument{'' if instruments == 1 else 's'} in {files} VST3 file{'' if files == 1 else 's'}"
        if failed:
            text += f", {failed} could not be loaded"
        return text + "."

    def details_text(self) -> str:
        """Everything a person needs to see why a plug-in is, or is not, in the list."""
        lines = ["Folders searched:"]
        lines.extend(f"  {folder}" for folder in (self._result.folders or self.default_folders + self._folders))
        instruments = self._result.instruments
        if instruments:
            lines.append("")
            lines.append("Instruments:")
            lines.extend(f"  {plugin.display_name}  ({Path(plugin.path).name})" for plugin in instruments[:60])
            if len(instruments) > 60:
                lines.append(f"  … and {len(instruments) - 60} more")
        effects = [plugin for plugin in self._result.plugins if not plugin.is_instrument]
        if effects:
            lines.append("")
            lines.append(f"{len(effects)} effect plug-in{'' if len(effects) == 1 else 's'} found. "
                         "Only instruments can be played.")
        failures = self._result.failures
        if failures:
            lines.append("")
            lines.append("Could not be loaded:")
            for item in failures[:40]:
                lines.append(f"  {Path(item.path).name}: {item.error}")
                lines.append(f"      {item.path}")
            if len(failures) > 40:
                lines.append(f"  … and {len(failures) - 40} more")
        lines.append("")
        lines.append(self.host_text())
        return "\n".join(lines)

    def host_text(self) -> str:
        if not self.available:
            return PLUGIN_SUPPORT_HINT
        try:
            host = f"pedalboard {metadata.version('pedalboard')}"
        except Exception:
            host = "pedalboard"
        bits = "64-bit" if sys.maxsize > 2 ** 32 else "32-bit"
        return (f"Plug-in host: {host}, Python {platform.python_version()} ({bits}), "
                f"{platform.system()} {platform.machine()}")

    def rescan(self, force: bool = False) -> bool:
        if not self.available or self._closed:
            return False
        if self._thread is not None:
            self._rescan_pending = bool(force) or bool(self._rescan_pending)
            return True
        self._stop_event = threading.Event()
        self._thread = threading.Thread(
            target=self._scan_worker,
            args=(self._folders, bool(force), self._stop_event),
            name="OpenPiano plug-in scan",
            daemon=False,
        )
        self._thread.start()
        self.statusChanged.emit()
        return True

    def _scan_worker(self, folders: tuple[str, ...], force: bool, stop_event: threading.Event) -> None:
        def progress(position: int, total: int, path: str) -> None:
            try:
                self._scanProgressReady.emit(int(position), int(total), str(path))
            except RuntimeError:
                pass

        result: PluginScanResult | None = None
        try:
            result = self._scanner(self.cache_path, folders, force=force, stop_event=stop_event,
                                   on_progress=progress)
        except Exception:
            result = None
        if stop_event.is_set():
            result = None
        try:
            self._scanFinishedReady.emit(result)
        except RuntimeError:
            pass

    def _on_scan_progress(self, position: int, total: int, path: str) -> None:
        if self._thread is None or self._closed:
            return
        self._progress = (position, total, path)
        self.statusChanged.emit()

    def _on_scan_finished(self, result: object) -> None:
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join()
        self._progress = None
        if self._closed:
            return
        changed = isinstance(result, PluginScanResult) and self._apply_result(result)
        pending, self._rescan_pending = self._rescan_pending, None
        if changed:
            self.changed.emit()
        if pending is not None:
            self.rescan(force=pending)
        else:
            self.statusChanged.emit()

    def close(self) -> None:
        self._closed = True
        self._rescan_pending = None
        self._stop_event.set()

    def wait_closed(self, timeout: float | None = None) -> bool:
        thread = self._thread
        if thread is None:
            return True
        thread.join(timeout)
        if thread.is_alive():
            return False
        self._thread = None
        return True
