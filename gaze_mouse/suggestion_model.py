"""Offline word prediction using a sorted prefix index and short word contexts."""

from __future__ import annotations

import gzip
import hashlib
import json
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from functools import lru_cache
from pathlib import Path

from .suggestion_text import START, index_key, prefix_pattern, query, valid_word

MODEL_VERSION = 1
MODEL_PATH = Path(__file__).resolve().parent / "assets" / "bosnian-model.json.gz"
MODEL_METADATA_PATH = MODEL_PATH.with_name("bosnian-model.meta.json")


class WordModel:
    context_discount = 10.0

    def __init__(
        self,
        counts: Mapping[tuple[str, ...], int] | Iterable[tuple[tuple[str, ...], int]],
    ) -> None:
        rows = counts.items() if isinstance(counts, Mapping) else counts
        self.contexts: dict[tuple[str, ...], dict[str, int]] = defaultdict(dict)
        for key, count in rows:
            self.contexts[key[:-1]][key[-1]] = count
        self.vocabulary = self.contexts[()].keys()
        self.index = sorted((index_key(word), word) for word in self.vocabulary)
        self.keys = [item[0] for item in self.index]
        self.totals = {context: sum(values.values()) for context, values in self.contexts.items()}
        self.unigram_ranking = sorted(
            self.vocabulary, key=lambda word: (-self.contexts[()].get(word, 0), word)
        )

    def predict(
        self,
        text: str,
        personal: Counter[tuple[str, ...]] | None = None,
        *,
        limit: int = 5,
        contextual: bool = True,
    ) -> list[str]:
        request = query(text)
        if request is None:
            return []
        prefix, context, _start = request
        learned = personal or Counter()
        pattern = prefix_pattern(prefix)
        if prefix:
            key = index_key(prefix)
            begin, end = bisect_left(self.keys, key), bisect_right(self.keys, key + "\U0010ffff")
            candidates = {word for _key, word in self.index[begin:end] if pattern.match(word)}
        else:
            candidates = set(self.unigram_ranking[:limit])
            if contextual:
                for size in range(1, len(context) + 1):
                    candidates.update(self.contexts.get(context[-size:], {}))
        candidates.update(
            key[0]
            for key, count in learned.items()
            if len(key) == 1 and count > 0 and pattern.match(key[0])
        )
        if not candidates:
            return []

        contexts = [()]
        if contextual:
            contexts.extend(context[-size:] for size in range(1, len(context) + 1))
        personal_rows: dict[tuple[str, ...], dict[str, int]] = {item: {} for item in contexts}
        for key, count in learned.items():
            if key[:-1] in personal_rows and count > 0:
                personal_rows[key[:-1]][key[-1]] = count

        rows = []
        evidence = []
        for preceding in contexts:
            base = self.contexts.get(preceding, {})
            own = personal_rows[preceding]
            total, own_total = self.totals.get(preceding, 0), sum(own.values())
            if not total and not own_total:
                continue
            own_weight = (
                min(0.8, own_total / (own_total + 4))
                if preceding
                else min(0.25, own_total / (own_total + 40))
            )
            distinct = len(base) + sum(word not in base for word in own)
            evidence.append((preceding, total + own_total, distinct))
            rows.append((base, own, max(1, total), max(1, own_total), own_weight))
        weights = self._context_weights(evidence)
        weighted_rows = [(*row, weight) for row, weight in zip(rows, weights, strict=True)]

        def score(word: str) -> tuple[float, str]:
            value = sum(
                weight
                * ((1 - boost) * base.get(word, 0) / total + boost * own.get(word, 0) / own_total)
                for base, own, total, own_total, boost, weight in weighted_rows
            )
            return -value, word

        return [word.upper() for word in sorted(candidates, key=score)[:limit]]

    def _context_weights(self, evidence: list[tuple[tuple[str, ...], int, int]]) -> list[float]:
        weights: list[float] = []
        for _context, total, distinct in evidence:
            # Sparse contexts retain support from shorter contexts. Keep a floor
            # for fallback words even when weighted training counts are large.
            confidence = min(0.9, total / (total + self.context_discount * distinct))
            if not weights:
                weights.append(1.0)
            else:
                weights = [weight * (1 - confidence) for weight in weights]
                weights.append(confidence)
        return weights


def load_model_from_paths(model_path: Path, metadata_path: Path) -> WordModel:
    """Load and validate a prepared model from explicit paths."""
    data = model_path.read_bytes()
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if hashlib.sha256(data).hexdigest() != metadata["model_sha256"]:
        raise ValueError("Prediction model checksum mismatch")
    payload = json.loads(gzip.decompress(data))
    if payload.get("version") != MODEL_VERSION:
        raise ValueError("Unsupported prediction model version")
    rows = payload.get("counts")
    if not rows:
        raise ValueError("Empty prediction model")
    return WordModel(_validated_counts(rows))


def _validated_counts(rows: Iterable[tuple[str, int]]) -> Iterable[tuple[tuple[str, ...], int]]:
    for key, count in rows:
        parts = tuple(key.split(" "))
        if not 1 <= len(parts) <= 3 or not isinstance(count, int) or count <= 0:
            raise ValueError("Invalid prediction count")
        if not all(
            valid_word(word) or (index == 0 and word == START and len(parts) > 1)
            for index, word in enumerate(parts)
        ):
            raise ValueError("Invalid prediction word")
        yield parts, count


@lru_cache(maxsize=1)
def load_model() -> WordModel:
    return load_model_from_paths(MODEL_PATH, MODEL_METADATA_PATH)
