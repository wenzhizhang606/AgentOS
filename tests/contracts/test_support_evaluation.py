import pytest

from agentos.evaluation.support import score


def test_ac_014_score_includes_missing_and_failed_runs():
    ref = {"source_id": "policy", "version": 2, "location": "p3"}
    cases = [{"case_id": "answer", "answer_pattern": "7 days", "evidence": [ref]},
             {"case_id": "missing", "answer_pattern": "other", "evidence": [ref]},
             {"case_id": "refusal", "expected_error": "MISSING_EVIDENCE", "evidence": []}]
    report = score(cases, [{"case_id": "answer", "answer": "7 days", "state": "succeeded",
                            "citations": [ref], "sent_sources": [ref]},
                           {"case_id": "refusal", "state": "failed", "error_code": "MISSING_EVIDENCE"}])
    assert report["fixture_pass_rate"] == 2 / 3
    assert report["mean_final_evidence_coverage"] == 0.5
    assert report["observed"] == 2


def test_ac_014_reject_duplicate_or_unmatched_observation():
    case = {"case_id": "one", "evidence": [], "expected_error": "MISSING_EVIDENCE"}
    with pytest.raises(ValueError):
        score([case], [case, case])
    with pytest.raises(ValueError):
        score([case], [{"case_id": "typo"}])
