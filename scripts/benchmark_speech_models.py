"""Build and compare Bosnian suggestion model sizes on development data."""

from __future__ import annotations

import argparse
import json
import platform
import sys
import time
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gaze_mouse.suggestion_model import load_model_from_paths
from gaze_mouse.suggestion_text import words
from scripts.evaluate_speech_model import FIXTURES, evaluate
from scripts.prepare_speech_model import build_variants, metadata_path


def _development_words() -> set[str]:
    result = set()
    lines = (FIXTURES / "development.tsv").read_text(encoding="utf-8").splitlines()[1:]
    for line in lines:
        _identifier, _category, text = line.split("\t", 2)
        result.update(token.text for token in words(text))
    return result


def choose_candidate(candidates: list[dict]) -> dict:
    eligible = [row for row in candidates if row["latency"]["p95_ms"] <= 50]
    if not eligible:
        raise ValueError("No candidate meets the 50 ms model latency budget")
    best_activations = min(row["activations"] for row in eligible)
    quality_band = best_activations * 1.005
    return min(
        (row for row in eligible if row["activations"] <= quality_band),
        key=lambda row: (row["development_missing_word_count"], row["compressed_bytes"]),
    )


def benchmark(
    source: Path,
    supplement: Path,
    output_dir: Path,
    vocabularies: list[int],
) -> dict:
    if len(set(vocabularies)) != len(vocabularies) or any(value < 1 for value in vocabularies):
        raise ValueError("Vocabulary sizes must be distinct positive integers")
    destinations = {
        size: output_dir / f"vocabulary-{size}" / "bosnian-model.json.gz" for size in vocabularies
    }
    started = time.perf_counter()
    metadata = build_variants(source, supplement, destinations)
    preparation_seconds = time.perf_counter() - started
    development_words = _development_words()
    candidates = []
    for size in vocabularies:
        model_path = destinations[size]
        result = evaluate(
            "development",
            model_path=model_path,
            metadata_path=metadata_path(model_path),
        )
        model = load_model_from_paths(model_path, metadata_path(model_path))
        missing = sorted(development_words - model.vocabulary)
        candidates.append(
            {
                "requested_vocabulary": size,
                "actual_vocabulary": metadata[size]["counts"]["words"],
                "bigrams": metadata[size]["counts"]["bigrams"],
                "trigrams": metadata[size]["counts"]["trigrams"],
                "compressed_bytes": metadata[size]["compressed_bytes"],
                "development_missing_words": missing,
                "development_missing_word_count": len(missing),
                "loading_ms": result["loading_ms"],
                "latency": result["latency"]["contextual"],
                "activations": result["totals"]["contextual"]["activations"],
                "activation_reduction_percent": result["totals"]["contextual"][
                    "activation_reduction_percent"
                ],
                "next_word_top_one_percent": result["totals"]["contextual"][
                    "next_word_top_one_percent"
                ],
                "next_word_top_five_percent": result["totals"]["contextual"][
                    "next_word_top_five_percent"
                ],
            }
        )

    # Stay comfortably below the 100 ms Windows end-to-end target on the Mac
    # model-only benchmark. Within 0.5% of the best activation count, prefer
    # development coverage and then the smaller release artifact.
    recommended = choose_candidate(candidates)
    return {
        "version": 1,
        "dataset": "development",
        "selection_rule": (
            "p95 model latency at most 50 ms; then within 0.5% of the fewest "
            "activations prefer fewer missing development words and a smaller model"
        ),
        "environment": {"platform": platform.platform(), "python": platform.python_version()},
        "preparation_seconds": round(preparation_seconds, 2),
        "recommended_vocabulary": recommended["requested_vocabulary"],
        "candidates": candidates,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--supplement", type=Path, default=Path("language/bs/conversation.tsv"))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--vocabularies", type=int, nargs="+", default=[20_000, 40_000, 60_000])
    args = parser.parse_args()
    result = benchmark(args.source, args.supplement, args.output_dir, args.vocabularies)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
