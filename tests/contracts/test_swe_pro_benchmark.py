"""AC-014. The repository-repair pilot preserves the one-shot answer and official reward."""

import json

import pytest

from benchmarks.swe_pro import SOURCE_PATH, candidate_patch, score


def test_ac014_swe_candidate_is_one_cited_file():
    answer = "```python\nvalue = 2\n```\n[E1]"
    patch = candidate_patch("value = 1\n", answer)
    assert patch.startswith(f"--- a/{SOURCE_PATH}\n+++ b/{SOURCE_PATH}\n")
    assert "-value = 1\n+value = 2\n" in patch
    assert candidate_patch("value = 1\n", "value = 2") is None
    assert candidate_patch("value = 1\n", "value = 1\n[E1]") == ""


def test_ac014_swe_breakdown_agrees_with_official_two_group_grade(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({"fail_to_pass": ["repair"], "pass_to_pass": "['existing']"}))
    for name, status, reward in (("baseline", "FAILED", "0"), ("prediction", "PASSED", "1")):
        logs = tmp_path / name
        logs.mkdir()
        (logs / "reward.txt").write_text(reward)
        (logs / "output.json").write_text(json.dumps({"tests": [
            {"name": "repair", "status": status}, {"name": "existing", "status": "PASSED"},
        ]}))
    score(tmp_path)
    report = json.loads((tmp_path / "evaluation.json").read_text())
    assert report["baseline"]["fail_to_pass"]["unpassed"] == {"repair": "FAILED"}
    assert report["prediction"]["fail_to_pass"]["passed"] == report["prediction"]["pass_to_pass"]["passed"] == 1
    (tmp_path / "prediction" / "reward.txt").write_text("0")
    with pytest.raises(ValueError, match="Official reward"):
        score(tmp_path)
