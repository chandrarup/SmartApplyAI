from __future__ import annotations

import pytest

from backend.matcher.attenuation import attenuate_match_score, ghost_lambda


def test_ghost_lambda_known_value():
    assert ghost_lambda(90) == pytest.approx(0.7788, abs=0.0001)


def test_live_and_uncertain_scores_are_preserved():
    assert attenuate_match_score(90, "live", age_days=90) == 90
    assert attenuate_match_score(90, "uncertain", age_days=90) == 90


def test_expired_scores_are_attenuated_exponentially():
    assert attenuate_match_score(90, "expired", age_days=90) == pytest.approx(70.09, abs=0.01)
