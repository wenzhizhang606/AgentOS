"""AC-014 score and evidence semantics for the local collaboration pilot."""

from benchmarks.collaboration import CASES, run_collaboration


def _callback(*, failed_label=None, wrong_label=None):
    logged = []

    def run_task(label, question, sources):
        logged.append((label, question, sources))
        _, case_id, role = label.split("-")
        case = next(case for case in CASES if case.case_id == case_id)
        if role in ("left", "right"):
            value = getattr(case, role).expected_value
        else:
            value = case.expected_value
        if label == failed_label:
            return {
                "task_id": label, "state": "failed", "result": None,
                "error_code": "MISSING_EVIDENCE", "limit_micros": 20,
                "settled_micros": 0, "reserved_micros": 0, "accounting_state": "final",
            }
        if label == wrong_label:
            value = "987654"
        citations = [
            {key: source[key] for key in ("source_id", "location")}
            for source in sources if not source["source_id"].endswith("-handoff")
        ]
        return {
            "task_id": label, "attempt": 1, "state": "succeeded",
            "snapshot_id": f"{label}-snapshot", "dispatch_attempt_id": f"{label}-dispatch",
            "result": {"answer": f"VALUE={value} [E1]", "citations": citations},
            "limit_micros": 20, "settled_micros": 7, "reserved_micros": 0,
            "accounting_state": "final", "usage": {"input_tokens": 25, "output_tokens": 4},
        }

    return run_task, logged


def test_ac014_local_collaboration_retains_provenance_ledger_and_isolated_branches():
    callback, calls = _callback()
    report = run_collaboration(callback)

    assert report["single_passed"] == report["team_passed"] == report["cases"] == 4
    assert report["team_final_gain_percentage_points"] == 0
    assert report["single_totals"]["settled_micros_sum"] == 28
    assert report["team_totals"]["settled_micros_sum"] == 84
    assert report["team_totals"]["dispatches"] == 12
    assert [len(sources) for _, _, sources in calls] == [2, 1, 1, 3] * 4
    for row in report["rows"]:
        assert row["single"]["snapshot_id"]
        assert all(node["snapshot_id"] and node["grounded"] for node in row["team"])


def test_ac014_local_collaboration_keeps_failed_cases_and_disallows_unbound_handoffs():
    callback, calls = _callback(failed_label="collab-refund-left")
    report = run_collaboration(callback)

    assert report["team_passed"] == report["team_final_passed"] == 3
    assert report["cases"] == report["single_passed"] == 4
    assert report["team_final_gain_percentage_points"] == -25
    assert "collab-refund-merge" not in {label for label, _, _ in calls}


def test_ac014_local_collaboration_never_uses_gold_to_gate_merge():
    callback, calls = _callback(wrong_label="collab-refund-left")
    report = run_collaboration(callback)

    assert report["team_passed"] == 3
    assert report["team_final_passed"] == 4
    merge_sources = next(sources for name, _, sources in calls if name == "collab-refund-merge")
    assert "987654" in merge_sources[-1]["text"]


def test_ac014_local_collaboration_accepts_punctuation_after_exact_value():
    callback, _ = _callback()

    def with_punctuation(label, question, sources):
        task = callback(label, question, sources)
        if label == "collab-capacity-single":
            task["result"]["answer"] = "VALUE=WAIT. Only 38 items are available. [E1] [E2]"
        return task

    assert run_collaboration(with_punctuation)["single_passed"] == 4
