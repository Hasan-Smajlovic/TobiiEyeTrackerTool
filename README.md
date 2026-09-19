# Pogled Assist

Windows desktop controls for a Tobii Eye Tracker 4C. The app turns gaze into
pointer movement, dwell clicks, a Bosnian speech keyboard, an on-screen keyboard,
and a compact controller panel.

[Download a Windows release](https://github.com/Hasan-Smajlovic/TobiiEyeTrackerTool/releases)

## Features

- A top hotbar that reserves desktop space through the Windows AppBar API.
- Pointer movement and dwell actions only while both eyes have valid tracking.
- Left, right, and double-click modes with an optional precision zoom step.
- A radial Quick actions menu for choosing a click at the current gaze target.
- A visible gaze bubble and action progress overlay.
- A full-screen Bosnian speech keyboard with offline word completion and
  next-word suggestions, reusable phrases, and local personal learning.
- Right-side Keyboard and Controller panels for typing and common shortcuts.
- Gaze-selectable settings for timing, smoothing, speech, startup, and logging.
- Tobii Pro SDK, Stream Engine, and optional 32-bit Stream Engine bridge support.

## Install

### Windows release

Download the versioned ZIP and matching `.sha256` file from
[GitHub Releases](https://github.com/Hasan-Smajlovic/TobiiEyeTrackerTool/releases).
Verify the checksum, extract the ZIP, then run:

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force
.\install_windows.ps1 -Launch
```

The installer copies the app to `C:\PogledAssist`, verifies that the packaged
executable starts, and creates a desktop shortcut. Future updates are installed
from verified stable GitHub Release artifacts by the packaged
`update_windows.ps1`. The release includes Python and the application
dependencies. Tobii software, tracker calibration, speech engines, and the
optional 32-bit bridge runtime remain separate.

The previous application under `C:\TobiiExec` is a separate installation. The
Pogled Assist installer and updater refuse that legacy path, so its files,
desktop shortcut, startup task, settings, and logs remain unchanged.

See the [Windows release guide](docs/WINDOWS_RELEASE.md) for checksum verification,
external components, rollback, and clean-machine checks.

### Source installation

The source setup installs Python 3.10 when needed, eSpeak NG, the optional 32-bit
Python bridge, a local `.venv`, launchers, and the desktop shortcut:

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force
.\setup_windows.ps1
```

Source installations live under `C:\PogledAssist`. Add `-Launch` to start the app
after setup or `-NoPause` when running from an existing terminal. Existing
installations under `C:\TobiiExec` are intentionally left in place and can be
used alongside Pogled Assist. Later Pogled Assist releases update through the
packaged `update_windows.ps1`.

## Run

Installed release or source setup:

```powershell
.\start_gaze_mouse.ps1
```

Development checkout:

```powershell
.\dev.ps1 setup
.\dev.ps1 run
```

The real application starts Tobii discovery and enables Windows input. Use the UI
preview command when you only want to inspect layout and styling.

To exercise gaze feedback and dwell interactions without Tobii hardware, use the
development-only mouse simulator:

```powershell
.\dev.ps1 simulate
```

Moving the mouse supplies gaze coordinates and simulates both eyes as valid. The
simulator does not move the pointer automatically because the pointer is its gaze
source, but armed dwell click actions still use the normal Windows input path.

## Local development

The repository uses one PowerShell entry point for setup and verification:

```powershell
.\dev.ps1 setup
.\dev.ps1 check
```

`check` runs code-quality checks, the hardware-independent test suite with
coverage, and a clean Windows package build with frozen executable and isolated
installer smoke tests. To inspect the UI without a tracker or speech engine, run:

```powershell
.\dev.ps1 ui -Open
```

The complete command reference, test strategy, and manual UI checklist are in
the [development guide](docs/DEVELOPMENT.md).

## Documentation

| Need | Guide |
| --- | --- |
| Use and troubleshoot the application | [User guide](docs/USER_GUIDE.md) |
| Understand the runtime and compatibility baseline | [Architecture](docs/ARCHITECTURE.md) |
| Understand Bosnian word completion, prediction, and its verification | [Speech suggestions](docs/SPEECH_SUGGESTIONS.md) |
| Set up development and run checks | [Development guide](docs/DEVELOPMENT.md) |
| Prepare issues, pull requests, reviews, and merges | [Contributing](CONTRIBUTING.md) |
| Build, install, validate, or roll back a release | [Windows release guide](docs/WINDOWS_RELEASE.md) |

The [application UI reference](docs/design/speech-keyboard-reference.html) is a
development-only HTML prototype for the visible PySide6 interface. Its
historical filename is retained for stable links. The application does not load
or package it.

## Runtime requirements

| Component | Why it is needed |
| --- | --- |
| Tobii runtime and calibration | Device discovery and calibrated gaze data |
| `tobii-research` | Preferred tracker API, included in Python and release installs |
| Tobii Stream Engine | Fallback for consumer trackers such as Eye Tracker 4C |
| Python 3.10 x86 | Optional bridge when only a 32-bit Stream Engine DLL is available |
| eSpeak NG with `bs` voice | Default offline Bosnian speech |
| `edge-playback` | Optional online Bosnian neural voice |

The app searches common install locations. These environment variables override
discovery when needed:

| Variable | Value |
| --- | --- |
| `TOBII_STREAM_ENGINE_DLL` | Full path to `tobii_stream_engine.dll` |
| `POGLED_ASSIST_X86_PYTHON` | Full path to 32-bit Python 3.10 |
| `TOBII_CALIBRATION_COMMAND` | Custom Tobii calibration command |
| `ESPEAK_NG_EXE` | Full path to `espeak-ng.exe` |
| `EDGE_PLAYBACK_EXE` | Full path to `edge-playback.exe` |

## Data and logs

User data is kept outside the packaged binaries so upgrades and rollbacks can
preserve it:

```text
data\app_settings.json
data\speech_library.json
data\speech_phrases.json
data\speech_learning.json
logs\latest.txt
```

The launcher also writes `start_gaze_mouse.log`. Source setup and release updates
write their own logs in the installation directory.

## Current limitations

Windows x64 is the supported application platform. The executable is not
code-signed, multi-monitor mapping assumes the calibrated display is primary,
and Tobii hardware still requires manual validation. The complete list is in the
[Windows release guide](docs/WINDOWS_RELEASE.md#known-limits).

## CI and releases

Pull requests run code-quality, hardware-independent test, and Windows packaging
checks. Merges to `master` publish an immutable versioned release from the exact
merged commit.

Release construction, manual hardware validation, and rollback are documented in
the [Windows release guide](docs/WINDOWS_RELEASE.md).
