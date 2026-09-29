import pytest

from accompanist import config as c, patterns, simulate
from accompanist.recording import load_take
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


def test_builtin_patterns_all_parse():
    names = patterns.available()
    assert {"basic", "halftime", "soft", "sparse", "waltz", "seven-322"} <= set(names)
    for n in names:
        p = patterns.load(n)
        assert p.hits and all(0 <= s < p.beats * p.steps_per_beat for s, _, _ in p.hits)


@pytest.mark.parametrize("data,msg", [
    ({"beats": 4, "steps_per_beat": 2, "hits": {"kick": "X..."}}, "needs 8"),
    ({"beats": 4, "steps_per_beat": 1, "hits": {"cowbel": "x..."}}, "unknown instrument"),
    ({"beats": 4, "steps_per_beat": 1, "hits": {"kick": "x.o."}}, "not X, x, g"),
    ({"steps_per_beat": 1, "hits": {}}, "needs beats"),
])
def test_bad_patterns_are_readable(data, msg):
    with pytest.raises(c.ConfigError, match=msg):
        patterns.parse("t", data)


def test_user_patterns_override_and_unknown_is_readable(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "patterns").mkdir()
    (tmp_path / "patterns" / "mine.toml").write_text(
        'beats = 5\nsteps_per_beat = 1\n[hits]\nkick = "X.x.."\n')
    assert c.from_dict({"drums": {"pattern": "mine"}}).drums.pattern == "mine"
    with pytest.raises(c.ConfigError, match="unknown drum pattern 'nope'"):
        c.from_dict({"drums": {"pattern": "nope"}})


def drum_hits(res, cfg):
    return [(t, m.note, m.velocity) for t, m in res.timeline
            if m.type == "note_on" and m.channel == cfg.drums.channel - 1]


def replay_steady(extra):
    cfg = c.from_dict({"drums": {"enabled": True, **extra}, "lock": {"auto": False}})
    onsets = load_take(FIXTURES / "melody-90bpm.jsonl")
    return cfg, simulate.run(cfg, onsets=onsets, total=40.0)


def test_drums_play_the_pattern_on_the_beat():
    cfg, res = replay_steady({"pattern": "basic"})
    hits = drum_hits(res, cfg)
    assert hits
    period = 60 / 90
    kicks = [t for t, n, _ in hits if n == 36]
    snares = [t for t, n, _ in hits if n == 38]
    hats = [t for t, n, _ in hits if n == 42]
    assert len(hats) == pytest.approx(2 * (len(kicks) + len(snares)) // 1, abs=4)   # eighths
    gaps = sorted({round((b - a) / period, 1) for a, b in zip(kicks, kicks[1:])})
    assert gaps == [2.0]                            # kick every two beats (1 and 3)
    for t in snares:                                # snares fall between kicks
        assert any(abs(t - k - period) < 0.02 for k in kicks)
    accents = [v for t, n, v in hits if n == 36]
    assert max(accents) > min(accents)              # beat 1 accented over beat 3


def test_a_seven_beat_cycle_repeats_every_seven_beats():
    cfg, res = replay_steady({"pattern": "seven-322"})
    kicks = [t for t, n, _ in drum_hits(res, cfg) if n == 36]
    period = 60 / 90
    gaps = [round((b - a) / period) for a, b in zip(kicks, kicks[1:])]
    assert gaps[:6] == [3, 2, 2, 3, 2, 2]


def test_swing_delays_the_off_steps():
    period = 60 / 90
    for swing in (0.0, 0.33):
        cfg, res = replay_steady({"pattern": "basic", "swing": swing})
        hats = [t for t, n, _ in drum_hits(res, cfg) if n == 42]
        first = [round((b - a) / (period / 2), 2) for a, b in zip(hats, hats[1:])][:2]
        assert first[0] == pytest.approx(1 + swing, abs=0.03)


def test_drums_run_without_the_bass_and_are_off_by_default():
    cfg, res = replay_steady({"pattern": "basic"})
    assert drum_hits(res, cfg)
    cfg = c.from_dict({"drums": {"enabled": True}, "pulse": {"enabled": False}, "lock": {"auto": False}})
    res = simulate.run(cfg, onsets=load_take(FIXTURES / "melody-90bpm.jsonl"), total=40.0)
    assert drum_hits(res, cfg) and not [m for _, m in res.timeline if m.type == "note_on" and m.channel == 1]
    cfg = c.from_dict({})
    res = simulate.run(cfg, onsets=load_take(FIXTURES / "melody-90bpm.jsonl"), total=40.0)
    assert not drum_hits(res, cfg)


def test_panic_stops_scheduled_drum_hits():
    cfg, res = replay_steady({"pattern": "basic"})
    eng = res.engine
    eng.drums.on_beat(res.end_time + 1.0, 0.6)
    eng.panic()
    n = len(res.port.sent)
    eng.tick(res.end_time + 5.0)
    assert not [m for m in res.port.sent[n:] if m.type == "note_on"]
