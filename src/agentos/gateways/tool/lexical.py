"""Built-in lexical ranking adapter for the read-only evidence tool contract."""

from agentos.knowledge.retrieval import retrieve


def search(query: str, candidates: tuple[dict, ...], limit: int) -> list[dict]:
    hits = []
    for match in retrieve(query, candidates, limit):
        source = match.source
        hits.append({"source_id": source["source_id"], "version": source["version"],
                     "location": source["location"], "score": match.score})
    return hits
