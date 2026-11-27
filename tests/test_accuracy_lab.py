import pytest

from confdeck.evaluation.metrics import metrics
from confdeck.evaluation.protocol import CONDITIONS, protocol
from confdeck.evaluation.replay import replay
from confdeck.evaluation.session import Recorder, load
from confdeck.evaluation.synthetic import generate


def test_deterministic_legacy_and_calibration_isolation():
    session, settings = generate()
    first = replay(session, settings)
    assert first == replay(session, settings)
    assert all(c["mocked"] for c in first["commands"])
    score = metrics(session, first, settings)
    assert sum(score[k] for k in ("correct_rate", "wrong_rate", "no_decision_rate")) == pytest.approx(1)
    assert score["correct_rate"] > .70
    session["tag"] = "calibration"
    with pytest.raises(ValueError, match="never be scored"):
        replay(session, settings)


def test_recorder_round_trip_and_protocol(tmp_path):
    recorder = Recorder(tmp_path, tags=["room-a"], max_bytes=200, keep=20, monotonic_start=123.5)
    for n in range(10):
        recorder.put("mic", n/10, event={"azimuth": n, "elevation": 20})
    recorder.close()
    session = load(tmp_path.glob("*.gz"))
    assert len([r for r in session["rows"] if r["kind"] == "mic"]) == 10
    assert session["monotonic_start"] == 123.5
    trials = protocol(["a", "b"])
    assert trials == protocol(["a", "b"])
    assert set(t["condition"] for t in trials) == set(CONDITIONS)
