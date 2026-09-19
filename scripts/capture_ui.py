"""Render deterministic screenshots of the main UI surfaces without hardware."""

from __future__ import annotations

import argparse
from contextlib import ExitStack
from dataclasses import replace
from html import escape
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent, QObject, Signal
from PySide6.QtWidgets import QApplication, QWidget

from gaze_mouse import controller_window as controller_module
from gaze_mouse import keyboard_window as keyboard_module
from gaze_mouse import settings_window as settings_module
from gaze_mouse import toolbar as toolbar_module
from gaze_mouse.controller_window import ControllerWindow
from gaze_mouse.keyboard_window import KeyboardWindow
from gaze_mouse.mouse_controller import GazeSettings
from gaze_mouse.settings_window import SettingsWindow
from gaze_mouse.speech_library import PhraseRecord, SpeechLibrary, default_categories
from gaze_mouse.speech_service import SpeechSettings
from gaze_mouse.speech_window import SpeechWindow
from gaze_mouse.suggestion_learning import LearningStore
from gaze_mouse.suggestion_model import WordModel
from gaze_mouse.suggestion_text import START


class PreviewAppBar:
    supported = False

    def register(self, *_args: object, **_kwargs: object) -> bool:
        return False

    def unregister(self) -> None:
        return None

    def set_position(self, *_args: object) -> None:
        return None


class PreviewInput:
    def foreground_window(self) -> None:
        return None

    def belongs_to_current_process(self, _hwnd: object) -> bool:
        return False

    def cursor_position(self) -> tuple[int, int]:
        return 640, 360


class PreviewSpeech:
    available = True

    def __init__(self) -> None:
        self._settings = SpeechSettings()

    @property
    def settings(self) -> SpeechSettings:
        return replace(self._settings)

    def update_settings(self, settings: SpeechSettings) -> None:
        self._settings = replace(settings)

    def speak(self, _text: str, settings: SpeechSettings | None = None) -> bool:
        if settings is not None:
            self._settings = replace(settings)
        return True

    def stop(self) -> None:
        return None


class PreviewAlarmSound(QObject):
    failed = Signal(str)

    def start(self) -> bool:
        return True

    def stop(self) -> None:
        return None

    @property
    def last_error(self) -> str | None:
        return None


class PreviewSuggestionService(QObject):
    predictions_ready = Signal(object, int, object)
    status_changed = Signal(str)
    storage_finished = Signal(bool)

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        super().__init__()
        self.store = LearningStore()
        self.store.learn_text("Ćevapi džez kahva ljeto njiva šetnja")
        self._model = WordModel(
            {
                ("selam",): 100,
                ("ja",): 90,
                ("kako",): 80,
                ("može",): 70,
                ("hvala",): 60,
                ("želim",): 50,
                ("vodu",): 40,
                (START, "selam"): 1000,
                (START, "ja"): 900,
                (START, "kako"): 800,
                (START, "može"): 700,
                (START, "hvala"): 600,
                ("želim", "vodu"): 1000,
            }
        )

    @property
    def status(self) -> str:
        return self.store.error

    def request(self, owner: object, revision: int, text: str) -> None:
        self.predictions_ready.emit(
            owner, revision, self._model.predict(text, self.store.snapshot())
        )

    def persist(self) -> None:
        return None

    def forget(self, word: str) -> None:
        self.store.forget(word)
        self.storage_finished.emit(True)

    def retry(self) -> None:
        self.storage_finished.emit(True)

    def close(self) -> None:
        return None


class PreviewLibraryStore:
    def __init__(self, phrases: list[PhraseRecord]) -> None:
        self._library = SpeechLibrary(
            categories=default_categories(),
            phrases=phrases,
        )

    def load(self) -> SpeechLibrary:
        return self._library

    def save(self, _library: SpeechLibrary) -> bool:
        return True


class PreviewHotbar(toolbar_module.HotbarWindow):
    def _start_services(self) -> None:
        self._set_tracker_dot("green", "UI preview")
        self._set_eye_indicators(True, True)
        self._set_status("UI preview: hardware and Windows input are disabled.")


