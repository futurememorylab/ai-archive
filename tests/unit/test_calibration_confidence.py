from backend.app.services.calibration import confidence_for_samples


def test_confidence_thresholds():
    assert confidence_for_samples(0) == "rough"
    assert confidence_for_samples(2) == "rough"
    assert confidence_for_samples(3) == "fair"
    assert confidence_for_samples(9) == "fair"
    assert confidence_for_samples(10) == "good"
