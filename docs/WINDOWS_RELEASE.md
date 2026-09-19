# Windows release installation and validation

The versioned Windows x64 release is a ZIP containing a PyInstaller one-folder
application, an installer, a launcher, a release updater, this guide, and the
release `VERSION`.
The package includes Python, PySide6, QtAwesome resources, the app icon, the
Settings checkbox asset, the Tobii Pro SDK Python package, and the source needed
by the optional 32-bit Stream Engine bridge.

## Install a release

1. Download `PogledAssist-v<version>-windows-x64.zip` and its matching
   `.sha256` file from the same GitHub Release.
2. Verify the checksum:

   ```powershell
   $expected = (Get-Content .\PogledAssist-v0.1.0-windows-x64.zip.sha256).Split()[0]
   $actual = (Get-FileHash .\PogledAssist-v0.1.0-windows-x64.zip -Algorithm SHA256).Hash
   if ($actual -ne $expected) { throw "Checksum mismatch" }
   ```

3. Extract the ZIP, open the `PogledAssist` folder, and run:

   ```powershell
   Set-ExecutionPolicy -Scope Process Bypass -Force
   .\install_windows.ps1 -Launch
   ```

The installer requests Administrator access, preserves `data`, `logs`, root log
files, `install_info.json`, local `.venv` speech tools, and local `tools`
components, and creates the `Pogled Assist` desktop shortcut. Before
replacing files, it rejects both packaged and source-based running applications,
creates and smoke-tests a sibling staging directory, and renames the existing
installation to a backup. The backup remains until the new installed application
passes its smoke test. If installation or verification fails, it restores the
previous directory. Run
`C:\PogledAssist\start_gaze_mouse.ps1` or the shortcut later.

The previous application under `C:\TobiiExec` remains independent. The Pogled
Assist installer and updater reject that legacy path even when it is supplied
explicitly, and use their own executable name, desktop shortcut, startup task,
process checks, locks, environment variables, data, and logs.

The extracted folder is also portable. Run its `start_gaze_mouse.ps1` without
installing if a portable copy is preferred.

## Update an installed release

