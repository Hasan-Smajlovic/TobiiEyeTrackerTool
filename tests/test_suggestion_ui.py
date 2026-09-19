from __future__ import annotations

import copy
from dataclasses import replace

import pytest
from PySide6.QtCore import QObject, QPoint, Signal

from gaze_mouse.mouse_controller import GazeSettings
from gaze_mouse.settings_window import SettingsWindow
from gaze_mouse.speech_library import PhraseRecord, SpeechLibrary, default_categories
from gaze_mouse.speech_service import SpeechSettings
from gaze_mouse.speech_window import SPEECH_WINDOW_ACTION_PREFIX, SpeechWindow
from gaze_mouse.suggestion_learning import WRITE_ERROR, LearningStore
from gaze_mouse.suggestion_model import WordModel
from gaze_mouse.suggestion_service import SuggestionService
from gaze_mouse.suggestion_text import START


class FakeSpeech:
    def __init__(self, *, successful: bool = True) -> None:
        self._settings = SpeechSettings()
        self.successful = successful
        self.requests: list[str] = []

    @property
    def settings(self) -> SpeechSettings:
        return replace(self._settings)

    def speak(self, text: str, _settings: SpeechSettings | None = None) -> bool:
        self.requests.append(text)
        return self.successful

    def stop(self) -> None:
        return None


class FakeLibraryStore:
    def __init__(self) -> None:
        self.library = SpeechLibrary(categories=default_categories())
        self.fail_saves = False

    def load(self) -> SpeechLibrary:
        return copy.deepcopy(self.library)

    def save(self, library: SpeechLibrary) -> bool:
        if self.fail_saves:
            return False
        self.library = copy.deepcopy(library)
        return True


class ManualSuggestionService(QObject):
    predictions_ready = Signal(object, int, object)
    status_changed = Signal(str)
    storage_finished = Signal(bool)

    def __init__(self) -> None:
        super().__init__()
        self.store = LearningStore()
        self.requests: list[tuple[object, int, str]] = []

    @property
    def status(self) -> str:
        return self.store.error

    def request(self, owner: object, revision: int, text: str) -> None:
        self.requests.append((owner, revision, text))

    def persist(self) -> None:
        return None

    def forget(self, word: str) -> None:
        self.store.forget(word)
        successful = self.store.save()
        self.status_changed.emit(self.status)
        self.storage_finished.emit(successful)

    def retry(self) -> None:
        successful = self.store.save(retry=True)
        self.status_changed.emit(self.status)
        self.storage_finished.emit(successful)


@pytest.fixture
def suggestion_service_factory(tmp_path, request):
    services: list[SuggestionService] = []

    def create(counts: dict[tuple[str, ...], int]) -> SuggestionService:
        service = SuggestionService(
            path=tmp_path / f"learning-{len(services)}.json",
            model=WordModel(counts),
        )
        services.append(service)
        return service

    def close_services() -> None:
        for service in services:
            service.close()

    request.addfinalizer(close_services)
    return create


def conversation_model() -> dict[tuple[str, ...], int]:
    return {
        ("želim",): 100,
        ("vodu",): 80,
        ("može",): 70,
        ("hvala",): 60,
        ("razgovarati",): 50,
        (START, "želim"): 1000,
        (START, "hvala"): 900,
        ("želim", "vodu"): 1200,
        (START, "želim", "vodu"): 1400,
    }


@pytest.mark.e2e
def test_suggestions_complete_words_handle_punctuation_and_follow_the_caret(
    qtbot, suggestion_service_factory
):
    service = suggestion_service_factory(conversation_model())
    window = SpeechWindow(FakeSpeech(), library_store=FakeLibraryStore(), suggestions=service)
    qtbot.addWidget(window)
    window.show()

    window._input.setText("zel")
    qtbot.waitUntil(
        lambda: (
            window._prediction_buttons[0].isEnabled()
            and window._prediction_buttons[0].text() == "ŽELIM"
        )
    )
    window._prediction_buttons[0].click()
    assert window._input.text() == "ŽELIM "
    assert window._undo_word_button.isEnabled()

    qtbot.waitUntil(
        lambda: (
            window._prediction_buttons[0].isEnabled()
            and window._prediction_buttons[0].text() == "VODU"
        )
    )
    window._prediction_buttons[0].click()
    assert window._input.text() == "ŽELIM VODU "
    window._play_button.click()
    assert window._undo_word_button.isEnabled()

    window._append_text(".")
    assert window._input.text() == "ŽELIM VODU."
    assert not window._undo_word_button.isEnabled()
    qtbot.waitUntil(lambda: any(button.isEnabled() for button in window._prediction_buttons))

    window._input.setCursorPosition(0)
    assert all(not button.isEnabled() for button in window._prediction_buttons)
    window._input.setCursorPosition(len(window._input.text()))
    qtbot.waitUntil(lambda: any(button.isEnabled() for button in window._prediction_buttons))
    window._input.selectAll()
    assert all(not button.isEnabled() for button in window._prediction_buttons)
    window._input.deselect()
    window._input.setText("qwert")
    assert all(not button.isEnabled() for button in window._prediction_buttons)


