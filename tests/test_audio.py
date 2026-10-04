"""Hearing notes in audio, on synthetic sax-like tones (no hardware)."""
import numpy as np
import pytest

from accompanist import config as c
from accompanist.audio_io import read_wav, write_wav
from accompanist.audio_notes import NoteTracker, yin
from accompanist.cli import main

SR = 48000


def tone(midi, dur, db=-20.0, vibrato_cents=0.0, attack_ms=15.0, seed=0):
    """A reed-like tone: 8 harmonics falling off as 1/n, an attack, a short release, a little noise."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(dur * SR)) / SR
    f = 440 * 2 ** ((midi - 69) / 12) * 2 ** (vibrato_cents / 1200 * np.sin(2 * np.pi * 5 * t))
    ph = 2 * np.pi * np.cumsum(f) / SR
    x = sum(np.sin(k * ph) / k for k in range(1, 9))
    x /= np.max(np.abs(x))
    env = np.minimum(1, t / (attack_ms / 1000)) * np.minimum(1, (dur - t) / 0.02)
    return (10 ** (db / 20) * np.sqrt(2) * x * env + 10 ** (-70 / 20) * rng.standard_normal(len(t))).astype(np.float32)


def rest(dur, db=-70.0):
    return (10 ** (db / 20) * np.random.default_rng(1).standard_normal(int(dur * SR))).astype(np.float32)


def hear(*parts, block=512, **audio):
    sig = np.concatenate(parts)
    tr = NoteTracker(c.from_dict({"audio": audio}).audio, SR)
    ev = []
    for i in range(0, len(sig), block):
        ev += tr.process(sig[i:i + block], i / SR)
    return ev + tr.flush(len(sig) / SR)


def ons(events):
    return [(e.note, round(e.t, 2)) for e in events if e.kind == "on"]


def test_yin_finds_the_fundamental_of_a_reedy_tone():
    for midi in (44, 57, 69, 81):                    # tenor low Ab2 .. A5
        f0, ap = yin(tone(midi, 0.1)[:2048], SR, 60, 1600, 0.15)
        assert 12 * np.log2(f0 / 440) + 69 == pytest.approx(midi, abs=0.05) and ap < 0.15


def test_a_single_note_with_its_real_start_and_end():
    ev = hear(rest(0.2), tone(57, 0.5), rest(0.3))
    assert ons(ev) == [(57, 0.2)]
    off = [e for e in ev if e.kind == "off"]
    assert len(off) == 1 and off[0].t == pytest.approx(0.7, abs=0.03)


def test_legato_line_each_note_at_its_change():
    ev = hear(rest(0.2), tone(60, 0.3), tone(62, 0.3), tone(64, 0.3), rest(0.3))
    assert ons(ev) == [(60, 0.2), (62, 0.5), (64, 0.8)]


def test_tongued_repeated_notes_are_separate_notes():
    ev = hear(rest(0.2), *[tone(62, 0.3, attack_ms=5) for _ in range(3)], rest(0.3))
    assert ons(ev) == [(62, 0.2), (62, 0.5), (62, 0.8)]


def test_vibrato_and_a_swell_stay_one_note():
    assert ons(hear(rest(0.2), tone(67, 0.8, vibrato_cents=30), rest(0.3))) == [(67, 0.2)]
    t = np.arange(int(1.5 * SR)) / SR
    swell = (tone(62, 1.5) * (0.6 + 0.4 * np.sin(2 * np.pi * t))).astype(np.float32)
    assert ons(hear(rest(0.2), swell, rest(0.3))) == [(62, 0.2)]


def test_noise_and_quiet_sound_are_not_notes():
    assert hear(rest(1.0, db=-50)) == []
    assert hear(rest(0.2), tone(60, 0.5, db=-60), rest(0.2)) == []           # below the gate


def test_velocity_follows_the_level():
    v_soft = hear(rest(0.2), tone(60, 0.4, db=-40))[0].velocity
    v_loud = hear(rest(0.2), tone(60, 0.4, db=-14))[0].velocity
    assert v_soft < v_loud


def test_block_size_does_not_matter():
    parts = (rest(0.2), tone(60, 0.3), tone(62, 0.3), rest(0.3))
    assert ons(hear(*parts, block=128)) == ons(hear(*parts, block=4096))


def test_wav_round_trip_and_listen_command(tmp_path, capsys):
    f = tmp_path / "sax.wav"
    write_wav(f, np.concatenate([rest(0.2), tone(60, 0.3), tone(64, 0.3), rest(0.3)]), SR)
    samples, rate = read_wav(f)
    assert rate == SR and len(samples) == int(1.1 * SR)
    take = tmp_path / "sax.jsonl"
    assert main(["listen", str(f), "--save-take", str(take)]) == 0
    out = capsys.readouterr().out
    assert "C4" in out and "E4" in out and "2 notes heard" in out
    assert main(["replay", str(take)]) == 0                       # heard notes drive the engine


def test_audio_inputs_in_config():
    cfg = c.from_dict({"inputs": [{"name": "sax", "audio": "Scarlett Solo", "audio_channel": 1}]})
    assert cfg.inputs[0].is_audio and cfg.inputs[0].port is None
    with pytest.raises(c.ConfigError, match="needs one of port"):
        c.from_dict({"inputs": [{"name": "x"}]})
    with pytest.raises(c.ConfigError, match="needs one of port"):
        c.from_dict({"inputs": [{"port": "LPK", "audio": "Scarlett"}]})
    with pytest.raises(c.ConfigError, match="audio_channel"):
        c.from_dict({"inputs": [{"audio": "Scarlett", "audio_channel": 0}]})


def test_missing_wav_is_readable(tmp_path, capsys):
    assert main(["listen", str(tmp_path / "nope.wav")]) == 2
    assert "not found" in capsys.readouterr().err


def test_record_audio_without_an_audio_input_is_a_readable_error(tmp_path, capsys):
    f = tmp_path / "c.toml"
    f.write_text('[[inputs]]\nport = "LPK25"\n')
    assert main(["monitor", "-c", str(f), "--record-audio", str(tmp_path / "x.wav")]) == 2
    assert "needs an audio input" in capsys.readouterr().err


def test_an_attack_that_reads_an_octave_high_is_corrected_not_a_new_note():
    # A sax attack whose second harmonic leads for ~100 ms: C4 briefly, then the real C3.
    attack = tone(60, 0.1, attack_ms=5)
    body = tone(48, 0.5, attack_ms=1)
    ev = hear(rest(0.2), attack, body, rest(0.3))
    fixed = [e for e in ev if e.corrected]
    assert len(fixed) == 1 and fixed[0].note == 48 and fixed[0].t == pytest.approx(0.2, abs=0.02)
    # With the fix off, the slip shows as two notes.
    assert len([e for e in hear(rest(0.2), attack, body, rest(0.3), octave_fix_ms=0)
                if e.kind == "on"]) == 2


def test_a_real_octave_leap_after_the_settle_time_is_a_new_note():
    ev = hear(rest(0.2), tone(60, 0.3), tone(72, 0.3), rest(0.3))
    assert ons(ev) == [(60, 0.2), (72, 0.5)] and not any(e.corrected for e in ev)


def test_audio_plays_the_accompanist_like_a_keyboard():
    """Sax-like audio -> AudioFeed -> Controller: the pad follows the notes heard."""
    from accompanist.audio_notes import AudioFeed
    from accompanist.controller import Controller
    from accompanist.output import RecordingPort, SafeOutput

    ctl = Controller(c.from_dict({"harmony": {"root": "D"}, "lock": {"auto": False}}),
                     SafeOutput(RecordingPort()))
    heard = []
    feed = AudioFeed(ctl.cfg.audio, SR, lambda t, n, v, src: (heard.append((round(t, 2), n)),
                                                                ctl.on_note(t, n, v)), "sax")
    sig = np.concatenate([rest(0.2)] + [tone(n, 0.4) for n in (62, 66, 69, 62, 66, 69)] + [rest(0.5)])
    for i in range(0, len(sig), 512):
        feed.process(sig[i:i + 512], i / SR)
        ctl.tick(i / SR)
    assert [n for _, n in heard] == [62, 66, 69, 62, 66, 69]
    assert heard[0][0] == pytest.approx(0.2, abs=0.02)
    assert ctl.get_state(len(sig) / SR)["heard"] == "A4"


def test_octave_corrections_are_not_played_twice():
    from accompanist.audio_notes import AudioFeed

    got = []
    feed = AudioFeed(c.from_dict({}).audio, SR, lambda t, n, v, s: got.append(n))
    sig = np.concatenate([rest(0.2), tone(60, 0.1, attack_ms=5), tone(48, 0.5, attack_ms=1), rest(0.3)])
    for i in range(0, len(sig), 512):
        feed.process(sig[i:i + 512], i / SR)
    assert got == [60]            # the slip's note (same name as the corrected C3) only once
