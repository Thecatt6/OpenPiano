"""Helper process that inspects plug-in files for the plug-in scanner.

It deliberately imports nothing from Qt and reports through a file: plug-ins are free to
write to the standard streams, and a crash here must only cost the plug-in being inspected.
"""
from __future__ import annotations

import importlib
import json
import os
import platform
import sys
from typing import Sequence


def windows_bundle_folders() -> tuple[str, ...]:
    """Names of the folders inside a VST3 bundle that can hold a binary for this process."""
    if platform.machine().lower() in ("arm64", "aarch64"):
        return ("arm64-win", "arm64x-win")
    if sys.maxsize > 2 ** 32:
        return ("x86_64-win",)
    return ("x86-win",)


def plugin_binary_path(path: str, *, platform_name: str | None = None) -> str:
    """The path the plug-in host has to be given for a plug-in file or bundle.

    A VST3 is either a single file or a bundle folder, which on Windows looks like
    ``Name.vst3/Contents/x86_64-win/Name.vst3``. The host library takes the bundle folder
    itself on macOS and Linux, but on Windows it only accepts the binary inside it. A bundle
    with no binary for this process is returned unchanged."""
    target = str(path)
    if (platform_name or sys.platform) != "win32" or not os.path.isdir(target):
        return target
    stem = os.path.splitext(os.path.basename(os.path.normpath(target)))[0]
    for folder in windows_bundle_folders():
        directory = os.path.join(target, "Contents", folder)
        preferred = os.path.join(directory, stem + ".vst3")
        if os.path.isfile(preferred):
            return preferred
        try:
            names = sorted(os.listdir(directory), key=str.lower)
        except OSError:
            continue
        for name in names:
            candidate = os.path.join(directory, name)
            if name.lower().endswith(".vst3") and os.path.isfile(candidate):
                return candidate
    return target


def _describe(path: str) -> list[dict[str, object]]:
    import pedalboard

    plugin_class = pedalboard.VST3Plugin
    bundle = path
    path = plugin_binary_path(bundle)
    if sys.platform == "win32" and os.path.isdir(path):
        expected = " or ".join(windows_bundle_folders())
        raise RuntimeError(f"This VST3 bundle has no plug-in for this version of Windows (no Contents\\{expected}).")
    try:
        names = list(plugin_class.get_plugin_names_for_file(path))
    except Exception:
        names = []
    plugins: list[dict[str, object]] = []
    errors: list[str] = []
    for name in names or [None]:
        try:
            # No warm-up: only the description is needed, not a plug-in that is ready to play.
            plugin = plugin_class(path, plugin_name=name, initialization_timeout=0.0)
        except Exception as error:
            errors.append(str(error).strip().splitlines()[0] if str(error).strip() else type(error).__name__)
            continue
        try:
            plugins.append({
                "name": str(name or getattr(plugin, "name", "") or os.path.splitext(os.path.basename(bundle))[0]),
                "manufacturer": str(getattr(plugin, "manufacturer_name", "") or ""),
                "category": str(getattr(plugin, "category", "") or ""),
                "version": str(getattr(plugin, "version", "") or ""),
                "is_instrument": bool(getattr(plugin, "is_instrument", False)),
                "is_effect": bool(getattr(plugin, "is_effect", False)),
            })
        finally:
            del plugin
    if not plugins and errors:
        raise RuntimeError(errors[0])
    return plugins


def _write(handle, payload: dict[str, object]) -> None:
    handle.write(json.dumps(payload) + "\n")
    handle.flush()
    try:
        os.fsync(handle.fileno())
    except OSError:
        pass


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        input_path = arguments[arguments.index("--input") + 1]
        output_path = arguments[arguments.index("--output") + 1]
    except (ValueError, IndexError):
        return 2
    try:
        with open(input_path, encoding="utf-8") as source:
            paths = [str(item) for item in json.load(source)]
    except (OSError, ValueError, TypeError):
        return 2
    with open(output_path, "a", encoding="utf-8") as handle:
        try:
            importlib.import_module("pedalboard")
        except BaseException as failure:
            if isinstance(failure, (KeyboardInterrupt, SystemExit)):
                raise
            # Nothing can be inspected without the host library; say so once instead of
            # blaming every plug-in in turn.
            _write(handle, {"event": "fatal", "error": str(failure).strip() or type(failure).__name__})
            return 3
        for path in paths:
            _write(handle, {"event": "start", "path": path})
            try:
                plugins = _describe(path)
                error = ""
            except BaseException as failure:
                if isinstance(failure, (KeyboardInterrupt, SystemExit)):
                    raise
                plugins = []
                error = str(failure).strip() or type(failure).__name__
            _write(handle, {"event": "done", "path": path, "plugins": plugins, "error": error})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
