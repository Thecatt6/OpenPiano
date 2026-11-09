"""Helper process that inspects plug-in files for the plug-in scanner.

It deliberately imports nothing from Qt and reports through a file: plug-ins are free to
write to the standard streams, and a crash here must only cost the plug-in being inspected.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Sequence


def _describe(path: str) -> list[dict[str, object]]:
    import pedalboard

    plugin_class = pedalboard.VST3Plugin
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
                "name": str(name or getattr(plugin, "name", "") or os.path.splitext(os.path.basename(path))[0]),
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
