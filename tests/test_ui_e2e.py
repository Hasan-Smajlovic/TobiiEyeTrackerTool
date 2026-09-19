from __future__ import annotations

import copy
from dataclasses import replace

import pytest
from PySide6.QtCore import QObject, QPoint, QRect, Qt, Signal

from gaze_mouse.controller_window import (
    CONTROLLER_WINDOW_ACTION_PREFIX,
    ControllerWindow,
)
from gaze_mouse.keyboard_window import KEYBOARD_WINDOW_ACTION_PREFIX, KeyboardWindow
from gaze_mouse.mouse_controller import (
    CONTROLLER,
    KEYBOARD,
    LEFT_CLICK,
    QUICK_ACTIONS,
    SETTINGS,
    SPEECH,
    GazeSettings,
)
from gaze_mouse.settings_window import SettingsWindow
from gaze_mouse.speech_library import (
    CategoryRecord,
    PhraseRecord,
    SpeechLibrary,
    default_categories,
)
from gaze_mouse.speech_service import SpeechSettings
from gaze_mouse.speech_window import BOSNIAN_LETTERS, SPEECH_WINDOW_ACTION_PREFIX, SpeechWindow
from gaze_mouse.windows_startup import StartupTaskResult


class FakeSpeech:
    def __init__(self):
        self._settings = SpeechSettings()
        self.requests = []
        self.stop_calls = 0

    @property
    def settings(self):
        return replace(self._settings)

    def speak(self, text, settings=None):
        self.requests.append((text, replace(settings) if settings is not None else self.settings))
        return True

    def stop(self):
        self.stop_calls += 1


class FakeAlarmSound(QObject):
    failed = Signal(str)

    def __init__(self):
        super().__init__()
        self.start_calls = 0
        self.stop_calls = 0
        self.start_result = True
        self.last_error = None

    def start(self):
        self.start_calls += 1
        return self.start_result

    def stop(self):
        self.stop_calls += 1


class FakeLibraryStore:
    def __init__(self, library=None):
        self.library = copy.deepcopy(library or SpeechLibrary(categories=default_categories()))
        self.saved = []
        self.fail_saves = False

    def load(self):
        return copy.deepcopy(self.library)

    def save(self, library):
        if self.fail_saves:
            return False
        self.library = copy.deepcopy(library)
        self.saved.append(copy.deepcopy(library))
        return True


def click_speech_action(qtbot, window, command):
    button = window._action_buttons[f"{SPEECH_WINDOW_ACTION_PREFIX}{command}"]
    qtbot.waitUntil(button.isVisible)
    button.click()


class FakeKeyboardInput:
    def __init__(self):
        self.typed = []
        self.pressed = []

    def foreground_window(self):
        return 50

    def belongs_to_current_process(self, _hwnd):
        return False

    def type_text(self, text):
        self.typed.append(text)

    def press_key(self, key):
        self.pressed.append(key)

    def is_window(self, _hwnd):
        return True

    def set_foreground_window(self, _hwnd):
        return True


class FakeControllerInput(FakeKeyboardInput):
    def __init__(self):
        super().__init__()
        self.clicks = []
        self.scrolls = []

    def click(self, x, y, **options):
        self.clicks.append((x, y, options))

    def click_current(self, **options):
        self.clicks.append((None, None, options))

    def scroll(self, units):
        self.scrolls.append(units)


@pytest.mark.e2e
def test_speech_keyboard_entry_playback_and_phrase_workflow(qtbot):
    store = FakeLibraryStore()
    speech = FakeSpeech()
    window = SpeechWindow(speech, library_store=store)
    qtbot.addWidget(window)
    window.show()

    window._action_buttons[f"{SPEECH_WINDOW_ACTION_PREFIX}group:1"].click()
    window._action_buttons[f"{SPEECH_WINDOW_ACTION_PREFIX}letter:1:1"].click()
    window._action_buttons[f"{SPEECH_WINDOW_ACTION_PREFIX}space"].click()
    window._action_buttons[f"{SPEECH_WINDOW_ACTION_PREFIX}group:0"].click()
    window._action_buttons[f"{SPEECH_WINDOW_ACTION_PREFIX}letter:0:0"].click()
    window._play_button.click()

    assert window._input.text() == "DŽ A"
    assert speech.requests[0][0] == "DŽ A"

    window._phrases_button.click()
    window._add_item_button.click()
    window._input.setText("  Trebam   pomoć ")
    window._save_item_button.click()

    assert store.saved[-1].phrases == [PhraseRecord("TREBAM POMOĆ", 0)]
    assert window._input.text() == "DŽ A"

    phrase_button = window._action_buttons[f"{SPEECH_WINDOW_ACTION_PREFIX}list:select:0"]
    qtbot.waitUntil(phrase_button.isVisible)
    phrase_button.click()
    assert window._input.text() == "DŽ A TREBAM POMOĆ "
    assert store.saved[-1].phrases == [PhraseRecord("TREBAM POMOĆ", 1)]


