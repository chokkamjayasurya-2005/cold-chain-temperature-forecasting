from backend.services import classify_risk, classify_temperature_deviation, recommendation_for


def test_risk_threshold_boundaries():
    thresholds = {"medium": 20, "high": 40, "critical": 60}
    assert classify_risk(19.99, thresholds) == "LOW"
    assert classify_risk(20, thresholds) == "MEDIUM"
    assert classify_risk(40, thresholds) == "HIGH"
    assert classify_risk(60, thresholds) == "CRITICAL"


def test_recommendation_escalates_for_deviation_or_seal():
    thresholds = {
        "monitor_deviation_c": 1,
        "warning_deviation_c": 2,
        "critical_deviation_c": 4,
        "monitor_seal_integrity": 0.8,
        "warning_seal_integrity": 0.6,
    }
    risk_thresholds = {"medium": 20, "high": 40, "critical": 60}
    assert recommendation_for(0.1, 0.95, 4, risk_thresholds, thresholds).startswith("SAFE")
    assert recommendation_for(1, 0.95, 10, risk_thresholds, thresholds).startswith("MONITOR")
    assert recommendation_for(0.1, 0.59, 10, risk_thresholds, thresholds).startswith("WARNING")
    assert recommendation_for(-4, 1, 0, risk_thresholds, thresholds).startswith("CRITICAL")


def test_temperature_deviation_categories():
    thresholds = {"monitor_deviation_c": 1, "warning_deviation_c": 2}

    assert classify_temperature_deviation(-0.01, thresholds) == "Below Set Point"
    assert classify_temperature_deviation(0.99, thresholds) == "Near Set Point"
    assert classify_temperature_deviation(1, thresholds) == "Slightly Above Set Point"
    assert classify_temperature_deviation(2, thresholds) == "Significantly Above Set Point"