def _capture_widget(
    app: QApplication,
    widget: QWidget,
    output_dir: Path,
    name: str,
    width: int,
    height: int,
) -> Path:
    widget.resize(width, height)
    widget.show()
    app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    widget.resize(width, height)
    if widget.layout() is not None:
        widget.layout().activate()
    app.processEvents()

    image_path = output_dir / f"{name}.png"
    pixmap = widget.grab()
    if pixmap.isNull() or not pixmap.save(str(image_path), "PNG"):
        raise RuntimeError(f"Could not render UI snapshot: {name}")
    return image_path


def _write_gallery(output_dir: Path, snapshots: list[tuple[str, Path]]) -> Path:
    cards = "\n".join(
        f'<article><h2>{escape(title)}</h2><a href="{path.name}">'
        f'<img src="{path.name}" alt="{escape(title)}"></a></article>'
        for title, path in snapshots
    )
    document = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Pogled Assist UI preview</title>
  <style>
    body {{ margin: 0; padding: 32px; background: #0c0e12; color: #f6f7fb;
      font-family: "Segoe UI", Arial, sans-serif; }}
    header {{ max-width: 900px; margin: 0 auto 32px; }}
    h1 {{ margin-bottom: 8px; }}
    p {{ color: #aeb8c8; }}
    main {{ display: grid; gap: 24px; }}
    article {{ overflow: hidden; border: 1px solid #303746; border-radius: 12px;
      background: #151923; }}
    h2 {{ margin: 0; padding: 14px 18px; font-size: 16px; }}
    a {{ display: block; overflow: auto; background: #08090c; }}
    img {{ display: block; max-width: 100%; height: auto; margin: 0 auto; }}
  </style>
</head>
<body>
  <header>
    <h1>Pogled Assist UI preview</h1>
    <p>Generated from the real Qt widgets with hardware and Windows input disabled.</p>
  </header>
  <main>{cards}</main>
</body>
</html>
"""
    gallery_path = output_dir / "index.html"
    gallery_path.write_text(document, encoding="utf-8")
    return gallery_path


def capture_ui(output_dir: Path, *, width: int = 1440, height: int = 900) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication(["capture-ui"])
    gaze_settings = GazeSettings()
    speech_settings = SpeechSettings()
    sample_phrases = [
        PhraseRecord("Trebam pomoć", 8),
        PhraseRecord("Molim vas sačekajte", 5),
        PhraseRecord("Hvala", 3),
    ]

    patches = (
        patch.object(toolbar_module, "WindowsAppBar", PreviewAppBar),
        patch.object(toolbar_module, "WindowsInputController", PreviewInput),
        patch.object(toolbar_module, "SpeechService", PreviewSpeech),
        patch.object(toolbar_module, "SuggestionService", PreviewSuggestionService),
        patch.object(
            toolbar_module, "load_app_settings", return_value=(gaze_settings, speech_settings)
        ),
        patch.object(keyboard_module, "WindowsAppBar", PreviewAppBar),
        patch.object(controller_module, "WindowsAppBar", PreviewAppBar),
        patch.object(settings_module, "is_windows_startup_enabled", return_value=False),
    )
    widgets: list[QWidget] = []
    snapshots: list[tuple[str, Path]] = []
    with ExitStack() as stack:
        for active_patch in patches:
            stack.enter_context(active_patch)

        try:
            hotbar = PreviewHotbar()
            widgets.append(hotbar)
            snapshots.append(
                (
                    "Hotbar",
                    _capture_widget(app, hotbar, output_dir, "hotbar", width, hotbar.BAR_HEIGHT),
                )
            )

            suggestions = PreviewSuggestionService()
            settings = SettingsWindow(gaze_settings, speech_settings, suggestions=suggestions)
            widgets.append(settings)
            for index, name, title in (
                (0, "settings-general", "Settings: general"),
                (1, "settings-gaze", "Settings: gaze"),
                (2, "settings-speech", "Settings: speech"),
            ):
                settings._select_tab(index)
                snapshots.append(
                    (title, _capture_widget(app, settings, output_dir, name, width, height))
                )
            settings._open_learning()
            snapshots.append(
                (
                    "Settings: learned words",
                    _capture_widget(
                        app,
                        settings,
                        output_dir,
                        "settings-learned-words",
                        width,
                        height,
                    ),
                )
            )

            speech_suggestions = PreviewSuggestionService()
            speech_suggestions.store = LearningStore()
            speech = SpeechWindow(
                PreviewSpeech(),
                library_store=PreviewLibraryStore(sample_phrases),
                alarm_sound=PreviewAlarmSound(),
                suggestions=speech_suggestions,
            )
            widgets.append(speech)
            snapshots.append(
                (
                    "Speech keyboard",
                    _capture_widget(app, speech, output_dir, "speech", width, height),
                )
            )
            speech._view_mode = "categories"
            speech._show_list_level()
            snapshots.append(
                (
                    "Speech categories",
                    _capture_widget(
                        app,
                        speech,
                        output_dir,
                        "speech-categories",
                        width,
                        height,
                    ),
                )
            )
            speech._category_index = 0
            speech._view_mode = "answers"
            speech._show_list_level()
            snapshots.append(
                (
                    "Speech category answers",
                    _capture_widget(
                        app,
                        speech,
                        output_dir,
                        "speech-answers",
                        width,
                        height,
                    ),
                )
            )
            speech._view_mode = "phrases"
            speech._category_index = None
            speech._show_list_level()
            snapshots.append(
                (
                    "Saved phrases",
                    _capture_widget(app, speech, output_dir, "phrases", width, height),
                )
            )
            speech._start_editor()
            snapshots.append(
                (
                    "Speech shared editor",
                    _capture_widget(
                        app,
                        speech,
                        output_dir,
                        "speech-editor",
                        width,
                        height,
                    ),
                )
            )
            speech._start_alarm()
            snapshots.append(
                (
                    "Speech alarm",
                    _capture_widget(
                        app,
                        speech._alarm_dialog,
                        output_dir,
                        "speech-alarm",
                        speech._alarm_dialog.width(),
                        speech._alarm_dialog.height(),
                    ),
                )
            )
            speech._stop_alarm()
            speech._start_sleep()
            snapshots.append(
                (
                    "Speech sleep",
                    _capture_widget(
                        app,
                        speech._sleep_dialog,
                        output_dir,
                        "speech-sleep",
                        width,
                        height,
                    ),
                )
            )
            speech._wake_from_sleep()
            speech._open_exit_dialog()
            snapshots.append(
                (
                    "Speech exit confirmation",
                    _capture_widget(
                        app,
                        speech._exit_dialog,
                        output_dir,
                        "speech-exit",
                        speech._exit_dialog.width(),
                        speech._exit_dialog.height(),
                    ),
                )
            )
            speech._close_dialog()

            sidebar_width = 380
            keyboard = KeyboardWindow(speech_settings)
            widgets.append(keyboard)
            for show, name, title in (
                (None, "keyboard-letters", "Keyboard: letters"),
                (keyboard._show_numpad, "keyboard-numpad", "Keyboard: numpad"),
                (keyboard._show_symbols, "keyboard-symbols", "Keyboard: symbols"),
            ):
                if show is not None:
                    show()
                snapshots.append(
                    (title, _capture_widget(app, keyboard, output_dir, name, sidebar_width, height))
                )

            controller = ControllerWindow(gaze_settings, speech_settings)
            widgets.append(controller)
            for show, name, title in (
                (None, "controller-general", "Controller: general"),
                (controller._show_keyboard_tab, "controller-keyboard", "Controller: keyboard"),
                (controller._show_settings_tab, "controller-settings", "Controller: settings"),
            ):
                if show is not None:
                    show()
                snapshots.append(
                    (
                        title,
                        _capture_widget(app, controller, output_dir, name, sidebar_width, height),
                    )
                )
        finally:
            for widget in reversed(widgets):
                widget.close()
                widget.deleteLater()
            app.processEvents()

    _write_gallery(output_dir, snapshots)
    return [path for _, path in snapshots]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("dist/ui-preview"))
    parser.add_argument("--width", type=int, default=1440)
    parser.add_argument("--height", type=int, default=900)
    arguments = parser.parse_args()

    snapshots = capture_ui(
        arguments.output.resolve(), width=arguments.width, height=arguments.height
    )
    print(f"Rendered {len(snapshots)} UI snapshots: {arguments.output.resolve() / 'index.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
