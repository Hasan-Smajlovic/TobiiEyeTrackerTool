# Architecture

Pogled Assist is one Windows desktop process built with Python and PySide6.
It turns Tobii gaze samples into pointer movement, dwell actions, speech, and
on-screen controls while keeping all settings and phrases on the local machine.

## Runtime flow

```text
run_gaze_mouse.py
  -> gaze_mouse.main
  -> QApplication
  -> HotbarWindow
       -> TobiiGazeProvider
            -> tobii-research
            -> direct Tobii Stream Engine fallback
            -> 32-bit Stream Engine bridge fallback
       -> GazeMouseController
            -> WindowsInputController
       -> SpeechService
       -> SuggestionService
            -> bundled Bosnian model
            -> local personal learning
       -> Settings, Speech, Keyboard, and Controller windows
       -> gaze bubble, interaction overlay, precision zoom, and Quick actions
       -> WindowsAppBar
```

`gaze_mouse/main.py` enables Windows DPI awareness, configures logging, creates
the Qt application, and shows `HotbarWindow`. The hotbar is the composition root:
it creates the runtime services, connects their Qt signals, starts them after the
window appears, and stops child windows, Tobii backends, speech, overlays, and the
AppBar reservation during shutdown.

## Component map

| Area | Main files | Responsibility |
| --- | --- | --- |
| Entry points | `run_gaze_mouse.py`, `gaze_mouse/main.py` | Source and packaged startup, Qt setup, package smoke test |
| Runtime composition | `gaze_mouse/toolbar.py` | Hotbar UI, service wiring, child-window ownership, status, cleanup |
| Gaze acquisition | `gaze_mouse/gaze_provider.py`, `gaze_mouse/mouse_gaze_provider.py`, `gaze_mouse/tobii_stream_engine*.py` | Tracker discovery, development simulation, backend fallback, sample bounds, retry, x86 bridge |
| Gaze interaction | `gaze_mouse/mouse_controller.py`, `gaze_mouse/gaze_selection.py` | Coordinate mapping, smoothing, shared selection timing, click and Quick action requests |
| Windows integration | `gaze_mouse/windows_input.py`, `gaze_mouse/appbar.py`, `gaze_mouse/windows_*.py` | Physical input, work-area reservation, keyboard, startup, focus, z-order |
| User surfaces | `gaze_mouse/*_window.py`, `gaze_mouse/quick_action_*.py` | Settings, speech, keyboard, controller, radial menu, precision zoom |
| Feedback | `gaze_mouse/gaze_bubble.py`, `gaze_mouse/interaction_overlay.py`, `gaze_mouse/gaze_feedback.py` | Gaze position and dwell progress shown without taking focus |
| Speech | `gaze_mouse/speech_service.py`, `gaze_mouse/speech_window.py`, `gaze_mouse/speech_library.py`, `gaze_mouse/alarm_sound.py` | eSpeak NG and Edge playback, text entry, saved categories, answers and phrases, and the repeating local alarm |
| Suggestions | `gaze_mouse/suggestion_*.py`, `gaze_mouse/assets/bosnian-model.*` | Offline Bosnian tokenisation, completion and next-word ranking, input-scoped undo, and reversible personal learning |
| Persistent data | `gaze_mouse/settings_store.py`, `gaze_mouse/suggestion_learning.py`, `gaze_mouse/logging_setup.py` | Settings, phrase and personal-learning data, logs, safe defaults, and atomic writes |
| Distribution | `setup_windows.ps1`, `start_gaze_mouse.ps1`, `update_windows.ps1`, `packaging/`, `scripts/` | Source setup, launch, verified release update, package build, install, release |
| Verification | `dev.ps1`, `tests/`, `.github/workflows/` | Local checks, simulated hardware inputs, UI flows, CI, release checks |

## Gaze and input path

`TobiiGazeProvider` tries the available backends in this order:

1. `tobii-research`
2. direct Tobii Stream Engine
3. Tobii Stream Engine through a 32-bit Python bridge

If none starts, the provider reports a retry state and scans again every three
seconds. Raw samples may arrive faster than the UI can safely process them, so
the provider keeps the newest sample and emits at a bounded interval. This keeps
the Qt event loop responsive instead of replaying stale gaze positions.

Each backend also reports left and right eye validity. Gaze movement and dwell
actions continue only while both eyes are valid. Losing either eye clears pending
gaze work, cancels active dwell interactions, closes active Quick action layers,
and leaves the pointer at its last position.