@pytest.mark.e2e
def test_speech_message_does_not_silently_truncate_long_saved_phrases(qtbot):
    long_phrase = "Vrlo duga poruka " * 30
    store = FakeLibraryStore(
        SpeechLibrary(categories=default_categories(), phrases=[PhraseRecord(long_phrase)])
    )
    window = SpeechWindow(FakeSpeech(), library_store=store)
    qtbot.addWidget(window)
    window.show()
    window._input.setText("Početak")

    window._append_phrase_to_input(long_phrase)

    assert window._input.text() == f"POČETAK {long_phrase.strip().upper()} "


@pytest.mark.e2e
def test_speech_categories_answers_and_shared_editor_preserve_message(qtbot):
    store = FakeLibraryStore()
    window = SpeechWindow(FakeSpeech(), library_store=store)
    qtbot.addWidget(window)
    window.show()
    window._input.setText("Moja poruka")

    window._categories_button.click()
    assert window._view_mode == "categories"
    assert window._categories_button.text() == "Tastatura"
    assert window._page_label.text() == "1 / 1"
    category_button = window._action_buttons[f"{SPEECH_WINDOW_ACTION_PREFIX}list:select:0"]
    assert category_button.text().startswith("TREBAM\n")
    click_speech_action(qtbot, window, "list:select:0")
    assert window._view_mode == "answers"
    assert window._view_title.text() == "TREBAM"
    assert window._back_button.isVisible()
    answer_button = window._action_buttons[f"{SPEECH_WINDOW_ACTION_PREFIX}list:select:0"]
    assert answer_button.text() == "TREBAM VODE"

    click_speech_action(qtbot, window, "list:select:0")
    assert window._input.text() == "MOJA PORUKA TREBAM VODE "

    window._add_item_button.click()
    assert window._editor.kind == "answer"
    assert window._input.text() == ""
    assert not window._categories_button.isEnabled()
    assert not window._phrases_button.isEnabled()
    window._input.setText("Sedmi odgovor")
    window._save_item_button.click()

    assert window._view_mode == "answers"
    assert window._list_page == 1
    assert window._page_label.text() == "2 / 2"
    assert window._input.text() == "MOJA PORUKA TREBAM VODE "
    assert store.library.categories[0].answers[-1] == "SEDMI ODGOVOR"

    window._add_item_button.click()
    window._input.setText("   ")
    window._save_item_button.click()
    assert window._editor is not None
    assert window._input.text() == "   "
    assert window._status_label.text() == "Prvo unesite tekst."

    window._input.setText("sedmi   odgovor")
    window._save_item_button.click()
    assert window._editor is not None
    assert window._input.text() == "SEDMI   ODGOVOR"
    assert "već postoji" in window._status_label.text()
    window._cancel_editor_button.click()
    assert window._view_mode == "answers"
    assert window._list_page == 1
    assert window._input.text() == "MOJA PORUKA TREBAM VODE "


@pytest.mark.e2e
def test_speech_list_and_dialog_controls_are_large_gaze_targets(qtbot):
    window = SpeechWindow(FakeSpeech(), library_store=FakeLibraryStore())
    qtbot.addWidget(window)
    window.resize(1440, 900)
    window.show()
    qtbot.waitUntil(window.isVisible)

    window._categories_button.click()
    qtbot.waitUntil(window._add_item_button.isVisible)

    list_controls = (
        window._add_item_button,
        window._delete_mode_button,
        window._previous_page_button,
        window._next_page_button,
    )
    assert all(button.width() >= 160 for button in list_controls)
    assert all(button.height() >= 80 for button in list_controls)
    alarm = window._action_buttons[f"{SPEECH_WINDOW_ACTION_PREFIX}alarm:start"]
    assert window._key_grid_host.width() >= alarm.width() * 2
    assert f"{SPEECH_WINDOW_ACTION_PREFIX}close" not in window._action_buttons

    window._input.setText("Poruka")
    window._clear_button.click()
    qtbot.waitUntil(lambda: window._active_dialog is window._confirm_dialog)

    cancel = window._action_buttons[f"{SPEECH_WINDOW_ACTION_PREFIX}confirm:cancel"]
    confirm = window._action_buttons[f"{SPEECH_WINDOW_ACTION_PREFIX}confirm:accept"]
    assert window._confirm_dialog.width() >= 900
    assert window._confirm_dialog.height() >= 440
    assert cancel.height() >= 128
    assert confirm.height() >= 128


