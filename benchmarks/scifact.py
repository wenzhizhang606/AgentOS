"""Paired, model-free BEIR SciFact evaluation of the evidence tool adapters."""

import argparse
from concurrent.futures import ProcessPoolExecutor
import csv
from hashlib import sha256
import io
import json
from math import ceil, fsum, log2
from pathlib import Path
from platform import python_version
from random import Random
from statistics import mean
from time import perf_counter
from zipfile import ZipFile

from agentos.gateways.tool.evidence import execute_evidence
from agentos.gateways.tool.registry import resolve
from agentos.knowledge.rag import RagSettings


_DATASET_SHA = "536e14446a0ba56ed1398ab1055f39fe852686ecad24a6306c80c490fa8e0165"
_IMPLEMENTATIONS = "lexical_search/1", "bm25_search/1"
_TRAIN_SAMPLE_SIZE = 40
_TOP_K = 10


def _jsonl(archive, entry):
    with archive.open("scifact/" + entry) as raw:
        yield from map(json.loads, raw)


def load_data(path, split):
    if sha256(path.read_bytes()).hexdigest() != _DATASET_SHA:
        raise ValueError("SciFact archive does not match the published dataset version")
    with ZipFile(path) as archive:
        corpus = tuple({"source_id": row["_id"], "version": 1, "location": "abstract",
                        "title": row["title"], "text": row["text"]}
                       for row in _jsonl(archive, "corpus.jsonl"))
        queries = {row["_id"]: row["text"] for row in _jsonl(archive, "queries.jsonl")}
        qrels = {}
        with archive.open(f"scifact/qrels/{split}.tsv") as raw:
            for row in csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8"), delimiter="\t"):
                qrels.setdefault(row["query-id"], {})[row["corpus-id"]] = int(row["score"])
    ids = sorted(qrels, key=lambda qid: sha256(f"agentos-scifact-v1:{qid}".encode()).digest())
    if split == "train":
        ids = ids[:_TRAIN_SAMPLE_SIZE]
    return corpus, [(qid, queries[qid], qrels[qid]) for qid in ids]


def measure(ranked, qrels):
    relevant = {doc_id for doc_id, score in qrels.items() if score > 0}
    if not relevant:
        raise ValueError("SciFact qrels must contain a relevant document for each query")

    def discounted_gain(ratings):
        return fsum(rating / log2(position + 2) for position, rating in enumerate(ratings))

    ideal = sorted(qrels.values(), reverse=True)[:_TOP_K]
    actual = [qrels.get(doc_id, 0) for doc_id in ranked[:_TOP_K]]
    return discounted_gain(actual) / discounted_gain(ideal), len(relevant.intersection(ranked[:_TOP_K])) / len(relevant)


def _percentile(values, fraction):
    return sorted(values)[ceil(len(values) * fraction) - 1]


def _difference(before, after):
    deltas = [right - left for left, right in zip(before, after, strict=True)]
    rng = Random(20261118)
    means = [mean(rng.choices(deltas, k=len(deltas))) for _ in range(2000)]
    return {"mean": mean(deltas), "paired_bootstrap_95_ci": [_percentile(means, fraction) for fraction in (0.025, 0.975)]}


def _score_cases(corpus, indexed_cases, digests):
    settings = RagSettings(top_k=_TOP_K)
    samples = []
    for index, (query_id, query, qrels) in indexed_cases:
        sample = {"query_id": query_id}
        # Alternate execution order to reduce run-order effects on latency.
        order = _IMPLEMENTATIONS if index % 2 == 0 else reversed(_IMPLEMENTATIONS)
        for name in order:
            started = perf_counter()
            hits = execute_evidence(name, {"query": query, "limit": _TOP_K}, corpus,
                                    max_hits=_TOP_K, expected_digest=digests[name])
            ranked = [source["source_id"] for source in settings.select(query, hits)]
            latency = (perf_counter() - started) * 1000
            gain, recall = measure(ranked, qrels)
            sample[name] = {"ranked_doc_ids": ranked, "ndcg_at_10": gain,
                            "recall_at_10": recall, "latency_ms": latency}
        samples.append(sample)
    return samples


def run(path, split, workers):
    corpus, cases = load_data(path, split)
    digests = {name: resolve(name).digest for name in _IMPLEMENTATIONS}
    indexed_cases = list(enumerate(cases))
    if workers == 1:
        samples = _score_cases(corpus, indexed_cases, digests)
    else:
        size = ceil(len(cases) / workers)
        with ProcessPoolExecutor(max_workers=workers) as pool:
            batches = [pool.submit(_score_cases, corpus, indexed_cases[i:i + size], digests)
                       for i in range(0, len(cases), size)]
            samples = [sample for batch in batches for sample in batch.result()]

    summary = {}
    for name in _IMPLEMENTATIONS:
        runs = [case[name] for case in samples]
        summary[name] = {"ndcg_at_10": mean(run["ndcg_at_10"] for run in runs),
                         "recall_at_10": mean(run["recall_at_10"] for run in runs),
                         "empty_rankings": sum(not run["ranked_doc_ids"] for run in runs),
                         "p95_ms": _percentile([run["latency_ms"] for run in runs], 0.95)}
    baseline, candidate = _IMPLEMENTATIONS
    return {"dataset": "BEIR/SciFact", "dataset_sha256": _DATASET_SHA, "split": split,
            "python": python_version(), "corpus_size": len(corpus), "query_count": len(samples),
            "model_calls": 0, "top_k": _TOP_K, "min_score": 0.0, "workers": workers,
            "adapter_digests": digests, "summary": summary,
            "paired_delta": {metric: _difference([case[baseline][metric] for case in samples],
                                                  [case[candidate][metric] for case in samples])
                             for metric in ("ndcg_at_10", "recall_at_10")},
            "samples": samples}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--split", choices=("train", "test"), required=True)
    parser.add_argument("--workers", type=int, choices=range(1, 17), default=1,
                        help="Concurrent query processes; use 1 to measure single-process latency")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run(args.archive, args.split, args.workers)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "samples"}, indent=2))


if __name__ == "__main__":
    main()
