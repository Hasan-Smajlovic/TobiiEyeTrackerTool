# User guide

Pogled Assist places a toolbar at the top of the primary Windows display. It
can move the pointer from gaze, select its own controls by dwell, and perform a
click after you hold your gaze on a stable target.

## Before starting

Connect and calibrate the Tobii Eye Tracker 4C with the matching Tobii software.
The app tries these tracking backends in order:

1. Tobii Pro SDK through `tobii-research`.
2. A directly loadable Tobii Stream Engine DLL.
3. The optional 32-bit Python bridge for 32-bit Stream Engine installations.

The connection dot is green while tracking, yellow while waiting or retrying,
and red after an unavailable or failed backend. The two white dots show left and
right eye validity. Gaze control pauses when either eye becomes invalid.

## Hotbar controls

The left side of the hotbar contains:

1. Hide
2. Settings
3. Quick actions
4. Left click
5. Right click
6. Double click
7. Speech
8. Keyboard
9. Controller

Hide removes the AppBar reservation and leaves a floating Show button near the
top-left corner. Left, Right, and Double click arm one action. Keep your gaze on
the target until the progress overlay completes. The mode resets after the click
to reduce accidental repeats.

Every gaze selection starts with a configurable pause without a progress ring.
The default pause is 500 ms and can be adjusted between 100 and 2000 ms. The ring
then fills for the configured stare time, so the two default 500 ms values take
one second in total. After a control activates, look away from it before returning
to select it again. A mouse click remains immediate and cancels any pending gaze
selection.

A gaze-driven right click arms one direct left click for selecting a context-menu
item. That follow-up click skips precision zoom so the menu stays open.

## Quick actions

Quick actions is a toggle. With precision zoom enabled, holding gaze on a target
first opens a magnified square. Choose the exact point inside the square, then use
the radial menu:

- Top: left click
- Right: right click
- Bottom: double click
- Left: cancel

With precision zoom disabled, the radial menu opens directly at the stable gaze
point. Gaze does not move the real pointer while the zoom or menu is open.

## Speech

Speech opens a full-screen Bosnian keyboard. Select a letter group, then a letter.
Space and Backspace stay on the bottom row. `Izgovori` sends the current text to
the selected speech engine without clearing it. `Kategorije` contains saved groups
of answers, while `Fraze` contains standalone reusable text. Both lists support
adding and deleting entries with the same grouped keyboard. The conversation
message and all category, answer, and phrase content appear and are entered in
uppercase. Text typed or pasted with lowercase letters is converted immediately.

`Brzi izbor` shows up to five uppercase Bosnian suggestions. It completes the
word at the end of the input or adds a next word followed by one space. The
suggestions work without internet access, preserve the rest of the message, and
never speak automatically. They are inactive while text is selected, the cursor
is away from the end, or a category name is being entered.

`Poništi riječ` restores the exact text from before the most recent suggestion.
It remains available until the text is otherwise edited, even after `Izgovori`.
If `.`, `,`, `?`, or `!` is entered immediately after a suggestion, the keyboard
removes only the space it added and places the punctuation after the word.

The same suggestions are available while adding a phrase or category answer.
That editor has its own undo state and cannot replace the saved conversation
message. A phrase or answer contributes to personal learning only after it is
successfully saved. Category names are never learned.

The controls on the right remain available while browsing or adding entries:

- `Alarm` stops speech, repeats a local sound, and opens a dialog. Select
  `Zaustavi alarm` to silence it and return to the same Speech state.
- `Sleep` stops speech and blacks out the display. Select `Nastavi` by gaze or
  mouse to restore the message, current list and page, and any unfinished entry.
- `Izlaz` offers three choices. `Odustani` returns to the same state, `Izađi`
  closes only Speech mode while preserving the conversation message, and
  `Ugasi aplikaciju` closes the whole application through its normal shutdown
  path.