@pytest.mark.e2e
def test_speech_add_and_cancel_category_answer_and_phrase_editors(qtbot):
    phrases = [PhraseRecord(f"Fraza {index}") for index in range(7)]
    store = FakeLibraryStore(SpeechLibrary(categories=default_categories(), phrases=phrases))
    window = SpeechWindow(FakeSpeech(), library_store=store)
    qtbot.addWidget(window)
    window.show()
    window._input.setText("Razgovor")

    window._categories_button.click()
    window._add_item_button.click()
    window._input.setText("Nova kategorija")
    window._save_item_button.click()
    assert store.library.categories[-1] == CategoryRecord("NOVA KATEGORIJA")
    assert window._input.text() == "RAZGOVOR"

    window._add_item_button.click()
    window._input.setText("Odbačena kategorija")
    window._cancel_editor_button.click()
    assert all(item.name != "ODBAČENA KATEGORIJA" for item in store.library.categories)
    assert window._input.text() == "RAZGOVOR"

    click_speech_action(qtbot, window, "list:select:0")
    window._add_item_button.click()
    window._input.setText("Odbačeni odgovor")
    window._cancel_editor_button.click()
    assert "ODBAČENI ODGOVOR" not in store.library.categories[0].answers
    assert window._view_mode == "answers"
    assert window._input.text() == "RAZGOVOR"

    window._phrases_button.click()
    window._next_page_button.click()
    assert window._list_page == 1
    window._add_item_button.click()
    window._input.setText("ZZZ nova fraza")
    window._save_item_button.click()
    assert any(item.text == "ZZZ NOVA FRAZA" for item in store.library.phrases)
    assert window._list_page == 1
    assert window._input.text() == "RAZGOVOR"

    window._add_item_button.click()
    window._input.setText("Odbačena fraza")
    window._cancel_editor_button.click()
    assert all(item.text != "ODBAČENA FRAZA" for item in store.library.phrases)
    assert window._view_mode == "phrases"
    assert window._list_page == 1
    assert window._input.text() == "RAZGOVOR"


@pytest.mark.e2e
def test_speech_deletion_confirmation_clear_and_save_failures(qtbot):
    store = FakeLibraryStore(
        SpeechLibrary(
            categories=default_categories(),
            phrases=[PhraseRecord("Sačuvana fraza", 3)],
        )
    )
    window = SpeechWindow(FakeSpeech(), library_store=store)
    qtbot.addWidget(window)
    window.show()
    window._input.setText("Poruka ostaje")

    window._categories_button.click()
    window._delete_mode_button.click()
    click_speech_action(qtbot, window, "list:select:0")
    qtbot.waitUntil(lambda: window._active_dialog is window._confirm_dialog)
    assert "svi njeni odgovori" in window._confirm_copy.text()
    window._action_buttons[f"{SPEECH_WINDOW_ACTION_PREFIX}confirm:cancel"].click()
    qtbot.waitUntil(lambda: window._active_dialog is None)
    assert len(store.library.categories) == 4

    click_speech_action(qtbot, window, "list:select:0")
    window._action_buttons[f"{SPEECH_WINDOW_ACTION_PREFIX}confirm:accept"].click()
    qtbot.waitUntil(lambda: window._active_dialog is None)
    assert [category.name for category in store.library.categories] == [
        "Brzi odgovor",
        "Kako se osjećam",
        "Ljudi",
    ]
    assert window._deletion_mode is True

    window._delete_mode_button.click()
    click_speech_action(qtbot, window, "list:select:0")
    window._delete_mode_button.click()
    click_speech_action(qtbot, window, "list:select:0")
    window._action_buttons[f"{SPEECH_WINDOW_ACTION_PREFIX}confirm:accept"].click()
    qtbot.waitUntil(lambda: window._active_dialog is None)
    assert store.library.categories[0].answers == [
        "Ne",
        "Možda",
        "Hvala",
        "Molim te",
        "Nisam razumio",
    ]

    window._phrases_button.click()
    window._add_item_button.click()
    window._input.setText("Privremeni unos")
    window._clear_button.click()
    qtbot.waitUntil(lambda: window._active_dialog is window._confirm_dialog)
    assert "samo novi unos" in window._confirm_copy.text()
    window._action_buttons[f"{SPEECH_WINDOW_ACTION_PREFIX}confirm:accept"].click()
    assert window._input.text() == ""
    window._cancel_editor_button.click()
    assert window._input.text() == "PORUKA OSTAJE"

    window._delete_mode_button.click()
    click_speech_action(qtbot, window, "list:select:0")
    window._action_buttons[f"{SPEECH_WINDOW_ACTION_PREFIX}confirm:accept"].click()
    qtbot.waitUntil(lambda: window._active_dialog is None)
    assert store.library.phrases == []
    window._delete_mode_button.click()

    store.fail_saves = True
    window._add_item_button.click()
    window._input.setText("Neuspjela fraza")
    window._save_item_button.click()
    assert window._editor is not None
    assert window._input.text() == "NEUSPJELA FRAZA"
    assert window._status_label.text() == "Spremanje nije uspjelo. Novi unos nije sačuvan."
    assert all(item.text != "NEUSPJELA FRAZA" for item in store.library.phrases)


