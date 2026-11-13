import pytest

from geoagent.evaluation import (
    balanced_sample,
    geoguessr_score,
    request_cost,
    score_prediction,
    summarize,
)


def test_balanced_sample_spreads_over_countries_and_is_deterministic():
    rows = [{"id": str(i), "country": "US"} for i in range(100)]
    rows += [{"id": f"f{i}", "country": "FR"} for i in range(3)] + [{"id": "t", "country": "TW"}]
    sample = balanced_sample(rows, 5, seed=1)
    counts = {c: sum(r["country"] == c for r in sample) for c in ("US", "FR", "TW")}
    assert counts == {"US": 2, "FR": 2, "TW": 1}
    assert sample == balanced_sample(rows, 5, seed=1)
    assert len(balanced_sample(rows[:3], 10)) == 3


def test_request_cost_uses_per_million_prices():
    usage = {"input_tokens": 2000, "output_tokens": 1000}
    assert request_cost(usage, 2.0, 10.0) == pytest.approx(0.014)
    assert request_cost(None, 2.0, 10.0) == 0


def test_score_prediction_and_country_match():
    truth = {"latitude": 25.03, "longitude": 121.56, "country": "TW"}
    exact = score_prediction(truth, {"latitude": 25.03, "longitude": 121.56, "country_code": "tw"})
    assert exact == {"error_km": 0, "country_correct": True, "score": 5000}
    far = score_prediction(truth, {"latitude": 35.68, "longitude": 139.69, "country_code": "JP"})
    assert 2000 < far["error_km"] < 2200 and not far["country_correct"]
    assert geoguessr_score(1492.7) == pytest.approx(5000 / 2.71828, rel=1e-4)


def test_summarize_counts_failures_as_misses():
    results = [
        {"error_km": 10, "country_correct": True, "score": 4967, "cost_usd": 0.01},
        {"error_km": 300, "country_correct": False, "score": 4091, "cost_usd": 0.02},
        {"error_km": None, "error": "timeout"},
    ]
    s = summarize(results)
    assert s["n"] == 3 and s["failed"] == 1
    assert s["median_km"] == 155
    assert s["country_accuracy"] == pytest.approx(1 / 3)
    assert s["within_km"][25] == pytest.approx(1 / 3)
    assert s["within_km"][750] == pytest.approx(2 / 3)
    assert s["cost_usd"] == 0.03
    assert summarize([]) == {"n": 0}
