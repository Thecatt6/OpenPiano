from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence

from openpiano.core.config import APP_NAME
from openpiano.core.instrument_registry import InstrumentInfo
from openpiano.core.runtime_paths import app_local_data_dir, project_root

PLUGIN_SOURCE = "plugin"
PLUGIN_ID_PREFIX = "plugin:"
PLUGIN_EXTENSIONS = (".vst3",)
# Raised whenever results an older version cached could be wrong, so that they are redone.
PLUGIN_CACHE_VERSION = 2
PLUGIN_CACHE_FILE_NAME = "plugin_cache.json"
PLUGIN_PROBE_FLAG = "--openpiano-probe-plugins"
PLUGIN_SCAN_DEPTH = 4
# A probe that reports nothing for this long is treated as hung on the plug-in it started last.
PLUGIN_PROBE_STALL_SECONDS = 30.0

_ID_SEPARATOR = "::"


@dataclass(frozen=True, slots=True)
class PluginInfo:

    path: str
    name: str
    manufacturer: str = ""
    category: str = ""
    version: str = ""
    is_instrument: bool = False
    is_effect: bool = False

    @property
    def instrument_id(self) -> str:
        return plugin_instrument_id(self.path, self.name)

    @property
    def display_name(self) -> str:
        maker = " ".join(self.manufacturer.split())
        if maker and maker.casefold() not in self.name.casefold():
            return f"{self.name} ({maker})"
        return self.name


@dataclass(frozen=True, slots=True)
class PluginFileResult:

    path: str
    signature: tuple[int, int]
    plugins: tuple[PluginInfo, ...] = ()
    error: str = ""
    # True when the failure says nothing about the plug-in itself (the scanner could not
    # run). Such a result is shown but not remembered, so the next scan tries again.
    retry: bool = False


@dataclass(frozen=True, slots=True)
class PluginScanResult:

    files: tuple[PluginFileResult, ...] = ()
    folders: tuple[str, ...] = ()

    @property
    def plugins(self) -> tuple[PluginInfo, ...]:
        return tuple(plugin for item in self.files for plugin in item.plugins)

    @property
    def instruments(self) -> tuple[PluginInfo, ...]:
        return tuple(plugin for plugin in self.plugins if plugin.is_instrument)

    @property
    def failures(self) -> tuple[PluginFileResult, ...]:
        return tuple(item for item in self.files if item.error)


def plugin_support_available() -> bool:
    try:
        return importlib.util.find_spec("pedalboard") is not None
    except (ImportError, ValueError):
        return False


def plugin_instrument_id(path: str | Path, name: str) -> str:
    return f"{PLUGIN_ID_PREFIX}{path}{_ID_SEPARATOR}{name}"


def is_plugin_instrument_id(instrument_id: object) -> bool:
    return isinstance(instrument_id, str) and instrument_id.startswith(PLUGIN_ID_PREFIX)


def plugin_data_dir() -> Path:
    local_data_dir = app_local_data_dir(APP_NAME)
    if local_data_dir is not None:
        return local_data_dir
    return project_root() / "user_plugins"


def user_plugins_dir() -> Path:
    return plugin_data_dir() / "plugins"


def default_plugin_dirs() -> list[Path]:
    home = Path.home()
    folders: list[Path] = []
    if sys.platform == "win32":
        common = os.environ.get("CommonProgramW6432") or os.environ.get("CommonProgramFiles")
        if common:
            folders.append(Path(common) / "VST3")
        local = os.environ.get("LOCALAPPDATA")
        if local:
            folders.append(Path(local) / "Programs" / "Common" / "VST3")
    elif sys.platform == "darwin":
        folders.extend([Path("/Library/Audio/Plug-Ins/VST3"), home / "Library/Audio/Plug-Ins/VST3"])
    else:
        folders.extend([home / ".vst3", Path("/usr/lib/vst3"), Path("/usr/local/lib/vst3")])
    folders.append(user_plugins_dir())
    return folders


def plugin_search_dirs(extra_dirs: Iterable[str | Path] = ()) -> list[Path]:
    seen: set[str] = set()
    folders: list[Path] = []
    for folder in [*default_plugin_dirs(), *(Path(str(item)).expanduser() for item in extra_dirs if str(item).strip())]:
        key = os.path.normcase(os.path.abspath(str(folder)))
        if key not in seen:
            seen.add(key)
            folders.append(Path(os.path.abspath(str(folder))))
    return folders


