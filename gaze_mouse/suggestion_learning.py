"""Reversible local word counts with atomic persistence and explicit recovery."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .suggestion_text import START, spelling, valid_word, words

Key = tuple[str, ...]
READ_ERROR = "Naučene riječi nisu učitane. Datoteka je sačuvana. Pokušajte ponovo u Postavkama."
WRITE_ERROR = "Učenje nije sačuvano. Pokušajte ponovo u Postavkama."


@dataclass
class Contribution:
    key: Key
    generation: tuple[int, ...]
    active: bool = True


class LearningStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._io_lock = threading.Lock()
        self._counts: Counter[Key] = Counter()
        self._pending: Counter[Key] = Counter()
        self._forgotten: set[str] = set()
        self._generations: Counter[str] = Counter()
        self.revision = 0
        self.saved_revision = 0
        self.error = ""
        self._unreadable = False
        try:
            self._counts = self._read()
        except (OSError, ValueError, TypeError, KeyError):
            self._unreadable = True
            self.error = READ_ERROR

    def snapshot(self) -> Counter[Key]:
        with self._lock:
            return self._counts.copy()

    def learned_words(self) -> list[str]:
        with self._lock:
            return sorted(
                key[0] for key, count in self._counts.items() if len(key) == 1 and count > 0
            )

    def credit(self, key: Key) -> Contribution:
        with self._lock:
            self._change(key, 1)
            return Contribution(key, self._generation(key))

    def revoke(self, contribution: Contribution) -> None:
        with self._lock:
            if contribution.active and contribution.generation == self._generation(
                contribution.key
            ):
                self._change(contribution.key, -1)
            contribution.active = False

    def restore(self, contribution: Contribution) -> None:
        with self._lock:
            if not contribution.active and contribution.generation == self._generation(
                contribution.key
            ):
                self._change(contribution.key, 1)
                contribution.active = True

    def learn_text(self, text: str) -> None:
        for token in words(text):
            for key in token.keys:
                self.credit(key)

    def forget(self, word: str) -> None:
        word = spelling(word)
        with self._lock:
            self._generations[word] += 1
            self._forgotten.add(word)
            for key in list(self._counts):
                if word in key:
                    del self._counts[key]
            for key in list(self._pending):
                if word in key:
                    del self._pending[key]
            self.revision += 1

    def save(self, *, retry: bool = False) -> bool:
        """Persist a snapshot without blocking count updates during disk writes."""
        with self._io_lock:
            if self._unreadable:
                if not retry:
                    return False
                try:
                    disk = self._read()
                except (OSError, ValueError, TypeError, KeyError):
                    self.error = READ_ERROR
                    return False
                with self._lock:
                    disk = Counter(
                        {
                            key: count
                            for key, count in disk.items()
                            if not self._forgotten.intersection(key)
                        }
                    )
                    disk.update(self._pending)
                    self._counts = +disk
                    self._unreadable = False
            with self._lock:
                revision = self.revision
                pending = self._pending.copy()
                forgotten = {word: self._generations[word] for word in self._forgotten}
                payload = {
                    "version": 1,
                    "counts": [
                        [" ".join(key), count]
                        for key, count in sorted(self._counts.items())
                        if count > 0
                    ],
                }
            try:
                if self.path is not None:
                    self._write(payload)
            except (OSError, ValueError):
                self.error = WRITE_ERROR
                return False
            with self._lock:
                self.saved_revision = revision
                self._pending.subtract(pending)
                self._pending = Counter(
                    {key: count for key, count in self._pending.items() if count}
                )
                self._forgotten.difference_update(
                    word
                    for word, generation in forgotten.items()
                    if self._generations[word] == generation
                )
                self.error = ""
            return True

    def _generation(self, key: Key) -> tuple[int, ...]:
        return tuple(self._generations[word] for word in key)

    def _change(self, key: Key, delta: int) -> None:
        delta = max(-self._counts[key], delta)
        if delta:
            self._counts[key] += delta
            if not self._counts[key]:
                del self._counts[key]
            self._pending[key] += delta
            self.revision += 1

    def _read(self) -> Counter[Key]:
        if self.path is None or not self.path.exists():
            return Counter()
        if self.path.stat().st_size > 16 * 1024 * 1024:
            raise ValueError("Personal model exceeds the supported size")
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("version") != 1:
            raise ValueError("Unsupported personal data version")
        if not isinstance(payload.get("counts"), list):
            raise ValueError("Invalid personal data")
        counts: Counter[Key] = Counter()
        for row in payload["counts"]:
            if not isinstance(row, list) or len(row) != 2 or not isinstance(row[0], str):
                raise ValueError("Invalid learned entry")
            raw, count = row
            key = tuple(raw.split(" "))
            if not 1 <= len(key) <= 3 or type(count) is not int or not 0 < count <= 2**31 - 1:
                raise ValueError("Invalid learned count")
            if (
                not all(
                    valid_word(word) or (index == 0 and word == START and len(key) > 1)
                    for index, word in enumerate(key)
                )
                or key in counts
            ):
                raise ValueError("Invalid learned word")
            counts[key] = count
        return counts

    def _write(self, payload: dict) -> None:
        assert self.path is not None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=self.path.name + ".",
                suffix=".tmp",
                delete=False,
            ) as stream:
                temporary = Path(stream.name)
                json.dump(payload, stream, ensure_ascii=False, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
