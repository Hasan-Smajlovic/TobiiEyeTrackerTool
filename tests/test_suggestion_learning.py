from __future__ import annotations

import json

import pytest

from gaze_mouse.suggestion_composition import Composition
from gaze_mouse.suggestion_learning import READ_ERROR, WRITE_ERROR, LearningStore
from gaze_mouse.suggestion_model import WordModel


def test_undo_after_speech_reverses_only_selection_without_recrediting_typed_words():
    store = LearningStore()
    composition = Composition(store)
    composition.edit("Želim vo")
    composition.select("vodu")
    composition.submit()
    composition.submit()
    assert store.snapshot()[("želim",)] == 1
    assert store.snapshot()[("vodu",)] == 1
    assert composition.undo_selection() == "Želim vo"
    assert store.snapshot()[("vodu",)] == 0
    composition.submit()
    assert store.snapshot()[("želim",)] == 1
    assert store.snapshot()[("vo",)] == 1


def test_manual_correction_reverses_unsubmitted_selection_even_after_undo_expires():
    store = LearningStore()
    composition = Composition(store)
    composition.edit("ŽELIM VO")
    composition.select("voziti")
    composition.edit("ŽELIM VOZITI SADA")
    assert composition.undo is None
    composition.edit("ŽELIM VODU SADA")
    assert not any("voziti" in key for key in store.snapshot())
    composition.submit()
    assert store.snapshot()[("vodu",)] == 1
    assert store.snapshot()[("želim", "vodu")] == 1


def test_sentence_revision_learns_new_occurrences_and_contexts_only():
    store = LearningStore()
    composition = Composition(store)
    composition.edit("ŽELIM VODU")
    composition.submit()
    composition.edit("ŽELIM VODU MOLIM")
    composition.submit()
    assert store.snapshot()[("želim",)] == 1
    assert store.snapshot()[("vodu",)] == 1
    assert store.snapshot()[("molim",)] == 1
    composition.edit("ŽELIM HLADNU VODU MOLIM")
    composition.submit()
    assert store.snapshot()[("vodu",)] == 1
    assert store.snapshot()[("želim", "vodu")] == 1
    assert store.snapshot()[("hladnu", "vodu")] == 1
    composition.edit("")
    composition.edit("ŽELIM VODU")
    composition.submit()
    assert store.snapshot()[("vodu",)] == 2


def test_clearing_and_recomposing_the_same_message_counts_as_a_new_use():
    store = LearningStore()
    composition = Composition(store)
    composition.edit("HVALA")
    composition.submit()
    composition.submit()
    composition.edit("")
    composition.edit("HVALA")
    composition.submit()

    assert store.snapshot()[("hvala",)] == 2


def test_new_selection_after_speech_is_reversed_when_removed():
    store = LearningStore()
    composition = Composition(store)
    composition.edit("HVALA ")
    composition.submit()
    composition.select("lijepo")
    composition.edit("HVALA ")
    assert store.snapshot()[("hvala",)] == 1
    assert store.snapshot()[("lijepo",)] == 0


def test_manual_edit_after_speech_preserves_submitted_use():
    store = LearningStore()
    composition = Composition(store)
    composition.select("hvala")
    composition.submit()
    composition.edit("NE TREBA")
    assert store.snapshot()[("hvala",)] == 1


def test_punctuation_only_removes_automatic_space_and_keeps_word_learning():
    store = LearningStore()
    composition = Composition(store)
    composition.select("hvala")
    assert composition.edit("HVALA .") == "HVALA."
    assert composition.undo is None
    assert store.snapshot()[("hvala",)] == 1
    assert composition.select("molim") == "HVALA. MOLIM "
    assert composition.undo_selection() == "HVALA."
    composition.edit("HVALA  ")
    assert composition.edit("HVALA  .") == "HVALA  ."


@pytest.mark.parametrize("punctuation", [".", ",", "?", "!"])
def test_each_supported_punctuation_removes_only_the_automatic_space(punctuation):
    composition = Composition(LearningStore())
    composition.select("hvala")

    assert composition.edit(f"HVALA {punctuation}") == f"HVALA{punctuation}"
    assert composition.undo is None


