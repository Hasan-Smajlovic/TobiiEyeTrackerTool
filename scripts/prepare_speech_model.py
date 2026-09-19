"""Build compact Bosnian suggestion models from verified language data."""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gaze_mouse.suggestion_text import START, words

SOURCE_MD5 = "f92e72f4fe08362a1297f311ac20ad33"
SOURCE_URL = "https://www.clarin.si/repository/xmlui/bitstream/handle/11356/2079/CLASSLA-web.bs.2.0.jsonl.gz?isAllowed=y&sequence=11"
DEFAULT_VOCABULARY = 60_000
DEFAULT_SAMPLE_MODULUS = 20
DEFAULT_PER_DOMAIN_LIMIT = 240
DEFAULT_NEWS_LIMIT = 3_000
DEFAULT_SUPPLEMENT_MULTIPLIER = 40
DEFAULT_STARTER_MULTIPLIER = 200
MINIMUM_WORD_COUNT = 5
MINIMUM_NGRAM_COUNT = 3
PER_CONTEXT_LIMIT = 32
PER_NGRAM_ORDER_LIMIT = 160_000
GENRES = {
    "Forum",
    "Instruction",
    "Opinion/Argumentation",
    "Information/Explanation",
    "Prose/Lyrical",
    "News",
}
NOISE = {
    "www",
    "http",
    "https",
    "com",
    "org",
    "html",
    "cookie",
    "cookies",
    "javascript",
    "facebook",
    "instagram",
    "twitter",
    "copyright",
    "login",
    "newsletter",
    "the",
    "and",
    "with",
    "this",
    "that",
    "your",
    "you",
    "for",
    "from",
    "have",
    "has",
    "are",
    "was",
    "were",
}


@dataclass(frozen=True)
class PreparedCounts:
    counts: Counter[tuple[str, ...]]
    supplement_words: frozenset[str]
    source_md5: str
    source_sha256: str
    supplement_sha256: str
    starters_sha256: str
    sample: dict
    supplement: dict


def metadata_path(model_path: Path) -> Path:
    suffix = ".json.gz"
    if not model_path.name.endswith(suffix):
        raise ValueError("Model output must end in .json.gz")
    return model_path.with_name(model_path.name[: -len(suffix)] + ".meta.json")


def load_supplement(path: Path) -> tuple[list[tuple[str, int, str]], dict]:
    """Read reviewed conversation rows and reject ambiguous training input."""
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        if reader.fieldnames != ["category", "weight", "text"]:
            raise ValueError("Conversation supplement needs category, weight, and text columns")
        rows = []
        seen = set()
        categories: Counter[str] = Counter()
        weighted_rows: Counter[str] = Counter()
        for line_number, row in enumerate(reader, 2):
            category = row["category"].strip()
            text = row["text"].strip()
            try:
                weight = int(row["weight"])
            except ValueError as error:
                raise ValueError(f"Invalid weight on supplement line {line_number}") from error
            if not category or not text or not 1 <= weight <= 10:
                raise ValueError(f"Invalid supplement row on line {line_number}")
            normalized = " ".join(token.text for token in words(text))
            if not normalized:
                raise ValueError(f"Supplement line {line_number} contains no Bosnian words")
            if normalized in seen:
                raise ValueError(f"Duplicate supplement text on line {line_number}")
            seen.add(normalized)
            rows.append((text, weight, category))
            categories[category] += 1
            weighted_rows[category] += weight
    if not rows:
        raise ValueError("Conversation supplement is empty")
    return rows, {
        "rows": len(rows),
        "categories": dict(sorted(categories.items())),
        "weighted_rows": dict(sorted(weighted_rows.items())),
    }


def _source_hashes(source: Path) -> tuple[str, str]:
    md5, sha = hashlib.md5(), hashlib.sha256()
    with source.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            md5.update(block)
            sha.update(block)
    if md5.hexdigest() != SOURCE_MD5:
        raise ValueError("CLASSLA download does not match the published MD5")
    return md5.hexdigest(), sha.hexdigest()


