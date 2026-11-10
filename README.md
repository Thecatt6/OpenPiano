# OpenPiano

<div align="center">

[![Code: GitHub](https://img.shields.io/badge/Code-GitHub-111827.svg?style=flat&logo=github&logoColor=white)](https://github.com/Justagwas/openpiano)
[![Website](https://img.shields.io/badge/Website-OpenPiano-0ea5e9.svg?style=flat&logo=google-chrome&logoColor=white)](https://www.justagwas.com/projects/openpiano/)
[![Mirror: SourceForge](https://img.shields.io/badge/Mirror-SourceForge-ff6600.svg?style=flat&logo=sourceforge&logoColor=white)](https://sourceforge.net/projects/openpiano/)

</div>

<p align="center">
  <img
    width="128"
    height="128"
    alt="OpenPiano Logo"
    src="OpenPiano/icon.ico"
  />
</p>

<div align="center">

[![Download (Windows)](https://img.shields.io/badge/Download-Windows%20(OpenPianoSetup.exe)-2563eb.svg?style=flat&logo=windows&logoColor=white)](https://github.com/Justagwas/OpenPiano/releases/latest/download/OpenPianoSetup.exe)

</div>

<p align="center"><b>Play, practice, and record piano on your computer.</b></p>

<p align="center">Play with a computer keyboard, mouse, or MIDI controller, choose your sound, and record the performance when it is worth keeping</p>

<div align="center">

[![Version](https://img.shields.io/github/v/tag/Justagwas/openpiano.svg?label=Version)](https://github.com/Justagwas/openpiano/tags)
[![License](https://img.shields.io/github/license/Justagwas/openpiano.svg)](https://github.com/Justagwas/openpiano/blob/main/LICENSE)
[![Last Commit](https://img.shields.io/github/last-commit/Justagwas/openpiano/main.svg?style=flat&cacheSeconds=3600)](https://github.com/Justagwas/openpiano/commits/main)
[![Open Issues](https://img.shields.io/github/issues/Justagwas/openpiano.svg)](https://github.com/Justagwas/openpiano/issues)
[![Stars](https://img.shields.io/github/stars/Justagwas/openpiano.svg?style=flat&cacheSeconds=3600)](https://github.com/Justagwas/openpiano/stargazers)
![Downloads (7d)](https://img.shields.io/badge/dynamic/json?style=flat&url=https%3A%2F%2Fdownload-stats-worker.justagwas.workers.dev%2Fdownloads%2Fopenpiano%3Frange%3Dweek&query=%24.data.weekly.all&label=Downloads%20(7d))

</div>

## Overview

OpenPiano turns your Windows computer into a playable piano. Start with the computer keyboard or mouse, connect a MIDI controller such as a musical keyboard, and watch every note appear on the piano as you play.

Choose a 61 or 88 key layout, personalize the controls and appearance, explore different instrument sounds, and record an idea without leaving the application.

## Features

- 61 and 88 key piano layouts with clear visual feedback.
- Computer keyboard, mouse, and MIDI controller input in the same session.
- Included Default and Grand Piano sounds, an optional Steinway D-274 download, and compatible SF2/SF3 instrument files.
- VST3 instrument plug-ins installed on the computer are found automatically and can be played like any other sound.
- Keyboard split: up to three key ranges that each play their own sound, next to the main one.
- Custom key mappings with save, discard, and undo.
- Adjustable volume, note strength, sustain, pitch, animation speed, and interface size.
- Themes and individual piano key colors.
- A built-in tutorial that can be reopened at any time.
- Performance recording to editable MIDI notes or rendered WAV audio.
- Local playing and recording without an account, with installer and standalone versions.

## Basic usage

1. Get the [Windows installer or standalone app](https://www.justagwas.com/projects/openpiano/). Choose the installer for a normal setup, or standalone to run without installing.
2. Launch OpenPiano and choose your computer-keyboard layout when prompted.
3. Choose the 61 or 88 key range under **Settings > Keyboard**.
4. Play with the labeled computer keys, click or glide across the piano, or select a connected MIDI controller.
5. Open **Controls** to choose an instrument or MIDI input. Use **Settings > Sound** to adjust volume, velocity (note strength), sustain, or transpose (pitch).
6. Select **Start Recording** when you want to keep a performance.
7. Select **Stop Recording**, then **Save recording**. Choose `.mid` for editable notes or `.wav` for rendered audio, and select where to save it.

Save a take before starting another recording; a new take replaces the previous one in memory. The Windows app includes two piano sounds, so you can start playing without a separate sound download.

## Using OpenPiano

### Make the keyboard your own

Show the note names or computer-key labels you need, then customize the mappings under **Settings > Keyboard > Change Keybinds**. Assign notes to different keys, undo an assignment with **Ctrl+Z**, and choose **Save** or **Discard** when finished.

The computer keyboard and mouse use the note strength set under **Settings > Sound**. A MIDI controller supplies its own note strength and can send sustain-pedal input. The [playing guide](https://github.com/Justagwas/OpenPiano/wiki/Playing-OpenPiano) explains sustain, transpose, and keyboard ranges.

### Choose your sound

OpenPiano includes Default and Grand Piano SoundFonts, listed as **OpenPiano Built-in**. A SoundFont is an instrument sound file. For another piano sound, use **Settings > Download Hi-Fi D-274 Piano (~400 MB)**. The optional Steinway D-274 download is listed as **OpenPiano External**. You can also add compatible SF2 or SF3 files to `%LOCALAPPDATA%\OpenPiano\soundfonts`, then restart the app.

Use **Controls** to select the instrument. A SoundFont file holds many sounds: **Bank** is the group of sounds inside the file and **Preset** is the sound itself. Adjust volume, note strength, sustain and pitch in **Settings > Sound**.

### Play VST3 instrument plug-ins

OpenPiano looks for VST3 instrument plug-ins each time it starts and adds the ones it finds to the instrument list in **Controls**, marked **VST3 Plug-in**. It searches the standard Windows locations (`C:\Program Files\Common Files\VST3` and `%LOCALAPPDATA%\Programs\Common\VST3`) and `%LOCALAPPDATA%\OpenPiano\plugins`. Add more locations with **Settings > Plug-ins (VST3) > Plug-in Folders…**, and use **Rescan Plug-ins** after installing a new plug-in while OpenPiano is open.

Select a plug-in like any other instrument. Plug-ins do not use banks and presets, so while one is selected those two lists are replaced by the **Open Plug-in Window** button: the sounds are chosen in the plug-in's own window. OpenPiano remembers what you set there for the next session.

Each plug-in is checked in a separate helper process, so a plug-in that crashes or hangs while being checked is left out of the list instead of closing OpenPiano. If OpenPiano closes unexpectedly while it is loading a plug-in, it starts with a built-in sound the next time and tells you which plug-in was skipped.

When the status line says that some plug-ins could not be loaded, **Details…** lists the folders that were searched, the instruments that were found, and the reason for each plug-in that was left out. **Copy** puts that text on the clipboard, which is the useful thing to include when reporting a plug-in that does not show up.

Only VST3 instruments are supported. Effect plug-ins and the VST2, CLAP and AU formats are not listed.

### Split the keyboard

Click the **Keyboard Split** button in the bottom bar, next to Controls and Settings, to open its own panel and give a range of keys its own sound, for example a bass in the left hand and a piano in the right. The panel takes the place of Settings while it is open, so the window stays small enough for any screen; Controls can stay open next to it. Tick **Zone 1**, **Zone 2** or **Zone 3**, click the zone's key range to open a picture of the keyboard, and drag across the keys the zone should cover. A single click moves the nearer end of the range, and with a MIDI keyboard connected you can simply play the lowest and the highest key. The arrow keys move the top of the range, Shift with the arrow keys moves the bottom, and Esc or Enter closes the picture. Then pick the sound for that range: any SoundFont with its preset, or a VST3 plug-in. For a plug-in, the preset list gives way to a **Plug-in Window** button that opens the plug-in's window for that zone (switching the zone on first if it is off). Each zone runs its own copy of the plug-in and remembers its own sound, so the same plug-in can play something different in every zone and as the main instrument. Each zone has its own volume, relative to the main volume, and an octave shift. Keys outside every zone keep playing the main instrument.

A coloured strip above the keys shows where each zone lies. Zones do not layer: where two overlap, the lower-numbered zone plays. A zone applies to the note that sounds, the one that lights up on the piano, so transposing can move a key into or out of a zone.

The included SoundFonts use calibrated preset levels to reduce volume jumps while preserving touch response. 100% is the standard level; the range up to 200% provides additional gain and can engage overload protection during louder playing. WAV export uses the same preset levels.

### Keep the performance

Save a take as MIDI when you want to edit its notes, timing, velocity and recorded sustain-pedal events in music software. MIDI does not include the instrument sound. Choose WAV for a stereo audio file rendered with the SoundFont, bank, preset and volume selected when you export. WAV is rendered from the take; it is not a recording of every live sound-setting change.

WAV export renders SoundFonts only. It is not available while a VST3 plug-in is the main instrument, and with a keyboard split it plays the whole take with the main instrument. MIDI export is unaffected.

### Installer, standalone and local files

Both Windows versions include the components and piano sounds needed to start playing. No separate Python installation or sound download is required.

Settings, user SoundFonts, and the plug-in list and plug-in settings normally stay under `%LOCALAPPDATA%\OpenPiano`, including with standalone. Moving the executable does not move those files. Save recordings to the location you choose. Playing and recording work locally; updates, optional downloads, and links you open can use the internet.

## Project resources

- Project page: <https://www.justagwas.com/projects/openpiano/>
- Download page: <https://www.justagwas.com/projects/openpiano/download/>
- Documentation: <https://github.com/Justagwas/OpenPiano/wiki>
- Releases: <https://github.com/Justagwas/OpenPiano/releases>

<details>
<summary>For Developers</summary>

### Requirements

- Windows (primary runtime target).
- Python 3.11 or later.
- Dependencies in [`OpenPiano/requirements.txt`](https://github.com/Justagwas/OpenPiano/blob/main/OpenPiano/requirements.txt).
- VST3 hosting uses [`pedalboard`](https://github.com/spotify/pedalboard) (GPL-3.0), which brings `numpy` with it. OpenPiano also runs without it; the plug-in features are then switched off.

### Running From Source

```powershell
cd OpenPiano
py -m pip install -r requirements.txt
py OpenPiano.py
```

### Building the Windows Program

```powershell
cd OpenPiano
py -m pip install -r requirements.txt pyinstaller
py build_exe.py
```

[`build_exe.py`](OpenPiano/build_exe.py) runs PyInstaller and writes `dist\OpenPiano.exe`, a single file that runs without Python installed. It packs the FluidSynth libraries, the VST3 host and the SoundFonts found in `OpenPiano\soundfonts`; add `--soundfonts FOLDER` to pack the SoundFonts of another folder as well. `--folder` makes a folder that starts faster instead of one file, and `--console` keeps a console open to read error messages. VST3 plug-ins are not packed; they stay where they are installed.

### Configuration Files

- App constants and release endpoints: [`OpenPiano/openpiano/core/config.py`](https://github.com/Justagwas/OpenPiano/blob/main/OpenPiano/openpiano/core/config.py)
- Runtime settings serialization logic: [`OpenPiano/openpiano/core/settings_store.py`](https://github.com/Justagwas/OpenPiano/blob/main/OpenPiano/openpiano/core/settings_store.py)
- Theme palette definitions: [`OpenPiano/openpiano/core/theme.py`](https://github.com/Justagwas/OpenPiano/blob/main/OpenPiano/openpiano/core/theme.py)
- Tutorial step content: [`OpenPiano/openpiano/services/tutorial_flow.py`](https://github.com/Justagwas/OpenPiano/blob/main/OpenPiano/openpiano/services/tutorial_flow.py)
- Application entry point: [`OpenPiano/OpenPiano.py`](https://github.com/Justagwas/OpenPiano/blob/main/OpenPiano/OpenPiano.py)
- Keyboard split rules: [`OpenPiano/openpiano/core/split.py`](https://github.com/Justagwas/OpenPiano/blob/main/OpenPiano/openpiano/core/split.py)
- VST3 discovery and hosting: [`OpenPiano/openpiano/core/plugin_registry.py`](https://github.com/Justagwas/OpenPiano/blob/main/OpenPiano/openpiano/core/plugin_registry.py), [`OpenPiano/openpiano/core/plugin_host.py`](https://github.com/Justagwas/OpenPiano/blob/main/OpenPiano/openpiano/core/plugin_host.py)

### Packaging Notes

A VST3 plug-in is either a single `.vst3` file or a bundle folder (`Name.vst3\Contents\x86_64-win\Name.vst3`). On Windows `pedalboard` only accepts the file, so `plugin_binary_path` in `plugin_probe.py` resolves a bundle to the binary for the running architecture before the plug-in is scanned or loaded; the bundle folder stays the plug-in's identity in settings.

Plug-ins are checked in a helper process. From source this is `python -m openpiano.core.plugin_probe`; a frozen build starts its own executable again with `--openpiano-probe-plugins`, which `OpenPiano.py` handles before any window is created. A packaged build therefore has to include `pedalboard` and keep that argument check at the top of the entry point.

</details>

## Security and OS Warnings

- Windows SmartScreen may display a warning for a new or unsigned release.
- Obtain OpenPiano only from the official distribution locations:
  - <https://github.com/Justagwas/OpenPiano/releases>
  - <https://www.justagwas.com/projects/openpiano/download/>
  - <https://sourceforge.net/projects/openpiano/>
- Refer to the repository [Security Policy](https://github.com/Justagwas/OpenPiano/security/policy) for private vulnerability reporting.

## Contributing

Contributions are welcome.

- Start with [`.github/CONTRIBUTING.md`](https://github.com/Justagwas/OpenPiano/blob/main/.github/CONTRIBUTING.md)
- Follow the shared [Code of Conduct](https://github.com/Justagwas/.github/blob/main/CODE_OF_CONDUCT.md)
- Use [Issues](https://github.com/Justagwas/OpenPiano/issues) for defect reports, feature proposals, and questions
- Wiki: <https://github.com/Justagwas/OpenPiano/wiki>

## Contact

- Email: [email@justagwas.com](mailto:email@justagwas.com)
- Website: <https://www.justagwas.com/projects/openpiano/>

## License

Licensed under the GNU General Public License v3.0 (GPL-3.0).

See [`LICENSE`](https://github.com/Justagwas/OpenPiano/blob/main/LICENSE).
