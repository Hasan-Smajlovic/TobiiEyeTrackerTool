"""Full-screen Bosnian speech keyboard."""

from __future__ import annotations

import copy
import logging
import math
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Literal

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QCloseEvent, QGuiApplication, QPaintEvent, QPalette, QResizeEvent
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QStyle,
    QStyleOptionButton,
    QStylePainter,
    QVBoxLayout,
    QWidget,
)

from .alarm_sound import ALARM_UNAVAILABLE_MESSAGE, AlarmSound
from .gaze_feedback import set_gaze_feedback
from .logging_setup import get_project_root
from .speech_library import (
    CategoryRecord,
    PhraseRecord,
    SpeechLibrary,
    SpeechLibraryStore,
    clean_text,
    entry_exists,
    sorted_phrases,
    speech_library_store,
)
from .speech_service import SpeechService, SpeechSettings
from .suggestion_composition import Composition
from .suggestion_service import SuggestionService

logger = logging.getLogger(__name__)

SPEECH_WINDOW_ACTION_PREFIX = "speech_window:"

BOSNIAN_LETTERS = [
    "A",
    "B",
    "C",
    "Č",
    "Ć",
    "D",
    "DŽ",
    "Đ",
    "E",
    "F",
    "G",
    "H",
    "I",
    "J",
    "K",
    "L",
    "LJ",
    "M",
    "N",
    "NJ",
    "O",
    "P",
    "R",
    "S",
    "Š",
    "T",
    "U",
    "V",
    "Z",
    "Ž",
]
MULTI_CHARACTER_LETTERS = ("DŽ", "LJ", "NJ")
SYMBOLS = ("1", "2", "3", "4", "5", "6", "7", "8", "9", "0", ".", "?")
GROUP_GRID_MAX_COLUMNS = 6
KEY_GRID_MAX_ROWS = 8
GROUP_BUTTON_MIN_HEIGHT = 72
PHRASE_BUTTON_MIN_HEIGHT = 64
LIST_ACTION_MIN_HEIGHT = 80
LIST_ACTION_MIN_WIDTH = 160
DIALOG_ACTION_MIN_HEIGHT = 128
ITEMS_PER_PAGE = 6
EditorKind = Literal["category", "answer", "phrase"]


@dataclass(frozen=True)
class EditorContext:
    kind: EditorKind
    category_index: int | None
    page: int
    message: str