Close Pogled Assist, open PowerShell in `C:\PogledAssist`, and run:

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force
.\update_windows.ps1
```

The updater reads the installed `VERSION` and calls GitHub's latest stable
release endpoint for `Hasan-Smajlovic/TobiiEyeTrackerTool`. It accepts only a
stable `v<major>.<minor>.<patch>` tag and the exact
`PogledAssist-v<version>-windows-x64.zip` and matching `.sha256` assets from
that repository. It verifies the checksum and package `VERSION` before invoking
the package installer. A checksum, metadata, network, archive, or staging error
does not change application files. The updater refuses an automatic downgrade
when the installed version is newer than the latest stable release.

Only one updater and one installer can run at a time. Neither process stops a
running application automatically. Close the application and retry when the
updater reports a running process. Add `-Launch` to start the verified version
after a successful update. Update details are appended to
`C:\PogledAssist\update_windows.log`.

## Upgrade a Pogled Assist source installation

An existing Pogled Assist source installation uses its older updater, so it
cannot download this replacement updater by itself. Download the first stable
release manually, verify its checksum, and run that package's
`install_windows.ps1`. The installer recognizes the source layout under
`C:\PogledAssist` and upgrades it only after staging succeeds.

The migration preserves `data`, `logs`, `install_info.json`, root log files,
local `.venv` speech tools, and local `tools` components. The packaged
`update_windows.ps1` then installs only immutable stable releases and never
downloads a Git branch or repository source archive.

## Interrupted update recovery

The installer writes a transaction marker beside `C:\PogledAssist` immediately
before the directory swap. If the process or machine stops during that swap,
rerun `update_windows.ps1` or the verified package's `install_windows.ps1`. The
installer examines the current, staging, and backup directories under the
installer lock. It keeps a current version that passes the package smoke test or
restores the backup when the current version is missing or fails verification.

Do not manually delete hidden `.PogledAssist.install-*` or
`.PogledAssist.backup-*` paths while recovery is pending. If automatic rollback
also fails, the error identifies the retained transaction marker and backup for
manual recovery.

## Components installed separately

- Tobii runtime and calibration: install the official software appropriate for
  the tracker, connect the device, and complete calibration. The package cannot
  safely install or calibrate hardware.
- Tobii Stream Engine: Tobii Eye Tracker 4C setups commonly obtain
  `tobii_stream_engine.dll` from Tobii Core or Game Hub. The app searches common
  install locations. Set `TOBII_STREAM_ENGINE_DLL` when the DLL is elsewhere.
- 32-bit bridge: a 32-bit Tobii DLL cannot load in the packaged 64-bit process.
  Install 32-bit Python 3.10 and set `POGLED_ASSIST_X86_PYTHON` to its
  `python.exe`. The package already contains the bridge source.
- Default speech: install eSpeak NG 1.52 with the Bosnian `bs` voice. Set
  `ESPEAK_NG_EXE` if `espeak-ng.exe` is outside the standard install folders.
- Human-like speech: install the `edge-tts` package so `edge-playback.exe` is on
  `PATH`, or set `EDGE_PLAYBACK_EXE` directly. This voice uses Microsoft's online
  service and needs internet access at runtime.

The application launches without these external components. Missing Tobii
software disables gaze input while the provider retries. Missing speech tools
disable their corresponding voice preset.

## Build locally

Use Windows x64 and Python 3.10:

```powershell
.\dev.ps1 setup
.\dev.ps1 package
```

The build reads `VERSION`, uses the checked-in PyInstaller spec, includes the
release updater, checks required assets, executes the packaged
`--package-smoke-test`, installs an extracted copy in an isolated directory, and
creates:

```text
dist\PogledAssist-v<version>-windows-x64.zip
```

No physical tracker, Tobii runtime, eSpeak NG, or network speech service is used
by the package smoke test. It also verifies the bundled Bosnian model checksum
and computes a word completion from that model.

## Automated release

Pull requests targeting `development` or `master` run:

- `code-quality`
- `tests`
- `windows-package`

The first two checks include Ruff formatting, focused PSScriptAnalyzer rules,
hardware-independent UI rendering, and the enforced Python coverage floor.

After an approved merge to `master`, the release workflow checks out exactly
`github.sha`, repeats all software checks, builds the package, and creates the
`v<version>` tag at that commit. It uploads the ZIP and a SHA-256 file to a draft,
then publishes the draft only after both assets exist.

Rerunning the same commit is idempotent. A complete public release is left
unchanged. An incomplete draft for the same commit is repaired and published.
If the version tag points to any other commit, the workflow fails and requires a
new `VERSION`. It never moves or overwrites an existing version tag.

## Manual release validation

Report software-only and Tobii hardware checks separately.

Software-only checks on a clean Windows x64 environment:

- Verify the SHA-256 file before extraction.
- Run `install_windows.ps1` and confirm installation under `C:\PogledAssist`.
- Put a sentinel file under a test `C:\TobiiExec` installation and confirm the
  installer and updater reject that path without changing the sentinel.
- Install an older release, run `update_windows.ps1`, and confirm `VERSION`
  matches the latest stable release.
- Confirm a deliberately invalid checksum leaves the older version unchanged.
- Confirm an installed smoke-test failure restores the older version.
- Confirm `data`, including `speech_learning.json`, `logs`, `install_info.json`,
  and existing root logs other than the appended `update_windows.log` are
  byte-for-byte unchanged after update and rollback tests.
- Confirm any local `.venv` speech tools and `tools` bridge components remain
  available after source-install migration and release updates.
- Start the app from the desktop shortcut and confirm the toolbar appears.
- Open Settings, Speech, Keyboard, and Controller.
- Disconnect networking before first Speech use and confirm `Brzi izbor` still
  offers starting words, completions, and next words.
- Confirm suggestion selection and `Poništi riječ` produce the same text with
  mouse and simulated gaze, including Bosnian letters and punctuation spacing.
- Open a phrase or answer editor, use a suggestion, cancel, and confirm the
  conversation and its undo state return unchanged. Repeat with a successful save.
- Speak the same unchanged message twice, restart the app, and confirm personal
  learning was counted once and persists in `data\speech_learning.json`.
- Forget one learned word in Settings, restart, and confirm its personal boost
  remains removed while the message and saved library remain unchanged.
- Close and reopen the app and confirm settings persist under `data`.
- Confirm logs are written under `logs` when logging is enabled.

Hardware checks on the target Tobii machine:

- Confirm Tobii software detects and calibrates the tracker.
- Confirm the runtime log reports Tobii Pro SDK, Stream Engine, or the x86 bridge.
- Confirm both-eye tracking moves the pointer and dwell actions fire.
- Confirm left, right, double-click, keyboard, controller, and calibration actions.
- Confirm default eSpeak NG speech and optional human-like speech separately.

## Known limits

- The package supports Windows x64. AppBar behavior and Windows input do not work
  on other operating systems.
- The executable is not code-signed, so Windows may show an unknown-publisher
  warning.
- Multi-monitor mapping assumes the Tobii-calibrated display is primary.
- Physical Tobii behavior cannot be proven by GitHub-hosted runners.
- The human-like voice depends on an external online service.

## Rollback

Download an earlier immutable release, verify its checksum, extract it, and run
its `install_windows.ps1`. Close Pogled Assist first. The installer validates
the older package before replacing application binaries, preserves
`C:\PogledAssist\data` and `C:\PogledAssist\logs`, and restores the previous binaries if
installation fails.

Current releases keep the full Speech library in `data\speech_library.json` and
maintain a list-only `data\speech_phrases.json` for older releases. Do not delete
or rename either file during rollback. They also preserve
`data\speech_learning.json`; older releases ignore it, and a later compatible
release resumes using it. An older release reads and updates the
list-only file. When a current release is installed again, it imports newer
standalone phrase changes while retaining categories and answers from the full
library. If saved settings from a newer version are incompatible, back up `data`,
remove only `data\app_settings.json`, and restart the older version. Never move or
recreate an existing release tag during rollback.
