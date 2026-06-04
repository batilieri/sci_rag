from app.core.webhooks import build_event_envelope


def test_build_event_envelope():
    event = build_event_envelope("query.low_confidence", {"confianca": 0.5}, request_id="req_1")

    assert event["evento"] == "query.low_confidence"
    assert event["request_id"] == "req_1"
    assert event["dados"]["confianca"] == 0.5