@pytest.mark.e2e
def test_speech_modal_blocks_background_and_supports_gaze(qtbot):
    speech = FakeSpeech()
    window = SpeechWindow(speech, library_store=FakeLibraryStore())
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(window.isVisible)

    assert len(window._letter_groups) == 6
    assert all(len(group) == 5 for group in window._letter_groups)
    assert [letter for group in window._letter_groups for letter in group] == BOSNIAN_LETTERS

    group_action = f"{SPEECH_WINDOW_ACTION_PREFIX}group:1"
    qtbot.mouseClick(window._action_buttons[group_action], Qt.LeftButton)
    qtbot.waitUntil(lambda: window._active_dialog is window._letter_dialog)
    assert window._modal_backdrop.isVisible()

    qtbot.mouseClick(window._play_button, Qt.LeftButton)
    qtbot.mouseClick(window._action_buttons[f"{SPEECH_WINDOW_ACTION_PREFIX}space"], Qt.LeftButton)
    assert speech.requests == []
    assert window._input.text() == ""

    letter_action = f"{SPEECH_WINDOW_ACTION_PREFIX}letter:1:1"
    letter = window._action_buttons[letter_action]
    qtbot.waitUntil(letter.isVisible)
    qtbot.mouseClick(letter, Qt.LeftButton)
    qtbot.waitUntil(lambda: window._active_dialog is None)
    assert window._input.text() == "DŽ"
    assert not window._modal_backdrop.isVisible()

    gaze_group_action = f"{SPEECH_WINDOW_ACTION_PREFIX}group:0"
    group = window._action_buttons[gaze_group_action]
    group_center = group.mapToGlobal(group.rect().center())
    assert window.action_at_global_point(group_center) == gaze_group_action
    window.handle_gaze_action(gaze_group_action)
    qtbot.waitUntil(lambda: window._active_dialog is window._letter_dialog)

    space_center = window._space_button.mapToGlobal(window._space_button.rect().center())
    assert window.action_at_global_point(space_center) is None
    window.handle_gaze_action(f"{SPEECH_WINDOW_ACTION_PREFIX}space")
    assert window._input.text() == "DŽ"

    gaze_letter_action = f"{SPEECH_WINDOW_ACTION_PREFIX}letter:0:0"
    gaze_letter = window._action_buttons[gaze_letter_action]
    qtbot.waitUntil(gaze_letter.isVisible)
    letter_center = gaze_letter.mapToGlobal(gaze_letter.rect().center())
    assert window.action_at_global_point(letter_center) == gaze_letter_action
    window.handle_gaze_action(gaze_letter_action)
    qtbot.waitUntil(lambda: window._active_dialog is None)
    assert window._input.text() == "DŽA"


@pytest.mark.e2e
def test_speech_symbols_backspace_clear_and_play(qtbot):
    speech = FakeSpeech()
    window = SpeechWindow(speech, library_store=FakeLibraryStore())
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(window.isVisible)

    qtbot.mouseClick(window._keyboard_toggle_button, Qt.LeftButton)
    assert window._symbols_mode is True
    symbol = window._action_buttons[f"{SPEECH_WINDOW_ACTION_PREFIX}symbol:0"]
    qtbot.waitUntil(symbol.isVisible)
    qtbot.mouseClick(symbol, Qt.LeftButton)
    assert window._input.text() == "1"

    qtbot.mouseClick(window._keyboard_toggle_button, Qt.LeftButton)
    assert window._symbols_mode is False
    assert f"{SPEECH_WINDOW_ACTION_PREFIX}group:0" in window._action_buttons

    for value in ("DŽ", "LJ", "NJ", "dž", "lj", "nj"):
        window._input.setText(value)
        qtbot.mouseClick(window._backspace_button, Qt.LeftButton)
        assert window._input.text() == ""

    window._input.setText("Trebam pomoć")
    qtbot.mouseClick(window._play_button, Qt.LeftButton)
    assert speech.requests[-1][0] == "TREBAM POMOĆ"
    assert window._input.text() == "TREBAM POMOĆ"

    qtbot.mouseClick(window._clear_button, Qt.LeftButton)
    qtbot.waitUntil(lambda: window._active_dialog is window._confirm_dialog)
    qtbot.mouseClick(
        window._action_buttons[f"{SPEECH_WINDOW_ACTION_PREFIX}confirm:cancel"], Qt.LeftButton
    )
    qtbot.waitUntil(lambda: window._active_dialog is None)
    assert window._input.text() == "TREBAM POMOĆ"

    qtbot.mouseClick(window._clear_button, Qt.LeftButton)
    qtbot.waitUntil(lambda: window._active_dialog is window._confirm_dialog)
    qtbot.mouseClick(
        window._action_buttons[f"{SPEECH_WINDOW_ACTION_PREFIX}confirm:accept"], Qt.LeftButton
    )
    qtbot.waitUntil(lambda: window._active_dialog is None)
    assert window._input.text() == ""

    qtbot.mouseClick(window._clear_button, Qt.LeftButton)
    assert window._active_dialog is None