def prepare_counts(
    source: Path,
    supplement: Path,
    *,
    sample_modulus: int = DEFAULT_SAMPLE_MODULUS,
    per_domain_limit: int = DEFAULT_PER_DOMAIN_LIMIT,
    news_limit: int = DEFAULT_NEWS_LIMIT,
    supplement_multiplier: int = DEFAULT_SUPPLEMENT_MULTIPLIER,
    starter_multiplier: int = DEFAULT_STARTER_MULTIPLIER,
) -> PreparedCounts:
    if sample_modulus < 1 or per_domain_limit < 1 or news_limit < 1:
        raise ValueError("Corpus sampling limits must be positive")
    if supplement_multiplier < 1 or starter_multiplier < 1:
        raise ValueError("Conversation and starter multipliers must be positive")

    source_md5, source_sha256 = _source_hashes(source)
    conversation_rows, supplement_metadata = load_supplement(supplement)
    counts: Counter[tuple[str, ...]] = Counter()
    domains: Counter[str] = Counter()
    genres: Counter[str] = Counter()
    seen: set[str] = set()
    scanned = accepted = token_count = 0
    with gzip.open(source, "rt", encoding="utf-8") as stream:
        for line in stream:
            scanned += 1
            record = json.loads(line)
            if (
                record.get("lang") != "bs"
                or record.get("script", "Latin") != "Latin"
                or record.get("genre") not in GENRES
            ):
                continue
            identifier = hashlib.sha256(record["id"].encode()).digest()
            # Spread the sample across the complete corpus instead of the first domains.
            if int.from_bytes(identifier[:4], "big") % sample_modulus:
                continue
            domain = record["domain"]
            if domains[domain] >= per_domain_limit:
                continue
            if record["genre"] == "News" and genres["News"] >= news_limit:
                continue
            text = record["text"]
            digest = hashlib.sha256(text.encode()).hexdigest()
            if digest in seen:
                continue
            seen.add(digest)
            used = 0
            for paragraph in text.splitlines():
                tokens = words(paragraph)
                if not 4 <= len(tokens) <= 120 or any(token.text in NOISE for token in tokens):
                    continue
                for token in tokens:
                    if len(token.text) == 1 and token.text not in {"a", "i", "o", "s", "u"}:
                        continue
                    counts.update(token.keys)
                    used += 1
                if used >= 500:
                    break
            if used:
                domains[domain] += 1
                genres[record["genre"]] += 1
                accepted += 1
                token_count += used

    supplement_words = set()
    for text, weight, _category in conversation_rows:
        for token in words(text):
            supplement_words.add(token.text)
            for key in token.keys:
                counts[key] += weight * supplement_multiplier

    starters_path = supplement.with_name("starters.tsv")
    # Web headings and dates are poor defaults for starting a conversation.
    for key in list(counts):
        if len(key) == 2 and key[0] == START:
            del counts[key]
    starter_rows = 0
    seen_starters = set()
    with starters_path.open(encoding="utf-8") as stream:
        for row in csv.DictReader(stream, delimiter="\t"):
            word = row["word"].strip().lower()
            weight = int(row["weight"])
            tokens = words(word)
            if (
                not word
                or not 1 <= weight <= 100
                or len(tokens) != 1
                or tokens[0].text != word
                or word in seen_starters
            ):
                raise ValueError(f"Invalid starter row: {row!r}")
            seen_starters.add(word)
            counts[(START, word)] = weight * starter_multiplier
            supplement_words.add(word)
            starter_rows += 1

    return PreparedCounts(
        counts=counts,
        supplement_words=frozenset(supplement_words),
        source_md5=source_md5,
        source_sha256=source_sha256,
        supplement_sha256=hashlib.sha256(supplement.read_bytes()).hexdigest(),
        starters_sha256=hashlib.sha256(starters_path.read_bytes()).hexdigest(),
        sample={
            "records_scanned": scanned,
            "documents": accepted,
            "tokens": token_count,
            "domains": len(domains),
            "genres": dict(genres),
            "id_hash_modulus": sample_modulus,
            "per_domain_limit": per_domain_limit,
            "news_limit": news_limit,
        },
        supplement={**supplement_metadata, "starters": starter_rows},
    )


