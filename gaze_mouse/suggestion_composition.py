"""Track suggestion undo and learning for one active text input."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from difflib import SequenceMatcher

from .suggestion_learning import Contribution, Key, LearningStore
from .suggestion_text import Word, insert_word, words


@dataclass
class Occurrence:
    word: Word
    credits: dict[Key, Contribution] = field(default_factory=dict)
    submitted: bool = False


@dataclass
class Undo:
    text: str
    occurrences: list[Occurrence]
    selected: Occurrence
    revoked: list[Contribution]
    automatic_space: str | None


class Composition:
    def __init__(self, store: LearningStore, *, learn: bool = True) -> None:
        self.store = store
        self.learn = learn
        self.text = ""
        self.occurrences: list[Occurrence] = []
        self.undo: Undo | None = None
        self.automatic_space: str | None = None

    def edit(self, text: str) -> str:
        if text == self.text:
            return text
        if (
            self.automatic_space == self.text
            and len(text) == len(self.text) + 1
            and text.startswith(self.text)
            and text[-1] in ".,?!"
        ):
            text = self.text[:-1] + text[-1]
        self.undo = None
        self.automatic_space = None
        tokens = words(text)
        old = self.occurrences
        matching = SequenceMatcher(
            a=[item.word.text for item in old], b=[token.text for token in tokens], autojunk=False
        )
        kept = {}
        for block in matching.get_matching_blocks():
            for offset in range(block.size):
                kept[block.b + offset] = block.a + offset
        retained = set(kept.values())
        for index, occurrence in enumerate(old):
            if index not in retained and not occurrence.submitted:
                for credit in occurrence.credits.values():
                    self.store.revoke(credit)
        updated = []
        for index, token in enumerate(tokens):
            occurrence = old[kept[index]] if index in kept else Occurrence(token)
            if not occurrence.submitted:
                for key in list(occurrence.credits):
                    if key not in token.keys:
                        self.store.revoke(occurrence.credits.pop(key))
            occurrence.word = token
            updated.append(occurrence)
        self.occurrences = updated
        self.text = text
        return text

    def select(self, candidate: str) -> str:
        result = insert_word(self.text, candidate)
        if result == self.text:
            return result
        before = self.text
        previous = list(self.occurrences)
        occurrences = [replace(item, credits=item.credits.copy()) for item in self.occurrences]
        active = [
            credit for item in occurrences for credit in item.credits.values() if credit.active
        ]
        automatic_space = self.automatic_space
        self.edit(result)
        selected = self.occurrences[-1]
        for index, item in enumerate(previous):
            if item is not selected and any(item is current for current in self.occurrences):
                occurrences[index] = item
        if self.learn:
            self._credit(selected)
        self.undo = Undo(
            before,
            occurrences,
            selected,
            [credit for credit in active if not credit.active],
            automatic_space,
        )
        self.automatic_space = result
        return result

    def undo_selection(self) -> str:
        undo = self.undo
        if undo is None:
            return self.text
        before_credits = {
            id(credit) for item in undo.occurrences for credit in item.credits.values()
        }
        for credit in undo.selected.credits.values():
            if id(credit) not in before_credits:
                self.store.revoke(credit)
        for credit in undo.revoked:
            self.store.restore(credit)
        self.text = undo.text
        self.occurrences = undo.occurrences
        self.automatic_space = undo.automatic_space
        self.undo = None
        return self.text

    def submit(self) -> None:
        if not self.learn or not self.text.strip():
            return
        for occurrence in self.occurrences:
            self._credit(occurrence)
            occurrence.submitted = True

    def _credit(self, occurrence: Occurrence) -> None:
        for key in occurrence.word.keys:
            if key not in occurrence.credits:
                occurrence.credits[key] = self.store.credit(key)
