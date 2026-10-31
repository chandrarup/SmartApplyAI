from backend.matcher.visa_scorer import calculate_sponsorship_score, sponsorship_badge, sponsorship_knockout_reason


def test_score_formula_is_bounded():
    score = calculate_sponsorship_score(total_cases=20, certified_cases=18, recent_cases=10, matching_domain_cases=8, matching_soc_cases=4)
    assert score == 91.0
    assert 0.0 <= score <= 100.0


def test_badges_have_stable_thresholds():
    assert sponsorship_badge(85) == "strong"
    assert sponsorship_badge(70) == "moderate"
    assert sponsorship_badge(40) == "limited"
    assert sponsorship_badge(0) == "unknown"


def test_knockout_helpers_match_explicit_exclusions():
    assert sponsorship_knockout_reason("US citizens only; no sponsorship available")
    assert sponsorship_knockout_reason("We can sponsor H-1B transfers") is None