def write_model(
    prepared: PreparedCounts,
    destination: Path,
    *,
    vocabulary: int,
    supplement_multiplier: int = DEFAULT_SUPPLEMENT_MULTIPLIER,
    starter_multiplier: int = DEFAULT_STARTER_MULTIPLIER,
) -> dict:
    if vocabulary < 1:
        raise ValueError("Vocabulary size must be positive")
    counts = prepared.counts
    common = sorted(
        (
            (count, key[0])
            for key, count in counts.items()
            if len(key) == 1 and count >= MINIMUM_WORD_COUNT and key[0] not in NOISE
        ),
        key=lambda item: (-item[0], item[1]),
    )
    allowed = {word for _count, word in common[:vocabulary]} | set(prepared.supplement_words)
    retained = {(word,): counts[(word,)] for word in allowed}
    contexts: dict[tuple[str, ...], list[tuple[tuple[str, ...], int]]] = defaultdict(list)
    for key, count in counts.items():
        if (
            len(key) > 1
            and count >= MINIMUM_NGRAM_COUNT
            and all(word in allowed or word == START for word in key)
        ):
            contexts[key[:-1]].append((key, count))
    ngrams = []
    for context, rows in contexts.items():
        # Curated starters must survive both the context and global pruning limits.
        if context == (START,):
            retained.update(rows)
        else:
            ngrams.extend(sorted(rows, key=lambda item: (-item[1], item[0]))[:PER_CONTEXT_LIMIT])
    for length in (2, 3):
        retained.update(
            sorted(
                (item for item in ngrams if len(item[0]) == length),
                key=lambda item: (-item[1], item[0]),
            )[:PER_NGRAM_ORDER_LIMIT]
        )
    payload = {
        "version": 1,
        "counts": [[" ".join(key), count] for key, count in sorted(retained.items())],
    }
    encoded = gzip.compress(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), mtime=0
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(encoded)
    metadata = {
        "version": 1,
        "model_id": f"bs-classla-2.0-conversation-3-v{vocabulary}-2026-09-19",
        "source": "CLASSLA-web.bs 2.0",
        "source_url": SOURCE_URL,
        "source_license": "CC0-1.0",
        "source_md5": prepared.source_md5,
        "source_sha256": prepared.source_sha256,
        "supplement_sha256": prepared.supplement_sha256,
        "starters_sha256": prepared.starters_sha256,
        "preparation_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "model_sha256": hashlib.sha256(encoded).hexdigest(),
        "sample": prepared.sample,
        "supplement": prepared.supplement,
        "configuration": {
            "maximum_vocabulary": vocabulary,
            "minimum_word_count": MINIMUM_WORD_COUNT,
            "minimum_ngram_count": MINIMUM_NGRAM_COUNT,
            "per_context_limit": PER_CONTEXT_LIMIT,
            "per_ngram_order_limit": PER_NGRAM_ORDER_LIMIT,
            "supplement_multiplier": supplement_multiplier,
            "starter_multiplier": starter_multiplier,
        },
        "counts": {
            "words": len(allowed),
            "bigrams": sum(len(key) == 2 for key in retained),
            "trigrams": sum(len(key) == 3 for key in retained),
        },
        "compressed_bytes": len(encoded),
    }
    metadata_path(destination).write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return metadata


def build_variants(
    source: Path,
    supplement: Path,
    destinations: dict[int, Path],
    *,
    sample_modulus: int = DEFAULT_SAMPLE_MODULUS,
    per_domain_limit: int = DEFAULT_PER_DOMAIN_LIMIT,
    news_limit: int = DEFAULT_NEWS_LIMIT,
    supplement_multiplier: int = DEFAULT_SUPPLEMENT_MULTIPLIER,
    starter_multiplier: int = DEFAULT_STARTER_MULTIPLIER,
) -> dict[int, dict]:
    prepared = prepare_counts(
        source,
        supplement,
        sample_modulus=sample_modulus,
        per_domain_limit=per_domain_limit,
        news_limit=news_limit,
        supplement_multiplier=supplement_multiplier,
        starter_multiplier=starter_multiplier,
    )
    return {
        vocabulary: write_model(
            prepared,
            destination,
            vocabulary=vocabulary,
            supplement_multiplier=supplement_multiplier,
            starter_multiplier=starter_multiplier,
        )
        for vocabulary, destination in destinations.items()
    }


def build(
    source: Path,
    supplement: Path,
    destination: Path,
    *,
    vocabulary: int = DEFAULT_VOCABULARY,
    sample_modulus: int = DEFAULT_SAMPLE_MODULUS,
    per_domain_limit: int = DEFAULT_PER_DOMAIN_LIMIT,
    news_limit: int = DEFAULT_NEWS_LIMIT,
) -> dict:
    return build_variants(
        source,
        supplement,
        {vocabulary: destination},
        sample_modulus=sample_modulus,
        per_domain_limit=per_domain_limit,
        news_limit=news_limit,
    )[vocabulary]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--supplement", type=Path, default=Path("language/bs/conversation.tsv"))
    parser.add_argument(
        "--output", type=Path, default=Path("gaze_mouse/assets/bosnian-model.json.gz")
    )
    parser.add_argument("--vocabulary", type=int, default=DEFAULT_VOCABULARY)
    parser.add_argument("--sample-modulus", type=int, default=DEFAULT_SAMPLE_MODULUS)
    parser.add_argument("--per-domain-limit", type=int, default=DEFAULT_PER_DOMAIN_LIMIT)
    parser.add_argument("--news-limit", type=int, default=DEFAULT_NEWS_LIMIT)
    args = parser.parse_args()
    print(
        json.dumps(
            build(
                args.source,
                args.supplement,
                args.output,
                vocabulary=args.vocabulary,
                sample_modulus=args.sample_modulus,
                per_domain_limit=args.per_domain_limit,
                news_limit=args.news_limit,
            ),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