class SpeechWindow(QWidget):
    """Fullscreen speech entry surface with gaze-selectable buttons."""

    closed = Signal()
    interaction_context_changed = Signal()
    mouse_action_started = Signal()
    quit_requested = Signal()

    def __init__(
        self,
        speech: SpeechService,
        parent: QWidget | None = None,
        letters_per_group: int | None = None,
        library_store: SpeechLibraryStore | None = None,
        alarm_sound: AlarmSound | None = None,
        suggestions: SuggestionService | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("speechWindow")
        self.setWindowTitle("Govor")
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)

        self._speech = speech
        initial_settings = speech.settings
        if letters_per_group is not None:
            initial_settings = replace(
                initial_settings, letters_per_group=max(1, letters_per_group)
            )

        self._speech_settings = initial_settings
        self._letters_per_group = max(1, self._speech_settings.letters_per_group)
        self._letter_groups = _group_letters(BOSNIAN_LETTERS, self._letters_per_group)
        self._action_buttons: dict[str, QPushButton] = {}
        self._main_dynamic_actions: set[str] = set()
        self._letter_dialog_actions: set[str] = set()
        self._dialog_actions: set[str] = set()
        self._active_dialog: QDialog | None = None
        self._gaze_target_action: str | None = None
        self._library_store = library_store or speech_library_store(get_project_root())
        self._alarm_sound = alarm_sound or AlarmSound(self)
        self._alarm_sound.failed.connect(self._alarm_failed)
        self._library = self._library_store.load()
        self._list_page = 0
        self._category_index: int | None = None
        self._deletion_mode = False
        self._editor: EditorContext | None = None
        self._confirm_action: Callable[[], None] | None = None
        self._view_mode = "keyboard"
        self._symbols_mode = False
        self._suggestions = suggestions or SuggestionService(self)
        self._composition = Composition(self._suggestions.store)
        self._conversation = self._composition
        self._prediction_owner = object()
        self._prediction_revision = 0
        self._prediction_text = ""
        self._updating_input = False
        self._last_gaze_point: QPoint | None = None
        self._blocked_suggestion: QPushButton | None = None

        self._build_ui()
        self._build_dialogs()
        self._input.textChanged.connect(self._text_changed)
        self._input.cursorPositionChanged.connect(self._refresh_suggestions)
        self._input.selectionChanged.connect(self._refresh_suggestions)
        self._suggestions.predictions_ready.connect(self._receive_suggestions)
        self._suggestions.status_changed.connect(self._prediction_status)
        self._show_group_level()
        self._prediction_status(self._suggestions.status)
        logger.info("Speech window initialized with %s letter groups.", len(self._letter_groups))

    def show_full_screen(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            self.setGeometry(screen.geometry())

        self._restore_message_if_editing()
        self._editor = None
        self._view_mode = "keyboard"
        self._category_index = None
        self._list_page = 0
        self._deletion_mode = False
        self._symbols_mode = False
        self._show_group_level()
        self.showFullScreen()
        self.raise_()
        self.activateWindow()
        self._input.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
        logger.info("Speech window shown full-screen.")

    def update_settings(self, settings: SpeechSettings) -> None:
        old_letters_per_group = self._letters_per_group
        self._speech_settings = replace(settings)
        self._letters_per_group = max(1, self._speech_settings.letters_per_group)
        if self._letters_per_group != old_letters_per_group:
            self._close_dialog()
            self._letter_groups = _group_letters(BOSNIAN_LETTERS, self._letters_per_group)
            if self._is_list_mode():
                self._show_list_level()
            elif self._symbols_mode:
                self._show_symbols_level()
            else:
                self._show_group_level()

        logger.info("Speech window settings updated: %s", self._speech_settings)

    def closeEvent(self, event: QCloseEvent) -> None:
        logger.info("Speech window close event received.")
        self._restore_message_if_editing()
        self._alarm_sound.stop()
        if self._active_dialog is not None:
            self._active_dialog.done(0)
        self._set_gaze_target_action(None)
        self.interaction_context_changed.emit()
        self.closed.emit()
        super().closeEvent(event)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._modal_backdrop.setGeometry(self.rect())
        if self._active_dialog is not None:
            if self._active_dialog is self._sleep_dialog:
                self._position_sleep_dialog()
            else:
                self._position_dialog(self._active_dialog)

    def action_at_global_point(self, point: QPoint) -> str | None:
        self._last_gaze_point = QPoint(point)
        if self._blocked_suggestion is not None:
            button = self._blocked_suggestion
            if QRect(button.mapToGlobal(QPoint(0, 0)), button.size()).contains(point):
                return None
            self._blocked_suggestion = None
        actions = (
            self._dialog_actions if self._active_dialog is not None else self._action_buttons.keys()
        )
        for action in tuple(actions):
            button = self._action_buttons.get(action)
            if button is None or not button.isVisible() or not button.isEnabled():
                continue
            top_left = button.mapToGlobal(QPoint(0, 0))
            if QRect(top_left, button.size()).contains(point):
                return action

        return None

    def action_center_at_global_point(self, action: str, point: QPoint) -> QPoint | None:
        if self._active_dialog is not None and action not in self._dialog_actions:
            return None
        button = self._action_buttons.get(action)
        if button is None or not button.isVisible() or not button.isEnabled():
            return None
        top_left = button.mapToGlobal(QPoint(0, 0))
        rect = QRect(top_left, button.size())
        if not rect.contains(point):
            return None
        return rect.center()

    def contains_global_point(self, point: QPoint) -> bool:
        if not self.isVisible():
            return False
        top_left = self.mapToGlobal(QPoint(0, 0))
        return QRect(top_left, self.size()).contains(point)

    def handle_gaze_action(self, action: str) -> None:
        if not action.startswith(SPEECH_WINDOW_ACTION_PREFIX):
            return
        if self._active_dialog is not None and action not in self._dialog_actions:
            return
        if action.startswith(self._action("suggestion:")):
            button = self._action_buttons.get(action)
            if button is self._blocked_suggestion:
                return
            self._blocked_suggestion = button
        logger.info("Speech window gaze action requested: %s", action)
        self._trigger_action(action)

    def cancel_gaze_interaction(self) -> None:
        self._set_gaze_target_action(None)

    def set_gaze_target_action(self, action: str | None) -> None:
        self._set_gaze_target_action(action)

    def _build_ui(self) -> None:
        self.setStyleSheet(
            """
            QWidget#speechWindow {
                background: #111318;
                color: #f6f7fb;
                font-family: Segoe UI, Arial, sans-serif;
                font-size: 15px;
            }
            QLabel#sectionLabel, QLabel#messageLabel, QLabel#statusLabel {
                color: #bac5d4;
                font-weight: 500;
            }
            QLabel#sectionLabel { font-size: 16px; }
            QLabel#messageLabel, QLabel#statusLabel { font-size: 13px; }
            QLineEdit#speechInput {
                background: #0b0d11;
                border: 2px solid #465268;
                border-radius: 8px;
                color: #ffffff;
                font-size: 30px;
                padding: 8px 16px;
                placeholder-text-color: #a0adbf;
                selection-background-color: #245f9f;
            }
            QPushButton {
                border: 1px solid #303747;
                border-radius: 8px;
                background: #1c2029;
                color: #eef2f8;
                font-size: 20px;
                font-weight: 700;
                padding: 8px;
            }
            QPushButton:hover { background: #262c38; border-color: #4c5970; }
            QPushButton:pressed, QPushButton:checked {
                background: #245f9f;
                border-color: #67b7dc;
            }
            QPushButton:disabled {
                background: #171a22;
                border-color: #2a303d;
                color: #667184;
            }
            QPushButton#clearButton {
                border-color: #67b7dc;
                background: #252b36;
                color: #ffe58a;
            }
            QPushButton#primaryButton {
                background: #2d68a8;
                border-color: #67b7dc;
                color: #ffffff;
                font-size: 23px;
            }
            QPushButton#primaryButton:disabled {
                background: #171a22;
                border-color: #2a303d;
                color: #667184;
            }
            QPushButton#predictionButton {
                background: #173447;
                border-color: #366c8d;
                color: #eef2f8;
                font-size: 22px;
            }
            QPushButton#predictionButton:disabled { color: #667184; background: #171a22; }
            QPushButton#groupButton {
                background: #1c2029;
                font-size: 29px;
                letter-spacing: 2px;
            }
            QPushButton#symbolButton { font-size: 32px; }
            QPushButton#utilityButton { font-size: 22px; }
            QPushButton#systemAlarm {
                background: #552326;
                border-color: #94434a;
                color: #ffd5d7;
                font-size: 27px;
            }
            QPushButton#systemSleep {
                background: #202538;
                border-color: #5b658c;
                color: #e0e5ff;
                font-size: 27px;
            }
            QPushButton#systemExit {
                background: #1c2029;
                border-color: #303747;
                color: #eef2f8;
                font-size: 27px;
            }
            QPushButton#phraseButton {
                font-size: 19px;
            }
            QPushButton#deleteItemButton {
                background: #4b2224;
                border-color: #7d383e;
                color: #fecaca;
                font-size: 19px;
            }
            QPushButton#headerActionButton { font-size: 18px; }
            QFrame#modalBackdrop { background: rgba(0, 0, 0, 190); }
            QDialog#speechDialog {
                background: #111318;
                border: 2px solid #67b7dc;
                border-radius: 12px;
                color: #eef2f8;
            }
            QDialog#speechDialog QLabel#dialogTitle {
                color: #eef2f8;
                font-size: 30px;
                font-weight: 500;
            }
            QDialog#speechDialog QLabel#dialogCopy { color: #dce6f3; font-size: 22px; }
            QDialog#speechDialog QPushButton#dialogLetterButton {
                font-size: 38px;
                min-height: 120px;
            }
            QDialog#speechDialog QPushButton#dialogBackButton {
                font-size: 22px;
                min-height: 120px;
            }
            QDialog#speechDialog QPushButton#dialogConfirmButton {
                background: #552326;
                border-color: #94434a;
                color: #ffd5d7;
                font-size: 22px;
                min-height: 128px;
            }
            QDialog#speechDialog QPushButton#dialogCancelButton {
                font-size: 22px;
                min-height: 128px;
            }
            QDialog#sleepDialog { background: #000000; }
            QDialog#sleepDialog QPushButton#wakeButton {
                background: #08090b;
                border-color: #2b3038;
                color: #7f8794;
                font-size: 28px;
                min-height: 140px;
            }
            QDialog#sleepDialog QPushButton#wakeButton:hover {
                background: #111318;
                border-color: #657084;
                color: #bac5d4;
            }
            QWidget#speechWindow QPushButton[gazeTarget="true"][gazePulse="0"],
            QWidget#speechWindow QDialog#speechDialog QPushButton[gazeTarget="true"][gazePulse="0"] {
                background: #f0c84a;
                border: 4px solid #ffe58a;
                color: #111318;
            }
            QWidget#speechWindow QPushButton[gazeTarget="true"][gazePulse="1"],
            QWidget#speechWindow QDialog#speechDialog QPushButton[gazeTarget="true"][gazePulse="1"] {
                background: #16a34a;
                border: 4px solid #86efac;
                color: #ffffff;
            }
            """
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(10)

        topbar = QHBoxLayout()
        topbar.setContentsMargins(0, 0, 0, 0)
        topbar.setSpacing(10)
        self._clear_button = self._make_button("Obriši", "clear", "clearButton", minimum_height=86)
        self._categories_button = self._make_button(
            "Kategorije", "categories", "topButton", minimum_height=86, checkable=True
        )

        message_box = QWidget(self)
        message_layout = QVBoxLayout(message_box)
        message_layout.setContentsMargins(0, 0, 0, 0)
        message_layout.setSpacing(4)
        self._message_label = QLabel("Vaša poruka", message_box)
        self._message_label.setObjectName("messageLabel")
        self._status_label = QLabel("", message_box)
        self._status_label.setObjectName("statusLabel")
        self._status_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._status_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        message_header = QHBoxLayout()
        message_header.setContentsMargins(0, 0, 0, 0)
        message_header.addWidget(self._message_label)
        message_header.addWidget(self._status_label, 1)
        self._input = QLineEdit(message_box)
        self._input.setObjectName("speechInput")
        self._input.setAlignment(Qt.AlignCenter)
        self._input.setMinimumHeight(62)
        self._input.setPlaceholderText("Odaberite grupu slova…")
        self._input.returnPressed.connect(self._play)
        message_layout.addLayout(message_header)
        message_layout.addWidget(self._input, 1)

        self._play_button = self._make_button(
            "Izgovori", "play", "primaryButton", minimum_height=86
        )
        self._phrases_button = self._make_button(
            "Fraze", "phrases", "topButton", minimum_height=86, checkable=True
        )
        topbar.addWidget(self._clear_button, 8)
        topbar.addWidget(self._categories_button, 10)
        topbar.addWidget(message_box, 40)
        topbar.addWidget(self._play_button, 10)
        topbar.addWidget(self._phrases_button, 9)

        predictions = QVBoxLayout()
        predictions.setContentsMargins(0, 0, 0, 0)
        predictions.setSpacing(6)
        self._prediction_label = QLabel("Brzi izbor", self)
        self._prediction_label.setObjectName("sectionLabel")
        self._prediction_label.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        predictions.addWidget(self._prediction_label)
        prediction_row = QHBoxLayout()
        prediction_row.setContentsMargins(0, 0, 0, 0)
        prediction_row.setSpacing(10)
        self._prediction_buttons = []
        for index in range(5):
            button = self._make_button(
                "·", f"suggestion:{index}", "predictionButton", minimum_height=66
            )
            button.setEnabled(False)
            button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            self._prediction_buttons.append(button)
            prediction_row.addWidget(button, 1)
        self._undo_word_button = self._make_button(
            "Poništi riječ", "suggestion-undo", "utilityButton", minimum_height=66
        )
        self._undo_word_button.setFixedWidth(170)
        self._undo_word_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._undo_word_button.setEnabled(False)
        prediction_row.addWidget(self._undo_word_button)
        predictions.addLayout(prediction_row)

        workspace = QHBoxLayout()
        workspace.setContentsMargins(0, 0, 0, 0)
        workspace.setSpacing(10)
        main_panel = QWidget(self)
        main_layout = QVBoxLayout(main_panel)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(8)
        view_header = QHBoxLayout()
        view_header.setContentsMargins(0, 0, 0, 0)
        view_header.setSpacing(8)
        self._view_title = QLabel("Odaberite grupu slova", main_panel)
        self._view_title.setObjectName("sectionLabel")
        self._back_button = self._make_button(
            "Nazad",
            "list:back",
            "headerActionButton",
            minimum_height=LIST_ACTION_MIN_HEIGHT,
        )
        self._add_item_button = self._make_button(
            "Dodaj",
            "list:add",
            "headerActionButton",
            minimum_height=LIST_ACTION_MIN_HEIGHT,
        )
        self._delete_mode_button = self._make_button(
            "Obriši",
            "list:delete-mode",
            "headerActionButton",
            minimum_height=LIST_ACTION_MIN_HEIGHT,
            checkable=True,
        )
        self._cancel_editor_button = self._make_button(
            "Odustani",
            "editor:cancel",
            "headerActionButton",
            minimum_height=LIST_ACTION_MIN_HEIGHT,
        )
        self._save_item_button = self._make_button(
            "Sačuvaj", "editor:save", "primaryButton", minimum_height=LIST_ACTION_MIN_HEIGHT
        )
        for button in (
            self._back_button,
            self._add_item_button,
            self._delete_mode_button,
            self._cancel_editor_button,
            self._save_item_button,
        ):
            button.setMinimumWidth(LIST_ACTION_MIN_WIDTH)
        view_header.addWidget(self._view_title, 1)
        view_header.addWidget(self._back_button)
        view_header.addWidget(self._add_item_button)
        view_header.addWidget(self._delete_mode_button)
        view_header.addWidget(self._cancel_editor_button)
        view_header.addWidget(self._save_item_button)

        self._key_grid_host = QWidget(main_panel)
        self._key_grid_host.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._key_grid = QGridLayout(self._key_grid_host)
        self._key_grid.setContentsMargins(0, 0, 0, 0)
        self._key_grid.setHorizontalSpacing(10)
        self._key_grid.setVerticalSpacing(10)

        paging = QHBoxLayout()
        paging.setContentsMargins(0, 0, 0, 0)
        paging.setSpacing(8)
        self._previous_page_button = self._make_button(
            "Prethodna",
            "list:page:previous",
            "headerActionButton",
            minimum_height=LIST_ACTION_MIN_HEIGHT,
        )
        self._page_label = QLabel("", main_panel)
        self._page_label.setObjectName("sectionLabel")
        self._page_label.setAlignment(Qt.AlignCenter)
        self._next_page_button = self._make_button(
            "Sljedeća",
            "list:page:next",
            "headerActionButton",
            minimum_height=LIST_ACTION_MIN_HEIGHT,
        )
        self._previous_page_button.setMinimumWidth(LIST_ACTION_MIN_WIDTH)
        self._next_page_button.setMinimumWidth(LIST_ACTION_MIN_WIDTH)
        paging.addStretch(1)
        paging.addWidget(self._previous_page_button)
        paging.addWidget(self._page_label)
        paging.addWidget(self._next_page_button)
        paging.addStretch(1)
        main_layout.addLayout(view_header)
        main_layout.addWidget(self._key_grid_host, 1)
        main_layout.addLayout(paging)

        system_panel = QWidget(self)
        system_panel.setMinimumWidth(280)
        system_panel.setMaximumWidth(480)
        system_layout = QVBoxLayout(system_panel)
        system_layout.setContentsMargins(0, 0, 0, 0)
        system_layout.setSpacing(8)
        controls_label = QLabel("Kontrole", system_panel)
        controls_label.setObjectName("sectionLabel")
        system_layout.addWidget(controls_label)
        for title, subtitle, command, object_name in (
            ("Alarm", "Pozovi pomoć", "alarm:start", "systemAlarm"),
            ("Sleep", "Odmori oči", "sleep:start", "systemSleep"),
            ("Izlaz", "Izađi ili ugasi aplikaciju", "exit", "systemExit"),
        ):
            button = self._make_button(
                f"{title}\n{subtitle}",
                command,
                object_name,
                parent=system_panel,
                minimum_height=100,
            )
            system_layout.addWidget(button, 1)

        workspace.addWidget(main_panel, 3)
        workspace.addWidget(system_panel, 1)

        utility_row = QHBoxLayout()
        utility_row.setContentsMargins(0, 0, 0, 0)
        utility_row.setSpacing(10)
        self._space_button = self._make_button(
            "Razmak", "space", "utilityButton", minimum_height=76
        )
        self._backspace_button = self._make_button(
            "Obriši slovo", "backspace", "utilityButton", minimum_height=76
        )
        self._keyboard_toggle_button = self._make_button(
            "Brojevi i znakovi", "keyboard-toggle", "utilityButton", minimum_height=76
        )
        utility_row.addWidget(self._space_button, 14)
        utility_row.addWidget(self._backspace_button, 10)
        utility_row.addWidget(self._keyboard_toggle_button, 10)

        root.addLayout(topbar)
        root.addLayout(predictions)
        root.addLayout(workspace, 1)
        root.addLayout(utility_row)

        self._modal_backdrop = QFrame(self)
        self._modal_backdrop.setObjectName("modalBackdrop")
        self._modal_backdrop.hide()

    def _build_dialogs(self) -> None:
        self._letter_dialog = self._new_dialog()
        letter_layout = QVBoxLayout(self._letter_dialog)
        letter_layout.setContentsMargins(32, 30, 32, 32)
        letter_layout.setSpacing(24)
        self._letter_dialog_title = QLabel("Odaberite slovo", self._letter_dialog)
        self._letter_dialog_title.setObjectName("dialogTitle")
        self._letter_grid_host = QWidget(self._letter_dialog)
        self._letter_grid = QGridLayout(self._letter_grid_host)
        self._letter_grid.setContentsMargins(0, 0, 0, 0)
        self._letter_grid.setHorizontalSpacing(16)
        self._letter_grid.setVerticalSpacing(16)
        letter_layout.addWidget(self._letter_dialog_title)
        letter_layout.addWidget(self._letter_grid_host, 1)
        self._letter_dialog.finished.connect(
            lambda _result, dialog=self._letter_dialog: self._dialog_finished(dialog)
        )

        self._confirm_dialog = self._new_dialog()
        confirm_layout = QVBoxLayout(self._confirm_dialog)
        confirm_layout.setContentsMargins(32, 30, 32, 32)
        confirm_layout.setSpacing(24)
        self._confirm_title = QLabel("Potvrda", self._confirm_dialog)
        self._confirm_title.setObjectName("dialogTitle")
        self._confirm_copy = QLabel("Cijela poruka bit će obrisana.", self._confirm_dialog)
        self._confirm_copy.setObjectName("dialogCopy")
        self._confirm_copy.setWordWrap(True)
        confirm_actions = QHBoxLayout()
        confirm_actions.setContentsMargins(0, 0, 0, 0)
        confirm_actions.setSpacing(24)
        cancel = self._make_button(
            "Odustani",
            "confirm:cancel",
            "dialogCancelButton",
            parent=self._confirm_dialog,
            minimum_height=DIALOG_ACTION_MIN_HEIGHT,
        )
        self._confirm_button = self._make_button(
            "Potvrdi",
            "confirm:accept",
            "dialogConfirmButton",
            parent=self._confirm_dialog,
            minimum_height=DIALOG_ACTION_MIN_HEIGHT,
        )
        confirm_actions.addWidget(cancel, 1)
        confirm_actions.addWidget(self._confirm_button, 1)
        confirm_layout.addWidget(self._confirm_title)
        confirm_layout.addWidget(self._confirm_copy)
        confirm_layout.addStretch(1)
        confirm_layout.addLayout(confirm_actions)
        self._confirm_dialog.finished.connect(
            lambda _result, dialog=self._confirm_dialog: self._dialog_finished(dialog)
        )

        self._exit_dialog = self._new_dialog()
        exit_layout = QVBoxLayout(self._exit_dialog)
        exit_layout.setContentsMargins(32, 30, 32, 32)
        exit_layout.setSpacing(24)
        exit_title = QLabel("Izaći iz govornog načina?", self._exit_dialog)
        exit_title.setObjectName("dialogTitle")
        exit_copy = QLabel(
            "Možete se vratiti na razgovor, izaći iz govornog načina ili ugasiti cijelu aplikaciju.",
            self._exit_dialog,
        )
        exit_copy.setObjectName("dialogCopy")
        exit_copy.setWordWrap(True)
        exit_actions = QHBoxLayout()
        exit_actions.setContentsMargins(0, 0, 0, 0)
        exit_actions.setSpacing(24)
        cancel_exit = self._make_button(
            "Odustani",
            "exit:cancel",
            "dialogCancelButton",
            parent=self._exit_dialog,
            minimum_height=DIALOG_ACTION_MIN_HEIGHT,
        )
        leave_speech = self._make_button(
            "Izađi",
            "exit:leave-speech",
            "dialogCancelButton",
            parent=self._exit_dialog,
            minimum_height=DIALOG_ACTION_MIN_HEIGHT,
        )
        quit_app = self._make_button(
            "Ugasi aplikaciju",
            "exit:quit-app",
            "dialogConfirmButton",
            parent=self._exit_dialog,
            minimum_height=DIALOG_ACTION_MIN_HEIGHT,
        )
        exit_actions.addWidget(cancel_exit, 1)
        exit_actions.addWidget(leave_speech, 1)
        exit_actions.addWidget(quit_app, 1)
        exit_layout.addWidget(exit_title)
        exit_layout.addWidget(exit_copy)
        exit_layout.addStretch(1)
        exit_layout.addLayout(exit_actions)
        self._exit_dialog.finished.connect(
            lambda _result, dialog=self._exit_dialog: self._dialog_finished(dialog)
        )

        self._alarm_dialog = self._new_dialog()
        alarm_layout = QVBoxLayout(self._alarm_dialog)
        alarm_layout.setContentsMargins(32, 30, 32, 32)
        alarm_layout.setSpacing(24)
        alarm_title = QLabel("Alarm je uključen", self._alarm_dialog)
        alarm_title.setObjectName("dialogTitle")
        self._alarm_copy = QLabel(
            "Zvučni signal se ponavlja dok ga ne zaustavite.", self._alarm_dialog
        )
        self._alarm_copy.setObjectName("dialogCopy")
        self._alarm_copy.setWordWrap(True)
        stop_alarm = self._make_button(
            "Zaustavi alarm",
            "alarm:stop",
            "dialogConfirmButton",
            parent=self._alarm_dialog,
            minimum_height=140,
        )
        alarm_layout.addWidget(alarm_title)
        alarm_layout.addWidget(self._alarm_copy)
        alarm_layout.addStretch(1)
        alarm_layout.addWidget(stop_alarm)
        self._alarm_dialog.finished.connect(
            lambda _result, dialog=self._alarm_dialog: self._dialog_finished(dialog)
        )

        self._sleep_dialog = self._new_dialog(object_name="sleepDialog")
        sleep_layout = QVBoxLayout(self._sleep_dialog)
        sleep_layout.setContentsMargins(32, 32, 32, 56)
        sleep_layout.addStretch(1)
        self._wake_button = self._make_button(
            "Nastavi",
            "sleep:wake",
            "wakeButton",
            parent=self._sleep_dialog,
            minimum_height=140,
        )
        self._wake_button.setMinimumWidth(320)
        self._wake_button.setMaximumWidth(420)
        sleep_layout.addWidget(self._wake_button, 0, Qt.AlignHCenter)
        self._sleep_dialog.finished.connect(
            lambda _result, dialog=self._sleep_dialog: self._dialog_finished(dialog)
        )

    def _new_dialog(self, *, object_name: str = "speechDialog") -> QDialog:
        dialog = QDialog(self)
        dialog.setObjectName(object_name)
        dialog.setModal(True)
        dialog.setWindowModality(Qt.ApplicationModal)
        dialog.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        return dialog

    def _show_group_level(self) -> None:
        self._symbols_mode = False
        if self._editor is None:
            self._view_mode = "keyboard"
        self._clear_main_grid()
        self._view_title.setText(
            self._editor_title() if self._editor is not None else "Odaberite grupu slova"
        )

        group_count = len(self._letter_groups)
        columns = self._group_column_count(group_count)
        self._set_grid_stretch(group_count, columns)
        for index, group in enumerate(self._letter_groups):
            action = self._action(f"group:{index}")
            button = self._make_dynamic_button(" ".join(group), action, "groupButton")
            self._key_grid.addWidget(button, index // columns, index % columns)

        self._update_view_controls()
        self._set_status("")
        self._context_changed()

    def _show_symbols_level(self) -> None:
        if self._editor is None:
            self._view_mode = "keyboard"
        self._symbols_mode = True
        self._clear_main_grid()
        self._view_title.setText(
            self._editor_title() if self._editor is not None else "Brojevi i znakovi"
        )
        self._set_grid_stretch(len(SYMBOLS), 4)
        for index, symbol in enumerate(SYMBOLS):
            action = self._action(f"symbol:{index}")
            button = self._make_dynamic_button(symbol, action, "symbolButton")
            self._key_grid.addWidget(button, index // 4, index % 4)

        self._update_view_controls()
        self._set_status("")
        self._context_changed()

    def _show_list_level(self) -> None:
        self._symbols_mode = False
        self._clamp_list_page()
        self._clear_main_grid()
        self._view_title.setText(self._list_title())

        items = self._list_items()
        self._set_grid_stretch(ITEMS_PER_PAGE, 2)
        if not items:
            empty = QPushButton(
                "Lista je prazna. Odaberite Dodaj za novi unos.",
                self._key_grid_host,
            )
            empty.setObjectName("phraseButton")
            empty.setEnabled(False)
            self._key_grid.addWidget(empty, 0, 0, 3, 2)
            self._set_status("")
        else:
            page_start = self._list_page * ITEMS_PER_PAGE
            visible_items = items[page_start : page_start + ITEMS_PER_PAGE]
            for position, item in enumerate(visible_items):
                index = page_start + position
                button = self._make_dynamic_button(
                    self._list_item_text(item),
                    self._action(f"list:select:{index}"),
                    "deleteItemButton" if self._deletion_mode else "phraseButton",
                )
                button.setMinimumHeight(PHRASE_BUTTON_MIN_HEIGHT)
                self._key_grid.addWidget(button, position // 2, position % 2)
            self._set_status("Odaberite stavku za brisanje." if self._deletion_mode else "")

        self._update_view_controls()
        self._context_changed()

    def _list_items(self) -> list[CategoryRecord] | list[str] | list[PhraseRecord]:
        if self._view_mode == "categories":
            return self._library.categories
        if self._view_mode == "answers":
            category = self._active_category()
            return category.answers if category is not None else []
        if self._view_mode == "phrases":
            return self._library.phrases
        return []

    def _list_title(self) -> str:
        if self._view_mode == "phrases":
            return "Moje fraze"
        category = self._active_category()
        return (
            _uppercase(category.name) if self._view_mode == "answers" and category else "Kategorije"
        )

    def _list_item_text(self, item: CategoryRecord | PhraseRecord | str) -> str:
        if isinstance(item, CategoryRecord):
            suffix = "Obriši" if self._deletion_mode else f"Odgovori: {len(item.answers)}"
            return f"{_uppercase(item.name)}\n{suffix}"
        text = item.text if isinstance(item, PhraseRecord) else item
        text = _uppercase(text)
        return f"{text}\nObriši" if self._deletion_mode else text

    def _active_category(self) -> CategoryRecord | None:
        if self._category_index is None:
            return None
        if self._category_index < 0 or self._category_index >= len(self._library.categories):
            return None
        return self._library.categories[self._category_index]

    def _populate_letter_dialog(self, group_index: int) -> None:
        self._clear_letter_dialog()
        group = self._letter_groups[group_index]
        self._letter_dialog_title.setText(f"Odaberite slovo: {' '.join(group)}")
        item_count = len(group) + 1
        columns = 3 if item_count <= 6 else 4
        rows = math.ceil(item_count / columns)
        for column in range(4):
            self._letter_grid.setColumnStretch(column, 1 if column < columns else 0)
        for row in range(5):
            self._letter_grid.setRowStretch(row, 1 if row < rows else 0)

        for index, letter in enumerate(group):
            action = self._action(f"letter:{group_index}:{index}")
            button = self._make_button(
                letter,
                action,
                "dialogLetterButton",
                parent=self._letter_dialog,
                minimum_height=120,
            )
            self._letter_dialog_actions.add(action)
            self._letter_grid.addWidget(button, index // columns, index % columns)

        back_action = self._action("letters:close")
        back = self._make_button(
            "Nazad na grupe",
            back_action,
            "dialogBackButton",
            parent=self._letter_dialog,
            minimum_height=120,
        )
        self._letter_dialog_actions.add(back_action)
        back_index = len(group)
        self._letter_grid.addWidget(back, back_index // columns, back_index % columns)

    def _open_letter_dialog(self, group_index: int) -> None:
        if group_index < 0 or group_index >= len(self._letter_groups):
            return
        self._populate_letter_dialog(group_index)
        self._dialog_actions = set(self._letter_dialog_actions)
        item_count = len(self._letter_groups[group_index]) + 1
        columns = 3 if item_count <= 6 else 4
        rows = math.ceil(item_count / columns)
        self._open_dialog(self._letter_dialog, min(820, 210 + rows * 150))

    def _open_clear_dialog(self) -> None:
        if not self._input.text():
            self._set_status("Tekst je već prazan.")
            return
        copy_text = (
            "Obrisat će se samo novi unos. Vaša poruka za razgovor ostaje sačuvana."
            if self._editor is not None
            else "Cijela poruka bit će obrisana."
        )
        self._open_confirmation(
            "Obrisati sav tekst?",
            copy_text,
            "Obriši tekst",
            self._clear_input,
        )

    def _open_confirmation(
        self,
        title: str,
        copy_text: str,
        confirm_label: str,
        action: Callable[[], None],
    ) -> None:
        self._confirm_title.setText(title)
        self._confirm_copy.setText(copy_text)
        self._confirm_button.setText(confirm_label)
        self._confirm_action = action
        self._dialog_actions = {
            self._action("confirm:cancel"),
            self._action("confirm:accept"),
        }
        self._open_dialog(self._confirm_dialog, 460)

    def _cancel_confirmation(self) -> None:
        self._confirm_action = None
        self._close_dialog()

    def _accept_confirmation(self) -> None:
        action = self._confirm_action
        self._confirm_action = None
        self._close_dialog()
        if action is not None:
            action()

    def _start_alarm(self) -> None:
        self._speech.stop()
        self._alarm_copy.setText("Pokrećem zvučni signal…")
        self._dialog_actions = {self._action("alarm:stop")}
        self._open_dialog(self._alarm_dialog, 460)
        try:
            started = self._alarm_sound.start()
        except Exception:
            logger.exception("Alarm sound could not be started.")
            started = False
        if started:
            self._alarm_copy.setText("Zvučni signal se ponavlja dok ga ne zaustavite.")
            self._set_status("Alarm je uključen.")
        else:
            self._alarm_failed(self._alarm_sound.last_error or ALARM_UNAVAILABLE_MESSAGE)

    def _alarm_failed(self, message: str) -> None:
        if self._active_dialog is self._alarm_dialog:
            self._alarm_copy.setText(message)
        self._set_status(message)

    def _stop_alarm(self) -> None:
        self._alarm_sound.stop()
        if self._active_dialog is self._alarm_dialog:
            self._close_dialog()
        self._set_status("Alarm je zaustavljen.")

    def _start_sleep(self) -> None:
        self._speech.stop()
        self._dialog_actions = {self._action("sleep:wake")}
        self._context_changed()
        self._active_dialog = self._sleep_dialog
        self._modal_backdrop.hide()
        self._position_sleep_dialog()
        self._sleep_dialog.show()
        self._sleep_dialog.raise_()
        self._sleep_dialog.activateWindow()
        self._set_status("Odmor je uključen.")

    def _position_sleep_dialog(self) -> None:
        top_left = self.mapToGlobal(QPoint(0, 0))
        self._sleep_dialog.setGeometry(QRect(top_left, self.size()))

    def _wake_from_sleep(self) -> None:
        if self._active_dialog is self._sleep_dialog:
            self._close_dialog()
        self._set_status("Možete nastaviti.")

    def _open_exit_dialog(self) -> None:
        self._dialog_actions = {
            self._action("exit:cancel"),
            self._action("exit:leave-speech"),
            self._action("exit:quit-app"),
        }
        self._open_dialog(self._exit_dialog, 460)

    def _leave_speech_mode(self) -> None:
        self._close_dialog()
        self._speech.stop()
        self.close()

    def _request_quit(self) -> None:
        self._close_dialog()
        self._alarm_sound.stop()
        self._speech.stop()
        self.quit_requested.emit()

    def _clear_input(self) -> None:
        self._input.clear()
        self._set_status("Tekst je obrisan.")

    def _open_dialog(self, dialog: QDialog, height: int) -> None:
        self._context_changed()
        self._active_dialog = dialog
        self._modal_backdrop.setGeometry(self.rect())
        self._modal_backdrop.show()
        self._modal_backdrop.raise_()
        available_width = max(320, self.width() - 64)
        width = min(1280, max(680, round(self.width() * 0.68)), available_width)
        dialog.resize(width, min(height, max(320, self.height() - 64)))
        self._position_dialog(dialog)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _position_dialog(self, dialog: QDialog) -> None:
        center = self.mapToGlobal(self.rect().center())
        dialog.move(center.x() - dialog.width() // 2, center.y() - dialog.height() // 2)

    def _close_dialog(self) -> None:
        if self._active_dialog is not None:
            self._active_dialog.done(0)

    def _dialog_finished(self, dialog: QDialog) -> None:
        if dialog is not self._active_dialog:
            return
        self._active_dialog = None
        if dialog is self._alarm_dialog:
            self._alarm_sound.stop()
        if dialog is self._confirm_dialog:
            self._confirm_action = None
        self._dialog_actions.clear()
        self._modal_backdrop.hide()
        self._context_changed()

    def _clear_main_grid(self) -> None:
        self._set_gaze_target_action(None)
        for action in self._main_dynamic_actions:
            self._action_buttons.pop(action, None)
        self._main_dynamic_actions.clear()
        self._clear_layout(self._key_grid)
        self._set_grid_stretch(0, 1)

    def _clear_letter_dialog(self) -> None:
        for action in self._letter_dialog_actions:
            self._action_buttons.pop(action, None)
        self._letter_dialog_actions.clear()
        self._clear_layout(self._letter_grid)

    def _clear_layout(self, layout: QGridLayout | QHBoxLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()

    def _set_grid_stretch(self, item_count: int, columns: int) -> None:
        for column in range(GROUP_GRID_MAX_COLUMNS):
            self._key_grid.setColumnStretch(column, 0)
        for row in range(KEY_GRID_MAX_ROWS):
            self._key_grid.setRowStretch(row, 0)
        rows = max(1, math.ceil(max(1, item_count) / max(1, columns)))
        for column in range(columns):
            self._key_grid.setColumnStretch(column, 1)
        for row in range(rows):
            self._key_grid.setRowStretch(row, 1)

    def _group_column_count(self, item_count: int) -> int:
        if item_count <= 1:
            return 1
        if item_count <= 8:
            return 2
        return min(GROUP_GRID_MAX_COLUMNS, math.ceil(item_count / 5))

    def _make_dynamic_button(self, text: str, action: str, object_name: str) -> QPushButton:
        button = self._make_button(
            text,
            action,
            object_name,
            parent=self._key_grid_host,
            minimum_height=GROUP_BUTTON_MIN_HEIGHT,
        )
        self._main_dynamic_actions.add(action)
        return button

    def _make_button(
        self,
        text: str,
        command: str,
        object_name: str,
        *,
        parent: QWidget | None = None,
        minimum_height: int,
        checkable: bool = False,
    ) -> QPushButton:
        button_type = (
            _WrappedButton
            if object_name
            in ("groupButton", "phraseButton", "deleteItemButton", "predictionButton")
            else QPushButton
        )
        button = button_type(text, self if parent is None else parent)
        button.setObjectName(object_name)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setCheckable(checkable)
        button.setMinimumHeight(minimum_height)
        button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        if isinstance(button, _WrappedButton):
            button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        action = (
            self._action(command)
            if not command.startswith(SPEECH_WINDOW_ACTION_PREFIX)
            else command
        )
        self._register_button(action, button)
        return button

    def _register_button(self, action: str, button: QPushButton) -> None:
        self._action_buttons[action] = button
        button.setProperty("gazeTarget", False)
        button.setProperty("gazePulse", "")
        button.pressed.connect(self.mouse_action_started.emit)
        button.clicked.connect(lambda _checked=False, item=action: self._trigger_action(item))

    def _set_gaze_target_action(self, action: str | None) -> None:
        if action == self._gaze_target_action:
            return
        if self._gaze_target_action is not None:
            previous = self._action_buttons.get(self._gaze_target_action)
            if previous is not None:
                set_gaze_feedback(previous, False)
        self._gaze_target_action = action
        if action is not None:
            current = self._action_buttons.get(action)
            if current is not None:
                set_gaze_feedback(current, True)

    def _context_changed(self) -> None:
        self._set_gaze_target_action(None)
        self.interaction_context_changed.emit()

    def _update_view_controls(self) -> None:
        editor_mode = self._editor is not None
        list_mode = self._is_list_mode()
        category_mode = self._view_mode in ("categories", "answers")
        phrase_mode = self._view_mode == "phrases"
        self._categories_button.setChecked(category_mode)
        self._categories_button.setText("Tastatura" if category_mode else "Kategorije")
        self._phrases_button.setChecked(phrase_mode)
        self._phrases_button.setText("Tastatura" if phrase_mode else "Fraze")
        self._categories_button.setEnabled(not editor_mode)
        self._phrases_button.setEnabled(not editor_mode)
        self._play_button.setEnabled(not editor_mode)
        self._back_button.setVisible(self._view_mode == "answers" and not editor_mode)
        self._add_item_button.setVisible(list_mode)
        if self._view_mode == "phrases":
            self._add_item_button.setText("Dodaj frazu")
        elif self._view_mode == "answers":
            self._add_item_button.setText("Dodaj odgovor")
        else:
            self._add_item_button.setText("Dodaj kategoriju")
        self._delete_mode_button.setVisible(list_mode)
        self._delete_mode_button.setChecked(self._deletion_mode)
        self._delete_mode_button.setText("Gotovo" if self._deletion_mode else "Obriši")
        self._delete_mode_button.setEnabled(bool(self._list_items()) or self._deletion_mode)
        self._save_item_button.setVisible(editor_mode)
        self._cancel_editor_button.setVisible(editor_mode)
        self._previous_page_button.setVisible(list_mode)
        self._previous_page_button.setEnabled(self._list_page > 0)
        self._next_page_button.setVisible(list_mode)
        self._next_page_button.setEnabled(self._list_page < self._list_page_count() - 1)
        self._page_label.setVisible(list_mode)
        self._page_label.setText(
            f"{self._list_page + 1} / {self._list_page_count()}" if list_mode else ""
        )
        self._message_label.setText(self._editor_title() if editor_mode else "Vaša poruka")
        self._input.setPlaceholderText(
            "Unesite tekst…" if editor_mode else "Odaberite grupu slova…"
        )
        if list_mode:
            self._keyboard_toggle_button.setText("Tastatura")
        elif self._symbols_mode:
            self._keyboard_toggle_button.setText("Grupe slova")
        else:
            self._keyboard_toggle_button.setText("Brojevi i znakovi")
        self._refresh_suggestions()

    def _is_list_mode(self) -> bool:
        return self._view_mode in ("categories", "answers", "phrases")

    def _editor_title(self) -> str:
        if self._editor is None:
            return "Vaša poruka"
        return {
            "category": "Nova kategorija",
            "answer": "Novi odgovor",
            "phrase": "Nova fraza",
        }[self._editor.kind]

    def _trigger_action(self, action: str) -> None:
        if self._active_dialog is not None and action not in self._dialog_actions:
            return
        button = self._action_buttons.get(action)
        if button is None or not button.isVisible() or not button.isEnabled():
            return
        command = action.removeprefix(SPEECH_WINDOW_ACTION_PREFIX)

        if command == "clear":
            self._open_clear_dialog()
        elif command.startswith("suggestion:"):
            self._select_suggestion(int(command.rsplit(":", 1)[1]))
        elif command == "suggestion-undo":
            self._set_composed_text(self._composition.undo_selection())
        elif command == "confirm:cancel":
            self._cancel_confirmation()
        elif command == "confirm:accept":
            self._accept_confirmation()
        elif command == "play":
            self._play()
        elif command == "alarm:start":
            self._start_alarm()
        elif command == "alarm:stop":
            self._stop_alarm()
        elif command == "sleep:start":
            self._start_sleep()
        elif command == "sleep:wake":
            self._wake_from_sleep()
        elif command == "exit":
            self._open_exit_dialog()
        elif command == "exit:cancel":
            self._close_dialog()
        elif command == "exit:leave-speech":
            self._leave_speech_mode()
        elif command == "exit:quit-app":
            self._request_quit()
        elif command == "categories":
            self._toggle_categories()
        elif command == "phrases":
            self._toggle_phrases()
        elif command == "space":
            self._append_text(" ")
        elif command == "backspace":
            self._backspace()
        elif command == "keyboard-toggle":
            self._toggle_keyboard_view()
        elif command == "letters:close":
            self._close_dialog()
        elif command.startswith("group:"):
            self._open_letter_dialog(int(command.split(":", 1)[1]))
        elif command.startswith("letter:"):
            _prefix, group_text, letter_text = command.split(":", 2)
            group_index = int(group_text)
            letter_index = int(letter_text)
            self._append_text(self._letter_groups[group_index][letter_index])
            self._close_dialog()
        elif command.startswith("symbol:"):
            self._append_text(SYMBOLS[int(command.split(":", 1)[1])])
        elif command == "list:add":
            self._start_editor()
        elif command == "editor:save":
            self._save_editor()
        elif command == "editor:cancel":
            self._cancel_editor()
        elif command == "list:back":
            self._show_categories_from_answers()
        elif command == "list:delete-mode":
            self._toggle_deletion_mode()
        elif command == "list:page:previous":
            self._change_list_page(-1)
        elif command == "list:page:next":
            self._change_list_page(1)
        elif command.startswith("list:select:"):
            self._select_list_item(int(command.rsplit(":", 1)[1]))

    def _toggle_categories(self) -> None:
        if self._view_mode in ("categories", "answers"):
            self._show_keyboard_from_list()
        else:
            self._view_mode = "categories"
            self._category_index = None
            self._list_page = 0
            self._deletion_mode = False
            self._show_list_level()

    def _toggle_phrases(self) -> None:
        if self._view_mode == "phrases":
            self._show_keyboard_from_list()
        else:
            self._view_mode = "phrases"
            self._category_index = None
            self._list_page = 0
            self._deletion_mode = False
            self._show_list_level()

    def _toggle_keyboard_view(self) -> None:
        if self._is_list_mode():
            self._show_keyboard_from_list()
        elif self._symbols_mode:
            self._show_group_level()
        else:
            self._show_symbols_level()

    def _show_keyboard_from_list(self) -> None:
        self._view_mode = "keyboard"
        self._category_index = None
        self._list_page = 0
        self._deletion_mode = False
        self._show_group_level()

    def _show_categories_from_answers(self) -> None:
        if self._view_mode != "answers":
            return
        self._view_mode = "categories"
        self._category_index = None
        self._list_page = 0
        self._deletion_mode = False
        self._show_list_level()

    def _start_editor(self) -> None:
        if not self._is_list_mode():
            return
        kind: EditorKind
        if self._view_mode == "phrases":
            kind = "phrase"
        elif self._view_mode == "answers":
            kind = "answer"
        else:
            kind = "category"
        self._editor = EditorContext(
            kind=kind,
            category_index=self._category_index,
            page=self._list_page,
            message=self._input.text(),
        )
        self._conversation = self._composition
        self._composition = Composition(self._suggestions.store, learn=False)
        self._input.clear()
        self._view_mode = "editor"
        self._deletion_mode = False
        self._symbols_mode = False
        self._show_group_level()
        self._view_title.setText(self._editor_title())
        self._update_view_controls()
        self._set_status("Unesite tekst pomoću grupa slova, zatim odaberite Sačuvaj.")

    def _save_editor(self) -> None:
        if self._editor is None:
            return
        text = _uppercase(clean_text(self._input.text()))
        if not text:
            self._set_status("Prvo unesite tekst.")
            return
        destination = self._editor_destination(self._library)
        if destination is None:
            self._set_status("Odredišna lista više nije dostupna.")
            return
        destination_texts = []
        for item in destination:
            if isinstance(item, PhraseRecord):
                destination_texts.append(item.text)
            elif isinstance(item, CategoryRecord):
                destination_texts.append(item.name)
            else:
                destination_texts.append(item)
        if entry_exists(destination_texts, text):
            self._set_status("Ova stavka već postoji. Unesite drugi tekst ili odaberite Odustani.")
            return

        editor = self._editor
        candidate = copy.deepcopy(self._library)
        candidate_destination = self._editor_destination(candidate)
        if candidate_destination is None:
            self._set_status("Odredišna lista više nije dostupna.")
            return
        if editor.kind == "category":
            candidate_destination.append(CategoryRecord(name=text))
        elif editor.kind == "phrase":
            candidate_destination.append(PhraseRecord(text=text))
            candidate.phrases = sorted_phrases(candidate.phrases)
        else:
            candidate_destination.append(text)

        if not self._library_store.save(candidate):
            self._set_status("Spremanje nije uspjelo. Novi unos nije sačuvan.")
            return

        self._library = candidate
        if editor.kind in ("phrase", "answer"):
            self._suggestions.store.learn_text(text)
            self._suggestions.persist()
        self._composition = self._conversation
        self._input.setText(editor.message)
        self._view_mode = _list_mode(editor.kind)
        self._category_index = editor.category_index
        self._editor = None
        self._list_page = self._page_for_saved_item(editor.kind, text)
        self._show_list_level()
        self._set_status("Sačuvano. Vaša poruka je vraćena.")

    def _cancel_editor(self) -> None:
        if self._editor is None:
            return
        editor = self._editor
        self._composition = self._conversation
        self._input.setText(editor.message)
        self._view_mode = _list_mode(editor.kind)
        self._category_index = editor.category_index
        self._list_page = editor.page
        self._editor = None
        self._show_list_level()
        self._set_status("Dodavanje je otkazano. Vaša poruka je vraćena.")

    def _restore_message_if_editing(self) -> None:
        if self._editor is not None:
            self._composition = self._conversation
            self._input.setText(self._editor.message)

    def _editor_destination(
        self, library: SpeechLibrary
    ) -> list[CategoryRecord] | list[str] | list[PhraseRecord] | None:
        if self._editor is None:
            return None
        if self._editor.kind == "category":
            return library.categories
        if self._editor.kind == "phrase":
            return library.phrases
        category_index = self._editor.category_index
        if (
            category_index is None
            or category_index < 0
            or category_index >= len(library.categories)
        ):
            return None
        return library.categories[category_index].answers

    def _page_for_saved_item(self, kind: str, text: str) -> int:
        if kind == "category":
            values = [category.name for category in self._library.categories]
        elif kind == "phrase":
            values = [phrase.text for phrase in self._library.phrases]
        else:
            category = self._active_category()
            values = category.answers if category is not None else []
        normalized = text.casefold()
        for index, value in enumerate(values):
            if value.casefold() == normalized:
                return index // ITEMS_PER_PAGE
        return 0

    def _select_list_item(self, index: int) -> None:
        items = self._list_items()
        if index < 0 or index >= len(items):
            return
        if self._deletion_mode:
            self._confirm_item_deletion(index)
            return
        item = items[index]
        if self._view_mode == "categories" and isinstance(item, CategoryRecord):
            self._category_index = index
            self._view_mode = "answers"
            self._list_page = 0
            self._show_list_level()
            return
        if self._view_mode == "answers" and isinstance(item, str):
            self._append_phrase_to_input(item)
            self._set_status("Dodano u poruku. Odaberite Izgovori za čitanje.")
            return
        if self._view_mode == "phrases" and isinstance(item, PhraseRecord):
            self._select_phrase(item)

    def _select_phrase(self, phrase: PhraseRecord) -> None:
        self._append_phrase_to_input(phrase.text)
        candidate = copy.deepcopy(self._library)
        selected = next(
            (item for item in candidate.phrases if item.text.casefold() == phrase.text.casefold()),
            None,
        )
        if selected is None:
            return
        selected.uses += 1
        candidate.phrases = sorted_phrases(candidate.phrases)
        if not self._library_store.save(candidate):
            self._set_status("Dodano u poruku, ali broj korištenja nije sačuvan.")
            return
        self._library = candidate
        self._list_page = self._page_for_saved_item("phrase", phrase.text)
        self._show_list_level()
        self._set_status("Fraza je dodana u poruku.")

    def _toggle_deletion_mode(self) -> None:
        if not self._is_list_mode():
            return
        self._deletion_mode = not self._deletion_mode
        self._show_list_level()

    def _confirm_item_deletion(self, index: int) -> None:
        items = self._list_items()
        if index < 0 or index >= len(items):
            return
        item = items[index]
        if isinstance(item, CategoryRecord):
            name = _uppercase(item.name)
            copy_text = f'Kategorija "{name}" i svi njeni odgovori bit će obrisani.'
        else:
            text = item.text if isinstance(item, PhraseRecord) else item
            text = _uppercase(text)
            copy_text = f'"{text}" će biti obrisano iz liste.'
        view_mode = self._view_mode
        category_index = self._category_index
        self._open_confirmation(
            "Obrisati ovu stavku?",
            copy_text,
            "Obriši",
            lambda: self._delete_list_item(view_mode, category_index, index),
        )

    def _delete_list_item(
        self,
        view_mode: str,
        category_index: int | None,
        index: int,
    ) -> None:
        candidate = copy.deepcopy(self._library)
        if view_mode == "categories":
            items: list[CategoryRecord] | list[str] | list[PhraseRecord] = candidate.categories
        elif view_mode == "phrases":
            items = candidate.phrases
        elif category_index is not None and 0 <= category_index < len(candidate.categories):
            items = candidate.categories[category_index].answers
        else:
            return
        if index < 0 or index >= len(items):
            return
        del items[index]
        if not self._library_store.save(candidate):
            self._set_status("Brisanje nije sačuvano. Stavka nije obrisana.")
            return
        self._library = candidate
        self._clamp_list_page()
        self._show_list_level()
        self._set_status("Stavka je obrisana.")

    def _change_list_page(self, delta: int) -> None:
        old_page = self._list_page
        self._list_page = max(0, min(self._list_page_count() - 1, self._list_page + delta))
        if self._list_page != old_page:
            self._show_list_level()

    def _list_page_count(self) -> int:
        return max(1, math.ceil(len(self._list_items()) / ITEMS_PER_PAGE))

    def _clamp_list_page(self) -> None:
        self._list_page = max(0, min(self._list_page, self._list_page_count() - 1))

    def _text_changed(self, text: str) -> None:
        if self._updating_input:
            return
        cursor_position = self._input.cursorPosition()
        selection_start = self._input.selectionStart()
        selection_length = _utf16_length(self._input.selectedText())
        corrected = self._composition.edit(_uppercase(text))
        if corrected != text:
            self._updating_input = True
            self._input.setText(corrected)
            if selection_start >= 0:
                self._input.setSelection(selection_start, selection_length)
            else:
                self._input.setCursorPosition(min(cursor_position, _utf16_length(corrected)))
            self._updating_input = False
        self._suggestions.persist()
        self._refresh_suggestions()

    def _suggestion_allowed(self) -> bool:
        # Qt exposes cursor offsets as UTF-16 code units, unlike Python string indexes.
        return (
            (self._editor is None or self._editor.kind != "category")
            and not self._input.hasSelectedText()
            and self._input.cursorPosition() == len(self._input.text().encode("utf-16-le")) // 2
        )

    def _refresh_suggestions(self, *_args) -> None:
        if self._updating_input:
            return
        if self._last_gaze_point is not None:
            for button in self._prediction_buttons:
                if QRect(button.mapToGlobal(QPoint(0, 0)), button.size()).contains(
                    self._last_gaze_point
                ):
                    self._blocked_suggestion = button
                    break
        self._prediction_revision += 1
        for button in self._prediction_buttons:
            button.setEnabled(False)
        self._undo_word_button.setEnabled(self._composition.undo is not None)
        self._context_changed()
        if self._suggestion_allowed():
            self._prediction_text = self._input.text()
            self._suggestions.request(
                self._prediction_owner, self._prediction_revision, self._prediction_text
            )
        else:
            for button in self._prediction_buttons:
                button.setText("·")
                button.setAccessibleName("Nema prijedloga")

    def _receive_suggestions(self, owner: object, revision: int, candidates: list[str]) -> None:
        if owner is not self._prediction_owner or revision != self._prediction_revision:
            return
        if not self._suggestion_allowed() or self._input.text() != self._prediction_text:
            return
        for index, button in enumerate(self._prediction_buttons):
            candidate = _uppercase(candidates[index]) if index < len(candidates) else ""
            button.setText(candidate or "·")
            button.setAccessibleName(candidate or "Nema prijedloga")
            button.setEnabled(bool(candidate))

    def _prediction_status(self, message: str) -> None:
        self._prediction_label.setText(f"Brzi izbor · {message}" if message else "Brzi izbor")
        self._prediction_label.setToolTip(message)

    def _select_suggestion(self, index: int) -> None:
        if not self._suggestion_allowed() or self._input.text() != self._prediction_text:
            return
        button = self._prediction_buttons[index]
        if button.isEnabled():
            self._set_composed_text(self._composition.select(button.text()))

    def _set_composed_text(self, text: str) -> None:
        self._updating_input = True
        self._input.setText(_uppercase(text))
        self._input.setFocus(Qt.FocusReason.MouseFocusReason)
        self._updating_input = False
        self._suggestions.persist()
        self._refresh_suggestions()

    def _append_text(self, value: str) -> None:
        self._input.setText(f"{self._input.text()}{_uppercase(value)}")
        self._input.setFocus(Qt.FocusReason.MouseFocusReason)

    def _append_phrase_to_input(self, phrase: str) -> None:
        current = self._input.text()
        if current and not current.endswith(" "):
            current = f"{current} "
        self._input.setText(f"{current}{_uppercase(phrase.strip())} ")
        self._input.setFocus(Qt.FocusReason.MouseFocusReason)

    def _backspace(self) -> None:
        text = self._input.text()
        if not text:
            return
        folded = text.casefold()
        for letter in MULTI_CHARACTER_LETTERS:
            if folded.endswith(letter.casefold()):
                self._input.setText(text[: -len(letter)])
                return
        self._input.setText(text[:-1])

    def _play(self) -> None:
        if (
            self._editor is not None
            or self._active_dialog is not None
            or not self._play_button.isEnabled()
        ):
            return
        text = self._input.text().strip()
        if not text:
            self._set_status("Prvo sastavite poruku.")
            return
        self._composition.submit()
        self._suggestions.persist()
        if self._speech.speak(text, self._speech_settings):
            self._set_status("Poruka se izgovara.")
        else:
            self._set_status("Odabrani glas nije dostupan.")

    def _set_status(self, text: str) -> None:
        if text:
            logger.info("Speech status: %s", text)
        self._status_label.setText(text)

    def _action(self, name: str) -> str:
        return f"{SPEECH_WINDOW_ACTION_PREFIX}{name}"


class _WrappedButton(QPushButton):
    """Keep long phrases and letter groups inside their allotted grid cell."""

    def paintEvent(self, event: QPaintEvent) -> None:
        option = QStyleOptionButton()
        self.initStyleOption(option)
        text = option.text
        option.text = ""
        painter = QStylePainter(self)
        painter.drawControl(QStyle.ControlElement.CE_PushButton, option)
        rect = self.style().subElementRect(QStyle.SubElement.SE_PushButtonContents, option, self)
        painter.drawItemText(
            rect,
            int(Qt.AlignmentFlag.AlignCenter)
            | int(
                Qt.TextFlag.TextWrapAnywhere
                if self.objectName() == "predictionButton"
                else Qt.TextFlag.TextWordWrap
            ),
            option.palette,
            self.isEnabled(),
            text,
            QPalette.ColorRole.ButtonText,
        )


def _group_letters(letters: list[str], letters_per_group: int) -> list[list[str]]:
    return [
        letters[index : index + letters_per_group]
        for index in range(0, len(letters), letters_per_group)
    ]


def _list_mode(kind: EditorKind) -> str:
    return {
        "category": "categories",
        "answer": "answers",
        "phrase": "phrases",
    }[kind]


def _uppercase(text: str) -> str:
    return text.upper()


def _utf16_length(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2
