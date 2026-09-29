import pytest

from accompanist import config as c
from accompanist.controller import Controller
from accompanist.dynamics import Dynamics
from accompanist.output import RecordingPort, SafeOutput


def dyn(**cfg):
    return Dynamics(c.from_dict({"dynamics": cfg}).dynamics)


def test_busyness_rises_with_notes_and_fades_in_silence():
    d = dyn(memory_s=2, busy_notes_per_s=4)
    for i in range(40):
        d.observe(i * 0.2, 80)                     # 5 notes/s
    assert d.busyness(8.0) > 0.9
    assert d.busyness(16.0) < 0.1                  # 8 s of silence, 4 half-lives


def test_follow_gain_tracks_loudness_and_can_be_turned_off():
    d = dyn(follow=1.0, reference_velocity=80)
    for i in range(10):
        d.observe(i * 0.5, 40)
    assert d.follow_gain() == pytest.approx(0.5)
    assert dyn(follow=0.0).follow_gain() == 1.0


def test_pad_steps_back_when_busy_but_not_below_the_floor():
    d = dyn(duck=1.0, pad_floor=0.25, follow=0.0)
    quiet = d.pad_level(0.0)
    for i in range(80):
        d.observe(i * 0.1, 80)
    assert d.pad_level(8.0) == pytest.approx(0.25)
    assert quiet > 0.7


def pad_expressions(port, channel=0):
    return [m.value for m in port.sent if m.type == "control_change" and m.control == 11
            and m.channel == channel]


def run(ctl, notes, until, start=0.0, dt=0.01):
    i, now = 0, start
    while now <= until:
        while i < len(notes) and notes[i][0] <= now:
            ctl.on_note(*notes[i])
            i += 1
        ctl.tick(now)
        now = round(now + dt, 6)


def test_engine_rides_the_pad_on_cc11_and_bass_velocity_follows_you():
    port = RecordingPort()
    ctl = Controller(c.from_dict({"harmony": {"root": "D"}, "lock": {"auto": False},
                                  "dynamics": {"duck": 1.0, "follow": 1.0}}), SafeOutput(port))
    sparse = [(i * 1.0, 62, 110) for i in range(10)]                 # loud, 1 note/s
    run(ctl, sparse, 10.0)
    calm = pad_expressions(port)[-1]
    busy = [(10.0 + i * 0.1, 62 + i % 5, 110) for i in range(60)]  # 10 notes/s
    run(ctl, busy, 16.0, start=10.01)
    assert pad_expressions(port)[-1] < calm - 20                     # stepped back
    run(ctl, [], 30.0, start=16.01)
    assert pad_expressions(port)[-1] > pad_expressions(port)[-2]     # comes forward in the pause
    bass = [m.velocity for m in port.sent if m.type == "note_on" and m.channel == 1]
    assert bass and max(bass) > 45 + 25                              # louder than set: you played loud


def test_expression_can_be_turned_off():
    port = RecordingPort()
    ctl = Controller(c.from_dict({"pad": {"expression_cc": None}}), SafeOutput(port))
    run(ctl, [(0.0, 62, 90)], 2.0)
    assert pad_expressions(port) == []
