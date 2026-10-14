from app.services.units import (format_area, format_length, m2_to_sqft, parse_dimension,
                                rate_m2_to_sqft, sqft_to_m2)


def test_round_trip_ft_m():
    assert abs(sqft_to_m2(m2_to_sqft(100.0)) - 100.0) < 1e-6


def test_rate_conversion_divides():
    # a rate per m2 must become a *smaller* number per sqft
    assert rate_m2_to_sqft(2000.0) < 2000.0 / 5


def test_parse_dimension():
    assert parse_dimension("3.5m") == 3.5
    assert abs(parse_dimension("10 ft") - 3.048) < 1e-6
    assert parse_dimension("10'6\"") is not None


def test_format_helpers():
    assert "m" in format_length(3.0, "m")
    assert "sq" in format_area(12.0, "sqft") or "ft" in format_area(12.0, "sqft")