The source-only mouse gaze simulator bypasses tracker discovery and feeds the
primary-screen cursor position into the same gaze interaction path with both eyes
valid. Automatic pointer movement is disabled in that mode so the cursor can
remain the simulation input. It is a development aid, not hardware validation.

The controller keeps two coordinate spaces separate:

- Qt logical coordinates are used for hit testing buttons and windows.
- Windows physical coordinates are used for pointer movement and real clicks.

Smoothing affects visible pointer movement. Click targeting uses the current gaze
target so smoothing does not move the requested click away from the selected
point.

## UI and service ownership

`HotbarWindow` owns all long-lived services and top-level UI surfaces. It opens
the full-screen Speech and Settings windows, the right-side Keyboard and
Controller panels, the radial Quick actions menu, and precision zoom. Keyboard
and Controller panels are mutually exclusive. Feedback windows remain topmost
without taking focus from the application the user is controlling.

Settings changes update the live mouse and speech services and are saved
immediately. `SuggestionService` is also owned by the hotbar and shared by Speech
and Settings. It loads and queries the immutable base model away from the Qt event
loop, coalesces pending requests, and writes personal counts through a separate
worker. The Speech input validates the request owner, revision, text, caret, and
selection before displaying a result. Closing the hotbar closes every child
surface, flushes suggestion learning, stops speech and gaze workers, and
unregisters the AppBar so Windows restores the full work area.

## Persistent data and logs

The runtime root is the executable directory for a packaged build and the
repository root during source development. `POGLED_ASSIST_LOG_ROOT` can
override it for controlled launch and test scenarios.

```text
data/app_settings.json   gaze, interaction, startup, logging, and speech settings
data/speech_library.json saved categories, answers, phrases, and phrase use counts
data/speech_phrases.json rollback-compatible standalone phrases for older releases
data/speech_learning.json versioned local word and short-context counts
logs/latest.txt          current application log when logging is enabled
```

Missing or malformed settings fall back safely to defaults, with supported
values clamped to the same ranges as the Settings UI. Installation, update, and
rollback work must preserve `data/` and `logs/`.

The bundled suggestion model and writable learning profile have independent
version 1 schemas. A base-model release can therefore be replaced without
rewriting personal counts. An unreadable personal profile is reported and kept
unchanged until an explicit retry can merge new in-memory learning with readable
disk data.

## Packaging and installation

`dev.ps1` is the developer entry point. The PyInstaller specification under
`packaging/windows/` builds the frozen application and includes the icons, bridge
files, and versioned Bosnian model needed at runtime. The package smoke test
loads that model and computes an offline prediction. The release package contains
its own installer and launcher and installs under `C:\PogledAssist`.

`update_windows.ps1` reads the installed version, resolves the latest stable
release from `Hasan-Smajlovic/TobiiEyeTrackerTool`, downloads the exact Windows
ZIP and checksum assets, and verifies them before invoking the package installer.
The first packaged installer migrates an older source layout. The installer
stages and smoke-tests the new package, carries persistent data and external
runtime components into it, swaps sibling directories, and retains the previous
directory until the installed smoke test passes. A transaction marker lets the
next installer restore or finish an update interrupted during the directory swap.

## Design reference

[`design/speech-keyboard-reference.html`](design/speech-keyboard-reference.html)
is the self-contained visual and interaction reference used while developing
the visible PySide6 interface. Its historical filename is retained for stable
links. It is not loaded by the application, included by the PyInstaller build,
or required to install or run Pogled Assist.

## Compatibility contract

The current `development` behavior is the baseline for a user who already relies
on the application. Unless a linked issue explicitly changes a behavior, a
review-ready change must preserve:

- launch from the installed `C:\PogledAssist` location and from the development
  entry point;
- keep the previous installation under `C:\TobiiExec` independent and untouched;
- hotbar placement, AppBar work-area reservation, hide and restore behavior;
- tracker fallback, retry, x86 bridge, both-eye gate, and responsive gaze flow;
- pointer mapping and left, right, and double-click actions;
- dwell timing, precision zoom, Quick actions, gaze bubble, and action feedback;
- Speech, Keyboard, Controller, Settings, calibration, and quit flows;
- saved settings, saved phrases, logs, and user data during upgrades and
  rollbacks;
- clean shutdown of tracker subscriptions, bridge workers, speech processes,
  overlays, child windows, and AppBar state.

Automated tests use fake gaze, Windows input, speech, and external processes to
protect these flows without physical hardware. They do not prove real Tobii
tracking, calibration, Windows work-area behavior, gaze accuracy, or speech
playback. Record those checks separately and never report them as passed unless
they ran on the target Windows and Tobii setup.