@pytest.mark.e2e
def test_speech_alarm_stops_speech_blocks_background_and_reports_failure(qtbot):
    speech = FakeSpeech()
    alarm = FakeAlarmSound()
    window = SpeechWindow(speech, library_store=FakeLibraryStore(), alarm_sound=alarm)
    qtbot.addWidget(window)
    window.resize(1280, 720)
    window.show()
    window._input.setText("Poruka ostaje")

    alarm_action = f"{SPEECH_WINDOW_ACTION_PREFIX}alarm:start"
    stop_action = f"{SPEECH_WINDOW_ACTION_PREFIX}alarm:stop"
    window._action_buttons[alarm_action].click()
    qtbot.waitUntil(lambda: window._active_dialog is window._alarm_dialog)

    assert speech.stop_calls == 1
    assert alarm.start_calls == 1
    assert window._alarm_copy.text() == "Zvučni signal se ponavlja dok ga ne zaustavite."
    space_center = window._space_button.mapToGlobal(window._space_button.rect().center())
    assert window.action_at_global_point(space_center) is None
    assert window._input.text() == "PORUKA OSTAJE"

    window.handle_gaze_action(stop_action)
    qtbot.waitUntil(lambda: window._active_dialog is None)
    assert alarm.stop_calls >= 1
    assert window._input.text() == "PORUKA OSTAJE"

    window.handle_gaze_action(alarm_action)
    qtbot.waitUntil(lambda: window._active_dialog is window._alarm_dialog)
    window._action_buttons[stop_action].click()
    qtbot.waitUntil(lambda: window._active_dialog is None)

    alarm.start_result = False
    alarm.last_error = "Audio uređaj nije dostupan."
    window._action_buttons[alarm_action].click()
    qtbot.waitUntil(lambda: window._active_dialog is window._alarm_dialog)
    assert window._alarm_copy.text() == "Audio uređaj nije dostupan."
    assert window._status_label.text() == "Audio uređaj nije dostupan."

    alarm.failed.emit("Zvuk je prekinut.")
    assert window._alarm_copy.text() == "Zvuk je prekinut."
    window._action_buttons[stop_action].click()


@pytest.mark.e2e
def test_speech_sleep_preserves_unfinished_entry_and_supports_mouse_and_gaze(qtbot):
    phrases = [PhraseRecord(f"Fraza {index}") for index in range(7)]
    speech = FakeSpeech()
    window = SpeechWindow(
        speech,
        library_store=FakeLibraryStore(
            SpeechLibrary(categories=default_categories(), phrases=phrases)
        ),
        alarm_sound=FakeAlarmSound(),
    )
    qtbot.addWidget(window)
    window.resize(1280, 720)
    window.show()
    window._input.setText("Razgovor")
    window._phrases_button.click()
    window._next_page_button.click()
    window._add_item_button.click()
    window._input.setText("Nedovršena fraza")
    editor = window._editor

    sleep_action = f"{SPEECH_WINDOW_ACTION_PREFIX}sleep:start"
    wake_action = f"{SPEECH_WINDOW_ACTION_PREFIX}sleep:wake"
    window.handle_gaze_action(sleep_action)
    qtbot.waitUntil(lambda: window._active_dialog is window._sleep_dialog)

    assert speech.stop_calls == 1
    assert window._sleep_dialog.geometry() == QRect(window.mapToGlobal(QPoint(0, 0)), window.size())
    assert window._sleep_dialog.isVisible()
    assert window._input.text() == "NEDOVRŠENA FRAZA"
    assert window._editor is editor
    assert window._list_page == 1
    assert window._view_mode == "editor"
    assert editor is not None and editor.message == "RAZGOVOR"
    space_center = window._space_button.mapToGlobal(window._space_button.rect().center())
    assert window.action_at_global_point(space_center) is None

    qtbot.mouseClick(window._wake_button, Qt.LeftButton)
    qtbot.waitUntil(lambda: window._active_dialog is None)
    assert window._input.text() == "NEDOVRŠENA FRAZA"
    assert window._editor is editor
    assert window._list_page == 1

    window._action_buttons[sleep_action].click()
    qtbot.waitUntil(lambda: window._active_dialog is window._sleep_dialog)
    wake_center = window._wake_button.mapToGlobal(window._wake_button.rect().center())
    assert window.action_at_global_point(wake_center) == wake_action
    window.handle_gaze_action(wake_action)
    qtbot.waitUntil(lambda: window._active_dialog is None)
    assert window._input.text() == "NEDOVRŠENA FRAZA"
    assert window._editor is editor