Saved categories, answers, and phrases use UTF-8 text. Selecting an answer or
phrase appends it to the message, and saved phrases remain ordered by usage count.
The full library is stored in `data\speech_library.json`. The app also maintains
`data\speech_phrases.json` so older releases can read standalone phrases during
rollback. Personal word and short-context counts are stored locally in
`data\speech_learning.json`; message transcripts are not stored.

The Default voice uses eSpeak NG with the Bosnian `bs` voice. Human like uses
`edge-playback` with `bs-BA-GoranNeural` and requires internet access.

## Keyboard

Keyboard opens a right-side panel with Letters, Numpad, and Symbols tabs. The
panel sends normal Windows keyboard input to the last external foreground window.
It reserves the right work area while the hotbar is visible and expands to the
full screen height while the hotbar is hidden.

## Controller

Controller opens a right-side panel with General, Keyboard, Speech, and Settings
tabs. General provides left, right, and double-click actions, Enter, and scrolling.
The embedded Keyboard tab contains the same letters, numpad, and symbols controls.
Only Keyboard or Controller can reserve the right work area at one time.

## Settings

General settings controls startup, logging, and whether the PowerShell launcher
window stays visible. Start with Windows creates a per-user Scheduled Task that
runs the launcher with administrator privileges.

Gaze settings controls:

- Pause before gaze selection, defaulting to 500 ms and adjustable from 100 to 2000 ms
- Progress-ring fill time after the gaze pause
- Stable target radius
- Delay between actions
- Pointer smoothing
- Pointer movement from gaze
- Gaze bubble and action overlay visibility
- Precision zoom
- Tobii calibration launch

Speech settings controls eSpeak speed, letters per group, and the voice preset.
`Naučene riječi` opens the personal vocabulary. Select one word and then
`Zaboravi riječ` to remove its personal ranking contribution without changing
messages, phrases, answers, or the bundled dictionary. If learning cannot be
read or saved, the page preserves the last file, reports the problem, and offers
`Pokušaj ponovo` while normal typing and speech remain usable. Other settings are
saved immediately to `data\app_settings.json`.

## Update

Close Pogled Assist, open PowerShell in `C:\PogledAssist`, and run:

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force
.\update_windows.ps1
```

The updater requests Administrator access, compares the installed `VERSION` with
the latest stable GitHub Release, verifies the downloaded ZIP against its
published SHA-256 file, and tests the new application before replacing the old
one. Settings, saved phrases, logs, and installation metadata are preserved. If
the download, checksum, staging, or installed smoke test fails, the previous
installation remains available. Rerun the same command after an interrupted
update so the installer can recover the saved transaction.

An older source installation has the previous updater, so download and install
the first stable release manually to migrate it to the packaged channel. Its
installer keeps user data and local runtime components. Later updates never
install a repository branch, draft, or prerelease.

## Troubleshooting

If no tracker is found, confirm that Tobii software sees the device and that it is
calibrated. The runtime log should eventually include `Tracking with`,
`Stream Engine backend started`, or `x86 bridge started`.

If the log contains `[WinError 193] %1 is not a valid Win32 application`, the Tobii
DLL is probably 32-bit. Rerun source setup or configure a 32-bit Python 3.10 path:

```powershell
$env:POGLED_ASSIST_X86_PYTHON = "C:\Path\To\Python310-32\python.exe"
```

If Stream Engine is installed outside a common location:

```powershell
$env:TOBII_STREAM_ENGINE_DLL = "C:\Path\To\tobii_stream_engine.dll"
```

If calibration needs a product-specific command, set
`TOBII_CALIBRATION_COMMAND` before launching the app.

Runtime diagnostics are written to `logs\latest.txt` when logging is enabled.
The launcher writes `start_gaze_mouse.log` even when its window is hidden.
Updater diagnostics are appended to `update_windows.log` in the installation
folder.

## Exit

Use `Izlaz` on the Speech screen to return to the hotbar or close the whole
application. Quit app from General settings always closes the application.
`Ctrl+Q` works while the hotbar has focus, and `Alt+F4` closes the active
application window.