@pytest.mark.e2e
def test_stale_results_and_category_editor_results_never_become_selectable(qtbot):
    service = ManualSuggestionService()
    window = SpeechWindow(FakeSpeech(), library_store=FakeLibraryStore(), suggestions=service)
    qtbot.addWidget(window)
    window.show()

    window._input.setText("zel")
    stale_owner, stale_revision, _text = service.requests[-1]
    window._input.setText("mo")
    owner, revision, text = service.requests[-1]
    assert text == "MO"

    service.predictions_ready.emit(stale_owner, stale_revision, ["ŽELIM"])
    assert all(not button.isEnabled() for button in window._prediction_buttons)
    service.predictions_ready.emit(owner, revision, ["MOŽE"])
    assert window._prediction_buttons[0].isEnabled()
    assert window._prediction_buttons[0].text() == "MOŽE"

    window._categories_button.click()
    window._add_item_button.click()
    assert window._editor is not None and window._editor.kind == "category"
    window._input.setText("nova")
    assert all(not button.isEnabled() for button in window._prediction_buttons)
    assert service.requests[-1][2] == "MO"

    window._cancel_editor_button.click()
    category = window._action_buttons[f"{SPEECH_WINDOW_ACTION_PREFIX}list:select:0"]
    qtbot.waitUntil(category.isVisible)
    category.click()
    assert window._view_mode == "answers"
    window._add_item_button.click()
    assert window._editor is not None and window._editor.kind == "answer"
    window._input.setText("zel")
    answer_owner, answer_revision, answer_text = service.requests[-1]
    assert answer_text == "ZEL"
    service.predictions_ready.emit(answer_owner, answer_revision, ["ŽELIM"])
    window._prediction_buttons[0].click()
    assert window._input.text() == "ŽELIM "


@pytest.mark.e2e
def test_dynamic_suggestion_requires_gaze_to_leave_before_same_slot_reactivates(
    qtbot, suggestion_service_factory
):
    service = suggestion_service_factory(conversation_model())
    window = SpeechWindow(FakeSpeech(), library_store=FakeLibraryStore(), suggestions=service)
    qtbot.addWidget(window)
    window.resize(1280, 720)
    window.show()
    window._input.setText("zel")
    button = window._prediction_buttons[0]
    qtbot.waitUntil(lambda: button.isEnabled() and button.text() == "ŽELIM")

    action = f"{SPEECH_WINDOW_ACTION_PREFIX}suggestion:0"
    center = button.mapToGlobal(button.rect().center())
    assert window.action_at_global_point(center) == action
    window.handle_gaze_action(action)
    qtbot.waitUntil(lambda: button.isEnabled() and button.text() == "VODU")
    assert window._input.text() == "ŽELIM "
    assert window.action_at_global_point(center) is None

    input_center = window._input.mapToGlobal(window._input.rect().center())
    assert window.action_at_global_point(input_center) is None
    assert window.action_at_global_point(center) == action


@pytest.mark.e2e
def test_editor_suggestions_are_isolated_and_only_successful_save_learns(
    qtbot, suggestion_service_factory
):
    service = suggestion_service_factory(conversation_model())
    library = FakeLibraryStore()
    window = SpeechWindow(FakeSpeech(), library_store=library, suggestions=service)
    qtbot.addWidget(window)
    window.show()

    window._input.setText("vo")
    qtbot.waitUntil(
        lambda: (
            window._prediction_buttons[0].isEnabled()
            and window._prediction_buttons[0].text() == "VODU"
        )
    )
    window._prediction_buttons[0].click()
    assert service.store.snapshot()[("vodu",)] == 1

    window._phrases_button.click()
    window._add_item_button.click()
    window._input.setText("zel")
    qtbot.waitUntil(
        lambda: (
            window._prediction_buttons[0].isEnabled()
            and window._prediction_buttons[0].text() == "ŽELIM"
        )
    )
    window._prediction_buttons[0].click()
    assert service.store.snapshot()[("želim",)] == 0
    window._cancel_editor_button.click()
    assert window._input.text() == "VODU "
    assert window._undo_word_button.isEnabled()

    window._undo_word_button.click()
    assert window._input.text() == "VO"
    assert service.store.snapshot()[("vodu",)] == 0

    window._add_item_button.click()
    window._input.setText("ŽELIM VODU")
    window._save_item_button.click()
    assert library.library.phrases == [PhraseRecord("ŽELIM VODU")]
    assert service.store.snapshot()[("želim",)] == 1
    assert service.store.snapshot()[("vodu",)] == 1

    library.fail_saves = True
    window._add_item_button.click()
    window._input.setText("hva")
    qtbot.waitUntil(
        lambda: (
            window._prediction_buttons[0].isEnabled()
            and window._prediction_buttons[0].text() == "HVALA"
        )
    )
    window._prediction_buttons[0].click()
    window._save_item_button.click()
    assert service.store.snapshot()[("hvala",)] == 0
    assert window._editor is not None
    assert window._undo_word_button.isEnabled()


