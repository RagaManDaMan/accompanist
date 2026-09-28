import pytest
from accompanist import config as c, simulate


@pytest.fixture(scope="module")
def result():
    return simulate.run(c.from_dict({}), total=105.0)


def test_tempo_tracks_the_performance(result):
    onsets, truth = simulate.scripted_performance()
    # Re-run to the end of the performance and compare with the true tempo.
    res = simulate.run(c.from_dict({}), total=truth[-1][0] + 0.5)
    assert res.engine.tempo.bpm == pytest.approx(truth[-1][1], rel=0.06)


def test_pad_follows_the_harmony_with_lag(result):
    labels = [w for _, w in result.log]
    assert labels[0] == "pad -> D5"           # open fifth first
    assert "pad -> D" in labels               # then the major third arrives
    assert "pad -> Gm" in labels              # follows the modulation to G minor
    change = next(t for t, w in result.log if w == "pad -> G5")
    assert change > 33.0                      # the G phrase begins at ~ beat 32 (t ~ 25s); we lag on purpose


def test_pulse_ran_then_stopped(result):
    pulse_notes = [m for m in result.port.sent if m.type == "note_on" and m.channel == 1]
    assert 40 < len(pulse_notes) < 90
    assert not result.engine.clock.running


def test_pad_released_after_silence_and_nothing_hangs(result):
    assert result.engine.pad.current is None
    assert result.engine.out.sounding == set()


def test_panic_mutes_and_resume_recovers():
    res = simulate.run(c.from_dict({}), total=30.0)
    eng = res.engine
    eng.panic()
    assert eng.muted and eng.out.sounding == set()
    n = len(res.port.sent)
    eng.tick(31.0)
    assert len(res.port.sent) == n            # muted: no output
    eng.resume()
    assert not eng.muted