def find_plugin_files(folders: Iterable[str | Path], *, max_depth: int = PLUGIN_SCAN_DEPTH,
                      stop_event: threading.Event | None = None) -> list[Path]:
    seen: set[str] = set()
    found: list[Path] = []

    def walk(folder: Path, depth: int) -> None:
        if stop_event is not None and stop_event.is_set():
            return
        try:
            entries = sorted(os.scandir(folder), key=lambda entry: entry.name.lower())
        except OSError:
            return
        for entry in entries:
            if entry.name.startswith("."):
                continue
            if entry.name.lower().endswith(PLUGIN_EXTENSIONS):
                # A .vst3 is either a single file or a bundle folder; never look inside a bundle.
                key = os.path.normcase(os.path.abspath(entry.path))
                if key not in seen:
                    seen.add(key)
                    found.append(Path(os.path.abspath(entry.path)))
                continue
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
            except OSError:
                is_dir = False
            if is_dir and depth < max_depth:
                walk(Path(entry.path), depth + 1)

    for folder in folders:
        walk(Path(str(folder)), 0)
    return found


def plugin_file_signature(path: str | Path) -> tuple[int, int]:
    target = Path(str(path))
    try:
        stat = target.stat()
    except OSError:
        return 0, 0
    if not target.is_dir():
        return int(stat.st_size), int(stat.st_mtime_ns)
    # Updating a bundle rewrites the binary inside it without touching the bundle folder itself.
    size, newest = 0, int(stat.st_mtime_ns)
    try:
        for platform_dir in sorted((target / "Contents").iterdir()):
            if not platform_dir.is_dir():
                continue
            for binary in sorted(platform_dir.iterdir()):
                try:
                    inner = binary.stat()
                except OSError:
                    continue
                size += int(inner.st_size)
                newest = max(newest, int(inner.st_mtime_ns))
    except OSError:
        pass
    return size, newest


def _plugin_from_payload(path: str, raw: object) -> PluginInfo | None:
    if not isinstance(raw, dict):
        return None
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        return None

    def text(key: str) -> str:
        value = raw.get(key)
        return " ".join(value.split()) if isinstance(value, str) else ""

    return PluginInfo(
        path=path,
        name=name.strip(),
        manufacturer=text("manufacturer"),
        category=text("category"),
        version=text("version"),
        is_instrument=raw.get("is_instrument") is True,
        is_effect=raw.get("is_effect") is True,
    )


def _plugin_payload(plugin: PluginInfo) -> dict[str, object]:
    return {
        "name": plugin.name,
        "manufacturer": plugin.manufacturer,
        "category": plugin.category,
        "version": plugin.version,
        "is_instrument": plugin.is_instrument,
        "is_effect": plugin.is_effect,
    }


