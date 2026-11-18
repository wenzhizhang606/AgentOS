"""Fixed lexical retrieval over the source revisions authorized by the store."""

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
import re
import unicodedata

from agentos.contracts.errors import PlatformError


_WORDS = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+|[^\W_]+", re.UNICODE)


def _terms(text: str, single_han: set[str] | None = None) -> set[str]:
    tokens = set()
    # Split Han spans first, even when attached to an ASCII product identifier.
    text = re.sub(r"([\u3400-\u4dbf\u4e00-\u9fff]+)", r" \1 ",
                  unicodedata.normalize("NFKC", text).casefold())
    for word in _WORDS.findall(text):
        if "\u3400" <= word[0] <= "\u9fff":
            tokens.update(word[i:i + 2] for i in range(len(word) - 1))
            if len(word) == 1:
                tokens.add(word)
            elif single_han:
                tokens.update(ch for ch in word if ch in single_han)
        else:
            tokens.add(word)
    return tokens


@dataclass(frozen=True)
class RankedSource:
    source: dict
    score: float


def retrieve(query: str, candidates: Iterable[dict], limit: int = 10) -> list[RankedSource]:
    """Rank title and body matches; ties use immutable source coordinates."""
    query_terms = _terms(query)
    single_han = {t for t in query_terms if len(t) == 1 and "\u3400" <= t <= "\u9fff"}
    rows = {}
    frequencies = Counter()
    for source in candidates:
        key = (source.get("source_id"), source.get("version"), source.get("location"))
        if (not isinstance(key[0], str) or not key[0] or type(key[1]) is not int
                or key[1] < 1 or not isinstance(key[2], str) or not key[2]
                or not isinstance(source.get("text"), str)
                or not isinstance(source.get("title", ""), str)):
            raise PlatformError("CONTEXT_STALE", "Source revision lacks valid content or provenance")
        if key in rows:
            if rows[key][0] != source:
                raise PlatformError("CONTEXT_STALE", "Conflicting copies of a source revision")
            continue
        title_hits = query_terms & _terms(source.get("title", ""), single_han)
        body_hits = query_terms & _terms(source["text"], single_han)
        rows[key] = (source, title_hits, body_hits)
        frequencies.update(title_hits | body_hits)

    ranked = []
    for key, (source, title_hits, body_hits) in rows.items():
        # Rare matches carry more weight in the authorized corpus.
        score = sum(1 / frequencies[t] for t in sorted(body_hits))
        score += sum(2 / frequencies[t] for t in sorted(title_hits))
        if score > 0 and source["text"].strip():
            ranked.append((score, key, source))
    ranked.sort(key=lambda row: (-row[0], row[1]))
    return [RankedSource(source, score) for score, _, source in ranked[:limit]]
