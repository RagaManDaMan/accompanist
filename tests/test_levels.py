"""accompanist levels: measuring each voice alone and trimming it under your instrument."""
import numpy as np
import pytest

from accompanist import config as c, levels as lv

RATE = 8000.0


def cfg():
    return c.from_dict({"drums": {"enabled": True}, "piano": {"enabled": True},
                        "response": {"enabled": True}, "percussion": {"enabled": False}})


def synthetic(parts, voice_db, you_db, noise_db=-80.0):
    """A recording of the plan: lead-in silence, each part a tone at its level, you at yours."""
    rng = np.random.default_rng(0)
    total = lv.LEAD_IN_S + parts[-1].start + lv.YOU_S + 1
    x = rng.normal(0, 10 ** (noise_db / 20), int(total * RATE))
    for p in parts:
        db = you_db if p.voice == "you" else voice_db.get(p.voice)
        if db is None:
            continue
        length = lv.YOU_S if p.voice == "you" else lv.PART_S
        a = int((lv.LEAD_IN_S + p.start) * RATE)
        t = np.arange(int(length * RATE)) / RATE
        x[a:a + len(t)] += np.sqrt(2) * 10 ** (db / 20) * np.sin(2 * np.pi * 220 * t)
    return x.astype(np.float32)


def test_each_voice_is_measured_and_trimmed_to_its_target():
    conf = cfg()
    parts = lv.plan(conf)
    assert [p.voice for p in parts] == ["pad", "bass", "drums", "piano", "guitar", "you"]
    levels = {"pad": -20.0, "bass": -30.0, "drums": -14.0, "piano": -25.0, "guitar": -40.0}
    measured = lv.measure(synthetic(parts, levels, -16.0), RATE, parts)
    for v, db in levels.items():
        assert measured[v] == pytest.approx(db, abs=0.5)
    assert measured["you"] == pytest.approx(-16.0, abs=0.5)
    new = lv.trims(measured, conf)
    assert new["pad"] == pytest.approx(-16 - 12 + 20, abs=0.5)            # pad 12 dB under you
    assert new["guitar"] == 4.0                                          # can't go above +4


def test_a_silent_voice_is_reported_not_trimmed():
    conf = cfg()
    parts = lv.plan(conf)
    measured = lv.measure(synthetic(parts, {"pad": -20.0, "bass": -20.0, "drums": -20.0,
                                            "piano": -20.0}, -16.0), RATE, parts)
    assert measured["guitar"] is None and lv.trims(measured, conf)["guitar"] is None
    assert any("no sound" in line for line in lv.describe(measured, lv.trims(measured, conf), conf))


def test_trims_are_written_into_the_song_file_and_load():
    text = '[song]\ntitle = "X"\n\n[mix]\ntarget_pad_db = -10\npad_db = 2\n\n[drums]\nenabled = true\n'
    new = lv.update_song(text, {"pad": -3.5, "bass": 1.0})
    data = c.tomllib.loads(new)
    assert data["mix"] == {"target_pad_db": -10, "pad_db": -3.5, "bass_db": 1.0}
    assert data["drums"]["enabled"] is True
    plain = lv.update_song('[song]\ntitle = "Y"\n', {"pad": -2.0})
    assert c.tomllib.loads(plain)["mix"]["pad_db"] == -2.0


def test_cc7_follows_the_general_midi_curve():
    assert lv.cc7_value(0) == lv.CC7_UNITY
    assert lv.cc7_value(-12) == round(100 * 10 ** (-12 / 40))
    assert lv.cc7_value(10) == 127


def test_levels_command_measures_a_recording_and_writes_the_song(tmp_path, monkeypatch):
    import wave

    from accompanist import cli

    monkeypatch.chdir(tmp_path)
    (tmp_path / "songs").mkdir()
    (tmp_path / "songs" / "tune.toml").write_text(
        '[song]\ntitle = "Tune"\n\n[drums]\nenabled = true\n\n[piano]\nenabled = true\n\n'
        '[response]\nenabled = true\n\n[percussion]\nenabled = false\n')
    (tmp_path / "config.toml").write_text("")
    conf = c.load(str(tmp_path / "config.toml"), song="tune")
    parts = lv.plan(conf)
    x = synthetic(parts, {"pad": -20.0, "bass": -30.0, "drums": -14.0, "piano": -25.0,
                          "guitar": -22.0}, -16.0)
    path = tmp_path / "check.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(int(RATE))
        w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())
    assert cli.main(["levels", "--song", "tune", "--file", str(path), "--write"]) == 0
    mix = c.tomllib.loads((tmp_path / "songs" / "tune.toml").read_text())["mix"]
    assert mix["pad_db"] == pytest.approx(-16 - 12 + 20, abs=0.5)
    assert c.load(str(tmp_path / "config.toml"), song="tune").mix.pad_db == mix["pad_db"]