def test_undo_has_one_step_and_keeps_previous_selected_word():
    store = LearningStore()
    composition = Composition(store)
    composition.select("želim")
    composition.select("vodu")
    assert composition.undo_selection() == "ŽELIM "
    assert composition.undo_selection() == "ŽELIM "
    assert store.snapshot()[("želim",)] == 1
    assert store.snapshot()[("vodu",)] == 0


def test_editor_draft_and_undo_do_not_learn_but_saved_text_does():
    store = LearningStore()
    editor = Composition(store, learn=False)
    editor.select("dobro")
    editor.select("jutro")
    editor.undo_selection()
    editor.submit()
    assert store.snapshot() == {}
    store.learn_text(editor.text)
    assert store.snapshot()[("dobro",)] == 1
    assert store.snapshot()[("jutro",)] == 0


def test_counts_survive_restart_and_forgetting_removes_related_contexts(tmp_path):
    path = tmp_path / "learned.json"
    store = LearningStore(path)
    store.learn_text("Želim vode. Želim čaj.")
    assert store.save()
    restored = LearningStore(path)
    assert restored.snapshot() == store.snapshot()
    restored.forget("vode")
    assert restored.save()
    final = LearningStore(path)
    assert final.snapshot()[("želim",)] == 2
    assert final.snapshot()[("čaj",)] == 1
    assert not any("vode" in key for key in final.snapshot())
    final.learn_text("Vode")
    assert final.snapshot()[("vode",)] == 1


def test_forgetting_during_undo_does_not_remove_a_later_use():
    store = LearningStore()
    composition = Composition(store)
    composition.select("čaj")
    store.forget("ČAJ")
    store.learn_text("Čaj")
    composition.undo_selection()
    assert store.snapshot()[("čaj",)] == 1


@pytest.mark.parametrize(
    "data", ["{", '{"version": 2, "counts": []}', '{"version": 1, "counts": [[2, 3]]}']
)
def test_unreadable_data_is_never_overwritten_and_retry_merges_new_learning(tmp_path, data):
    path = tmp_path / "learned.json"
    path.write_text(data, encoding="utf-8")
    store = LearningStore(path)
    store.learn_text("Hvala")
    assert store.error == READ_ERROR
    assert not store.save()
    assert not store.save(retry=True)
    assert path.read_text(encoding="utf-8") == data
    path.write_text(json.dumps({"version": 1, "counts": [["čaj", 3]]}), encoding="utf-8")
    assert store.save(retry=True)
    assert LearningStore(path).snapshot()[("čaj",)] == 3
    assert LearningStore(path).snapshot()[("hvala",)] == 1


def test_failed_atomic_write_preserves_disk_and_retry_persists_forgetting(tmp_path, monkeypatch):
    path = tmp_path / "learned.json"
    store = LearningStore(path)
    store.learn_text("Hvala")
    assert store.save()
    original = path.read_bytes()
    store.forget("hvala")
    with monkeypatch.context() as context:
        context.setattr(
            "gaze_mouse.suggestion_learning.os.replace",
            lambda *_: (_ for _ in ()).throw(OSError("disk full")),
        )
        assert not store.save()
    assert store.error == WRITE_ERROR
    assert path.read_bytes() == original
    assert store.save(retry=True)
    assert LearningStore(path).snapshot() == {}


def test_learning_improves_repeated_context_and_adds_unknown_words():
    model = WordModel(
        {("kafu",): 100, ("vodu",): 1, ("želim", "kafu"): 100, ("pijem", "kafu"): 100}
    )
    store = LearningStore()
    for _ in range(6):
        store.learn_text("Želim vodu")
    assert model.predict("Želim ", store.snapshot())[0] == "VODU"
    assert model.predict("Pijem ", store.snapshot())[0] == "KAFU"
    store.learn_text("Zvrković")
    assert model.predict("zvr", store.snapshot()) == ["ZVRKOVIĆ"]
