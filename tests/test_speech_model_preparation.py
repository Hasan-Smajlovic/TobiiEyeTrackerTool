from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path

import pytest

from gaze_mouse.suggestion_model import load_model_from_paths
from gaze_mouse.suggestion_text import START, words
from scripts.benchmark_speech_models import choose_candidate
from scripts.prepare_speech_model import PreparedCounts, load_supplement, metadata_path, write_model


def test_curated_starters_survive_context_and_global_pruning(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    with (root / "language/bs/starters.tsv").open(encoding="utf-8") as stream:
        starters = {
            row["word"]: int(row["weight"]) * 200 for row in csv.DictReader(stream, delimiter="\t")
        }
    counts = Counter({(word,): 100 for word in starters})
    counts.update({(START, word): count for word, count in starters.items()})
    counts[("ja", "selam")] = 100_000
    prepared = PreparedCounts(counts, frozenset(starters), "", "", "", "", {}, {})
    monkeypatch.setattr("scripts.prepare_speech_model.PER_NGRAM_ORDER_LIMIT", 1)
    destination = tmp_path / "candidate.json.gz"

    write_model(prepared, destination, vocabulary=60_000)
    model = load_model_from_paths(destination, metadata_path(destination))

    assert model.contexts[(START,)] == starters
    assert model.predict("POM")[0] == "POMOZI"
    assert model.predict("ZAT")[0] == "ZATVORI"


def test_structured_conversation_data_records_category_weights(tmp_path):
    supplement = tmp_path / "conversation.tsv"
    supplement.write_text(
        "category\tweight\ttext\nneeds\t8\tTreba mi vode.\nconversation\t2\tKako si?\n",
        encoding="utf-8",
    )

    rows, details = load_supplement(supplement)

    assert rows == [("Treba mi vode.", 8, "needs"), ("Kako si?", 2, "conversation")]
    assert details == {
        "rows": 2,
        "categories": {"conversation": 1, "needs": 1},
        "weighted_rows": {"conversation": 2, "needs": 8},
    }


@pytest.mark.parametrize(
    "contents",
    [
        "text\tweight\tcategory\nTreba mi vode.\t8\tneeds\n",
        "category\tweight\ttext\nneeds\t0\tTreba mi vode.\n",
        "category\tweight\ttext\nneeds\t8\tTreba mi vode.\nneeds\t5\tTreba mi vode!\n",
    ],
)
def test_structured_conversation_data_rejects_ambiguous_rows(tmp_path, contents):
    supplement = tmp_path / "conversation.tsv"
    supplement.write_text(contents, encoding="utf-8")

    with pytest.raises(ValueError):
        load_supplement(supplement)


def test_model_metadata_path_follows_variant_name():
    assert metadata_path(Path("candidate-40000.json.gz")) == Path("candidate-40000.meta.json")
    with pytest.raises(ValueError):
        metadata_path(Path("candidate.json"))


def test_conversation_supplement_is_separate_from_evaluation_fixtures():
    root = Path(__file__).resolve().parents[1]
    supplement, _details = load_supplement(root / "language" / "bs" / "conversation.tsv")
    supplement_text = {
        " ".join(token.text for token in words(text)) for text, _weight, _ in supplement
    }
    fixtures = root / "tests" / "fixtures" / "speech_suggestions"

    for dataset in ("development", "heldout"):
        rows = (fixtures / f"{dataset}.tsv").read_text(encoding="utf-8").splitlines()[1:]
        evaluation_text = {
            " ".join(token.text for token in words(line.split("\t", 2)[2])) for line in rows
        }
        assert supplement_text.isdisjoint(evaluation_text)


def test_benchmark_prefers_coverage_and_size_inside_quality_band():
    candidates = [
        {
            "requested_vocabulary": 20_000,
            "activations": 1000,
            "development_missing_word_count": 2,
            "compressed_bytes": 500_000,
            "latency": {"p95_ms": 20},
        },
        {
            "requested_vocabulary": 40_000,
            "activations": 998,
            "development_missing_word_count": 0,
            "compressed_bytes": 700_000,
            "latency": {"p95_ms": 30},
        },
        {
            "requested_vocabulary": 60_000,
            "activations": 997,
            "development_missing_word_count": 0,
            "compressed_bytes": 900_000,
            "latency": {"p95_ms": 60},
        },
    ]

    assert choose_candidate(candidates)["requested_vocabulary"] == 40_000


def test_benchmark_rejects_candidates_that_all_exceed_latency_budget():
    with pytest.raises(ValueError, match="latency budget"):
        choose_candidate([{"latency": {"p95_ms": 51}}])
