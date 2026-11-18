import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("redact_under_test", Path(__file__).parent.parent / "custom_components/volvo_au/redact.py")
redact = importlib.util.module_from_spec(spec)
spec.loader.exec_module(redact)


def test_redact_removes_secrets_places_and_vin():
    data = {
        "entry": {"refresh_token": "secret", "vin": "YV1ABC", "options": {"idle_interval": 300}},
        "snapshot": {"location": {"latitude": 1.5, "longitude": 2.5, "city": "X"}, "note": "car YV1ABC is ok"},
        "trips": [{"id": "t1", "distance_km": 4.8, "start_place": {"city": "Y"}}],
    }
    out = redact.redact(data, vin="YV1ABC")
    assert out["entry"]["refresh_token"] == redact.REDACTED and out["entry"]["vin"] == redact.REDACTED
    assert out["entry"]["options"] == {"idle_interval": 300}
    assert out["snapshot"]["location"] == {"latitude": redact.REDACTED, "longitude": redact.REDACTED, "city": redact.REDACTED}
    assert out["snapshot"]["note"] == "car <VIN> is ok"
    assert out["trips"][0]["id"] == redact.REDACTED and out["trips"][0]["distance_km"] == 4.8
    assert "YV1ABC" not in str(out)
    assert data["entry"]["refresh_token"] == "secret"  # the input is not changed
