from dataclasses import replace

import pytest

from agentos.control.release import Release


@pytest.fixture
def profile():
    return Release("https://model.example/v1/chat/completions", "fixed-model", 8192,
                   512, 128, 10_000, 40_000, "metadata_only")


def test_ac_010_reconfiguration_changes_pinned_release(profile):
    assert profile.version_id == replace(profile).version_id
    assert len(profile.manifest["components_sha256"]["knowledge/retrieval.py"]) == 64
    for update in ({"model": "other"}, {"input_micros_per_million": 2}, {"retention_mode": "full"}):
        assert profile.version_id != replace(profile, **update).version_id


def test_ac_009_price_rounds_once_in_integer_micro_units(profile):
    assert profile.cost(1, 1) == 1
    assert profile.cost(0, 0) == 0
    assert profile.reservation_micros >= profile.cost(8192, 512)
    with pytest.raises(ValueError):
        profile.cost(-1, 0)