@pytest.mark.e2e
def test_speech_exit_offers_cancel_leave_speech_and_normal_app_shutdown(qtbot):
    speech = FakeSpeech()
    alarm = FakeAlarmSound()
    window = SpeechWindow(speech, library_store=FakeLibraryStore(), alarm_sound=alarm)
    qtbot.addWidget(window)
    window.resize(1280, 720)
    window.show()
    window._input.setText("Poruka")
    window._categories_button.click()
    click_speech_action(qtbot, window, "list:select:0")
    preserved_state = (
        window._input.text(),
        window._view_mode,
        window._category_index,
        window._list_page,
    )
    quit_requests = []
    window.quit_requested.connect(lambda: quit_requests.append(True))

    exit_action = f"{SPEECH_WINDOW_ACTION_PREFIX}exit"
    cancel_action = f"{SPEECH_WINDOW_ACTION_PREFIX}exit:cancel"
    leave_action = f"{SPEECH_WINDOW_ACTION_PREFIX}exit:leave-speech"
    quit_action = f"{SPEECH_WINDOW_ACTION_PREFIX}exit:quit-app"
    window._action_buttons[exit_action].click()
    qtbot.waitUntil(lambda: window._active_dialog is window._exit_dialog)

    cancel_button = window._action_buttons[cancel_action]
    leave_button = window._action_buttons[leave_action]
    quit_button = window._action_buttons[quit_action]
    cancel_rect = QRect(cancel_button.mapToGlobal(QPoint(0, 0)), cancel_button.size())
    leave_rect = QRect(leave_button.mapToGlobal(QPoint(0, 0)), leave_button.size())
    quit_rect = QRect(quit_button.mapToGlobal(QPoint(0, 0)), quit_button.size())
    assert leave_button.text() == "Izađi"
    assert quit_button.text() == "Ugasi aplikaciju"
    assert all(button.height() >= 128 for button in (cancel_button, leave_button, quit_button))
    assert not cancel_rect.intersects(leave_rect)
    assert not cancel_rect.intersects(quit_rect)
    assert not leave_rect.intersects(quit_rect)
    window.handle_gaze_action(cancel_action)
    qtbot.waitUntil(lambda: window._active_dialog is None)
    assert (
        window._input.text(),
        window._view_mode,
        window._category_index,
        window._list_page,
    ) == preserved_state
    assert quit_requests == []

    window.handle_gaze_action(exit_action)
    qtbot.waitUntil(lambda: window._active_dialog is window._exit_dialog)
    qtbot.mouseClick(leave_button, Qt.LeftButton)
    qtbot.waitUntil(window.isHidden)

    assert quit_requests == []
    assert speech.stop_calls == 1
    assert alarm.stop_calls >= 1

    window.show_full_screen()
    qtbot.waitUntil(window.isVisible)
    assert window._input.text() == "PORUKA"
    window.handle_gaze_action(exit_action)
    qtbot.waitUntil(lambda: window._active_dialog is window._exit_dialog)
    window.handle_gaze_action(quit_action)

    assert quit_requests == [True]
    assert speech.stop_calls == 2
    assert alarm.stop_calls >= 1


@pytest.mark.e2e
def test_keyboard_sidebar_routes_letters_numbers_symbols_and_keys(qtbot):
    window = KeyboardWindow(SpeechSettings(letters_per_group=5))
    qtbot.addWidget(window)
    assert window.windowTitle() == "Tastatura"
    assert [button.text() for button in window._tab_buttons.values()] == [
        "Slova",
        "Brojevi",
        "Znakovi",
    ]
    fake_input = FakeKeyboardInput()
    window._input = fake_input

    window._action_buttons[f"{KEYBOARD_WINDOW_ACTION_PREFIX}group:0"].click()
    window._action_buttons[f"{KEYBOARD_WINDOW_ACTION_PREFIX}letter:0:0"].click()
    window._action_buttons[f"{KEYBOARD_WINDOW_ACTION_PREFIX}tab:numpad"].click()
    window._action_buttons[f"{KEYBOARD_WINDOW_ACTION_PREFIX}numpad_group:0"].click()
    window._action_buttons[f"{KEYBOARD_WINDOW_ACTION_PREFIX}numpad:0"].click()
    window._action_buttons[f"{KEYBOARD_WINDOW_ACTION_PREFIX}tab:symbols"].click()
    window._action_buttons[f"{KEYBOARD_WINDOW_ACTION_PREFIX}symbol_group:0"].click()
    window._action_buttons[f"{KEYBOARD_WINDOW_ACTION_PREFIX}symbol:1"].click()
    window._action_buttons[f"{KEYBOARD_WINDOW_ACTION_PREFIX}backspace"].click()

    assert fake_input.typed == ["A", "7", ","]
    assert fake_input.pressed == ["backspace"]