def load_plugin_cache(cache_path: Path) -> dict[str, PluginFileResult]:
    try:
        payload = json.loads(Path(cache_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(payload, dict) or payload.get("version") != PLUGIN_CACHE_VERSION:
        return {}
    entries = payload.get("files")
    if not isinstance(entries, dict):
        return {}
    cache: dict[str, PluginFileResult] = {}
    for path, raw in entries.items():
        if not isinstance(path, str) or not isinstance(raw, dict):
            continue
        try:
            signature = int(raw.get("size")), int(raw.get("mtime_ns"))
        except (TypeError, ValueError):
            continue
        plugins = tuple(
            plugin for plugin in (_plugin_from_payload(path, item) for item in raw.get("plugins") or ())
            if plugin is not None
        )
        error = raw.get("error")
        cache[path] = PluginFileResult(path, signature, plugins, error if isinstance(error, str) else "")
    return cache


def save_plugin_cache(cache_path: Path, files: Iterable[PluginFileResult]) -> None:
    payload = {
        "version": PLUGIN_CACHE_VERSION,
        "files": {
            item.path: {
                "size": item.signature[0],
                "mtime_ns": item.signature[1],
                "plugins": [_plugin_payload(plugin) for plugin in item.plugins],
                "error": item.error,
            }
            for item in files
        },
    }
    target = Path(cache_path)
    temp_path: Path | None = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(target.parent),
                                         prefix=f".{target.name}.", suffix=".tmp", delete=False) as handle:
            temp_path = Path(handle.name)
            json.dump(payload, handle, indent=1)
        os.replace(temp_path, target)
    except OSError:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass


def cached_scan_result(cache_path: Path, extra_dirs: Iterable[str | Path] = ()) -> PluginScanResult:
    folders = plugin_search_dirs(extra_dirs)
    roots = [os.path.normcase(str(folder)) for folder in folders]

    def inside(path: str) -> bool:
        candidate = os.path.normcase(path)
        return any(candidate.startswith(root.rstrip("\\/") + os.sep) for root in roots)

    files = tuple(
        item for path, item in sorted(load_plugin_cache(cache_path).items())
        if inside(path) and plugin_file_signature(path) == item.signature
    )
    return PluginScanResult(files=files, folders=tuple(str(folder) for folder in folders))


def plugins_to_instruments(plugins: Iterable[PluginInfo]) -> list[InstrumentInfo]:
    instruments = [
        InstrumentInfo(id=plugin.instrument_id, name=plugin.display_name, path=Path(plugin.path), source=PLUGIN_SOURCE)
        for plugin in plugins if plugin.is_instrument
    ]
    instruments.sort(key=lambda item: (item.name.lower(), str(item.path).lower()))
    return instruments


def probe_command() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, PLUGIN_PROBE_FLAG]
    return [sys.executable, "-m", "openpiano.core.plugin_probe"]


def _probe_environment() -> dict[str, str]:
    environment = dict(os.environ)
    if not getattr(sys, "frozen", False):
        package_root = str(Path(__file__).resolve().parents[2])
        existing = environment.get("PYTHONPATH")
        environment["PYTHONPATH"] = package_root if not existing else package_root + os.pathsep + existing
    return environment


def _read_probe_events(output_path: Path) -> list[dict[str, object]]:
    try:
        text = output_path.read_text(encoding="utf-8")
    except OSError:
        return []
    events = []
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def _probe_failure_reason(exit_code: int | None, stderr_path: Path) -> str:
    """What is known about a helper that ended without reporting: its exit code and the last
    line it wrote to its error stream."""
    parts = []
    if exit_code is not None:
        parts.append(f"exit code {exit_code}")
    try:
        lines = stderr_path.read_bytes()[-4000:].decode("utf-8", errors="replace").splitlines()
    except OSError:
        lines = []
    last = next((line.strip() for line in reversed(lines) if line.strip()), "")
    if last:
        parts.append(last[:240].rstrip("."))
    return f" ({': '.join(parts)})" if parts else ""


