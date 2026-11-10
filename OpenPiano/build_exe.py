"""Packages OpenPiano as a Windows program that runs without Python installed.

    py -m pip install -r requirements.txt pyinstaller
    py build_exe.py             one file:  dist\\OpenPiano.exe
    py build_exe.py --folder    a folder:  dist\\OpenPiano\\OpenPiano.exe (starts faster)

The SoundFonts found in the "soundfonts" folder next to this script are packed in as the
built-in sounds; --soundfonts adds those of another folder. VST3 plug-ins are never packed:
they stay where they are installed on the computer.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP_NAME = "OpenPiano"
SOUNDFONT_SUFFIXES = {".sf2", ".sf3"}

# Import name -> what it is for. All of them are in requirements.txt, except PyInstaller.
REQUIRED_MODULES = {
    "PyInstaller": "the packaging tool",
    "PySide6": "the window",
    "fluidsynth": "SoundFont playback (pyfluidsynth)",
    "mido": "MIDI",
    "rtmidi": "MIDI keyboards (python-rtmidi)",
    "pedalboard": "VST3 plug-ins",
}

# Loaded by name while the program runs, so PyInstaller cannot see that they are needed.
HIDDEN_IMPORTS = (
    "fluidsynth",
    "mido",
    "mido.backends.rtmidi",
    "rtmidi",
    "pedalboard",
    "pedalboard_native",
    "numpy",
)
COLLECT_SUBMODULES = ("pedalboard", "rtmidi")
COPY_METADATA = ("pedalboard", "mido")


def _missing_modules() -> list[str]:
    missing = []
    for name, purpose in REQUIRED_MODULES.items():
        try:
            found = importlib.util.find_spec(name) is not None
        except (ImportError, ValueError):
            found = False
        if not found:
            missing.append(f"  {name}: {purpose}")
    return missing


def _soundfonts_in(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    return sorted(path for path in folder.iterdir()
                  if path.is_file() and path.suffix.lower() in SOUNDFONT_SUFFIXES)


def _gather_soundfonts(folders: list[Path], staging: Path) -> list[Path]:
    """Copies the SoundFonts of every folder into one folder that can be packed as the
    built-in sounds."""
    gathered: list[Path] = []
    taken: set[str] = set()
    for folder in folders:
        for soundfont in _soundfonts_in(folder):
            if soundfont.name.lower() in taken:
                continue
            taken.add(soundfont.name.lower())
            gathered.append(soundfont)
            shutil.copy2(soundfont, staging / soundfont.name)
            # "Name.sf3.json" holds the display name and the default preset of "Name.sf3".
            details = soundfont.with_name(soundfont.name + ".json")
            if details.is_file():
                shutil.copy2(details, staging / details.name)
    return gathered


def _data(source: Path, destination: str) -> str:
    return f"{source}{os.pathsep}{destination}"


def build(*, one_file: bool, console: bool, soundfont_folders: list[Path]) -> int:
    missing = _missing_modules()
    if missing:
        print("These are not installed for this Python:\n" + "\n".join(missing))
        print("\nInstall them with:\n  py -m pip install -r requirements.txt pyinstaller")
        return 1
    if sys.platform != "win32":
        print("Note: PyInstaller packages for the system it runs on. Run this on Windows to get a .exe.")

    dist_dir = ROOT / "dist"
    work_dir = ROOT / "build" / "pyinstaller"
    command = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--noupx",
        "--name", APP_NAME,
        "--onefile" if one_file else "--onedir",
        "--console" if console else "--windowed",
        "--distpath", str(dist_dir),
        "--workpath", str(work_dir),
        "--specpath", str(work_dir),
        "--paths", str(ROOT),
    ]

    icon = ROOT / "icon.ico"
    if icon.is_file():
        command += ["--icon", str(icon), "--add-data", _data(icon, ".")]

    # FluidSynth and the audio helper. The program looks for them in this same folder
    # inside the package (see openpiano/core/fluidsynth_loader.py).
    dll_dir = ROOT / "third_party" / "fluidsynth" / "bin"
    dlls = sorted(dll_dir.glob("*.dll"))
    if sys.platform == "win32" and not dlls:
        print(f"No DLL files in {dll_dir}: the packaged program would have no SoundFont sound.")
        return 1
    for dll in dlls:
        command += ["--add-binary", _data(dll, "third_party/fluidsynth/bin")]

    for name in HIDDEN_IMPORTS:
        command += ["--hidden-import", name]
    for name in COLLECT_SUBMODULES:
        command += ["--collect-submodules", name]
    for name in COPY_METADATA:
        command += ["--copy-metadata", name]

    with tempfile.TemporaryDirectory(prefix="openpiano-soundfonts-") as staging_name:
        staging = Path(staging_name)
        soundfonts = _gather_soundfonts(soundfont_folders, staging)
        if soundfonts:
            command += ["--add-data", _data(staging, "soundfonts")]

        command.append(str(ROOT / "OpenPiano.py"))
        print("Packaging, this takes a few minutes...\n", flush=True)
        result = subprocess.run(command, cwd=str(ROOT))

    if result.returncode != 0:
        print("\nPackaging failed; the reason is in the messages above.")
        return result.returncode

    suffix = ".exe" if sys.platform == "win32" else ""
    program = dist_dir / (APP_NAME + suffix) if one_file else dist_dir / APP_NAME / (APP_NAME + suffix)
    print(f"\nDone: {program}")
    if not one_file:
        print(f"Keep the whole folder together: {program.parent}")
    if soundfonts:
        megabytes = sum(path.stat().st_size for path in soundfonts) / (1024 * 1024)
        print(f"Built-in sounds packed in ({megabytes:.0f} MB):")
        for path in soundfonts:
            print(f"  {path.name}")
    else:
        print("No SoundFont was packed in. The program will only have the SoundFonts found in\n"
              "%LOCALAPPDATA%\\OpenPiano\\soundfonts on the computer where it runs.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Package OpenPiano with PyInstaller.")
    parser.add_argument("--folder", action="store_true",
                        help="make a folder with the program and its files instead of one .exe; it starts faster")
    parser.add_argument("--console", action="store_true",
                        help="keep a console window open to read error messages while testing the package")
    parser.add_argument("--soundfonts", action="append", default=[], type=Path, metavar="FOLDER",
                        help="also pack the .sf2 and .sf3 files of this folder as built-in sounds (can be repeated)")
    arguments = parser.parse_args()

    folders = [ROOT / "soundfonts"]
    for folder in arguments.soundfonts:
        folder = folder.expanduser().resolve()
        if not folder.is_dir():
            parser.error(f"not a folder: {folder}")
        folders.append(folder)
    return build(one_file=not arguments.folder, console=arguments.console, soundfont_folders=folders)


if __name__ == "__main__":
    raise SystemExit(main())