@pytest.mark.e2e
def test_settings_controls_emit_bounded_updates(qtbot, monkeypatch):
    monkeypatch.setattr("gaze_mouse.settings_window.is_windows_startup_enabled", lambda: False)
    monkeypatch.setattr(
        "gaze_mouse.settings_window.set_application_logging_enabled", lambda _enabled: None
    )
    monkeypatch.setattr(
        "gaze_mouse.settings_window.set_windows_startup_enabled",
        lambda enabled, **_options: StartupTaskResult(enabled, True, "updated"),
    )
    window = SettingsWindow(GazeSettings(), SpeechSettings())
    qtbot.addWidget(window)
    assert window.windowTitle() == "Postavke"
    assert window._general_tab_button.text() == "Opće postavke"
    assert window._gaze_tab_button.text() == "Postavke pogleda"
    assert window._speech_tab_button.text() == "Postavke govora"
    gaze_updates = []
    speech_updates = []
    window.gaze_settings_changed.connect(gaze_updates.append)
    window.speech_settings_changed.connect(speech_updates.append)
    window.show()

    qtbot.mouseClick(window._gaze_tab_button, Qt.LeftButton)
    qtbot.mouseClick(window._move_pointer_button, Qt.LeftButton)
    qtbot.mouseClick(window._precision_zoom_checkbox, Qt.LeftButton)
    for _ in range(100):
        window._adjust_selection_pause(-100)
        window._adjust_dwell_ms(-100)
        window._adjust_smoothing(-0.1)
    qtbot.mouseClick(window._speech_tab_button, Qt.LeftButton)
    for _ in range(100):
        window._adjust_speech_speed(10)
        window._adjust_letters_per_group(1)
    window._cycle_voice_preset()

    assert window._stack.currentIndex() == 2
    assert gaze_updates[-1].move_mouse is False
    assert gaze_updates[-1].use_precision_zoom is False
    assert gaze_updates[-1].selection_pause_ms == 100
    assert gaze_updates[-1].dwell_ms == 150
    assert gaze_updates[-1].smoothing == 0.05
    assert speech_updates[-1].speed == 320
    assert speech_updates[-1].letters_per_group == 12
    assert speech_updates[-1].voice_preset == "human_like"


@pytest.mark.e2e
def test_settings_gaze_waits_before_progress_and_locks_completed_control(qtbot, monkeypatch):
    monkeypatch.setattr("gaze_mouse.settings_window.is_windows_startup_enabled", lambda: False)
    window = SettingsWindow(GazeSettings(selection_pause_ms=250, dwell_ms=200), SpeechSettings())
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(window.isVisible)
    progress = []
    window.interaction_progress_changed.connect(
        lambda _point, value, _label: progress.append(value)
    )
    center = window._gaze_tab_button.mapToGlobal(window._gaze_tab_button.rect().center())
    times = iter((1.0, 1.249, 1.25, 1.45, 3.0, 4.0))
    monkeypatch.setattr("gaze_mouse.settings_window.time.monotonic", lambda: next(times))

    window.handle_gaze(QPoint(center))
    window.handle_gaze(QPoint(center))

    assert progress == []
    assert window._stack.currentIndex() == 0

    window.handle_gaze(QPoint(center))
    window.handle_gaze(QPoint(center))

    assert progress == [0.0, 1.0]

    window.pause_gaze_interaction()
    window.handle_gaze(QPoint(center))

    assert progress == [0.0, 1.0]
    assert window._stack.currentIndex() == 1

    window.handle_gaze(QPoint(center))

    assert progress == [0.0, 1.0]


