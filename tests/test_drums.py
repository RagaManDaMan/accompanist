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
    # These tests are about pattern playback: the bar is the pulse's, not the groove heard,
    # and no dynamics (fills, crashes) on top of the pattern.
    cfg = c.from_dict({"drums": {"enabled": True, "dynamics": 0.0, **extra}, "lock": {"auto": False},
                       "groove": {"auto": False}})
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


def test_the_percussionist_plays_latin_on_its_own_channel_in_the_counted_meter():
    from accompanist.patterns import GM_DRUMS

    for taps, pattern in ((4, "latin"), (3, "latin-waltz"), (5, "latin-five"), (6, "bembe"),
                          (7, "latin-seven")):
        actions = [(1.0 + i * 0.5, "tap_tempo") for i in range(taps)]
        cfg = c.from_dict({"drums": {"enabled": True}, "lock": {"auto": False},
                           "percussion": {"enabled": True, "presence": 1.0},
                           "harmony": {"root": "D"}})
        res = simulate.run(cfg, onsets=[], actions=actions, total=12.0)
        assert res.engine.percussion.pattern_name in (None, pattern)
        assert (res.engine.percussion.pattern_name or cfg.percussion.pattern) == pattern
        perc = [m.note for _, m in res.timeline if m.type == "note_on" and m.channel == 10]
        assert perc and set(perc) <= {GM_DRUMS[n] for n in
                                      ("conga_mute", "conga_high", "shaker", "cowbell")}
        drums = [m.note for _, m in res.timeline if m.type == "note_on" and m.channel == 9]
        assert drums                                              # the drummer plays too


def test_the_percussion_steps_forward_in_the_gaps():
    def perc_levels(spotlight):
        notes = [(0.2 + i * 0.25, 62 + i % 5, 80) for i in range(48)]          # 12 s of playing
        taps = [(1.0 + i * 0.5, "tap_tempo") for i in range(4)]
        cfg = c.from_dict({"drums": {"enabled": True}, "lock": {"auto": False},
                           "percussion": {"enabled": True, "spotlight": spotlight,
                                          "dynamics": 0.0, "presence": 1.0},
                           "harmony": {"root": "D"}})
        res = simulate.run(cfg, onsets=notes, actions=taps, total=26.0)
        v = [(t, m.velocity) for t, m in res.timeline if m.type == "note_on" and m.channel == 10]
        playing = [x for t, x in v if 4 < t < 12]
        gap = [x for t, x in v if 20 < t < 26]                             # quiet all round
        return sum(playing) / len(playing), sum(gap) / len(gap)

    playing, gap = perc_levels(1.0)
    assert gap > playing * 1.4
    playing, gap = perc_levels(0.0)
    assert gap == pytest.approx(playing, rel=0.1)


def test_the_percussion_plays_only_in_occasional_spells():
    notes = [(0.2 + i * 0.25, 62 + i % 5, 80) for i in range(400)]        # 100 s of playing
    taps = [(1.0 + i * 0.5, "tap_tempo") for i in range(4)]
    cfg = c.from_dict({"drums": {"enabled": True}, "lock": {"auto": False},
                       "percussion": {"enabled": True, "presence": 0.3, "spell_bars": 4},
                       "harmony": {"root": "D"}})
    res = simulate.run(cfg, onsets=notes, actions=taps, total=100.0)
    perc = [t for t, m in res.timeline if m.type == "note_on" and m.channel == 10]
    bars = {int((t - 3.0) // 2.0) for t in perc}                           # 2 s bars at 120
    share = len(bars) / 48
    assert 0.1 < share < 0.65                                              # now and then
    runs = [b for b in bars if b - 1 not in bars]                          # each spell's start
    assert len(runs) < len(bars) / 3                                       # in spells of bars


def _triplet_run(seconds, triplets=1.0):
    notes = [(0.2 + i * 0.25, 62 + i % 5, 80) for i in range(int(seconds * 4))]   # soloing
    taps = [(1.0 + i * 0.5, "tap_tempo") for i in range(4)]
    cfg = c.from_dict({"drums": {"enabled": True}, "lock": {"auto": False},
                       "percussion": {"enabled": True, "presence": 0.0, "triplets": triplets,
                                      "triplet_build_s": 20.0},
                       "harmony": {"root": "D"}})
    res = simulate.run(cfg, onsets=notes, actions=taps, total=seconds)
    return [t for t, m in res.timeline if m.type == "note_on" and m.channel == 10]


def test_percussion_triplets_build_with_soloing():
    perc = _triplet_run(60.0)
    assert not [t for t in perc if t < 8.0]                           # not straight away
    late = sorted(t for t in perc if t > 30.0)
    assert late                                                       # but later on, yes...
    gaps = {round(b - a, 2) for a, b in zip(late, late[1:]) if b - a < 0.4}
    assert any(abs(g - 1 / 3) < 0.02 or abs(g - 1 / 6) < 0.02 for g in gaps)  # ...in triplets
    assert not _triplet_run(60.0, triplets=0.0)
