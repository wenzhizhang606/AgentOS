"""BM25-style ranking using title boosts over the authorized corpus."""

from collections import Counter
from math import log1p
import re
import unicodedata

from agentos.knowledge.retrieval import _WORDS, _terms


_HAN = re.compile(r"([\u3400-\u4dbf\u4e00-\u9fff]+)")
_TITLE_WEIGHT = 2
_K1 = 0.9
_B = 0.4


def _count_matches(text, query_terms, single_han):
    normalized = unicodedata.normalize("NFKC", text).casefold()
    words = _WORDS.findall(_HAN.sub(r" \1 ", normalized))
    counts = Counter()
    length = 0
    for word in words:
        if "\u3400" <= word[0] <= "\u9fff" and len(word) > 1:
            for offset in range(len(word) - 1):
                term = word[offset:offset + 2]
                if term in query_terms:
                    counts[term] += 1
            for char in word:
                if char in single_han:
                    counts[char] += 1
            length += len(word) - 1
        else:
            if word in query_terms:
                counts[word] += 1
            length += 1
    return counts, length


def search(query: str, candidates: tuple[dict, ...], limit: int) -> list[dict]:
    """Rank immutable source coordinates with bounded term-frequency influence."""
    query_terms = _terms(query)
    if not query_terms:
        return []
    single_han = {term for term in query_terms if len(term) == 1 and "\u3400" <= term <= "\u9fff"}
    scored_rows = []
    frequencies = Counter()
    total_length = 0
    corpus_size = 0
    for source in candidates:
        if not source["text"].strip():
            continue
        title, title_length = _count_matches(source["title"], query_terms, single_han)
        counts, body_length = _count_matches(source["text"], query_terms, single_han)
        for term, count in title.items():
            counts[term] += _TITLE_WEIGHT * count
        length = title_length + body_length
        total_length += length
        corpus_size += 1
        if counts:
            frequencies.update(counts.keys())
            scored_rows.append((source, counts, length))
    if not scored_rows:
        return []
    mean_length = total_length / corpus_size
    idf = {term: log1p((corpus_size - df + 0.5) / (df + 0.5)) for term, df in frequencies.items()}
    ranked = []
    for source, counts, length in scored_rows:
        norm = _K1 * (1 - _B + _B * length / mean_length)
        score = sum(idf[term] * count * (_K1 + 1) / (count + norm)
                    for term, count in sorted(counts.items()))
        coords = source["source_id"], source["version"], source["location"]
        ranked.append((score, coords))
    ranked.sort(key=lambda row: (-row[0], row[1]))
    return [{"source_id": coords[0], "version": coords[1], "location": coords[2], "score": score}
            for score, coords in ranked[:limit]]