@pytest.mark.e2e
def test_controller_routes_shortcuts_keyboard_and_quick_settings(qtbot, monkeypatch):
    window = ControllerWindow(GazeSettings(), SpeechSettings())
    qtbot.addWidget(window)
    assert window.windowTitle() == "Upravljač"
    assert [button.text() for button in window._tab_buttons.values()] == [
        "Opće",
        "Tastatura",
        "Govor",
        "Postavke",
    ]
    fake_input = FakeControllerInput()
    window._input = fake_input
    window.set_target_cursor_position((300, 400))
    settings = []
    speech_requests = []
    window.gaze_settings_changed.connect(settings.append)
    window.speech_requested.connect(lambda: speech_requests.append(True))

    window._trigger_action(f"{CONTROLLER_WINDOW_ACTION_PREFIX}left_click", source="mouse")
    window._trigger_action(f"{CONTROLLER_WINDOW_ACTION_PREFIX}double_left_click", source="gaze")
    window._trigger_action(f"{CONTROLLER_WINDOW_ACTION_PREFIX}enter")
    window._trigger_action(f"{CONTROLLER_WINDOW_ACTION_PREFIX}scroll_up")
    window._trigger_action(f"{CONTROLLER_WINDOW_ACTION_PREFIX}tab:keyboard")
    window._trigger_action(f"{CONTROLLER_WINDOW_ACTION_PREFIX}keyboard_group:0")
    window._trigger_action(f"{CONTROLLER_WINDOW_ACTION_PREFIX}keyboard_letter:0:0")
    window._trigger_action(f"{CONTROLLER_WINDOW_ACTION_PREFIX}tab:settings")
    window._trigger_action(f"{CONTROLLER_WINDOW_ACTION_PREFIX}settings:precision_zoom")
    window._trigger_action(f"{CONTROLLER_WINDOW_ACTION_PREFIX}settings:gaze_cursor")
    window._trigger_action(f"{CONTROLLER_WINDOW_ACTION_PREFIX}tab:speech")

    assert fake_input.clicks[0] == (300, 400, {"button": "left", "clicks": 1, "interval": 0.04})
    assert fake_input.clicks[1] == (None, None, {"button": "left", "clicks": 2, "interval": 0.04})
    assert fake_input.pressed == ["enter"]
    assert fake_input.scrolls == [3]
    assert fake_input.typed == ["A"]
    assert settings[-1].use_precision_zoom is False
    assert settings[-1].show_gaze_bubble is False
    assert speech_requests == [True]


class FakeAppBar:
    supported = False

    def register(self, *_args, **_kwargs):
        return False

    def unregister(self):
        return None

    def set_position(self, *_args):
        return None


class FakeHotbarInput(FakeControllerInput):
    def cursor_position(self):
        return 600, 500


class FakeSpeechService(FakeSpeech):
    available = True

    def update_settings(self, settings):
        self._settings = replace(settings)

    def stop(self):
        return None


@pytest.mark.e2e
def test_hotbar_coordinates_primary_ui_surfaces(qtbot, monkeypatch):
    from gaze_mouse import controller_window, keyboard_window, settings_window, toolbar

    monkeypatch.setattr(toolbar, "WindowsAppBar", FakeAppBar)
    monkeypatch.setattr(toolbar, "WindowsInputController", FakeHotbarInput)
    monkeypatch.setattr(toolbar, "SpeechService", FakeSpeechService)
    monkeypatch.setattr(toolbar, "load_app_settings", lambda: (GazeSettings(), SpeechSettings()))
    monkeypatch.setattr(toolbar, "save_app_settings", lambda *_settings: None)
    monkeypatch.setattr(keyboard_window, "WindowsAppBar", FakeAppBar)
    monkeypatch.setattr(keyboard_window, "WindowsInputController", FakeHotbarInput)
    monkeypatch.setattr(controller_window, "WindowsAppBar", FakeAppBar)
    monkeypatch.setattr(controller_window, "WindowsInputController", FakeHotbarInput)
    monkeypatch.setattr(settings_window, "is_windows_startup_enabled", lambda: False)

    window = toolbar.HotbarWindow()
    qtbot.addWidget(window)
    quit_requests = []
    window._quit_application = lambda: quit_requests.append(True)

    assert window._hide_button.text() == "Sakrij"
    assert window._settings_button.text() == "Postavke"
    assert window._quick_actions_button.text() == "Brze radnje"
    assert window._foreground_input is not None
    assert window._last_external_foreground_window == 50
    assert window._last_external_cursor_position == (600, 500)

    window._run_toolbar_action(LEFT_CLICK, checked=True, source="mouse")
    assert window._mouse.active_mode == LEFT_CLICK

    window._run_toolbar_action(QUICK_ACTIONS, checked=True, source="mouse")
    assert window._mouse.quick_actions_enabled is True
    assert window._buttons[LEFT_CLICK].isHidden()

    window._run_toolbar_action(KEYBOARD, checked=True, source="mouse")
    assert window._keyboard_window is not None and window._keyboard_window.isVisible()

    window._run_toolbar_action(CONTROLLER, checked=True, source="mouse")
    assert window._keyboard_window.isHidden()
    assert window._controller_window is not None and window._controller_window.isVisible()

    window._run_toolbar_action(SPEECH, source="mouse")
    assert window._speech_window is not None and window._speech_window.isVisible()
    window._speech_window.quit_requested.emit()
    assert quit_requests == [True]

    window._run_toolbar_action(SETTINGS, source="mouse")
    assert window._controller_window.isHidden()
    assert window._settings_window is not None and window._settings_window.isVisible()
