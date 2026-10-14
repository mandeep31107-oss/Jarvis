from app.services.budget import CATEGORIES, estimate_budget
from app.services.designer import quick_project


def test_fourteen_categories_and_disclaimer():
    assert len(CATEGORIES) == 14
    data = quick_project(30, 50, "ft", floors=2, bedrooms=3, bathrooms=3)
    est = estimate_budget(data)
    assert len(est.lines) == 14
    assert est.total_low <= est.total_mid <= est.total_high
    assert "not" in est.disclaimer.lower()
    assert all(not line.verified for line in est.lines)
    assert est.rate_per_sqft_low < est.rate_per_sqft_high


def test_manual_rates_are_respected():
    data = quick_project(30, 50, "ft", floors=1, bedrooms=2, bathrooms=2)
    base = estimate_budget(data)
    data.budget.custom_rates = {cat.key: 100.0 for cat in CATEGORIES}
    data.budget.verified = True
    custom = estimate_budget(data)
    assert custom.total_mid < base.total_mid