@pytest.mark.e2e
def test_speech_failure_still_learns_once_for_an_unchanged_message(
    qtbot, suggestion_service_factory
):
    service = suggestion_service_factory(conversation_model())
    speech = FakeSpeech(successful=False)
    window = SpeechWindow(speech, library_store=FakeLibraryStore(), suggestions=service)
    qtbot.addWidget(window)
    window.show()

    window._input.setText("HVALA")
    window._play_button.click()
    window._play_button.click()

    assert speech.requests == ["HVALA", "HVALA"]
    assert service.store.snapshot()[("hvala",)] == 1


@pytest.mark.e2e
def test_settings_forgets_one_word_and_retries_failed_storage(qtbot, tmp_path, monkeypatch):
    path = tmp_path / "learning.json"
    service = SuggestionService(path=path, model=WordModel(conversation_model()))
    try:
        service.store.learn_text("Želim vodu i čaj")
        assert service.store.save()
        window = SettingsWindow(GazeSettings(), SpeechSettings(), suggestions=service)
        qtbot.addWidget(window)
        window.show()
        window._speech_tab_button.click()
        window._learned_words_button.click()
        assert window._stack.currentIndex() == 3

        index = window._visible_words.index("čaj")
        window._word_buttons[index].click()
        original_write = service.store._write
        monkeypatch.setattr(
            service.store,
            "_write",
            lambda _payload: (_ for _ in ()).throw(OSError("disk full")),
        )
        window._forget_word_button.click()
        qtbot.waitUntil(lambda: not window._learning_busy)
        assert service.store.error == WRITE_ERROR
        assert window._retry_learning_button.isEnabled()
        assert "čaj" not in service.store.learned_words()
        assert LearningStore(path).snapshot()[("čaj",)] == 1

        monkeypatch.setattr(service.store, "_write", original_write)
        window._retry_learning_button.click()
        qtbot.waitUntil(lambda: not window._learning_busy)
        assert service.store.error == ""
        assert ("čaj",) not in LearningStore(path).snapshot()
        assert ("vodu",) in LearningStore(path).snapshot()

        first_visible = window._word_buttons[0]
        center = first_visible.mapToGlobal(first_visible.rect().center())
        assert window._gaze_action_at(QPoint(center)) is not None
    finally:
        service.close()


@pytest.mark.e2e
def test_unavailable_base_model_never_blocks_message_editing_or_speech(qtbot, monkeypatch):
    def unavailable_model():
        raise ValueError("missing model")

    monkeypatch.setattr("gaze_mouse.suggestion_service.load_model", unavailable_model)
    service = SuggestionService()
    speech = FakeSpeech()
    try:
        window = SpeechWindow(speech, library_store=FakeLibraryStore(), suggestions=service)
        qtbot.addWidget(window)
        window.show()
        window._input.setText("HVALA")
        window._play_button.click()

        qtbot.waitUntil(lambda: bool(service.status))
        assert window._input.text() == "HVALA"
        assert speech.requests == ["HVALA"]
        assert all(not button.isEnabled() for button in window._prediction_buttons)
    finally:
        service.close()


@pytest.mark.e2e
def test_unreadable_personal_file_is_preserved_while_base_suggestions_work(qtbot, tmp_path):
    path = tmp_path / "learning.json"
    unreadable = "{not valid json"
    path.write_text(unreadable, encoding="utf-8")
    service = SuggestionService(path=path, model=WordModel(conversation_model()))
    try:
        window = SpeechWindow(FakeSpeech(), library_store=FakeLibraryStore(), suggestions=service)
        qtbot.addWidget(window)
        window.show()
        window._input.setText("zel")

        qtbot.waitUntil(
            lambda: (
                window._prediction_buttons[0].isEnabled()
                and window._prediction_buttons[0].text() == "ŽELIM"
            )
        )
        window._prediction_buttons[0].click()
        qtbot.wait(200)
        assert path.read_text(encoding="utf-8") == unreadable
        assert "nisu učitane" in service.status
    finally:
        service.close()