def run_probe_process(paths: Sequence[str], *, stop_event: threading.Event | None = None,
                      on_progress: Callable[[str], None] | None = None,
                      stall_seconds: float = PLUGIN_PROBE_STALL_SECONDS,
                      command: Sequence[str] | None = None) -> dict[str, dict[str, object]]:
    """Probe plug-in files in a helper process so that a plug-in which crashes or hangs while
    it is being inspected cannot take OpenPiano down with it."""
    results: dict[str, dict[str, object]] = {}
    remaining = [str(path) for path in paths]
    base_command = list(command) if command is not None else probe_command()
    with tempfile.TemporaryDirectory(prefix="openpiano-plugins-") as work:
        attempt = 0
        while remaining and not (stop_event is not None and stop_event.is_set()):
            attempt += 1
            input_path = Path(work) / f"input-{attempt}.json"
            output_path = Path(work) / f"output-{attempt}.jsonl"
            input_path.write_text(json.dumps(remaining), encoding="utf-8")
            output_path.write_text("", encoding="utf-8")
            options: dict[str, object] = {}
            if sys.platform == "win32":
                options["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            # Kept so that a helper which dies before it can report anything leaves a reason.
            stderr_path = Path(work) / f"stderr-{attempt}.txt"
            try:
                with open(stderr_path, "wb") as stderr_file:
                    process = subprocess.Popen(
                        [*base_command, "--input", str(input_path), "--output", str(output_path)],
                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=stderr_file,
                        env=_probe_environment(), **options)
            except OSError as error:
                for path in remaining:
                    results[path] = {"plugins": [], "retry": True,
                                     "error": f"Could not start the plug-in scanner: {error}"}
                break
            seen_events = 0
            last_progress = time.monotonic()
            stalled = False
            while True:
                finished = process.poll() is not None
                events = _read_probe_events(output_path)
                if len(events) > seen_events:
                    for event in events[seen_events:]:
                        if event.get("event") == "start" and on_progress is not None:
                            on_progress(str(event.get("path") or ""))
                    seen_events = len(events)
                    last_progress = time.monotonic()
                if finished:
                    break
                if stop_event is not None and stop_event.is_set():
                    break
                if time.monotonic() - last_progress > stall_seconds:
                    stalled = True
                    break
                time.sleep(0.05)
            if process.poll() is None:
                process.kill()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
            started: str | None = None
            fatal = ""
            for event in _read_probe_events(output_path):
                if event.get("event") == "fatal":
                    fatal = str(event.get("error") or "unknown error")
                    continue
                path = event.get("path")
                if not isinstance(path, str):
                    continue
                if event.get("event") == "start":
                    started = path
                elif event.get("event") == "done":
                    plugins = event.get("plugins")
                    error = event.get("error")
                    results[path] = {
                        "plugins": plugins if isinstance(plugins, list) else [],
                        "error": error if isinstance(error, str) else "",
                    }
                    if started == path:
                        started = None
            if stop_event is not None and stop_event.is_set():
                break
            if fatal:
                for path in remaining:
                    results.setdefault(path, {"plugins": [], "retry": True,
                                              "error": f"The plug-in host could not start: {fatal}"})
                break
            if started is not None and started not in results:
                reason = ("did not respond while it was being scanned" if stalled
                          else "crashed while it was being scanned")
                results[started] = {"plugins": [], "error": f"The plug-in {reason}."}
            unfinished = [path for path in remaining if path not in results]
            if len(unfinished) == len(remaining):
                # The helper made no progress at all, so this is not about any one plug-in.
                reason = _probe_failure_reason(process.poll(), stderr_path)
                for path in unfinished:
                    results[path] = {"plugins": [], "retry": True,
                                     "error": f"The plug-in scanner stopped unexpectedly{reason}."}
                break
            remaining = unfinished
    return results


ProbeRunner = Callable[..., dict[str, dict[str, object]]]


def scan_plugins(cache_path: Path, extra_dirs: Iterable[str | Path] = (), *, force: bool = False,
                 stop_event: threading.Event | None = None,
                 on_progress: Callable[[int, int, str], None] | None = None,
                 probe_runner: ProbeRunner | None = None) -> PluginScanResult:
    folders = plugin_search_dirs(extra_dirs)
    try:
        user_plugins_dir().mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    cache = {} if force else load_plugin_cache(cache_path)
    paths = [str(path) for path in find_plugin_files(folders, stop_event=stop_event)]
    signatures = {path: plugin_file_signature(path) for path in paths}
    files: dict[str, PluginFileResult] = {}
    pending: list[str] = []
    for path in paths:
        cached = cache.get(path)
        if cached is not None and cached.signature == signatures[path]:
            files[path] = cached
        else:
            pending.append(path)

    if pending and not (stop_event is not None and stop_event.is_set()):
        total = len(pending)
        position = {path: index for index, path in enumerate(pending)}

        def report(path: str) -> None:
            if on_progress is not None:
                on_progress(position.get(path, 0) + 1, total, path)

        runner = probe_runner or run_probe_process
        probed = runner(pending, stop_event=stop_event, on_progress=report)
        for path in pending:
            raw = probed.get(path)
            if raw is None:
                continue
            plugins = tuple(
                plugin for plugin in (_plugin_from_payload(path, item) for item in raw.get("plugins") or ())
                if plugin is not None
            )
            files[path] = PluginFileResult(path, signatures[path], plugins, str(raw.get("error") or ""),
                                           retry=raw.get("retry") is True)

    ordered = tuple(files[path] for path in paths if path in files)
    if not (stop_event is not None and stop_event.is_set()):
        save_plugin_cache(cache_path, (item for item in ordered if not item.retry))
    return PluginScanResult(files=ordered, folders=tuple(str(folder) for folder in folders))
