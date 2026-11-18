"""Score the small, synthetic M1 support fixture against exported observations."""

import json
from pathlib import Path
import re


def _index(rows):
    indexed = {}
    for row in rows:
        case_id = row["case_id"]
        if case_id in indexed:
            raise ValueError(f"Duplicate case {case_id}")
        indexed[case_id] = row
    return indexed


def _refs(rows):
    return {(row["source_id"], row["version"], row["location"]) for row in rows}


def score(cases, observations):
    expected, observed = _index(cases), _index(observations)
    if not expected or observed.keys() - expected.keys():
        raise ValueError("Require a nonempty fixture and observations from that fixture")
    passed = 0
    evidence_scores = []
    details = []
    for case_id, case in expected.items():
        run = observed.get(case_id, {})
        gold = _refs(case["evidence"])
        # The export must refer to the snapshot actually sent, not a retrieval candidate list.
        sent = _refs(run.get("sent_sources", []))
        cited = _refs(run.get("citations", []))
        if gold:
            evidence_scores.append(len(gold & sent) / len(gold))
        if "expected_error" in case:
            ok = run.get("state") == "failed" and run.get("error_code") == case["expected_error"]
        else:
            ok = (run.get("state") == "succeeded" and gold <= cited <= sent
                  and bool(re.fullmatch(case["answer_pattern"], run.get("answer", "").strip())))
        passed += int(ok)
        details.append({"case_id": case_id, "observed": case_id in observed, "passed": ok})
    return {
        "scorer_version": "m1-fixture/1", "cases": len(expected), "observed": len(observed),
        "passed": passed, "fixture_pass_rate": passed / len(expected),
        "mean_final_evidence_coverage": (sum(evidence_scores) / len(evidence_scores)
                                         if evidence_scores else None),
        "evidence_cases": len(evidence_scores), "details": details,
    }


def score_files(cases: Path, observations: Path):
    return score(json.loads(cases.read_text(encoding="utf-8-sig")),
                 json.loads(observations.read_text(encoding="utf-8-sig")))
