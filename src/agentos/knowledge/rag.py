"""Release-scoped selection of authorized hits returned by the evidence adapter."""

from collections import Counter
from dataclasses import dataclass
from math import isfinite

from agentos.gateways.tool.evidence import MAX_HITS
from agentos.knowledge.retrieval import RankedSource, _terms


@dataclass(frozen=True)
class RagSettings:
    top_k: int = MAX_HITS
    min_score: float = 0.0
    title_weight: float = 2.0
    body_weight: float = 1.0

    def __post_init__(self):
        if type(self.top_k) is not int or not 1 <= self.top_k <= MAX_HITS:
            raise ValueError(f"rag.top_k must be an integer from 1 to {MAX_HITS}")
        for name in ("min_score", "title_weight", "body_weight"):
            value = getattr(self, name)
            if type(value) not in (int, float):
                raise ValueError(f"rag.{name} must be a finite nonnegative number")
            try:
                value = float(value)
            except OverflowError as exc:
                raise ValueError(f"rag.{name} must be finite") from exc
            if not isfinite(value) or value < 0:
                raise ValueError(f"rag.{name} must be a finite nonnegative number")
            object.__setattr__(self, name, value)
        if self.title_weight == self.body_weight == 0:
            raise ValueError("At least one lexical field weight must be positive")

    @property
    def rerank(self):
        return (self.title_weight, self.body_weight) != (2.0, 1.0)

    def select(self, question: str, hits: list[RankedSource]) -> list[dict]:
        if not self.rerank:
            return [hit.source for hit in hits if hit.score > 0 and hit.score >= self.min_score][:self.top_k]

        # Frequency is counted once per candidate, including terms found in both fields.
        query_terms = _terms(question)
        single_han = {term for term in query_terms if len(term) == 1 and "\u3400" <= term <= "\u9fff"}
        frequencies = Counter()
        matched = []
        for hit in hits:
            title = query_terms & _terms(hit.source.get("title", ""), single_han)
            body = query_terms & _terms(hit.source["text"], single_han)
            frequencies.update(title | body)
            matched.append((hit, title, body))

        # Compare in common scaled units so even large valid weights cannot overflow.
        scale = max(self.title_weight, self.body_weight)
        title_weight, body_weight = self.title_weight / scale, self.body_weight / scale
        minimum = self.min_score / scale
        ranked = []
        for hit, title, body in matched:
            score = sum(title_weight / frequencies[term] for term in sorted(title))
            score += sum(body_weight / frequencies[term] for term in sorted(body))
            if score > 0 and score >= minimum:
                key = (hit.source["source_id"], hit.source["version"], hit.source["location"])
                ranked.append((score, key, hit))
        ranked.sort(key=lambda row: (-row[0], row[1]))
        return [hit.source for _, _, hit in ranked[:self.top_k]]
