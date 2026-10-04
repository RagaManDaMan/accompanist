"""Your body as an input: heartbeats from a pulse wave, head gestures from motion, and what
the band does with them."""
import math

import numpy as np
import pytest

from accompanist import config as c, simulate
from accompanist.bio import GestureDetector, HeartTracker
from accompanist.controller import Controller
from accompanist.output import RecordingPort, SafeOutput


def pulse_wave(bpm, seconds, rate=64.0, noise=0.05, seed=0):
    t = np.arange(0, seconds, 1 / rate)
    phase = (t * bpm / 60) % 1.0
    beat = np.exp(-((phase - 0.2) ** 2) / 0.004)               # a sharp systolic peak
    rng = np.random.default_rng(seed)
    return t, 1000 + 40 * beat + 15 * np.sin(2 * np.pi * 0.25 * t) + noise * 40 * rng.normal(size=len(t))


def test_heart_rate_from_a_pulse_wave():
    h = HeartTracker()
    t, x = pulse_wave(78, 30)
    for i in range(0, len(t), 16):                             # in packets, as from the headband
        h.process(t[i:i + 16], x[i:i + 16])
    assert h.bpm == pytest.approx(78, abs=2)
    assert len(h.beats) >= 8


def motion(events, seconds=20.0, rate=52.0):
    """Still, with gestures: ('nod', t) a down-up of the head; ('tilt', t, side) two tilts."""
    n = int(seconds * rate)
    t = np.arange(n) / rate
    acc = np.tile([-0.47, 0.0, 0.88], (n, 1))
    gyro = np.zeros((n, 3))
    for ev in events:
        if ev[0] == "nod":
            m = (t >= ev[1]) & (t < ev[1] + 0.8)
            gyro[m, 1] = 70 * np.sin(2 * np.pi * (t[m] - ev[1]) / 0.8)
        elif ev[0] == "tilt":
            for k in range(2):
                s = ev[1] + k * 1.0
                m = (t >= s) & (t < s + 0.7)
                acc[m, 1] = ev[2] * 0.6 * np.sin(np.pi * (t[m] - s) / 0.7)
                gyro[m, 0] = ev[2] * 60 * np.cos(np.pi * (t[m] - s) / 0.7)
        elif ev[0] == "lift":                                  # picking up a sax: one quick lurch
            m = (t >= ev[1]) & (t < ev[1] + 0.15)
            acc[m, 1] = -0.45
            gyro[m, 2] = -100
    return t, acc, gyro


def gestures(events):
    g = GestureDetector()
    t, acc, gyro = motion(events)
    return [(round(float(ti)), e) for ti, a, w in zip(t, acc, gyro) if (e := g.process(ti, a, w))]


def test_nods_and_tilts_are_told_apart_and_counted_once():
    found = gestures([("nod", 2.0), ("nod", 2.9), ("tilt", 7.0, -1), ("tilt", 13.0, +1)])
    assert [e for _, e in found] == ["nod", "tilt_left", "tilt_right"]


def test_lifting_the_sax_is_not_a_gesture():
    assert gestures([("lift", 3.0), ("lift", 9.0)]) == []


def test_gestures_do_their_actions():
    ctl = Controller(c.from_dict({"body": {"nod": "break", "tilt_right": "", "tilt_left": "finish"}}),
                     SafeOutput(RecordingPort()))
    assert ctl.on_body(0.0, "gesture", "nod") == ("break", "(nod) nothing playing to break")
    assert ctl.on_body(0.0, "gesture", "tilt_right")[0] is None
    with pytest.raises(c.ConfigError, match="not an action"):
        c.from_dict({"body": {"nod": "dance"}})


def test_before_the_music_the_band_breathes_with_your_heartbeat():
    cfg = c.from_dict({"drums": {"enabled": True}, "lock": {"auto": False},
                       "harmony": {"keys": "D minor"}, "song": {"tempo": 120, "count": 4}})
    port = RecordingPort()
    ctl = Controller(cfg, SafeOutput(port))
    for k in range(600):                                       # 75 bpm heartbeat, 6 s
        t = k / 100
        if k % 80 == 0:
            ctl.on_body(t, "beat", 75.0)
        ctl.tick(t)
    kicks = [m for m in port.sent if m.type == "note_on" and m.channel == 9 and m.note == 36]
    pad = {m.note % 12 for m in port.sent if m.type == "note_on" and m.channel == 0}
    assert len(kicks) >= 7 and max(m.velocity for m in kicks) <= 40   # soft, every beat
    assert pad >= {2, 9}                                       # D minor's home chord
    ctl.do("song_start", 6.0)                                  # the music begins: it stops
    n = len(kicks)
    ctl.on_body(6.5, "beat", 75.0)
    assert len([m for m in port.sent if m.type == "note_on" and m.channel == 9
                and m.note == 36 and m.velocity <= 40]) == n
